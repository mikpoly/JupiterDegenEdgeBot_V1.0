from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .market_parser import looks_like_crypto_market, parse_crypto_market
from .models import EngineEstimate, Market, Signal
from .probability import TIMED_DIRECTION_MODEL_NAME, clamp


@dataclass(slots=True)
class RiskDecision:
    accepted: bool
    reason: str
    signal: Signal | None = None


def _mode_call(db, method: str, *args, mode: str):
    """Use mode-aware storage without breaking small legacy test doubles."""
    fn = getattr(db, method)
    try:
        return fn(*args, mode=mode)
    except TypeError:
        return fn(*args)


def _kelly_stake(settings, probability: float, price: float) -> float:
    price = clamp(float(price), 0.001, 0.999)
    probability = clamp(float(probability), 0.001, 0.999)
    b = (1.0 - price) / price
    full_kelly = (b * probability - (1.0 - probability)) / max(b, 1e-9)
    fraction = max(0.0, full_kelly) * max(0.0, float(settings.kelly_fraction))
    raw = float(settings.starting_bankroll_usd) * fraction
    minimum = float(settings.min_order_usd)
    cap = float(settings.max_stake_usd)
    if str(settings.trading_mode).casefold() == "live":
        cap = min(cap, float(settings.max_live_stake_usd))
    return round(clamp(max(raw, minimum), minimum, max(minimum, cap)), 2)


def _trade_options(market: Market, p_yes: float):
    if bool(getattr(market, "one_sided_yes", False)):
        return (("YES", float(market.yes_price), float(market.sell_yes_price), p_yes),)
    return (
        ("YES", float(market.yes_price), float(market.sell_yes_price), p_yes),
        ("NO", float(market.no_price), float(market.sell_no_price), 1.0 - p_yes),
    )


def _timed_live_calibration_reason(settings, db, spec) -> str:
    if not bool(getattr(settings, "timed_direction_live_enabled", False)):
        return "direction timed LIVE désactivée; apprentissage PAPER/SHADOW uniquement"
    remaining_hours = (float(spec.expiry_ts) - time.time()) / 3600.0
    minimum_hours = float(getattr(settings, "timed_direction_live_min_hours_to_close", 0.03))
    if remaining_hours < minimum_hours:
        return f"direction timed trop proche de l'échéance: {remaining_hours:.3f} h < {minimum_hours:.3f} h"
    try:
        with db.connect(readonly=True) as conn:
            row = conn.execute(
                """SELECT COUNT(DISTINCT event_id) n, AVG(brier_score) brier, AVG(log_loss) log_loss
                   FROM shadow_predictions
                   WHERE status='RESOLVED' AND asset=? AND settlement_kind='timed_direction'
                     AND model_name=?""",
                (spec.asset, TIMED_DIRECTION_MODEL_NAME),
            ).fetchone()
    except Exception as exc:
        return f"calibration timed indisponible: {exc}"
    n = int(row["n"] or 0) if row is not None else 0
    brier = float(row["brier"]) if row is not None and row["brier"] is not None else None
    log_loss = float(row["log_loss"]) if row is not None and row["log_loss"] is not None else None
    minimum = int(getattr(settings, "timed_direction_live_min_settled", 20))
    if n < minimum:
        return f"calibration timed V2 {spec.asset}: {n}/{minimum} événements réglés ({TIMED_DIRECTION_MODEL_NAME})"
    max_brier = float(getattr(settings, "timed_direction_live_max_brier", 0.22))
    max_log = float(getattr(settings, "timed_direction_live_max_log_loss", 0.68))
    if brier is None or brier > max_brier:
        return f"calibration timed V2 {spec.asset}: Brier {brier if brier is not None else 'absent'} > {max_brier:.3f}"
    if log_loss is None or log_loss > max_log:
        return f"calibration timed V2 {spec.asset}: log-loss {log_loss if log_loss is not None else 'absent'} > {max_log:.3f}"
    return ""


