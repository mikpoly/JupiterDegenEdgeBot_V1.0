from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .execution import AmbiguousSendError, LiveFundingPause, Executor, _first, _order_part
from .calibration import calculate_calibration
from .http import HttpClient
from .jupiter import JupiterClient
from .memory import available_gb, require_free_memory
from .models import EngineEstimate, Market
from .paper import PaperTracker
from .probability import CryptoProbabilityEngine, TIMED_DIRECTION_MODEL_NAME
from .market_parser import parse_crypto_market
from .risk import evaluate_market, evaluate_market_exploration
from .storage import DB
from .wallet import Wallet
from .lifecycle import LiveLifecycle
from .local_ai import LocalAIReviewer, apply_ai_review
from .shadow import ShadowPredictionTracker

log = logging.getLogger(__name__)

# Serializes the final mutable-risk recheck + real order path across the normal
# scanner and the independent TIMED worker. Analysis remains concurrent, but two
# threads can never commit LIVE candidates at the same instant.
_LIVE_EXECUTION_LOCK = threading.RLock()


@dataclass(slots=True)
class PreparedCandidate:
    run_id: int
    market: Market
    estimate: EngineEstimate
    signal: object
    signal_id: int


class BotEngine:
    def __init__(self, settings, db: DB):
        self.s = settings
        self.db = db
        self.http = HttpClient(settings)
        self.jupiter = JupiterClient(settings)
        self.wallet = Wallet(settings)
        self.quant = CryptoProbabilityEngine(settings, self.http, db)
        self.ai = LocalAIReviewer(settings)
        self.executor = Executor(settings, db, self.jupiter, self.wallet)
        self.lifecycle = LiveLifecycle(settings, db, self.jupiter, self.wallet)
        self.paper = PaperTracker(settings, db, self.jupiter)
        self.shadow = ShadowPredictionTracker(settings, db, self.jupiter)
        self._live_block_reason = ""
        self._cycle_live_orders = 0
        self._cycle_live_exposure_usd = 0.0
        self._positions_snapshot_complete = False
        self._open_live_markets: dict[str, list[dict]] = {}
        self._open_live_events: dict[str, list[dict]] = {}
        self._open_position_cost_usd = 0.0
        # Persistent only for the dedicated short-horizon worker. A market is
        # normally analysed once per process/window; transient source failures
        # get a small bounded retry allowance before the window is abandoned.
        self._timed_fast_seen: dict[str, float] = {}
        self._timed_fast_attempts: dict[str, int] = {}
        self._timed_fast_worker_mode = False

    def enable_timed_fast_worker_mode(self) -> None:
        """Enable bounded read latency on this engine instance only.

        The normal long-cycle engine is not changed. All real-order mutation
        methods keep their existing safety gates and transaction semantics.
        """
        self._timed_fast_worker_mode = True
        # Quantitative public market-data reads are separate from real-order
        # submission.  Bound them only on this dedicated worker instance.
        if hasattr(self.http, "enable_fast_read_mode"):
            self.http.enable_fast_read_mode(
                timeout_seconds=3.0, max_attempts=1,
                max_rate_wait_seconds=0.75, min_cache_seconds=2.0,
            )
        if hasattr(self.quant, "enable_timed_fast_mode"):
            self.quant.enable_timed_fast_mode()
        if hasattr(self.jupiter, "enable_fast_read_mode"):
            self.jupiter.enable_fast_read_mode(timeout_seconds=6.0, max_retries=0)
        if hasattr(self.wallet, "enable_fast_read_mode"):
            self.wallet.enable_fast_read_mode(timeout_seconds=2.5, max_urls=2)

    def recover(self) -> int:
        recovered = self.db.recover_runs()
        if recovered:
            log.warning("%d cycle(s) précédent(s) marqué(s) interrupted", recovered)
        return recovered

    def maintenance(self) -> dict:
        result = {
            "positions": 0,
            "positions_snapshot_complete": False,
            "open_position_guards": [],
            "reconcile": {"checked": 0, "filled": 0, "failed": 0, "pending": 0, "errors": []},
            "claims": {"claimable": 0, "claimed": 0, "errors": []},
            "paper": {"checked": 0, "updated": 0, "errors": [], "summary": self.db.paper_summary()},
            "shadow": {"checked": 0, "resolved": 0, "errors": [], "summary": self.db.shadow_summary()},
            "learning": {"profiles": 0, "active": 0, "error": ""},
            "history_memory": self.db.history_storage_status(getattr(self.s, "history_max_db_gb", 10.0)),
            "summary": self.db.live_summary(),
            "errors": [],
        }
        if self.quant.memory is not None and bool(getattr(self.s, "adaptive_learning_enabled", True)):
            try:
                profiles = self.quant.memory.rebuild()
                result["learning"] = {
                    "profiles": len(profiles),
                    "active": sum(1 for profile in profiles if profile.active),
                    "error": "",
                }
            except Exception as exc:
                result["learning"] = {"profiles": 0, "active": 0, "error": str(exc)}
        if not self.s.jupiter_api_key:
            return result
        paper_parallel = (
            self.s.trading_mode != "live"
            or bool(getattr(self.s, "paper_parallel_live_enabled", False))
        )

        # PAPER and SHADOW learning are independent from the LIVE wallet.
        # When parallel PAPER is enabled, both books keep resolving while LIVE
        # lifecycle/reconciliation continues below.
        if paper_parallel and bool(getattr(self.s, "paper_tracking_enabled", True)):
            try:
                result["paper"] = self.paper.refresh()
                result["paper"]["parallel_live"] = self.s.trading_mode == "live"
                result["paper"]["calibration"] = calculate_calibration(self.db, persist=True)["overall"]
            except Exception as exc:
                result["errors"].append(f"paper: {exc}")
        elif self.s.trading_mode == "live":
            result["paper"] = {"disabled_in_live": True}

        if bool(getattr(self.s, "shadow_learning_enabled", True)):
            try:
                result["shadow"] = self.shadow.refresh()
            except Exception as exc:
                result["errors"].append(f"shadow: {exc}")

        if self.quant.memory is not None and bool(getattr(self.s, "adaptive_learning_enabled", True)):
            try:
                profiles = self.quant.memory.rebuild()
                result["learning"] = {
                    "profiles": len(profiles),
                    "active": sum(1 for profile in profiles if profile.active),
                    "error": "",
                }
            except Exception as exc:
                result["learning"] = {"profiles": 0, "active": 0, "error": str(exc)}

        if self.s.trading_mode != "live":
            return result

        try:
            live = self.lifecycle.maintenance(auto_claim=True)
            # Preserve the independent PAPER/SHADOW sections prepared above.
            paper_state = result.get("paper")
            shadow_state = result.get("shadow")
            learning_state = result.get("learning")
            result.update(live)
            if paper_state is not None:
                result["paper"] = paper_state
            if shadow_state is not None:
                result["shadow"] = shadow_state
            if learning_state is not None:
                result["learning"] = learning_state
            result["errors"].extend(live.get("reconcile", {}).get("errors", []))
            result["errors"].extend(live.get("claims", {}).get("errors", []))
        except Exception as exc:
            result["errors"].append(f"live: {exc}")
        return result

    def _stage(self, run_id: int, number: int, total: int, detail: str) -> None:
        text = f"[{number}/{total}] {detail}"
        log.info("CYCLE DEGEN %s", text)
        self.db.log("cycle_stage", "", f"run={run_id} {text}")

    def _record_estimate(self, run_id: int, market: Market, estimate: EngineEstimate) -> None:
        self.db.add_observations(run_id, market.id, estimate.engine, estimate.observations)
        if not estimate.supported:
            self.db.log(
                "estimate_rejected", market.id,
                f"{estimate.engine}: {estimate.reject_reason or estimate.reasoning}",
            )

    @staticmethod
    def _fatal_live_execution_error(text: str) -> bool:
        lowered = str(text or "").casefold()
        markers = (
            "not a required signer",
            "not the fee payer / first required signer",
            "additional signer(s) without a pre-applied signature",
            "local wallet signature was not applied",
            "pre-applied signature for co-signer",
            "propriétaire de transaction jupiter inattendu",
            "wallet file is empty",
            "unsupported wallet json format",
            "no solana rpc url configured",
        )
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _market_guard_key(market_id: str) -> str:
        return str(market_id or "").strip().casefold()

    @staticmethod
    def _event_guard_key(event_id: str) -> str:
        return str(event_id or "").strip().casefold()

    def _load_position_guards(self, maintenance: dict) -> None:
        """Install the fresh Jupiter position snapshot for this cycle."""
        self._positions_snapshot_complete = maintenance.get("positions_snapshot_complete") is True
        grouped_markets: dict[str, list[dict]] = {}
        grouped_events: dict[str, list[dict]] = {}
        total_cost = 0.0
        for row in maintenance.get("open_position_guards") or []:
            if not isinstance(row, dict):
                continue
            item = dict(row)
            market_key = self._market_guard_key(item.get("market_id", ""))
            event_key = self._event_guard_key(item.get("event_id", ""))
            if market_key:
                grouped_markets.setdefault(market_key, []).append(item)
            if event_key:
                grouped_events.setdefault(event_key, []).append(item)
            try:
                total_cost += float(item.get("cost_usd") or 0.0)
            except (TypeError, ValueError):
                pass
        self._open_live_markets = grouped_markets
        self._open_live_events = grouped_events
        self._open_position_cost_usd = total_cost

    def _position_guard_reason(self, market: Market) -> str:
        """Block reinforcement, correlated event exposure and unresolved orders."""
        if self.s.trading_mode != "live":
            return ""
        if not getattr(self, "_positions_snapshot_complete", False):
            return "instantané positions Jupiter incomplet; nouvel achat refusé par sécurité"

        market_key = self._market_guard_key(market.id)
        held = (getattr(self, "_open_live_markets", {}) or {}).get(market_key) or []
        if held:
            sides = ",".join(sorted({str(row.get("outcome") or "?").upper() for row in held}))
            keys = ",".join(str(row.get("position_key") or "")[:10] for row in held if row.get("position_key"))
            suffix = f" côté(s) {sides}" if sides else ""
            if keys:
                suffix += f" · position(s) {keys}"
            return "position LIVE déjà ouverte sur ce marché; renforcement et couverture opposée interdits" + suffix

        event_key = self._event_guard_key(market.event_id)
        if bool(getattr(self.s, "require_event_diversification", True)) and event_key:
            event_positions = (getattr(self, "_open_live_events", {}) or {}).get(event_key) or []
            if event_positions:
                questions = ", ".join(
                    str(row.get("question") or row.get("market_id") or "")[:24]
                    for row in event_positions[:3]
                )
                return (
                    "position LIVE déjà ouverte sur cet événement; "
                    "plusieurs buckets corrélés interdits"
                    + (f" ({questions})" if questions else "")
                )

        if hasattr(self.db, "active_live_order_for_market"):
            pending = self.db.active_live_order_for_market(market.id)
            if pending is not None:
                try:
                    status = str(pending["status"] or "pending")
                    order_id = str(pending["id"] or "?")
                except (KeyError, IndexError, TypeError):
                    status = "pending"
                    order_id = "?"
                return f"ordre LIVE non résolu déjà présent sur ce marché (id={order_id}, état={status})"

        if (
            bool(getattr(self.s, "require_event_diversification", True))
            and market.event_id
            and hasattr(self.db, "active_live_order_for_event")
        ):
            pending_event = self.db.active_live_order_for_event(market.event_id)
            if pending_event is not None:
                try:
                    status = str(pending_event["status"] or "pending")
                    order_id = str(pending_event["id"] or "?")
                    other_market = str(pending_event["market_id"] or "")
                except (KeyError, IndexError, TypeError):
                    status, order_id, other_market = "pending", "?", ""
                return (
                    f"ordre LIVE non résolu déjà présent sur cet événement "
                    f"(id={order_id}, marché={other_market or '?'}, état={status})"
                )
        return ""

    def _remember_committed_market(self, market: Market, outcome: str, position_pubkey: str = "") -> None:
        market_key = self._market_guard_key(market.id)
        event_key = self._event_guard_key(market.event_id)
        item = {
            "market_id": market.id,
            "event_id": market.event_id,
            "outcome": str(outcome or "").upper(),
            "position_key": str(position_pubkey or ""),
            "question": str(getattr(market, "question", "")),
            "shares": 0.0,
            "cost_usd": 0.0,
            "source": "ordre engagé dans le cycle courant",
        }
        if not hasattr(self, "_open_live_markets") or self._open_live_markets is None:
            self._open_live_markets = {}
        if not hasattr(self, "_open_live_events") or self._open_live_events is None:
            self._open_live_events = {}
        if market_key:
            self._open_live_markets.setdefault(market_key, []).append(item)
        if event_key:
            self._open_live_events.setdefault(event_key, []).append(item)

    def _reject(self, summary: dict, market: Market, reason: str, kind: str = "signal_rejected") -> None:
        summary["rejected"] += 1
        summary["rejection_reasons"][reason] = summary["rejection_reasons"].get(reason, 0) + 1
        self.db.log(kind, market.id, reason)

    @staticmethod
    def _is_short_timed_spec(spec) -> bool:
        """Return True for an unambiguous 5m/15m TIMED direction contract."""
        if spec is None or bool(getattr(spec, "ambiguous", False)):
            return False
        if str(getattr(spec, "settlement_kind", "") or "") != "timed_direction":
            return False
        try:
            start = int(getattr(spec, "window_start_ts", 0) or 0)
            end = int(getattr(spec, "expiry_ts", 0) or 0)
        except (TypeError, ValueError):
            return False
        duration = end - start
        return start > 0 and duration > 0 and duration <= 15 * 60 + 5

    @classmethod
    def _is_active_short_timed_spec(cls, spec, now_ts: float | None = None) -> bool:
        """Return True only while a short TIMED window is currently open."""
        if not cls._is_short_timed_spec(spec):
            return False
        start = int(getattr(spec, "window_start_ts", 0) or 0)
        end = int(getattr(spec, "expiry_ts", 0) or 0)
        current = time.time() if now_ts is None else float(now_ts)
        return start <= current < end

    def _prepare_fast_live_guards(self, summary: dict) -> None:
        """Refresh only the safety state needed immediately before TIMED LIVE.

        The normal cycle may spend many minutes resolving PAPER/SHADOW history.
        The TIMED worker must not wait for that work, but it still needs a fresh
        Jupiter position snapshot, version locks and wallet readiness before any
        real order can pass. This method intentionally does not claim positions,
        retrain models or resolve shadow history.
        """
        self._live_block_reason = ""
        self._cycle_live_orders = 0
        self._cycle_live_exposure_usd = 0.0
        self._positions_snapshot_complete = self.s.trading_mode != "live"
        self._open_live_markets = {}
        self._open_live_events = {}
        self._open_position_cost_usd = 0.0
        if self.s.trading_mode != "live":
            return

        if not self.s.live_release_enabled:
            self._live_block_reason = "LIVE_RELEASE_ENABLED=false: la v1.0.0 MICRO-LIVE reste volontairement PAPER"
        elif not bool(getattr(self.s, "release_live_capable", False)):
            self._live_block_reason = "release_live_capable=false: verrou codé Research/Validation"
        elif not self.s.live_allowed_by_version:
            self._live_block_reason = "LIVE_ALLOWED_BY_VERSION=false: verrou Research/Validation"

        try:
            positions, synced = self.lifecycle.sync_positions()
            guards: list[dict] = []
            if positions is not None:
                for position in positions:
                    if not self.lifecycle._position_blocks_new_buy(position):
                        continue
                    guards.append({
                        "market_id": str(position.get("market_id") or ""),
                        "event_id": str(position.get("event_id") or ""),
                        "event_title": str(position.get("event_title") or ""),
                        "outcome": str(position.get("outcome") or "").upper(),
                        "position_key": str(position.get("position_key") or ""),
                        "question": str(position.get("question") or ""),
                        "shares": float(position.get("shares") or 0.0),
                        "cost_usd": float(position.get("cost_usd") or 0.0),
                    })
            live_state = {
                "positions": synced,
                "positions_snapshot_complete": positions is not None,
                "open_position_guards": guards,
            }
            summary["fast_live_guard"] = live_state
            self._load_position_guards(live_state)
        except Exception as exc:
            summary["fast_live_guard"] = {
                "positions_snapshot_complete": False,
                "error": str(exc),
            }
            self._positions_snapshot_complete = False
            self._live_block_reason = " | ".join(
                x for x in (self._live_block_reason, f"instantané positions Jupiter indisponible: {exc}") if x
            )

        portfolio_reasons: list[str] = []
        if not self._positions_snapshot_complete:
            portfolio_reasons.append("instantané positions Jupiter incomplet")
        open_positions = sum(len(rows) for rows in self._open_live_markets.values())
        if self.s.max_open_positions > 0 and open_positions >= self.s.max_open_positions:
            portfolio_reasons.append(f"plafond positions {open_positions}/{self.s.max_open_positions}")
        if self.s.max_open_events > 0 and len(self._open_live_events) >= self.s.max_open_events:
            portfolio_reasons.append(f"plafond événements {len(self._open_live_events)}/{self.s.max_open_events}")
        if self._open_position_cost_usd + self.s.min_order_usd > self.s.max_total_open_exposure_usd + 1e-9:
            portfolio_reasons.append("plafond exposition ouverte atteint")
        if portfolio_reasons:
            self._live_block_reason = " | ".join(
                x for x in (self._live_block_reason, *portfolio_reasons) if x
            )

        if hasattr(self.executor, "live_readiness"):
            try:
                readiness = self.executor.live_readiness(self.s.min_order_usd)
                summary["live_readiness"] = readiness
                if not readiness.get("ready"):
                    self._live_block_reason = " | ".join(x for x in (
                        self._live_block_reason,
                        " | ".join(readiness.get("reasons") or ["wallet LIVE non prêt"]),
                    ) if x)
            except Exception as exc:
                summary["live_readiness"] = {"ready": False, "reasons": [str(exc)]}
                self._live_block_reason = " | ".join(
                    x for x in (self._live_block_reason, str(exc)) if x
                )

        if self._live_block_reason:
            self.db.log("timed_fast_live_observation_only", "", self._live_block_reason)

    def _timed_fast_reprice_candidate(self, candidate: PreparedCandidate, summary: dict) -> bool:
        """Refresh the executable Jupiter price and recompute the strict signal.

        Short prediction markets can move several cents while crypto snapshots and
        LIVE guards are refreshed.  The FAST worker must never send a signal whose
        edge was computed from an old event-list price.  Reprice only immediately
        before execution, then reuse the authoritative existing risk evaluator.
        The normal scanner is unchanged and LIVE_MAX_PRICE_DRIFT remains a final
        second-line protection in Executor.preflight_market().
        """
        signal_type = str(getattr(candidate.signal, "signal_type", ""))
        if signal_type != TIMED_DIRECTION_MODEL_NAME:
            return True
        if not hasattr(self.jupiter, "trade_quote"):
            return True

        market = candidate.market
        old_price = float(getattr(candidate.signal, "price", 0.0) or 0.0)
        old_edge = float(getattr(candidate.signal, "edge", 0.0) or 0.0)
        is_yes = str(getattr(candidate.signal, "outcome", "YES")).upper() == "YES"
        try:
            quote = self.jupiter.trade_quote(market.id, is_yes)
            buy = float(quote.get("buy") or 0.0)
            sell = float(quote.get("sell") or 0.0)
            if not (0.0 < buy < 1.0):
                raise RuntimeError(f"prix LIVE invalide: {buy}")

            if is_yes:
                market.yes_price = buy
                market.sell_yes_price = sell
            else:
                market.no_price = buy
                market.sell_no_price = sell
            volume = float(quote.get("volume_usd") or 0.0)
            liquidity = float(quote.get("liquidity_usd") or 0.0)
            if volume > 0:
                market.volume_usd = volume
            if liquidity > 0:
                market.liquidity_usd = liquidity

            decision = evaluate_market(self.s, self.db, market, candidate.estimate)
            if not decision.accepted or decision.signal is None:
                reason = decision.reason or "signal refusé après repricing LIVE"
                self._reject(
                    summary, market,
                    f"reprice {old_price:.3f}->{buy:.3f}; {reason}",
                    "timed_fast_reprice_rejected",
                )
                return False

            # A timed direction market is one-sided YES.  Fail closed if a future
            # parser/API change unexpectedly flips the selected side.
            if str(decision.signal.outcome).upper() != str(candidate.signal.outcome).upper():
                self._reject(
                    summary, market,
                    f"reprice a changé le côté {candidate.signal.outcome}->{decision.signal.outcome}",
                    "timed_fast_reprice_rejected",
                )
                return False

            candidate.signal = decision.signal
            self.db.update_signal(candidate.signal_id, candidate.signal)
            new_edge = float(getattr(candidate.signal, "edge", 0.0) or 0.0)
            summary.setdefault("timed_fast_repriced", 0)
            summary["timed_fast_repriced"] += 1
            summary.setdefault("timed_fast_reprice", {})[market.id] = {
                "old_price": round(old_price, 6),
                "new_price": round(buy, 6),
                "old_edge": round(old_edge, 6),
                "new_edge": round(new_edge, 6),
            }
            self.db.log(
                "timed_fast_reprice", market.id,
                f"signal_id={candidate.signal_id} price={old_price:.3f}->{buy:.3f} "
                f"edge={old_edge:.3f}->{new_edge:.3f}",
            )
            return True
        except Exception as exc:
            self._reject(
                summary, market,
                f"reprice LIVE indisponible: {exc}",
                "timed_fast_reprice_rejected",
            )
            return False

    def _timed_fast_live_ready_assets(self) -> set[str]:
        """Return assets that pass the exact existing TIMED V2 calibration gate.

        This is only an early performance filter. evaluate_market() still repeats
        the authoritative gate immediately before a signal can become LIVE.
        """
        if str(self.s.trading_mode).casefold() != "live":
            return {str(x).upper() for x in self.s.crypto_assets}
        if not bool(getattr(self.s, "timed_direction_live_enabled", False)):
            return set()
        minimum = int(getattr(self.s, "timed_direction_live_min_settled", 20))
        max_brier = float(getattr(self.s, "timed_direction_live_max_brier", 0.22))
        max_log = float(getattr(self.s, "timed_direction_live_max_log_loss", 0.68))
        ready: set[str] = set()
        try:
            with self.db.connect(readonly=True) as conn:
                rows = conn.execute(
                    """SELECT asset, COUNT(DISTINCT event_id) n,
                              AVG(brier_score) brier, AVG(log_loss) log_loss
                       FROM shadow_predictions
                       WHERE status='RESOLVED' AND settlement_kind='timed_direction'
                         AND model_name=?
                       GROUP BY asset""",
                    (TIMED_DIRECTION_MODEL_NAME,),
                ).fetchall()
            for row in rows:
                n = int(row["n"] or 0)
                brier = float(row["brier"]) if row["brier"] is not None else None
                log_loss = float(row["log_loss"]) if row["log_loss"] is not None else None
                if n >= minimum and brier is not None and brier <= max_brier \
                        and log_loss is not None and log_loss <= max_log:
                    ready.add(str(row["asset"] or "").upper())
        except Exception as exc:
            # Fail closed: the normal scanner still runs and the authoritative
            # risk gate is untouched.
            self.db.log("timed_fast_ready_gate_error", "", str(exc)[:1000])
            return set()
        return ready

    def scan_timed_fast_once(self) -> dict:
        """Independently analyse newly active 5m/15m markets and execute now.

        This is deliberately separate from ``scan_once``. The main cycle can
        take tens of minutes; this method only asks Jupiter for the current Degen
        set, analyses unseen active short windows and then reuses the exact same
        ranking, AI, risk, preflight and execution methods as the normal engine.
        """
        now_ts = time.time()
        self._timed_fast_seen = {
            market_id: expiry for market_id, expiry in self._timed_fast_seen.items()
            if float(expiry) > now_ts
        }
        self._timed_fast_attempts = {
            market_id: attempts for market_id, attempts in self._timed_fast_attempts.items()
            if market_id not in self._timed_fast_seen
        }

        markets = self.jupiter.live_degen_markets()
        specs = {market.id: parse_crypto_market(market) for market in markets}
        active_all = [
            market for market in markets
            if market.id not in self._timed_fast_seen
            and self._is_active_short_timed_spec(specs.get(market.id), now_ts)
        ]
        if not active_all:
            return {"processed": 0, "active": 0, "orders": 0, "signals": 0}

        live_mode = str(self.s.trading_mode).casefold() == "live"
        ready_assets = self._timed_fast_live_ready_assets() if live_mode else {
            str(specs[m.id].asset).upper() for m in active_all
        }

        # TIMED_FAST_LEARNING_ALL_ASSETS_V2_5
        # Learning and LIVE eligibility are deliberately separate.  The old
        # ready-only filter below created a deadlock in LIVE mode: when every
        # asset drifted just above the TIMED calibration threshold, no short
        # market was analysed, so no fresh V2 shadow labels could ever be
        # collected to improve/re-evaluate calibration.  scan_once() cannot
        # rescue this because all 5m/15m TIMED markets are intentionally
        # delegated to this worker.
        #
        # Analyse every currently active short TIMED market.  Real-money safety
        # is NOT relaxed: evaluate_market() still applies the authoritative
        # TIMED calibration gate, and ranked_live is additionally restricted to
        # ready_assets below.  Unready assets can therefore produce SHADOW/PAPER
        # learning only; they cannot reach the LIVE executor.
        active = list(active_all)
        unready_learning_markets = (
            sum(
                1 for market in active
                if str(specs[market.id].asset).upper() not in ready_assets
            )
            if live_mode else 0
        )

        run_id = self.db.start_run("timed_fast")
        summary = {
            "run_id": run_id,
            "mode": self.s.trading_mode,
            "jupiter_degen_markets": len(markets),
            "degen_checked": 0,
            "degen_supported": 0,
            "degen_unsupported": 0,
            "unsupported_examples": [],
            "signals": 0,
            "orders": 0,
            "ai_reviews": 0,
            "ai_rejected": 0,
            "ai_unavailable": 0,
            "shadow_predictions": 0,
            "strict_signals": 0,
            "exploration_candidates": 0,
            "exploration_signals": 0,
            "paper_parallel_orders": 0,
            "paper_parallel_strict_orders": 0,
            "paper_parallel_exploration_orders": 0,
            "paper_parallel_skipped": 0,
            "rejected": 0,
            "rejection_reasons": {},
            "order_statuses": {},
            "maintenance": {},
            "memory_free_gb": round(available_gb(), 2),
            "cycle_outcome": "running",
            "errors": [],
            "timed_fast_active_markets": len(active),
            "timed_fast_candidates": 0,
            "timed_fast_ranked": 0,
            "timed_fast_all_active_markets": len(active_all),
            "timed_fast_ready_assets": sorted(ready_assets),
            "timed_fast_learning_assets": sorted({
                str(specs[m.id].asset).upper() for m in active
            }),
            # Compatibility key kept for existing dashboards/scripts.  V2.5 no
            # longer skips unready assets from learning.
            "timed_fast_skipped_unready": 0,
            "timed_fast_unready_learning_markets": unready_learning_markets,
            "timed_fast_snapshot_seconds": {},
            "timed_fast_snapshot_errors": {},
        }
        try:
            require_free_memory(self.s.min_free_memory_gb)
            self.wallet.start_cycle()
            self.ai.reset_cycle()
            # V2.2: analyse/rank first. A slow wallet/position RPC must never
            # consume the 5-minute window when there is no strict LIVE candidate.
            # Authoritative LIVE guards are refreshed only immediately before
            # a real candidate is allowed to reach the executor.
            summary["timed_fast_live_guard_deferred"] = True
            self.db.upsert_markets(active, {market.id: specs[market.id] for market in active}, run_id=run_id)

            candidates: list[PreparedCandidate] = []

            # Fetch market data ONCE per asset, not once per UP/DOWN/5m/15m
            # contract. Assets are independent and can be fetched concurrently.
            assets = sorted({str(specs[m.id].asset).upper() for m in active})
            snapshots = {}
            snapshot_errors: dict[str, str] = {}
            snapshot_started: dict[str, float] = {}

            def _fetch_asset(asset: str):
                snapshot_started[asset] = time.monotonic()
                return self.quant.data.fetch(asset, persist=False)

            max_workers = max(1, min(3, len(assets)))
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="timed-data") as pool:
                futures = {pool.submit(_fetch_asset, asset): asset for asset in assets}
                for future in as_completed(futures):
                    asset = futures[future]
                    try:
                        snapshots[asset] = future.result()
                    except Exception as exc:
                        snapshot_errors[asset] = str(exc)[:1000]
                    finally:
                        summary["timed_fast_snapshot_seconds"][asset] = round(
                            time.monotonic() - snapshot_started.get(asset, time.monotonic()), 3
                        )

            summary["timed_fast_snapshot_errors"] = snapshot_errors
            # Persist sequentially after the concurrent reads, avoiding sqlite
            # writer contention while keeping the same historical data.
            for snapshot in snapshots.values():
                try:
                    self.db.add_crypto_snapshot(snapshot)
                except Exception as exc:
                    summary["errors"].append(f"snapshot persist {snapshot.asset}: {exc}")

            for market in active:
                spec = specs[market.id]
                summary["degen_checked"] += 1
                summary["degen_supported"] += 1
                require_free_memory(self.s.min_free_memory_gb)
                snapshot = snapshots.get(str(spec.asset).upper())
                try:
                    if snapshot is None:
                        raise RuntimeError(snapshot_errors.get(str(spec.asset).upper(), "snapshot timed absent"))
                    estimate = self.quant.estimate_timed_from_snapshot(
                        market, snapshot, run_id=run_id
                    )
                except Exception as exc:
                    estimate = EngineEstimate(
                        engine="DEGEN_QUANT_V3_RESEARCH_ENSEMBLE", probability_yes=0.5,
                        confidence=0.0, reliability=0.0, source_agreement=0.0,
                        reasoning=str(exc), evidence=[], observations=[], supported=False,
                        reject_reason="engine_error",
                    )

                # A supported estimate (accepted or rejected by risk) is a real
                # evaluation and is done once for this market. Transient source
                # startup failures may retry twice during the first ~30 seconds.
                attempts = int(self._timed_fast_attempts.get(market.id, 0)) + 1
                self._timed_fast_attempts[market.id] = attempts
                age = max(0.0, time.time() - float(spec.window_start_ts or now_ts))
                if estimate.supported or attempts >= 3 or age >= 30.0:
                    self._timed_fast_seen[market.id] = float(spec.expiry_ts or time.time() + 60.0)
                    self._timed_fast_attempts.pop(market.id, None)

                candidate = self._prepare_candidate(run_id, market, estimate, summary)
                if candidate is not None:
                    candidates.append(candidate)

            parallel_mode = live_mode and bool(getattr(self.s, "paper_parallel_live_enabled", False))
            if parallel_mode:
                strict_candidates = [
                    c for c in candidates
                    if "PAPER_EXPLORE" not in str(getattr(c.signal, "signal_type", ""))
                    and str(getattr(c.signal, "asset", "") or "").upper() in ready_assets
                ]
                exploration_candidates = [
                    c for c in candidates
                    if "PAPER_EXPLORE" in str(getattr(c.signal, "signal_type", ""))
                ]
                ranked_live = self._rank_candidates(strict_candidates, summary)
                ranked_exploration = self._rank_candidates(exploration_candidates, summary)
            else:
                ranked_all = self._rank_candidates(candidates, summary)
                if live_mode:
                    ranked_live = [
                        c for c in ranked_all
                        if "PAPER_EXPLORE" not in str(getattr(c.signal, "signal_type", ""))
                        and str(getattr(c.signal, "asset", "") or "").upper() in ready_assets
                    ]
                    ranked_exploration = [
                        c for c in ranked_all
                        if "PAPER_EXPLORE" in str(getattr(c.signal, "signal_type", ""))
                    ]
                else:
                    ranked_live = []
                    ranked_exploration = ranked_all

            ranked = ranked_live + ranked_exploration if live_mode else ranked_exploration
            summary["ranked_live_candidates"] = len(ranked_live)
            summary["ranked_paper_exploration_candidates"] = len(ranked_exploration)
            summary["timed_fast_candidates"] = len(candidates)
            summary["timed_fast_ranked"] = len(ranked)
            summary["ranked_correlated_candidates"] = len(ranked)

            # PAPER exploration never waits for LIVE wallet/RPC readiness.
            for candidate in ranked_exploration:
                self.db.log(
                    "timed_fast_execution", candidate.market.id,
                    f"worker=standalone signal_id={candidate.signal_id} "
                    f"type={getattr(candidate.signal, 'signal_type', '')}",
                )
                self._execute_candidate(candidate, summary)

            # Only a genuine strict LIVE candidate pays the cost of authoritative
            # Jupiter positions + Solana balance checks. In FAST worker mode all
            # read-only network calls are bounded; failures remain fail-closed.
            if live_mode and ranked_live:
                guard_started = time.monotonic()
                self._prepare_fast_live_guards(summary)
                summary["timed_fast_live_guard_seconds"] = round(
                    time.monotonic() - guard_started, 3
                )
                if self._live_block_reason:
                    self.db.log(
                        "timed_fast_live_guard_blocked", "",
                        f"{summary['timed_fast_live_guard_seconds']:.3f}s | {self._live_block_reason}",
                    )
                for candidate in ranked_live:
                    # V2.4: event-list prices can be stale by several cents after
                    # snapshot collection + LIVE guards. Refresh the executable
                    # Jupiter quote NOW and recompute edge/risk before execution.
                    # Do not relax LIVE_MAX_PRICE_DRIFT: preflight still checks it
                    # again immediately before signing.
                    if not self._timed_fast_reprice_candidate(candidate, summary):
                        continue
                    self.db.log(
                        "timed_fast_execution", candidate.market.id,
                        f"worker=standalone signal_id={candidate.signal_id} "
                        f"type={getattr(candidate.signal, 'signal_type', '')} "
                        f"guard_s={summary['timed_fast_live_guard_seconds']:.3f}",
                    )
                    self._execute_candidate(candidate, summary)
            elif live_mode:
                summary["timed_fast_live_guard_seconds"] = 0.0
                summary["timed_fast_live_guard_skipped"] = "no_strict_live_candidate"

            summary["cycle_outcome"] = self._cycle_outcome(summary)
            status = "ok" if not summary["errors"] else "ok_with_errors"
            self.db.finish_run(run_id, status, json.dumps(summary, ensure_ascii=False))
            return {**summary, "processed": len(active), "active": len(active)}
        except Exception as exc:
            summary["errors"].append(str(exc))
            summary["cycle_outcome"] = "failed"
            self.db.finish_run(run_id, "failed", json.dumps(summary, ensure_ascii=False))
            raise

    def _prepare_candidate(
        self,
        run_id: int,
        market: Market,
        estimate: EngineEstimate,
        summary: dict,
    ) -> PreparedCandidate | None:
        self._record_estimate(run_id, market, estimate)
        strict = evaluate_market(self.s, self.db, market, estimate)
        exploration = None
        paper_learning_mode = (
            str(self.s.trading_mode).casefold() == "paper"
            or (
                str(self.s.trading_mode).casefold() == "live"
                and bool(getattr(self.s, "paper_parallel_live_enabled", False))
            )
        )
        if (not strict.accepted and paper_learning_mode
                and bool(getattr(self.s, "paper_exploration_enabled", True))):
            exploration = evaluate_market_exploration(self.s, self.db, market, estimate)
        try:
            spec = parse_crypto_market(market)
            if estimate.supported and not spec.ambiguous:
                self.db.upsert_shadow_prediction(
                    market, estimate, spec, strict_ok=bool(strict.accepted),
                    exploration_ok=bool(exploration and exploration.accepted),
                )
                summary["shadow_predictions"] = summary.get("shadow_predictions", 0) + 1
        except Exception as exc:
            summary.setdefault("errors", []).append(f"shadow {market.id}: {exc}")

        decision = strict
        if not strict.accepted and exploration is not None and exploration.accepted:
            if summary.get("exploration_candidates", 0) >= int(getattr(self.s, "paper_exploration_max_per_cycle", 3)):
                self._reject(summary, market, "exploration PAPER: plafond du cycle atteint")
                return None
            decision = exploration
            summary["exploration_candidates"] = summary.get("exploration_candidates", 0) + 1
            self.db.log("paper_exploration_selected", market.id, strict.reason)
        elif not strict.accepted:
            detail = strict.reason
            if exploration is not None and not exploration.accepted:
                detail += f" | {exploration.reason}"
            self._reject(summary, market, detail)
            return None

        if decision.signal is None:
            self._reject(summary, market, decision.reason)
            return None
        signal = decision.signal
        signal_id = self.db.add_signal(run_id, market.event_id, signal)
        summary["signals"] += 1
        if "PAPER_EXPLORE" in str(signal.signal_type):
            summary["exploration_signals"] = summary.get("exploration_signals", 0) + 1
        else:
            summary["strict_signals"] = summary.get("strict_signals", 0) + 1
        return PreparedCandidate(
            run_id=run_id, market=market, estimate=estimate,
            signal=signal, signal_id=signal_id,
        )

    def _review_candidate(self, candidate: PreparedCandidate, summary: dict) -> bool:
        if not bool(getattr(self.s, "local_ai_enabled", True)):
            return True
        review = self.ai.review(candidate.market, candidate.estimate, candidate.signal)
        summary["ai_reviews"] = summary.get("ai_reviews", 0) + 1
        if not review.available:
            summary["ai_unavailable"] = summary.get("ai_unavailable", 0) + 1
        if review.verdict == "reject":
            summary["ai_rejected"] = summary.get("ai_rejected", 0) + 1
        self.db.add_ai_review(candidate.run_id, candidate.market.id, candidate.signal_id, review)
        reviewed_estimate = apply_ai_review(self.s, candidate.estimate, review)
        if "PAPER_EXPLORE" in str(getattr(candidate.signal, "signal_type", "")):
            decision = evaluate_market_exploration(self.s, self.db, candidate.market, reviewed_estimate)
        else:
            decision = evaluate_market(self.s, self.db, candidate.market, reviewed_estimate)
        if not decision.accepted or decision.signal is None:
            self._reject(summary, candidate.market, decision.reason, "local_ai_guard_rejected")
            return False
        candidate.estimate = reviewed_estimate
        candidate.signal = decision.signal
        self.db.update_signal(candidate.signal_id, candidate.signal)
        return True

    def _rank_candidates(self, candidates: list[PreparedCandidate], summary: dict) -> list[PreparedCandidate]:
        if not candidates:
            return []
        if not bool(getattr(self.s, "event_ranking_enabled", True)):
            ranked = sorted(
                candidates,
                key=lambda c: (float(c.signal.score), float(c.signal.edge)),
                reverse=True,
            )
            return [candidate for candidate in ranked if self._review_candidate(candidate, summary)]

        grouped: dict[str, list[PreparedCandidate]] = {}
        for candidate in candidates:
            key = str(getattr(candidate.signal, "event_family", "") or self._event_guard_key(candidate.market.event_id) or self._market_guard_key(candidate.market.id)).casefold()
            grouped.setdefault(key, []).append(candidate)

        selected: list[PreparedCandidate] = []
        for group in grouped.values():
            ranked = sorted(
                group,
                key=lambda c: (
                    float(c.signal.score),
                    float(c.signal.edge),
                    float(c.signal.confidence),
                    float(c.signal.reliability),
                    -float(c.signal.price),
                ),
                reverse=True,
            )
            winner_index = None
            for index, candidate in enumerate(ranked):
                if self._review_candidate(candidate, summary):
                    selected.append(candidate)
                    winner_index = index
                    break
            if winner_index is not None:
                for candidate in ranked[winner_index + 1:]:
                    self._reject(
                        summary,
                        candidate.market,
                        "candidat non retenu: meilleur signal déjà sélectionné dans le même groupe corrélé",
                        "event_candidate_ranked_out",
                    )
        return sorted(
            selected,
            key=lambda c: (float(c.signal.score), float(c.signal.edge)),
            reverse=True,
        )

    def _runtime_risk_reason(self, candidate: PreparedCandidate, mode: str | None = None) -> str:
        """Recheck mutable portfolio limits immediately before each order.

        Candidate analysis is intentionally performed before ranking. This second
        gate prevents several individually valid candidates from jointly exceeding
        daily, asset, event or exposure limits during the same cycle.
        """
        market = candidate.market
        signal = candidate.signal
        effective_mode = str(mode or self.s.trading_mode).casefold()
        exploring = "PAPER_EXPLORE" in str(getattr(signal, "signal_type", ""))
        if self.db.market_ordered_today(market.id, mode=effective_mode):
            return "marché exact déjà engagé aujourd'hui"
        if exploring and effective_mode == "paper":
            with self.db.connect(readonly=True) as conn:
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
            if int(daily["n"] or 0) >= int(self.s.paper_exploration_max_orders_per_day):
                return "exploration: limite quotidienne atteinte avant exécution"
            if int(open_row["n"] or 0) >= int(self.s.paper_exploration_max_open_positions):
                return "exploration: plafond de positions ouvertes atteint"
            return ""
        if self.s.max_orders_per_event_per_day > 0 and market.event_id:
            if self.db.event_orders_today(market.event_id, mode=effective_mode) >= self.s.max_orders_per_event_per_day:
                return "limite par événement atteinte avant exécution"
        asset = str(getattr(signal, "asset", "") or "")
        if self.s.max_orders_per_asset_per_day > 0 and asset:
            if self.db.asset_orders_today(asset, mode=effective_mode) >= self.s.max_orders_per_asset_per_day:
                return f"limite quotidienne {asset} atteinte avant exécution"
        orders, exposure = self.db.orders_today(mode=effective_mode)
        if orders >= int(self.s.max_orders_per_day):
            return "nombre maximal d'ordres quotidien atteint avant exécution"
        stake = float(getattr(signal, "stake_usd", 0.0) or 0.0)
        if exposure + stake > float(self.s.daily_exposure_limit_usd) + 1e-9:
            return "limite d'exposition quotidienne atteinte avant exécution"

        if effective_mode == "paper":
            open_asset, _asset_exposure = self.db.paper_open_for_asset(asset)
            open_count, open_exposure, open_events = self.db.paper_open_summary()
            correlated = self.db.paper_correlated_exposure(str(getattr(signal, "event_family", "") or ""))
        else:
            open_asset, _asset_exposure = self.db.open_positions_for_asset(asset)
            open_count, open_exposure, open_events = self.db.live_open_summary()
            correlated = self.db.correlated_exposure(str(getattr(signal, "event_family", "") or ""))
        if self.s.max_open_positions_per_asset > 0 and open_asset >= self.s.max_open_positions_per_asset:
            return f"positions ouvertes {asset}: {open_asset}/{self.s.max_open_positions_per_asset}"
        if self.s.max_open_positions > 0 and open_count >= self.s.max_open_positions:
            return f"plafond positions ouvertes {open_count}/{self.s.max_open_positions}"
        if self.s.max_open_events > 0 and open_events >= self.s.max_open_events:
            return f"plafond événements ouverts {open_events}/{self.s.max_open_events}"
        if correlated + stake > float(self.s.max_correlated_exposure_usd) + 1e-9:
            return "exposition corrélée dépassée avant exécution"
        if open_exposure + stake > float(self.s.max_total_open_exposure_usd) + 1e-9:
            return "exposition totale potentielle dépassée avant exécution"
        return ""

    def _record_parallel_paper(self, candidate: PreparedCandidate, summary: dict) -> bool:
        """Record an isolated PAPER fill while the operational mode stays LIVE."""
        if str(self.s.trading_mode).casefold() != "live":
            return False
        if not bool(getattr(self.s, "paper_parallel_live_enabled", False)):
            return False
        if not bool(getattr(self.s, "paper_tracking_enabled", True)):
            return False

        market = candidate.market
        signal = candidate.signal
        exploring = "PAPER_EXPLORE" in str(getattr(signal, "signal_type", ""))

        # Keep PAPER exposure deliberately small and completely separate from
        # the real-money stake. This amount never touches the wallet.
        configured = max(0.01, float(getattr(self.s, "paper_parallel_stake_usd", 1.0)))
        signal_stake = max(0.01, float(getattr(signal, "stake_usd", configured) or configured))
        amount = min(configured, signal_stake)

        # PAPER guards are intentionally mode-specific. A simulated fill must
        # never consume or block a LIVE daily/asset/event/exposure allowance.
        if self.db.market_ordered_today(market.id, mode="paper"):
            reason = "marché déjà enregistré en PAPER aujourd'hui"
            summary["paper_parallel_skipped"] = summary.get("paper_parallel_skipped", 0) + 1
            self.db.log("paper_parallel_skipped", market.id, reason)
            return False

        with self.db.connect(readonly=True) as conn:
            today = conn.execute(
                "SELECT COUNT(*) n FROM orders WHERE mode='paper' "
                "AND date(created_at)=date('now') "
                "AND status NOT IN ('failed','simulation_error','blocked')"
            ).fetchone()
            opened = conn.execute(
                "SELECT COUNT(*) n FROM orders WHERE mode='paper' AND status='paper_filled'"
            ).fetchone()
            explore_today = conn.execute(
                "SELECT COUNT(*) n FROM orders o LEFT JOIN signals s ON s.id=o.signal_id "
                "WHERE o.mode='paper' AND date(o.created_at)=date('now') "
                "AND s.signal_type LIKE '%PAPER_EXPLORE%'"
            ).fetchone()
            explore_open = conn.execute(
                "SELECT COUNT(*) n FROM orders o LEFT JOIN signals s ON s.id=o.signal_id "
                "WHERE o.mode='paper' AND o.status='paper_filled' "
                "AND s.signal_type LIKE '%PAPER_EXPLORE%'"
            ).fetchone()

        if int(today["n"] or 0) >= int(getattr(self.s, "paper_parallel_max_orders_per_day", 20)):
            reason = "plafond PAPER parallèle quotidien"
        elif int(opened["n"] or 0) >= int(getattr(self.s, "paper_parallel_max_open_positions", 40)):
            reason = "plafond PAPER parallèle ouvert"
        elif exploring and int(explore_today["n"] or 0) >= int(getattr(self.s, "paper_exploration_max_orders_per_day", 12)):
            reason = "plafond exploration PAPER quotidien"
        elif exploring and int(explore_open["n"] or 0) >= int(getattr(self.s, "paper_exploration_max_open_positions", 25)):
            reason = "plafond exploration PAPER ouvert"
        else:
            reason = ""
        if reason:
            summary["paper_parallel_skipped"] = summary.get("paper_parallel_skipped", 0) + 1
            self.db.log("paper_parallel_skipped", market.id, reason)
            return False

        order_id = self.db.add_order(
            run_id=summary["run_id"], signal_id=candidate.signal_id, market_id=market.id,
            event_id=market.event_id, outcome=signal.outcome, amount_usd=amount,
            mode="paper", status="paper_filled",
            response={
                "price": float(getattr(signal, "price", 0.0) or 0.0),
                "parallel_live": True,
                "source_signal_type": str(getattr(signal, "signal_type", "")),
            },
        )
        summary["paper_parallel_orders"] = summary.get("paper_parallel_orders", 0) + 1
        if exploring:
            summary["paper_parallel_exploration_orders"] = summary.get("paper_parallel_exploration_orders", 0) + 1
        else:
            summary["paper_parallel_strict_orders"] = summary.get("paper_parallel_strict_orders", 0) + 1
        self.db.log("paper_parallel_order", market.id, f"order_id={order_id} amount={amount:.2f}")
        return True

    def _execute_candidate(self, candidate: PreparedCandidate, summary: dict) -> None:
        with _LIVE_EXECUTION_LOCK:
            self._execute_candidate_locked(candidate, summary)

    def _execute_candidate_locked(self, candidate: PreparedCandidate, summary: dict) -> None:
        market = candidate.market
        signal = candidate.signal
        signal_id = candidate.signal_id
        exploring = "PAPER_EXPLORE" in str(getattr(signal, "signal_type", ""))

        # In LIVE+PAPER parallel mode, every selected strict signal gets an
        # independent PAPER mirror. Exploration signals remain PAPER-only and
        # can never reach the real-money executor.
        if str(self.s.trading_mode).casefold() == "live" and bool(getattr(self.s, "paper_parallel_live_enabled", False)):
            self._record_parallel_paper(candidate, summary)
            if exploring:
                return

        runtime_reason = self._runtime_risk_reason(candidate, mode=self.s.trading_mode)
        if runtime_reason:
            self._reject(summary, market, runtime_reason, "runtime_risk_guard")
            return

        if self.s.trading_mode == "live":
            guard_reason = self._position_guard_reason(market)
            if guard_reason:
                self._reject(summary, market, guard_reason, "live_position_guard")
                return

        if self.s.trading_mode == "live" and self._live_block_reason:
            self._reject(
                summary,
                market,
                f"LIVE observation-only: {self._live_block_reason}",
                "live_funding_block",
            )
            return

        if self.s.trading_mode == "live":
            max_orders = max(1, int(getattr(self.s, "max_live_orders_per_cycle", 3)))
            max_exposure = max(
                float(self.s.min_order_usd),
                float(getattr(self.s, "max_live_exposure_per_cycle_usd", 15.0)),
            )
            if self._cycle_live_orders >= max_orders:
                self._live_block_reason = (
                    f"limite LIVE du cycle atteinte: {self._cycle_live_orders}/{max_orders} ordre(s)"
                )
            elif self._cycle_live_exposure_usd + float(signal.stake_usd) > max_exposure + 1e-9:
                self._live_block_reason = (
                    f"budget LIVE du cycle atteint: {self._cycle_live_exposure_usd:.2f}$ / {max_exposure:.2f}$"
                )
            if self._live_block_reason:
                reason = f"LIVE observation-only: {self._live_block_reason}"
                self._reject(summary, market, reason, "live_cycle_budget_block")
                log.warning("Nouveaux ordres LIVE suspendus pour ce cycle: %s", self._live_block_reason)
                return

            if hasattr(self.executor, "funding_readiness"):
                funding = self.executor.funding_readiness(signal.stake_usd)
                summary["live_funding"] = funding
                if not funding.get("ready"):
                    self._live_block_reason = " | ".join(
                        funding.get("reasons") or ["fonds LIVE insuffisants"]
                    )
                    reason = f"LIVE observation-only: {self._live_block_reason}"
                    self._reject(summary, market, reason, "live_funding_pause")
                    log.warning("Nouveaux ordres LIVE suspendus pour ce cycle: %s", self._live_block_reason)
                    return

            try:
                preflight = (
                    self.executor.preflight_market(signal, market)
                    if hasattr(self.executor, "preflight_market")
                    else {"ready": True, "compatibility_adapter": True}
                )
            except Exception as exc:
                self._reject(
                    summary, market,
                    f"pré-contrôle liquidité indisponible: {exc}",
                    "live_market_preflight",
                )
                return
            summary.setdefault("market_preflight", {})[market.id] = {
                k: v for k, v in preflight.items() if k != "market"
            }
            if not preflight.get("ready"):
                self._reject(
                    summary, market,
                    " | ".join(preflight.get("reasons") or ["pré-contrôle marché refusé"]),
                    "live_market_preflight",
                )
                return

            guard_reason = self._position_guard_reason(market)
            if guard_reason:
                self._reject(summary, market, guard_reason, "live_position_guard")
                return

        order_id = None
        if self.s.trading_mode == "live":
            order_id = self.db.add_order(
                run_id=summary["run_id"], signal_id=signal_id, market_id=market.id,
                event_id=market.event_id, outcome=signal.outcome,
                amount_usd=signal.stake_usd, mode="live", status="preparing",
                response={"stage": "intent_persisted_before_order_creation"},
            )

        try:
            execution = (
                self.executor.execute(signal, market)
                if order_id is None
                else self.executor.execute(signal, market, order_id=order_id)
            )
            response = dict(execution.response)
            response["market_id"] = market.id
            if order_id is None:
                order_id = self.db.add_order(
                    run_id=summary["run_id"], signal_id=signal_id, market_id=market.id,
                    event_id=market.event_id, outcome=signal.outcome,
                    amount_usd=signal.stake_usd, mode=self.s.trading_mode,
                    status=execution.status, deposit_mint=execution.deposit_mint,
                    response=response, order_pubkey=execution.order_pubkey,
                    position_pubkey=execution.position_pubkey, signature=execution.signature,
                )
            else:
                self.db.update_order(
                    order_id, execution.status, response,
                    order_pubkey=execution.order_pubkey,
                    position_pubkey=execution.position_pubkey,
                    signature=execution.signature,
                    deposit_mint=execution.deposit_mint,
                )
            summary["orders"] += 1
            summary["order_statuses"][execution.status] = (
                summary["order_statuses"].get(execution.status, 0) + 1
            )
            committed_statuses = {
                "unknown_send", "sent", "pending_fill", "partial_fill",
                "filled", "confirmed", "paper_filled",
            }
            if self.s.trading_mode == "live" and execution.status in committed_statuses:
                self._cycle_live_orders += 1
                self._cycle_live_exposure_usd += float(signal.stake_usd)
                self._remember_committed_market(market, signal.outcome, execution.position_pubkey)
                summary["live_cycle_budget"] = {
                    "orders": self._cycle_live_orders,
                    "exposure_usd": round(self._cycle_live_exposure_usd, 2),
                    "max_orders": int(getattr(self.s, "max_live_orders_per_cycle", 3)),
                    "max_exposure_usd": float(getattr(self.s, "max_live_exposure_per_cycle_usd", 15.0)),
                }
            self.db.log("order_recorded", market.id, f"order_id={order_id} status={execution.status}")
        except LiveFundingPause as exc:
            text = str(exc)
            self._live_block_reason = text
            if order_id is not None:
                self.db.update_order(order_id, "blocked", {"funding_pause": text})
            self._reject(
                summary, market, f"LIVE observation-only: {text}", "live_funding_pause"
            )
            log.warning("Nouveaux ordres LIVE suspendus pour ce cycle: %s", text)
        except Exception as exc:
            text = str(exc)
            lowered = text.casefold()
            if isinstance(exc, AmbiguousSendError):
                status = "unknown_send"
            elif "simulation" in lowered:
                status = "simulation_error"
            else:
                status = "failed"
            if self.s.trading_mode == "live" and self._fatal_live_execution_error(text):
                self._live_block_reason = (
                    "blocage sécurité après erreur d'infrastructure LIVE: " + text[:500]
                )
                self.db.log("live_cycle_blocked", market.id, self._live_block_reason)
                log.error("Toutes les autres exécutions LIVE de ce cycle sont bloquées: %s", text)
            if order_id is None:
                order_id = self.db.add_order(
                    run_id=summary["run_id"], signal_id=signal_id, market_id=market.id,
                    event_id=market.event_id, outcome=signal.outcome,
                    amount_usd=signal.stake_usd, mode=self.s.trading_mode,
                    status=status, response={"error": text},
                )
            else:
                self.db.update_order(order_id, status, {"error": text})
            summary["errors"].append(f"{market.id}: {text}")
            self.db.log(status, market.id, text)
            log.error("Ordre Degen %s en erreur: %s", market.id, text)

    def _handle_estimate(self, run_id: int, market: Market, estimate: EngineEstimate, summary: dict) -> None:
        # Backward-compatible single-market path used by focused tests and
        # diagnostic tools. Normal cycles collect and rank all candidates first.
        summary.setdefault("run_id", run_id)
        candidate = self._prepare_candidate(run_id, market, estimate, summary)
        if candidate is not None:
            self._execute_candidate(candidate, summary)

    @staticmethod
    def _cycle_outcome(summary: dict) -> str:
        if summary["jupiter_degen_markets"] == 0:
            return "no_degen_market"
        if summary["degen_supported"] == 0:
            return "degen_markets_unsupported"
        if summary["orders"] > 0:
            return "degen_orders_created"
        if summary["signals"] > 0:
            return "degen_signals_without_order"
        return "no_degen_edge"

    def scan_once(self) -> dict:
        require_free_memory(self.s.min_free_memory_gb)
        run_id = self.db.start_run("degen")
        summary = {
            "run_id": run_id,
            "mode": self.s.trading_mode,
            "jupiter_degen_markets": 0,
            "degen_checked": 0,
            "degen_supported": 0,
            "degen_unsupported": 0,
            "unsupported_examples": [],
            "signals": 0,
            "orders": 0,
            "ai_reviews": 0,
            "ai_rejected": 0,
            "ai_unavailable": 0,
            "shadow_predictions": 0,
            "strict_signals": 0,
            "exploration_candidates": 0,
            "exploration_signals": 0,
            "paper_parallel_orders": 0,
            "paper_parallel_strict_orders": 0,
            "paper_parallel_exploration_orders": 0,
            "paper_parallel_skipped": 0,
            "rejected": 0,
            "rejection_reasons": {},
            "order_statuses": {},
            "maintenance": {},
            "memory_free_gb": round(available_gb(), 2),
            "cycle_outcome": "running",
            "errors": [],
        }
        try:
            self.wallet.start_cycle()
            self.ai.reset_cycle()
            self._live_block_reason = ""
            self._cycle_live_orders = 0
            self._cycle_live_exposure_usd = 0.0
            self._positions_snapshot_complete = self.s.trading_mode != "live"
            self._open_live_markets = {}
            self._open_live_events = {}
            self._open_position_cost_usd = 0.0
            summary["live_cycle_budget"] = {
                "orders": 0,
                "exposure_usd": 0.0,
                "max_orders": int(self.s.max_live_orders_per_cycle),
                "max_exposure_usd": float(self.s.max_live_exposure_per_cycle_usd),
            }

            maintenance_label = "maintenance PAPER" if self.s.trading_mode != "live" else "maintenance LIVE, réconciliation et positions"
            self._stage(run_id, 1, 5, maintenance_label)
            summary["maintenance"] = self.maintenance()
            if self.s.trading_mode == "live":
                if not self.s.live_release_enabled:
                    self._live_block_reason = "LIVE_RELEASE_ENABLED=false: la v1.0.0 MICRO-LIVE reste volontairement PAPER"
                elif not bool(getattr(self.s, "release_live_capable", False)):
                    self._live_block_reason = "release_live_capable=false: verrou codé Research/Validation"
                elif not self.s.live_allowed_by_version:
                    self._live_block_reason = "LIVE_ALLOWED_BY_VERSION=false: verrou Research/Validation"
                self._load_position_guards(summary["maintenance"])
                summary["position_guard"] = {
                    "snapshot_complete": self._positions_snapshot_complete,
                    "blocked_markets": len(self._open_live_markets),
                    "blocked_events": len(self._open_live_events),
                    "open_cost_usd": round(self._open_position_cost_usd, 2),
                    "reinforcement_allowed": False,
                }
                portfolio_reasons: list[str] = []
                if not self._positions_snapshot_complete:
                    portfolio_reasons.append("instantané positions Jupiter incomplet")
                open_positions = sum(len(rows) for rows in self._open_live_markets.values())
                if self.s.max_open_positions > 0 and open_positions >= self.s.max_open_positions:
                    portfolio_reasons.append(f"plafond positions {open_positions}/{self.s.max_open_positions}")
                if self.s.max_open_events > 0 and len(self._open_live_events) >= self.s.max_open_events:
                    portfolio_reasons.append(f"plafond événements {len(self._open_live_events)}/{self.s.max_open_events}")
                if self._open_position_cost_usd + self.s.min_order_usd > self.s.max_total_open_exposure_usd + 1e-9:
                    portfolio_reasons.append("plafond exposition ouverte atteint")
                if portfolio_reasons:
                    self._live_block_reason = " | ".join(x for x in (self._live_block_reason, *portfolio_reasons) if x)
                if hasattr(self.executor, "live_readiness"):
                    try:
                        readiness = self.executor.live_readiness(self.s.min_order_usd)
                        summary["live_readiness"] = readiness
                        if not readiness.get("ready"):
                            self._live_block_reason = " | ".join(x for x in (
                                self._live_block_reason,
                                " | ".join(readiness.get("reasons") or ["wallet LIVE non prêt"]),
                            ) if x)
                    except Exception as exc:
                        summary["live_readiness"] = {"ready": False, "reasons": [str(exc)]}
                        self._live_block_reason = " | ".join(x for x in (self._live_block_reason, str(exc)) if x)
                if self._live_block_reason:
                    self.db.log("live_observation_only", "", self._live_block_reason)
                    log.warning("LIVE BLOQUÉ — analyse seulement: %s", self._live_block_reason)

            self._stage(run_id, 2, 5, "découverte et validation Jupiter des marchés crypto")
            markets = self.jupiter.markets()
            specs = {market.id: parse_crypto_market(market) for market in markets}
            self.db.upsert_markets(markets, specs, run_id=run_id)
            summary["jupiter_degen_markets"] = len(markets)

            self._stage(run_id, 3, 5, "contrôle du parseur Degen multi-actifs")

            # TIMED 5m/15m are intentionally NOT analysed in this long cycle.
            # The scheduler runs scan_timed_fast_once() in an independent worker
            # every few seconds. Keeping short contracts out of this 300-market
            # loop prevents future/expired TIMED rows from consuming 10-40 min
            # and prevents duplicate signals after the worker already evaluated
            # the live window.
            scan_limit = max(1, int(self.s.degen_markets_per_cycle))
            timed_delegated_ids = {
                market.id for market in markets
                if self._is_short_timed_spec(specs.get(market.id))
            }
            summary["timed_delegated_to_fast_worker"] = len(timed_delegated_ids)
            regular_order = [market for market in markets if market.id not in timed_delegated_ids]
            scan_markets = regular_order[:scan_limit]

            supported_markets: list[Market] = []
            for market in scan_markets:
                spec = specs[market.id]
                summary["degen_checked"] += 1
                if spec.ambiguous:
                    summary["degen_unsupported"] += 1
                    summary["rejected"] += 1
                    reason = spec.reject_reason or "parseur ambigu"
                    summary["rejection_reasons"][reason] = summary["rejection_reasons"].get(reason, 0) + 1
                    self.db.log("degen_unsupported", market.id, f"{reason} | {market.question}"[:1000])
                    if len(summary["unsupported_examples"]) < 10:
                        summary["unsupported_examples"].append({
                            "market_id": market.id, "reason": reason,
                            "event": market.event_title[:160], "question": market.question[:160],
                        })
                    continue
                summary["degen_supported"] += 1
                supported_markets.append(market)

            self._stage(
                run_id, 4, 5,
                f"analyse quantitative normale de {len(supported_markets)} marché(s); TIMED court géré en parallèle",
            )
            candidates: list[PreparedCandidate] = []
            for market in supported_markets:
                require_free_memory(self.s.min_free_memory_gb)
                try:
                    estimate = self.quant.estimate(market, run_id=run_id)
                except Exception as exc:
                    estimate = EngineEstimate(
                        engine="DEGEN_QUANT_V3_RESEARCH_ENSEMBLE", probability_yes=0.5, confidence=0.0,
                        reliability=0.0, source_agreement=0.0, reasoning=str(exc), evidence=[],
                        observations=[], supported=False, reject_reason="engine_error",
                    )
                candidate = self._prepare_candidate(run_id, market, estimate, summary)
                if candidate is not None:
                    candidates.append(candidate)

            if (str(self.s.trading_mode).casefold() == "live"
                    and bool(getattr(self.s, "paper_parallel_live_enabled", False))):
                strict_candidates = [
                    c for c in candidates
                    if "PAPER_EXPLORE" not in str(getattr(c.signal, "signal_type", ""))
                ]
                exploration_candidates = [
                    c for c in candidates
                    if "PAPER_EXPLORE" in str(getattr(c.signal, "signal_type", ""))
                ]
                ranked_live = self._rank_candidates(strict_candidates, summary)
                ranked_exploration = self._rank_candidates(exploration_candidates, summary)
                ranked = ranked_live + ranked_exploration
                summary["ranked_live_candidates"] = len(ranked_live)
                summary["ranked_paper_exploration_candidates"] = len(ranked_exploration)
            else:
                ranked = self._rank_candidates(candidates, summary)
            summary["ranked_correlated_candidates"] = len(ranked)
            for candidate in ranked:
                self._execute_candidate(candidate, summary)

            summary["cycle_outcome"] = self._cycle_outcome(summary)
            self.db.log("cycle_outcome", "", json.dumps({
                "run_id": run_id, "outcome": summary["cycle_outcome"],
                "jupiter": summary["jupiter_degen_markets"],
                "supported": summary["degen_supported"],
                "signals": summary["signals"], "orders": summary["orders"],
            }, ensure_ascii=False))
            self._stage(run_id, 5, 5, f"terminé: {summary['cycle_outcome']}")
            status = "ok" if not summary["errors"] else "ok_with_errors"
            self.db.finish_run(run_id, status, json.dumps(summary, ensure_ascii=False))
            return summary
        except Exception as exc:
            summary["errors"].append(str(exc))
            summary["cycle_outcome"] = "failed"
            self.db.finish_run(run_id, "failed", json.dumps(summary, ensure_ascii=False))
            raise
