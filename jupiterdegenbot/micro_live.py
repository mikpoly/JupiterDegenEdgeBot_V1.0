from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .research_ml import MODEL_SCHEMA_VERSION
from .storage import now

# Kept for backward compatibility with CONFIGURE_MICRO_LIVE.ps1 and Settings.live_enabled().
MICRO_LIVE_CONFIRMATION = "I_ACCEPT_MICRO_LIVE_5_USD_RISK"

_ACTIVE_POSITION_STATUSES = (
    "open",
    "active",
    "pending",
    "claimable",
    "closing",
    "closing_unknown",
)
_ACTIVE_ORDER_STATUSES = (
    "preparing",
    "unsigned_ready",
    "sent",
    "pending_fill",
    "partial_fill",
    "unknown_send",
)
_FINAL_REJECTED_ORDER_STATUSES = ("failed", "simulation_error", "blocked")


def _parse_utc(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _as_positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed


class MicroLiveGate:
    """Persistent LIVE gate with daily risk limits.

    This version has no calendar expiration. It keeps the statistical, wallet,
    simulation and signal checks, while taking order, exposure and position
    limits from Settings/.env. Daily limits reset at 00:00 UTC.

    It never authorizes PAPER exploration signals and it still blocks while
    an order is pending or has an uncertain send status.
    """

    def __init__(self, settings, db):
        self.s = settings
        self.db = db

    def _limits(self) -> dict[str, Any]:
        return {
            "max_stake": _as_positive_float(getattr(self.s, "max_live_stake_usd", 5.0), 5.0),
            "max_orders": _as_positive_int(getattr(self.s, "max_orders_per_day", 1), 1),
            "max_cycle_orders": _as_positive_int(getattr(self.s, "max_live_orders_per_cycle", 1), 1),
            "daily_exposure": _as_positive_float(getattr(self.s, "daily_exposure_limit_usd", 5.0), 5.0),
            "max_open_exposure": _as_positive_float(
                getattr(self.s, "max_total_open_exposure_usd", 5.0), 5.0
            ),
            "max_correlated_exposure": _as_positive_float(
                getattr(self.s, "max_correlated_exposure_usd", 5.0), 5.0
            ),
            "max_open_positions": _as_positive_int(getattr(self.s, "max_open_positions", 1), 1),
            "max_open_events": _as_positive_int(getattr(self.s, "max_open_events", 1), 1),
            "max_open_per_asset": _as_positive_int(
                getattr(self.s, "max_open_positions_per_asset", 1), 1
            ),
            "max_orders_per_asset": _as_positive_int(
                getattr(self.s, "max_orders_per_asset_per_day", 1), 1
            ),
            "max_orders_per_event": _as_positive_int(
                getattr(self.s, "max_orders_per_event_per_day", 1), 1
            ),
        }

    @staticmethod
    def _validate_limits(limits: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        labels = {
            "max_stake": "MAX_LIVE_STAKE_USD",
            "max_orders": "MAX_ORDERS_PER_DAY",
            "max_cycle_orders": "MAX_LIVE_ORDERS_PER_CYCLE",
            "daily_exposure": "DAILY_EXPOSURE_LIMIT_USD",
            "max_open_exposure": "MAX_TOTAL_OPEN_EXPOSURE_USD",
            "max_correlated_exposure": "MAX_CORRELATED_EXPOSURE_USD",
            "max_open_positions": "MAX_OPEN_POSITIONS",
            "max_open_events": "MAX_OPEN_EVENTS",
            "max_open_per_asset": "MAX_OPEN_POSITIONS_PER_ASSET",
            "max_orders_per_asset": "MAX_ORDERS_PER_ASSET_PER_DAY",
            "max_orders_per_event": "MAX_ORDERS_PER_EVENT_PER_DAY",
        }
        for key, label in labels.items():
            if limits[key] <= 0:
                reasons.append(f"{label} doit être supérieur à zéro")

        if limits["max_stake"] > limits["daily_exposure"] + 1e-9:
            reasons.append("MAX_LIVE_STAKE_USD dépasse DAILY_EXPOSURE_LIMIT_USD")
        if limits["max_stake"] > limits["max_open_exposure"] + 1e-9:
            reasons.append("MAX_LIVE_STAKE_USD dépasse MAX_TOTAL_OPEN_EXPOSURE_USD")
        if limits["max_cycle_orders"] > limits["max_orders"]:
            reasons.append("MAX_LIVE_ORDERS_PER_CYCLE dépasse MAX_ORDERS_PER_DAY")
        if limits["max_open_per_asset"] > limits["max_open_positions"]:
            reasons.append("MAX_OPEN_POSITIONS_PER_ASSET dépasse MAX_OPEN_POSITIONS")
        return reasons

    def evaluate(self, persist: bool = True, ignore_order_id: int | None = None) -> dict[str, Any]:
        reasons: list[str] = []
        utc_now = datetime.now(timezone.utc)
        day_start = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
        limits = self._limits()
        reasons.extend(self._validate_limits(limits))

        if not bool(getattr(self.s, "release_live_capable", False)):
            reasons.append("release non capable LIVE")
        if not bool(getattr(self.s, "live_allowed_by_version", False)):
            reasons.append("LIVE_ALLOWED_BY_VERSION=false")
        if not bool(getattr(self.s, "micro_live_enabled", False)):
            reasons.append("MICRO_LIVE_ENABLED=false")
        if str(getattr(self.s, "micro_live_confirmation", "")) != MICRO_LIVE_CONFIRMATION:
            reasons.append("confirmation MICRO-LIVE invalide")
        if str(getattr(self.s, "trading_mode", "")) != "live":
            reasons.append("TRADING_MODE=live requis")
        if not bool(getattr(self.s, "auto_execute", False)):
            reasons.append("AUTO_EXECUTE=true requis")
        if str(getattr(self.s, "live_confirmation", "")) != "I_ACCEPT_REAL_MONEY_RISK":
            reasons.append("LIVE_CONFIRMATION invalide")
        if not bool(getattr(self.s, "live_release_enabled", False)):
            reasons.append("LIVE_RELEASE_ENABLED=false")
        if not bool(getattr(self.s, "live_simulate_before_send", False)):
            reasons.append("simulation Solana obligatoire")

        if float(getattr(self.s, "min_edge", 0.0)) < 0.06:
            reasons.append("MIN_EDGE inférieur à 6%")
        if float(getattr(self.s, "min_confidence", 0.0)) < 0.75:
            reasons.append("MIN_CONFIDENCE inférieur à 75%")
        if float(getattr(self.s, "min_reliability", 0.0)) < 0.68:
            reasons.append("MIN_RELIABILITY inférieur à 68%")

        metrics: dict[str, Any] = {
            "persistent_live": True,
            "day_start_utc": day_start.isoformat(),
            "started_at": None,
            "expires_at": None,
            "remaining_hours": None,
            "configured_limits": limits,
        }

        with self.db.connect(readonly=True) as conn:
            paper = conn.execute(
                """SELECT COUNT(*) settled, COALESCE(SUM(paper_pnl_usd),0) pnl
                   FROM orders WHERE mode='paper' AND paper_result IN ('WON','LOST')"""
            ).fetchone()
            cal = conn.execute(
                "SELECT brier_score,log_loss FROM calibration_results "
                "WHERE asset='ALL' AND horizon='ALL' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            validation = conn.execute(
                "SELECT status,finished_at FROM validation_runs "
                "WHERE kind='walk_forward_neural' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            neural = conn.execute(
                "SELECT COUNT(*) n FROM neural_models WHERE active=1 AND version=?",
                (MODEL_SCHEMA_VERSION,),
            ).fetchone()

            window_start = day_start.isoformat()
            exclude_clause = " AND id<>?" if ignore_order_id is not None else ""
            window_params = (window_start, int(ignore_order_id)) if ignore_order_id is not None else (window_start,)
            pending_params = (int(ignore_order_id),) if ignore_order_id is not None else ()

            live_window = conn.execute(
                """SELECT COUNT(*) n,COALESCE(SUM(amount_usd),0) exposure
                   FROM orders WHERE mode='live' AND created_at>=?
                   AND status NOT IN ('failed','simulation_error','blocked')""" + exclude_clause,
                window_params,
            ).fetchone()
            pending = conn.execute(
                """SELECT COUNT(*) n FROM orders WHERE mode='live'
                   AND status IN ('preparing','unsigned_ready','sent','pending_fill',
                                  'partial_fill','unknown_send')""" + exclude_clause,
                pending_params,
            ).fetchone()
            uncertain = conn.execute(
                """SELECT COUNT(*) n FROM orders WHERE mode='live'
                   AND status IN ('preparing','unsigned_ready','unknown_send')""" + exclude_clause,
                pending_params,
            ).fetchone()
            open_positions = conn.execute(
                """SELECT COUNT(*) n,COALESCE(SUM(cost_usd),0) exposure,
                          COUNT(DISTINCT CASE WHEN event_id<>'' THEN event_id END) events
                   FROM positions
                   WHERE claimed=0 AND status IN ('open','active','pending','claimable',
                                                  'closing','closing_unknown')"""
            ).fetchone()
            incidents = conn.execute(
                """SELECT COUNT(*) n FROM incidents
                   WHERE severity IN ('critical','fatal')
                   AND created_at>=datetime('now','-1 day')"""
            ).fetchone()

            cycle_orders = 0
            current_run_id = None
            if ignore_order_id is not None:
                current = conn.execute("SELECT run_id FROM orders WHERE id=?", (int(ignore_order_id),)).fetchone()
                current_run_id = int(current["run_id"]) if current and current["run_id"] is not None else None
                if current_run_id is not None:
                    row = conn.execute(
                        """SELECT COUNT(*) n FROM orders
                           WHERE mode='live' AND run_id=?
                           AND status NOT IN ('failed','simulation_error','blocked')
                           AND id<>?""",
                        (current_run_id, int(ignore_order_id)),
                    ).fetchone()
                    cycle_orders = int(row["n"] or 0)

        settled = int(paper["settled"] or 0)
        pnl = float(paper["pnl"] or 0.0)
        brier = float(cal["brier_score"]) if cal and cal["brier_score"] is not None else None
        log_loss = float(cal["log_loss"]) if cal and cal["log_loss"] is not None else None
        active_models = int(neural["n"] or 0)
        live_count = int(live_window["n"] or 0)
        live_exposure = float(live_window["exposure"] or 0.0)
        pending_count = int(pending["n"] or 0)
        uncertain_count = int(uncertain["n"] or 0)
        open_count = int(open_positions["n"] or 0)
        open_exposure = float(open_positions["exposure"] or 0.0)
        open_events = int(open_positions["events"] or 0)
        incident_count = int(incidents["n"] or 0)

        metrics.update(
            {
                "paper_settled": settled,
                "paper_realized_pnl_usd": pnl,
                "paper_brier": brier,
                "paper_log_loss": log_loss,
                "active_neural_models": active_models,
                "live_orders_in_window": live_count,
                "live_orders_today": live_count,
                "live_exposure_in_window_usd": live_exposure,
                "live_exposure_today_usd": live_exposure,
                "live_orders_in_current_cycle": cycle_orders,
                "current_run_id": current_run_id,
                "pending_live_orders": pending_count,
                "uncertain_live_orders": uncertain_count,
                "allow_multiple_pending": bool(getattr(self.s, "live_allow_multiple_pending", False)),
                "open_live_positions": open_count,
                "open_live_events": open_events,
                "open_live_exposure_usd": open_exposure,
                "critical_incidents_24h": incident_count,
            }
        )

        if settled < int(getattr(self.s, "micro_live_min_paper_settled", 20)):
            reasons.append(f"PAPER réglés {settled}/{int(getattr(self.s, 'micro_live_min_paper_settled', 20))}")
        if pnl <= 0:
            reasons.append("P&L PAPER réalisé non positif")
        if brier is None or brier > float(getattr(self.s, "micro_live_max_paper_brier", 0.30)):
            reasons.append("Brier PAPER absent ou trop élevé")
        if log_loss is None or log_loss > float(getattr(self.s, "micro_live_max_paper_log_loss", 0.90)):
            reasons.append("log-loss PAPER absente ou trop élevée")
        if active_models < int(getattr(self.s, "micro_live_min_active_models", 1)):
            reasons.append("aucun modèle neuronal actif")
        if not validation or str(validation["status"]) != "passed":
            reasons.append("walk-forward non validé")

        if live_count >= limits["max_orders"]:
            reasons.append(f"plafond ordres LIVE atteint {live_count}/{limits['max_orders']}")
        if live_exposure >= limits["daily_exposure"] - 1e-9:
            reasons.append(
                f"plafond exposition quotidienne atteint {live_exposure:.2f}/{limits['daily_exposure']:.2f}$"
            )
        if cycle_orders >= limits["max_cycle_orders"]:
            reasons.append(f"plafond ordres du cycle atteint {cycle_orders}/{limits['max_cycle_orders']}")
        if pending_count > 0:
            if not bool(getattr(self.s, "live_allow_multiple_pending", False)):
                reasons.append("ordre LIVE en attente ou incertain")
            elif uncertain_count > 0:
                # A truly ambiguous/unfinished send remains a global stop.
                # Normal sent/pending_fill/partial_fill orders are already
                # counted in exposure/order limits and guarded per market/event.
                reasons.append("ordre LIVE au statut de sécurité incertain")
        if open_count >= limits["max_open_positions"]:
            reasons.append(f"plafond positions ouvertes atteint {open_count}/{limits['max_open_positions']}")
        if open_events >= limits["max_open_events"]:
            reasons.append(f"plafond événements ouverts atteint {open_events}/{limits['max_open_events']}")
        if open_exposure >= limits["max_open_exposure"] - 1e-9:
            reasons.append(
                f"plafond exposition ouverte atteint {open_exposure:.2f}/{limits['max_open_exposure']:.2f}$"
            )
        if incident_count > 0:
            reasons.append("incident critique LIVE dans les dernières 24h")

        result = {"passed": not reasons, "reasons": reasons, "metrics": metrics, "checked_at": now()}
        if persist:
            with self.db.connect() as conn:
                conn.execute(
                    "INSERT INTO live_gate_checks(passed,reasons_json,metrics_json,checked_at) VALUES(?,?,?,?)",
                    (
                        int(result["passed"]),
                        json.dumps(reasons, ensure_ascii=False),
                        json.dumps({"micro_live": True, "multi_order": True, **metrics}, ensure_ascii=False),
                        result["checked_at"],
                    ),
                )
        return result

    def require(self, ignore_order_id: int | None = None) -> None:
        result = self.evaluate(persist=True, ignore_order_id=ignore_order_id)
        if not result["passed"]:
            raise RuntimeError("Verrou MICRO-LIVE: " + " | ".join(result["reasons"]))

    def require_signal(self, signal, ignore_order_id: int | None = None) -> None:
        self.require(ignore_order_id=ignore_order_id)
        limits = self._limits()

        signal_type = str(getattr(signal, "signal_type", "") or "").upper()
        question = str(getattr(signal, "question", "") or "").casefold()
        if "PAPER_EXPLORE" in signal_type:
            raise RuntimeError("signal d'exploration PAPER interdit en MICRO-LIVE")
        if "touch" in question or " hit " in f" {question} ":
            raise RuntimeError("marché touch/hit interdit pendant le test MICRO-LIVE")

        stake = float(getattr(signal, "stake_usd", 0.0) or 0.0)
        min_order = float(getattr(self.s, "min_order_usd", 5.0))
        if stake < min_order - 1e-9:
            raise RuntimeError(f"mise {stake:.2f}$ inférieure au minimum {min_order:.2f}$")
        if stake > limits["max_stake"] + 1e-9:
            raise RuntimeError(
                f"mise {stake:.2f}$ supérieure au plafond LIVE {limits['max_stake']:.2f}$"
            )

        min_edge = float(getattr(self.s, "min_edge", 0.10))
        min_confidence = float(getattr(self.s, "min_confidence", 0.80))
        min_reliability = float(getattr(self.s, "min_reliability", 0.70))
        if float(getattr(signal, "edge", 0.0) or 0.0) < min_edge:
            raise RuntimeError(f"edge inférieur à {min_edge:.1%}")
        if float(getattr(signal, "confidence", 0.0) or 0.0) < min_confidence:
            raise RuntimeError(f"confiance inférieure à {min_confidence:.1%}")
        if float(getattr(signal, "reliability", 0.0) or 0.0) < min_reliability:
            raise RuntimeError(f"fiabilité inférieure à {min_reliability:.1%}")

        asset = str(getattr(signal, "asset", "") or "").upper()
        event_id = str(getattr(signal, "event_id", "") or "")
        market_id = str(getattr(signal, "market_id", "") or "")
        window_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        exclude_clause = " AND id<>?" if ignore_order_id is not None else ""
        exclude_params: tuple[Any, ...] = (int(ignore_order_id),) if ignore_order_id is not None else ()

        with self.db.connect(readonly=True) as conn:
            if asset:
                model = conn.execute(
                    "SELECT 1 FROM neural_models WHERE active=1 AND version=? AND model_key LIKE ? LIMIT 1",
                    (MODEL_SCHEMA_VERSION, f"{asset}:%"),
                ).fetchone()
                if not model:
                    raise RuntimeError(f"aucun modèle neuronal actif pour {asset}")

            open_summary = conn.execute(
                """SELECT COUNT(*) n,COALESCE(SUM(cost_usd),0) exposure
                   FROM positions WHERE claimed=0
                   AND status IN ('open','active','pending','claimable','closing','closing_unknown')"""
            ).fetchone()
            open_count = int(open_summary["n"] or 0)
            open_exposure = float(open_summary["exposure"] or 0.0)
            if open_count + 1 > limits["max_open_positions"]:
                raise RuntimeError(
                    f"nouvelle position dépasserait MAX_OPEN_POSITIONS "
                    f"({open_count + 1}/{limits['max_open_positions']})"
                )
            if open_exposure + stake > limits["max_open_exposure"] + 1e-9:
                raise RuntimeError(
                    f"nouvelle mise dépasserait MAX_TOTAL_OPEN_EXPOSURE_USD "
                    f"({open_exposure + stake:.2f}/{limits['max_open_exposure']:.2f}$)"
                )

            live_summary = conn.execute(
                """SELECT COUNT(*) n,COALESCE(SUM(amount_usd),0) exposure
                   FROM orders WHERE mode='live' AND created_at>=?
                   AND status NOT IN ('failed','simulation_error','blocked')""" + exclude_clause,
                (window_start, *exclude_params),
            ).fetchone()
            live_count = int(live_summary["n"] or 0)
            live_exposure = float(live_summary["exposure"] or 0.0)
            if live_count + 1 > limits["max_orders"]:
                raise RuntimeError(
                    f"nouvel ordre dépasserait MAX_ORDERS_PER_DAY "
                    f"({live_count + 1}/{limits['max_orders']})"
                )
            if live_exposure + stake > limits["daily_exposure"] + 1e-9:
                raise RuntimeError(
                    f"nouvelle mise dépasserait DAILY_EXPOSURE_LIMIT_USD "
                    f"({live_exposure + stake:.2f}/{limits['daily_exposure']:.2f}$)"
                )

            if asset:
                asset_positions = conn.execute(
                    """SELECT COUNT(*) n,COALESCE(SUM(cost_usd),0) exposure
                       FROM positions WHERE claimed=0 AND UPPER(COALESCE(asset,''))=?
                       AND status IN ('open','active','pending','claimable','closing','closing_unknown')""",
                    (asset,),
                ).fetchone()
                asset_count = int(asset_positions["n"] or 0)
                asset_exposure = float(asset_positions["exposure"] or 0.0)
                if asset_count + 1 > limits["max_open_per_asset"]:
                    raise RuntimeError(
                        f"nouvelle position {asset} dépasserait MAX_OPEN_POSITIONS_PER_ASSET "
                        f"({asset_count + 1}/{limits['max_open_per_asset']})"
                    )
                if asset_exposure + stake > limits["max_correlated_exposure"] + 1e-9:
                    raise RuntimeError(
                        f"exposition corrélée {asset} dépasserait le plafond "
                        f"({asset_exposure + stake:.2f}/{limits['max_correlated_exposure']:.2f}$)"
                    )
                asset_orders = conn.execute(
                    """SELECT COUNT(*) n FROM orders WHERE mode='live' AND created_at>=?
                       AND UPPER(COALESCE(asset,''))=?
                       AND status NOT IN ('failed','simulation_error','blocked')""" + exclude_clause,
                    (window_start, asset, *exclude_params),
                ).fetchone()
                if int(asset_orders["n"] or 0) + 1 > limits["max_orders_per_asset"]:
                    raise RuntimeError(
                        f"nouvel ordre {asset} dépasserait MAX_ORDERS_PER_ASSET_PER_DAY"
                    )

            if event_id:
                event_orders = conn.execute(
                    """SELECT COUNT(*) n FROM orders WHERE mode='live' AND created_at>=?
                       AND event_id=? AND status NOT IN ('failed','simulation_error','blocked')"""
                    + exclude_clause,
                    (window_start, event_id, *exclude_params),
                ).fetchone()
                if int(event_orders["n"] or 0) + 1 > limits["max_orders_per_event"]:
                    raise RuntimeError(
                        "nouvel ordre dépasserait MAX_ORDERS_PER_EVENT_PER_DAY pour cet événement"
                    )

            if market_id:
                market_order = conn.execute(
                    """SELECT 1 FROM orders WHERE mode='live' AND created_at>=?
                       AND market_id=? AND status NOT IN ('failed','simulation_error','blocked')"""
                    + exclude_clause
                    + " LIMIT 1",
                    (window_start, market_id, *exclude_params),
                ).fetchone()
                if market_order:
                    raise RuntimeError("un ordre LIVE existe déjà pour ce marché aujourd'hui UTC")