def evaluate_market(settings, db, market: Market, estimate: EngineEstimate) -> RiskDecision:
    if not estimate.engine.startswith("DEGEN_QUANT"):
        return RiskDecision(False, "barrière DEGEN_ONLY: moteur non quantitatif")
    if not looks_like_crypto_market(market):
        return RiskDecision(False, "barrière DEGEN_ONLY: marché non crypto")
    if not estimate.supported:
        return RiskDecision(False, estimate.reject_reason or "moteur quantitatif non exploitable")

    spec = parse_crypto_market(market)
    if spec.ambiguous:
        return RiskDecision(False, spec.reject_reason or "marché ambigu")
    if settings.require_resolution_source and not spec.resolution_source:
        return RiskDecision(False, "source de résolution absente")

    live_mode = str(settings.trading_mode).casefold() == "live"
    if live_mode and spec.settlement_kind == "timed_direction":
        timed_reason = _timed_live_calibration_reason(settings, db, spec)
        if timed_reason:
            return RiskDecision(False, timed_reason)

    p_yes = clamp(float(estimate.probability_yes), 0.001, 0.999)
    options = _trade_options(market, p_yes)
    outcome, price, exit_price, probability = max(options, key=lambda row: row[3] - row[1])
    edge = probability - price
    spread = price - exit_price if exit_price > 0 else None

    if not (settings.min_trade_price <= price <= settings.max_trade_price):
        return RiskDecision(False, f"prix {price:.3f} hors plage")
    if settings.require_exit_price_for_new_buy and exit_price <= 0:
        return RiskDecision(False, "prix de sortie absent")
    if spread is not None and spread > float(settings.max_entry_exit_spread):
        return RiskDecision(False, f"spread {spread:.3f} > {settings.max_entry_exit_spread:.3f}")
    spread_ratio_limit = float(settings.live_max_entry_exit_spread_ratio if live_mode else settings.max_entry_exit_spread_ratio)
    spread_ratio = (spread / price) if spread is not None and price > 0 else 0.0
    if spread is not None and spread_ratio > spread_ratio_limit:
        return RiskDecision(False, f"spread relatif {spread_ratio:.1%} > {spread_ratio_limit:.1%}")
    volume_min = float(settings.live_min_market_volume_usd if live_mode else settings.min_market_volume_usd)
    liquidity_min = float(settings.live_min_market_liquidity_usd if live_mode else settings.min_market_liquidity_usd)
    # Missing Jupiter volume/liquidity metadata is not trusted here. In LIVE it
    # is checked again against the official orderbook immediately before signing.
    if volume_min > 0 and market.volume_usd > 0 and market.volume_usd < volume_min:
        return RiskDecision(False, f"volume {market.volume_usd:.2f}$ insuffisant")
    if liquidity_min > 0 and market.liquidity_usd > 0 and market.liquidity_usd < liquidity_min:
        return RiskDecision(False, f"liquidité {market.liquidity_usd:.2f}$ insuffisante")
    if edge < settings.min_edge:
        return RiskDecision(False, f"edge {edge:.3f} < {settings.min_edge:.3f}")
    if edge > settings.edge_hard_cap:
        return RiskDecision(False, f"edge {edge:.3f} > plafond {settings.edge_hard_cap:.3f}")
    if estimate.confidence < settings.min_confidence:
        return RiskDecision(False, f"confiance {estimate.confidence:.3f} < {settings.min_confidence:.3f}")
    if estimate.reliability < settings.min_reliability:
        return RiskDecision(False, f"fiabilité {estimate.reliability:.3f} < {settings.min_reliability:.3f}")

    source_count = len({o.source for o in estimate.observations if o.source and o.metadata.get("quantitative", True)})
    if source_count < int(settings.crypto_min_sources):
        return RiskDecision(False, f"seulement {source_count} source(s) quantitative(s)")
    if estimate.source_agreement < float(settings.data_min_source_agreement):
        return RiskDecision(False, f"accord sources {estimate.source_agreement:.3f} < {settings.data_min_source_agreement:.3f}")
    mode = str(settings.trading_mode).casefold()
    if _mode_call(db, "market_ordered_today", market.id, mode=mode):
        return RiskDecision(False, "marché exact déjà engagé aujourd'hui")
    if settings.max_orders_per_event_per_day > 0 and market.event_id:
        if _mode_call(db, "event_orders_today", market.event_id, mode=mode) >= settings.max_orders_per_event_per_day:
            return RiskDecision(False, "limite par événement atteinte")
    if settings.max_orders_per_asset_per_day > 0:
        if _mode_call(db, "asset_orders_today", spec.asset, mode=mode) >= settings.max_orders_per_asset_per_day:
            return RiskDecision(False, f"limite quotidienne {spec.asset} atteinte")

    paper_mode = str(settings.trading_mode).casefold() == "paper"
    if paper_mode and hasattr(db, "paper_open_for_asset"):
        open_count, open_asset_exposure = db.paper_open_for_asset(spec.asset)
        total_open_count, total_open_exposure, open_events = db.paper_open_summary()
    else:
        open_count, open_asset_exposure = db.open_positions_for_asset(spec.asset)
        if hasattr(db, "live_open_summary"):
            total_open_count, total_open_exposure, open_events = db.live_open_summary()
        else:
            total_open_count, total_open_exposure, open_events = open_count, open_asset_exposure, open_count
    if settings.max_open_positions_per_asset > 0 and open_count >= settings.max_open_positions_per_asset:
        return RiskDecision(False, f"positions ouvertes {spec.asset}: {open_count}/{settings.max_open_positions_per_asset}")
    if settings.max_open_positions > 0 and total_open_count >= settings.max_open_positions:
        return RiskDecision(False, f"plafond positions ouvertes {total_open_count}/{settings.max_open_positions}")
    if settings.max_open_events > 0 and open_events >= settings.max_open_events:
        return RiskDecision(False, f"plafond événements ouverts {open_events}/{settings.max_open_events}")

    orders, exposure = _mode_call(db, "orders_today", mode=mode)
    if orders >= int(settings.max_orders_per_day):
        return RiskDecision(False, "nombre maximal d'ordres quotidien atteint")

    stake = _kelly_stake(settings, probability, price)
    if exposure + stake > float(settings.daily_exposure_limit_usd) + 1e-9:
        return RiskDecision(False, "limite d'exposition quotidienne atteinte")
    correlated = (
        db.paper_correlated_exposure(spec.event_family)
        if paper_mode and hasattr(db, "paper_correlated_exposure")
        else db.correlated_exposure(spec.event_family)
    )
    if correlated + stake > float(settings.max_correlated_exposure_usd) + 1e-9:
        return RiskDecision(False, f"exposition corrélée {correlated + stake:.2f}$ > {settings.max_correlated_exposure_usd:.2f}$")
    if total_open_exposure + stake > float(settings.max_total_open_exposure_usd) + 1e-9:
        return RiskDecision(False, "exposition totale potentielle dépassée")

    score = edge * math.sqrt(max(0.01, estimate.confidence * estimate.reliability))
    signal = Signal(
        market_id=market.id, question=market.question, outcome=outcome, price=price,
        probability=probability, confidence=float(estimate.confidence),
        reliability=float(estimate.reliability), edge=edge, score=score, stake_usd=stake,
        signal_type=estimate.engine, reasoning=estimate.reasoning, evidence=list(estimate.evidence),
        source_count=source_count, source_agreement=float(estimate.source_agreement),
        asset=spec.asset, expiry=spec.expiry_ts, resolution_source=spec.resolution_source,
        volatility=estimate.volatility, liquidity=market.liquidity_usd or market.volume_usd,
        entry_price=price, exit_price=exit_price, spread=float(spread or 0.0),
        event_family=spec.event_family, evidence_json=estimate.evidence_json,
    )
    return RiskDecision(True, "accepted", signal)


def evaluate_market_exploration(settings, db, market: Market, estimate: EngineEstimate) -> RiskDecision:
    """Relaxed PAPER-only gate used to collect labels, never for LIVE execution.

    Core data integrity, parser, source agreement, exit price and hard edge-cap
    protections remain. Only the commercial thresholds are relaxed.
    """
    mode = str(settings.trading_mode).casefold()
    paper_learning_mode = mode == "paper" or (
        mode == "live" and bool(getattr(settings, "paper_parallel_live_enabled", False))
    )
    if not paper_learning_mode or not bool(getattr(settings, "paper_exploration_enabled", True)):
        return RiskDecision(False, "exploration PAPER désactivée")
    if not estimate.engine.startswith("DEGEN_QUANT") or not estimate.supported:
        return RiskDecision(False, estimate.reject_reason or "moteur non exploitable")
    spec = parse_crypto_market(market)
    if spec.ambiguous or not looks_like_crypto_market(market):
        return RiskDecision(False, spec.reject_reason or "marché ambigu/non crypto")
    if settings.require_resolution_source and not spec.resolution_source:
        return RiskDecision(False, "source de résolution absente")

    p_yes = clamp(float(estimate.probability_yes), 0.001, 0.999)
    options = _trade_options(market, p_yes)
    outcome, price, exit_price, probability = max(options, key=lambda row: row[3] - row[1])
    edge = probability - price
    spread = price - exit_price if exit_price > 0 else None
    spread_ratio = (spread / price) if spread is not None and price > 0 else 0.0

    if not (settings.min_trade_price <= price <= settings.max_trade_price):
        return RiskDecision(False, f"exploration: prix {price:.3f} hors plage")
    if settings.require_exit_price_for_new_buy and exit_price <= 0:
        return RiskDecision(False, "exploration: prix de sortie absent")
    if spread is not None and spread > float(settings.max_entry_exit_spread):
        return RiskDecision(False, f"exploration: spread {spread:.3f} trop élevé")
    if spread is not None and spread_ratio > float(settings.paper_exploration_max_spread_ratio):
        return RiskDecision(False, f"exploration: spread relatif {spread_ratio:.1%} trop élevé")
    if edge < float(settings.paper_exploration_min_edge):
        return RiskDecision(False, f"exploration: edge {edge:.3f} < {settings.paper_exploration_min_edge:.3f}")
    if edge > float(settings.edge_hard_cap):
        return RiskDecision(False, f"exploration: edge {edge:.3f} > plafond")
    if estimate.confidence < float(settings.paper_exploration_min_confidence):
        return RiskDecision(False, f"exploration: confiance {estimate.confidence:.3f} insuffisante")
    if estimate.reliability < float(settings.paper_exploration_min_reliability):
        return RiskDecision(False, f"exploration: fiabilité {estimate.reliability:.3f} insuffisante")
    source_count = len({o.source for o in estimate.observations if o.source and o.metadata.get("quantitative", True)})
    if source_count < int(settings.crypto_min_sources):
        return RiskDecision(False, f"exploration: seulement {source_count} source(s)")
    if estimate.source_agreement < float(settings.data_min_source_agreement):
        return RiskDecision(False, "exploration: désaccord des sources")
    if _mode_call(db, "market_ordered_today", market.id, mode="paper"):
        return RiskDecision(False, "exploration: marché déjà engagé aujourd'hui")

    with db.connect(readonly=True) as conn:
        daily = conn.execute(
            """SELECT COUNT(*) n FROM orders o LEFT JOIN signals s ON s.id=o.signal_id
               WHERE o.mode='paper' AND date(o.created_at)=date('now')
               AND s.signal_type LIKE '%PAPER_EXPLORE%'"""
        ).fetchone()
        open_row = conn.execute(
            """SELECT COUNT(*) n FROM orders o LEFT JOIN signals s ON s.id=o.signal_id
               WHERE o.mode='paper' AND o.status='paper_filled'
               AND s.signal_type LIKE '%PAPER_EXPLORE%'"""
        ).fetchone()
    if int(daily["n"] or 0) >= int(settings.paper_exploration_max_orders_per_day):
        return RiskDecision(False, "exploration: limite quotidienne atteinte")
    if int(open_row["n"] or 0) >= int(settings.paper_exploration_max_open_positions):
        return RiskDecision(False, "exploration: trop de positions ouvertes")

    stake = max(0.10, float(settings.paper_exploration_stake_usd))
    score = edge * math.sqrt(max(0.01, estimate.confidence * estimate.reliability))
    signal = Signal(
        market_id=market.id, question=market.question, outcome=outcome, price=price,
        probability=probability, confidence=float(estimate.confidence),
        reliability=float(estimate.reliability), edge=edge, score=score, stake_usd=stake,
        signal_type=estimate.engine + "_PAPER_EXPLORE", reasoning=estimate.reasoning,
        evidence=list(estimate.evidence), source_count=source_count,
        source_agreement=float(estimate.source_agreement), asset=spec.asset,
        expiry=spec.expiry_ts, resolution_source=spec.resolution_source,
        volatility=estimate.volatility, liquidity=market.liquidity_usd or market.volume_usd,
        entry_price=price, exit_price=exit_price, spread=float(spread or 0.0),
        event_family=spec.event_family, evidence_json={**estimate.evidence_json, "paper_exploration": True},
    )
    return RiskDecision(True, "accepted_exploration", signal)
