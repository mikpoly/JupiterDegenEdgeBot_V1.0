from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from dataclasses import dataclass

from .execution import _first, _order_part
from .live_gate import LiveValidationGate
from .micro_live import MICRO_LIVE_CONFIRMATION
from .instance_lock import old_android_bot_running, pc_bot_processes
from .positions import extract_position_rows, parse_position
from .wallet import WalletSendError

log = logging.getLogger(__name__)

PENDING_ORDER_STATUSES = {"preparing", "unsigned_ready", "sent", "pending_fill", "partial_fill", "unknown_send"}
FILLED_STATUSES = {"filled", "fully_filled", "confirmed", "complete", "completed", "settled"}
FAILED_STATUSES = {"failed", "cancelled", "canceled", "rejected", "expired"}


@dataclass(slots=True)
class ReconcileReport:
    orders_checked: int = 0
    orders_filled: int = 0
    orders_failed: int = 0
    orders_pending: int = 0
    positions_synced: int = 0
    claimable: int = 0
    claimed: int = 0
    errors: list[str] | None = None

    def as_dict(self) -> dict:
        return {
            "orders_checked": self.orders_checked,
            "orders_filled": self.orders_filled,
            "orders_failed": self.orders_failed,
            "orders_pending": self.orders_pending,
            "positions_synced": self.positions_synced,
            "claimable": self.claimable,
            "claimed": self.claimed,
            "errors": self.errors or [],
        }


@dataclass(slots=True)
class ExitDecision:
    action: str
    reason: str
    current_price: float


def _number(value, default: float = -1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number / 1_000_000 if number > 1.5 else number


def _timestamp(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
        return number / 1000 if number > 10_000_000_000 else number
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def decide_exit(position: dict, market: dict, settings, now_ts: float | None = None) -> ExitDecision:
    """Fail-closed exit rule used only when POSITION_MANAGEMENT_ENABLED=true."""
    now_ts = time.time() if now_ts is None else now_ts
    raw = position.get("raw") if isinstance(position.get("raw"), dict) else {}
    current = _number(_first(raw, "sellPriceUsd", "markPriceUsd", default=None))
    outcome = str(position.get("outcome") or "").upper()
    pricing = market.get("pricing") if isinstance(market, dict) and isinstance(market.get("pricing"), dict) else (market if isinstance(market, dict) else {})
    if current < 0:
        key = "sellYesPriceUsd" if outcome == "YES" else "sellNoPriceUsd"
        current = _number(pricing.get(key))
    if current < 0:
        return ExitDecision("hold", "prix de sortie indisponible", -1)
    close_time = _first(market if isinstance(market, dict) else {}, "closeTime", "close_time", default=None)
    if close_time in (None, ""):
        close_time = _first(raw, "closeTime", "close_time", default=None)
    close_ts = _timestamp(close_time)
    if close_ts is not None:
        minutes_left = (close_ts - now_ts) / 60.0
        if 0 <= minutes_left <= float(getattr(settings, "exit_minutes_before_close", 15.0)):
            return ExitDecision("sell", f"sortie programmée avant clôture ({minutes_left:.0f} min)", current)
    entry = _number(position.get("entry_price"), 0.0)
    if entry > 0:
        take_profit = float(getattr(settings, "take_profit_price_delta", 0.20))
        stop_loss = float(getattr(settings, "stop_loss_price_delta", 0.20))
        if current >= min(0.99, entry + take_profit):
            return ExitDecision("sell", f"prise de profit {entry:.3f} -> {current:.3f}", current)
        if current <= max(0.01, entry - stop_loss):
            return ExitDecision("sell", f"coupe de perte {entry:.3f} -> {current:.3f}", current)
    return ExitDecision("hold", "position dans les bornes", current)


class LiveLifecycle:
    MAX_POSITION_PAGES = 50
    MAX_ORDER_PAGES = 50
    MAX_HISTORY_PAGES = 50

    def __init__(self, settings, db, jupiter, wallet):
        self.s = settings
        self.db = db
        self.jupiter = jupiter
        self.wallet = wallet
        self._owner: str | None = None

    def owner(self) -> str:
        if not self._owner:
            self._owner = self.wallet.owner()
        return self._owner

    def _require_live_mutation(self) -> None:
        if not bool(getattr(self.s, "live_release_enabled", False)):
            raise RuntimeError("LIVE désactivé: LIVE_RELEASE_ENABLED=false; utilise PAPER")
        if not bool(getattr(self.s, "release_live_capable", True)):
            raise RuntimeError("LIVE interdit par le code de la v1.0.0 MICRO-LIVE/Validation")
        if hasattr(self.s, "live_allowed_by_version") and not bool(self.s.live_allowed_by_version):
            raise RuntimeError("LIVE interdit dans la v1.0.0 MICRO-LIVE/Validation")
        # Exits and claims must remain possible after a MICRO-LIVE buy window
        # expires. New buys are gated in Executor; lifecycle mutations require
        # the explicit consent flags but not another statistical/new-order gate.
        if bool(getattr(self.s, "micro_live_enabled", False)):
            if str(getattr(self.s, "micro_live_confirmation", "")) != MICRO_LIVE_CONFIRMATION:
                raise RuntimeError("MICRO_LIVE_CONFIRMATION incorrect")
        elif bool(getattr(self.s, "live_validation_gate_enabled", False)):
            LiveValidationGate(self.s, self.db).require()
        if self.s.trading_mode != "live":
            raise RuntimeError("TRADING_MODE=live requis pour signer")
        if not self.s.auto_execute:
            raise RuntimeError("AUTO_EXECUTE=true requis pour signer")
        if self.s.live_confirmation != "I_ACCEPT_REAL_MONEY_RISK":
            raise RuntimeError("LIVE_CONFIRMATION incorrect")
        if self.s.refuse_live_while_old_bot_running and old_android_bot_running():
            raise RuntimeError("ancien bot Jupiter encore actif; mutation LIVE bloquée")
        duplicates = pc_bot_processes()
        if duplicates:
            detail = ", ".join(f"PID {row['pid']}" for row in duplicates)
            raise RuntimeError(f"autre JupiterDegenEdgeBot actif; mutation LIVE bloquée ({detail})")

    @staticmethod
    def _pagination(payload: dict) -> dict:
        return payload.get("pagination") if isinstance(payload, dict) and isinstance(payload.get("pagination"), dict) else {}

    def fetch_positions_all(self) -> list[dict] | None:
        collected: list[dict] = []
        start: int | None = None
        for page in range(self.MAX_POSITION_PAGES):
            try:
                payload = self.jupiter.positions(self.owner(), start=start)
            except Exception as exc:
                self.db.log("positions_error", "", str(exc))
                return None
            rows = extract_position_rows(payload)
            pagination = self._pagination(payload)
            if isinstance(payload, dict) and isinstance(payload.get("data"), list) and not isinstance(pagination.get("hasNext"), bool):
                self.db.log("positions_error", "", "réponse positions data sans pagination.hasNext booléen")
                return None
            collected.extend(rows)
            if not pagination.get("hasNext"):
                return collected
            try:
                start = int(pagination["end"]) + 1
            except (KeyError, TypeError, ValueError):
                self.db.log("positions_error", "", "pagination positions incomplète; instantané refusé")
                return None
        self.db.log("positions_error", "", f"plus de {self.MAX_POSITION_PAGES} pages; instantané refusé")
        return None

    def fetch_orders_all(self) -> list[dict] | None:
        collected: list[dict] = []
        start: int | None = None
        for _page in range(self.MAX_ORDER_PAGES):
            try:
                payload = self.jupiter.orders(self.owner(), start=start)
            except Exception as exc:
                self.db.log("orders_error", "", str(exc))
                return None
            rows = payload.get("data") if isinstance(payload, dict) else payload
            rows = rows if isinstance(rows, list) else []
            collected.extend(row for row in rows if isinstance(row, dict))
            pagination = self._pagination(payload if isinstance(payload, dict) else {})
            if not pagination.get("hasNext"):
                return collected
            try:
                start = int(pagination["end"]) + 1
            except (KeyError, TypeError, ValueError):
                self.db.log("orders_error", "", "pagination ordres incomplète")
                return None
        self.db.log("orders_error", "", f"plus de {self.MAX_ORDER_PAGES} pages d'ordres")
        return None

    def fetch_history_all(self, position_pubkey: str | None = None) -> list[dict] | None:
        # FINAL_POSITION_RECONCILE_V1: optional authoritative per-position history.
        collected: list[dict] = []
        start: int | None = None
        for _page in range(self.MAX_HISTORY_PAGES):
            try:
                payload = self.jupiter.history(
                    self.owner(),
                    start=start,
                    position_pubkey=position_pubkey,
                )
            except Exception as exc:
                self.db.log("history_error", "", str(exc))
                return None
            rows = payload.get("data") if isinstance(payload, dict) else payload
            rows = rows if isinstance(rows, list) else []
            collected.extend(row for row in rows if isinstance(row, dict))
            pagination = self._pagination(payload if isinstance(payload, dict) else {})
            if not pagination.get("hasNext"):
                return collected
            try:
                start = int(pagination["end"]) + 1
            except (KeyError, TypeError, ValueError):
                self.db.log("history_error", "", "pagination historique incompl?te")
                return None
        self.db.log("history_error", "", f"plus de {self.MAX_HISTORY_PAGES} pages d'historique")
        return None

    def sync_positions(self) -> tuple[list[dict] | None, int]:
        """Synchronize active positions and close only authoritative terminal ones.

        FINAL_POSITION_RECONCILE_V1
        A position missing from a complete /positions snapshot is never closed
        by absence alone. Its filtered /history must contain either
        position_lost or payout_claimed.
        """
        raw_rows = self.fetch_positions_all()
        if raw_rows is None:
            return None, 0

        parsed_rows: list[dict] = []
        for raw in raw_rows:
            parsed = parse_position(raw)
            if not parsed.get("position_key") or not parsed.get("market_id"):
                self.db.log("positions_error", "", f"position mal form?e: {str(raw)[:250]}")
                return None, 0
            self.db.upsert_position(parsed)
            parsed_rows.append(parsed)

        remote_keys = {
            str(p.get("position_key") or "")
            for p in parsed_rows
            if p.get("position_key")
        }

        with self.db.connect(readonly=True) as conn:
            local_active = [
                dict(row) for row in conn.execute(
                    """SELECT position_key,market_id,asset,status,cost_usd,fees_paid_usd
                       FROM positions
                       WHERE claimed=0
                         AND status IN (
                           'open','active','pending','claimable',
                           'closing','closing_unknown'
                         )"""
                ).fetchall()
            ]

        missing = [
            row for row in local_active
            if str(row.get("position_key") or "") not in remote_keys
        ]

        for local in missing:
            position_key = str(local.get("position_key") or "")
            market_id = str(local.get("market_id") or "")
            if not position_key:
                continue

            events = self.fetch_history_all(position_pubkey=position_key)
            if events is None:
                self.db.log(
                    "position_reconcile_deferred",
                    market_id,
                    f"{position_key[:12]}: historique Jupiter indisponible; position conserv?e",
                )
                continue

            final_events: list[dict] = []
            for event in events:
                et = str(event.get("eventType") or "").strip().casefold()
                if et in {"position_lost", "payout_claimed"}:
                    final_events.append(event)

            if not final_events:
                self.db.log(
                    "position_reconcile_pending",
                    market_id,
                    f"{position_key[:12]} absent du snapshot mais aucun ?v?nement terminal; position conserv?e",
                )
                continue

            def _event_order(event: dict) -> tuple[int, int]:
                try:
                    ts = int(event.get("timestamp") or 0)
                except (TypeError, ValueError):
                    ts = 0
                try:
                    eid = int(event.get("id") or 0)
                except (TypeError, ValueError):
                    eid = 0
                return ts, eid

            final_event = max(final_events, key=_event_order)

            if self.db.finalize_position_from_history(position_key, final_event):
                detail = {
                    "position": position_key,
                    "eventType": final_event.get("eventType"),
                    "realizedPnl": final_event.get("realizedPnl"),
                    "realizedPnlBeforeFees": final_event.get("realizedPnlBeforeFees"),
                    "payoutAmountUsd": final_event.get("payoutAmountUsd"),
                    "signature": final_event.get("signature"),
                    "timestamp": final_event.get("timestamp"),
                }
                self.db.log(
                    "position_terminal_reconciled",
                    market_id,
                    json.dumps(detail, ensure_ascii=False, default=str),
                )

        return parsed_rows, len(parsed_rows)

    @staticmethod
    def _normalized_order_status(payload: dict) -> str:
        part = _order_part(payload)
        raw = str(_first(part, "status", "orderStatus", "state", default="")).strip().casefold()
        return raw.replace("-", "_").replace(" ", "_")

    def reconcile_orders(self, positions: list[dict] | None = None) -> dict:
        rows = self.db.live_orders_for_reconcile()
        report = {
            "checked": 0, "filled": 0, "failed": 0, "pending": 0,
            "errors": [], "official_orders": 0, "history_events": 0,
        }
        if not rows:
            return report

        exact_positions = {
            str(p.get("position_key")): p
            for p in (positions or [])
            if p.get("position_key")
        }
        official_orders = self.fetch_orders_all()
        history = self.fetch_history_all()
        report["official_orders"] = len(official_orders or [])
        report["history_events"] = len(history or [])

        order_map: dict[str, dict] = {}
        for item in official_orders or []:
            key = str(_first(item, "pubkey", "orderPubkey", "order", default=""))
            if key:
                order_map[key] = item

        history_by_order: dict[str, list[dict]] = {}
        for item in history or []:
            key = str(_first(item, "orderPubkey", "order", default=""))
            if key:
                history_by_order.setdefault(key, []).append(item)

        for row in rows:
            report["checked"] += 1
            order_id = int(row["id"])
            market_id = str(row["market_id"] or "")
            order_pubkey = str(row["order_pubkey"] or "")
            position_pubkey = str(row["position_pubkey"] or "")
            signature = str(row["signature"] or "")
            local_status = str(row["status"] or "")

            if local_status == "preparing" and not order_pubkey and not position_pubkey and not signature:
                self.db.update_order(
                    order_id, "failed",
                    {"reason": "interrupted_before_unsigned_order_was_persisted"},
                )
                report["failed"] += 1
                continue

            payload: dict = {}
            official = order_map.get(order_pubkey, {}) if order_pubkey else {}
            if order_pubkey:
                try:
                    payload = self.jupiter.order_status(order_pubkey)
                except Exception as exc:
                    text_error = str(exc)
                    if "not found" not in text_error.casefold() and "404" not in text_error:
                        report["errors"].append(f"{order_pubkey[:10]}: {text_error[:180]}")
                if not payload and official:
                    payload = official
            elif official:
                payload = official

            order_part = _order_part(payload) if payload else official
            status = self._normalized_order_status(order_part or {})
            official_position = str(
                _first(order_part or {}, "positionPubkey", "position", default="")
            )
            if not official_position and order_pubkey:
                for event in history_by_order.get(order_pubkey, []):
                    official_position = str(_first(event, "positionPubkey", default=""))
                    if official_position:
                        break
            if official_position and official_position != position_pubkey:
                position_pubkey = official_position
                self.db.update_order(
                    order_id, local_status or "pending_fill",
                    {"official_position_pubkey": official_position},
                    position_pubkey=official_position,
                )

            if status in FILLED_STATUSES:
                self.db.update_order(
                    order_id, "filled",
                    {"official_order": order_part or payload, "reconciled_via": "order_status"},
                    position_pubkey=position_pubkey,
                )
                report["filled"] += 1
                continue
            if status in FAILED_STATUSES:
                self.db.update_order(
                    order_id, "failed",
                    {"official_order": order_part or payload, "reconciled_via": "order_status"},
                    position_pubkey=position_pubkey,
                )
                report["failed"] += 1
                continue
            if status in {"partial", "partially_filled", "partial_fill", "partiallyfilled"}:
                self.db.update_order(
                    order_id, "partial_fill",
                    {"official_order": order_part or payload, "reconciled_via": "order_status"},
                    position_pubkey=position_pubkey,
                )
                report["pending"] += 1
                continue

            events = history_by_order.get(order_pubkey, []) if order_pubkey else []
            history_filled = False
            history_failed = False
            history_position = ""
            for event in events:
                event_type = str(_first(event, "eventType", "type", default="")).casefold()
                filled_contracts = _number(_first(event, "filledContracts", default=0), 0.0)
                history_position = history_position or str(
                    _first(event, "positionPubkey", default="")
                )
                if event_type in {"order_filled", "position_updated"} or filled_contracts > 0:
                    history_filled = True
                if event_type in {"order_failed", "order_cancelled", "order_canceled"}:
                    history_failed = True
            if history_position and history_position != position_pubkey:
                position_pubkey = history_position
            if history_filled:
                self.db.update_order(
                    order_id, "filled",
                    {"history_events": events, "reconciled_via": "history"},
                    position_pubkey=position_pubkey,
                )
                report["filled"] += 1
                continue
            if history_failed:
                self.db.update_order(
                    order_id, "failed",
                    {"history_events": events, "reconciled_via": "history"},
                    position_pubkey=position_pubkey,
                )
                report["failed"] += 1
                continue

            if signature:
                try:
                    chain_status = self.wallet.signature_status(signature)
                except Exception as exc:
                    chain_status = None
                    report["errors"].append(f"signature {signature[:10]}: {str(exc)[:160]}")
                if chain_status and chain_status.get("err"):
                    self.db.update_order(
                        order_id, "failed",
                        {"signature_status": chain_status, "official_order": payload},
                        position_pubkey=position_pubkey,
                    )
                    report["failed"] += 1
                    continue

            if position_pubkey and position_pubkey in exact_positions:
                p = exact_positions[position_pubkey]
                self.db.update_order(
                    order_id, "filled",
                    {
                        "reconciled_from_position": p.get("raw", {}),
                        "official_order": payload,
                        "reconciled_via": "exact_position",
                    },
                    position_pubkey=position_pubkey,
                )
                report["filled"] += 1
                continue

            self.db.update_order(
                order_id, "pending_fill",
                {
                    "official_order": payload,
                    "history_events": events,
                    "reconciled": True,
                },
                position_pubkey=position_pubkey,
            )
            report["pending"] += 1
            self.db.log(
                "order_pending", market_id,
                f"order={order_pubkey or '-'} position={position_pubkey or '-'}",
            )
        return report

    @staticmethod
    def _transaction_payload(payload: dict) -> tuple[str, dict]:
        part = _order_part(payload)
        transaction = str(_first(payload, "transaction", "serializedTransaction", default="") or _first(part, "transaction", "serializedTransaction", default=""))
        meta = payload.get("txMeta") or part.get("txMeta") or {}
        return transaction, meta

    def claim_position(self, position: dict) -> dict:
        self._require_live_mutation()
        key = str(position.get("position_key") or "")
        if not key:
            raise RuntimeError("positionPubkey absent")
        payload = self.jupiter.claim(self.owner(), key)
        transaction, meta = self._transaction_payload(payload)
        if not transaction:
            message = str(payload.get("message") or payload.get("detail") or "")
            if payload.get("claimRequired") is False or "claim not required" in message.casefold() or "no claim" in message.casefold():
                self.db.mark_position_status(key, "claimed", claimable=False, claimed=True)
                self.db.add_claim(key, str(position.get("market_id") or ""), "not_required", response=payload)
                return {"ok": True, "claim_required": False, "response": payload}
            raise RuntimeError("transaction de claim absente")
        sent = self.wallet.sign_and_send(transaction, meta)
        self.db.mark_position_status(key, "claimed", claimable=False, claimed=True)
        signature = str(sent.get("signature") or sent.get("txid") or "") if isinstance(sent, dict) else ""
        self.db.add_claim(key, str(position.get("market_id") or ""), "sent", signature, {"send": sent, "response": payload})
        self.db.log("claim_sent", str(position.get("market_id") or ""), json.dumps({"position": key, "send": sent}, ensure_ascii=False))
        return {"ok": True, "claim_required": True, "send": sent, "response": payload}

    def claim_all(self, positions: list[dict] | None = None) -> dict:
        # CLAIM_FALLBACK_SINGLE_POSITION_V1
        #
        # Le snapshot global Jupiter peut parfois omettre une position
        # deja connue localement. Dans ce cas seulement, on relit cette
        # position via GET /positions/{positionPubkey}.
        #
        # On ne claim JAMAIS sur une supposition locale :
        # Jupiter doit confirmer claimable=True et claimed=False.

        if positions is None:
            positions, _ = self.sync_positions()

        result = {
            "claimable": 0,
            "claimed": 0,
            "fallback_checked": 0,
            "fallback_found": 0,
            "errors": [],
        }

        if positions is None:
            result["errors"].append("instantan? positions indisponible")
            return result

        candidates = list(positions)

        seen_keys = {
            str(p.get("position_key") or "")
            for p in candidates
            if p.get("position_key")
        }

        # Positions que le bot connait encore comme actives.
        # Maximum volontairement faible pour ne pas surcharger Jupiter.
        try:
            with self.db.connect(readonly=True) as conn:
                local_rows = conn.execute(
                    """
                    SELECT position_key, market_id
                    FROM positions
                    WHERE claimed=0
                      AND COALESCE(position_key,'')<>''
                      AND status IN (
                          'open','active','pending','claimable',
                          'closing','closing_unknown'
                      )
                    ORDER BY updated_at DESC
                    LIMIT 50
                    """
                ).fetchall()
        except Exception as exc:
            self.db.log("claim_fallback_error", "", f"lecture positions locales: {exc}")
            local_rows = []

        for row in local_rows:
            key = str(row["position_key"] or "")
            local_market_id = str(row["market_id"] or "")

            if not key or key in seen_keys:
                continue

            result["fallback_checked"] += 1

            try:
                raw = self.jupiter.get(f"/positions/{key}")
            except Exception as exc:
                message = str(exc)
                # Une ancienne position perdue/disparue ne doit pas casser
                # toute la maintenance.
                if "404" not in message and "not found" not in message.casefold():
                    self.db.log(
                        "claim_fallback_lookup_error",
                        local_market_id,
                        f"{key}: {message}"[:6000],
                    )
                continue

            if not isinstance(raw, dict):
                self.db.log(
                    "claim_fallback_invalid",
                    local_market_id,
                    f"{key}: r?ponse individuelle non dictionnaire",
                )
                continue

            try:
                parsed = parse_position(raw)
            except Exception as exc:
                self.db.log(
                    "claim_fallback_invalid",
                    local_market_id,
                    f"{key}: parse impossible: {exc}",
                )
                continue

            official_key = str(parsed.get("position_key") or "")
            if official_key != key:
                self.db.log(
                    "claim_fallback_mismatch",
                    local_market_id,
                    f"attendu={key} re?u={official_key}",
                )
                continue

            if parsed.get("market_id"):
                self.db.upsert_position(parsed)

            candidates.append(parsed)
            seen_keys.add(key)

            if parsed.get("claimable") and not parsed.get("claimed"):
                result["fallback_found"] += 1
                self.db.log(
                    "claim_fallback_found",
                    str(parsed.get("market_id") or local_market_id),
                    f"position={key}; Jupiter individuel confirme claimable=true",
                )

        # Traitement normal existant.
        for position in candidates:
            if not position.get("claimable") or position.get("claimed"):
                continue

            result["claimable"] += 1
            market_id = str(position.get("market_id") or "")

            if (
                self.db.recent_log("claim_sent", market_id, minutes=30)
                or self.db.recent_log("claim_unknown", market_id, minutes=60)
            ):
                continue

            try:
                self.claim_position(position)
                result["claimed"] += 1

            except WalletSendError as exc:
                kind = "claim_unknown" if exc.ambiguous else "claim_error"
                self.db.log(
                    kind,
                    market_id,
                    str(exc),
                )
                result["errors"].append(
                    f"{position.get('position_key','')[:10]}: {exc}"
                )

            except Exception as exc:
                self.db.log(
                    "claim_error",
                    market_id,
                    str(exc),
                )
                result["errors"].append(
                    f"{position.get('position_key','')[:10]}: {exc}"
                )

        return result

    def _close_known_position(self, position: dict, reason: str = "fermeture manuelle") -> dict:
        self._require_live_mutation()
        position_key = str(position.get("position_key") or "")
        if not position_key:
            raise RuntimeError("positionPubkey absent")
        market_id = str(position.get("market_id") or "")
        outcome = str(position.get("outcome") or "").upper()
        shares = float(position.get("shares") or 0.0)
        if not market_id or outcome not in {"YES", "NO"} or shares <= 0:
            raise RuntimeError("position incomplète; vente refusée")
        if hasattr(self.jupiter, "create_sell_order"):
            payload = self.jupiter.create_sell_order(
                self.owner(), position_key, market_id, outcome == "YES", shares
            )
            order = payload.get("order") if isinstance(payload, dict) and isinstance(payload.get("order"), dict) else _order_part(payload)
            returned_owner = str(_first(order, "userPubkey", "ownerPubkey", "owner", default=""))
            returned_market = str(_first(order, "marketId", "market", default=""))
            if returned_owner and returned_owner != self.owner():
                raise RuntimeError("propriétaire de vente Jupiter inattendu")
            if returned_market and returned_market != market_id:
                raise RuntimeError("marché de vente Jupiter inattendu")
            if _first(order, "isBuy", default=False) is not False:
                raise RuntimeError("réponse Jupiter ne confirme pas isBuy=false")
            if _first(order, "isYes", default=(outcome == "YES")) is not (outcome == "YES"):
                raise RuntimeError("côté de vente Jupiter inattendu")
        else:
            # Compatibility for test doubles only; production JupiterClient
            # exposes create_sell_order with the current POST /orders schema.
            payload = self.jupiter.close_position(self.owner(), position_key)
        transaction, meta = self._transaction_payload(payload)
        if not transaction:
            raise RuntimeError("transaction de vente absente")
        try:
            sent = self.wallet.sign_and_send(transaction, meta)
        except WalletSendError as exc:
            kind = "close_unknown" if exc.ambiguous else "close_error"
            if exc.ambiguous:
                self.db.mark_position_status(position_key, "closing_unknown")
            self.db.log(kind, str(position.get("market_id") or ""), json.dumps({"position": position_key, "signature": exc.signature, "reason": reason, "error": str(exc)}, ensure_ascii=False))
            raise
        self.db.mark_position_status(position_key, "closing")
        self.db.log("close_sent", str(position.get("market_id") or ""), json.dumps({"position": position_key, "reason": reason, "send": sent}, ensure_ascii=False))
        return {"ok": True, "position": position_key, "reason": reason, "send": sent, "response": payload}

    def close_position(self, position_key: str) -> dict:
        self._require_live_mutation()
        positions, _ = self.sync_positions()
        if positions is None:
            raise RuntimeError("positions indisponibles; fermeture refusée")
        position = next((p for p in positions if p.get("position_key") == position_key), None)
        if not position:
            raise RuntimeError("position introuvable")
        return self._close_known_position(position)

    def manage_positions(self, positions: list[dict] | None) -> dict:
        result = {"checked": 0, "sell_signals": 0, "sold": 0, "errors": []}
        if not self.s.position_management_enabled or positions is None:
            return result
        if not (self.s.trading_mode == "live" and self.s.auto_execute and self.s.live_confirmation == "I_ACCEPT_REAL_MONEY_RISK"):
            result["errors"].append("gestion de position activée mais verrous LIVE incomplets")
            return result
        for position in positions:
            if str(position.get("status") or "").casefold() != "open" or float(position.get("shares") or 0) <= 0:
                continue
            result["checked"] += 1
            market_id = str(position.get("market_id") or "")
            position_key = str(position.get("position_key") or "")
            if (
                self.db.recent_log("close_sent", market_id, minutes=30)
                or self.db.recent_log("close_unknown", market_id, minutes=60)
            ):
                continue
            market = self.jupiter.market(market_id) or {}
            decision = decide_exit(position, market, self.s)
            if decision.action != "sell":
                continue
            result["sell_signals"] += 1
            try:
                self._close_known_position(position, decision.reason)
                result["sold"] += 1
            except Exception as exc:
                result["errors"].append(f"{position_key[:10]}: {exc}")
        return result

    @staticmethod
    def _position_blocks_new_buy(position: dict) -> bool:
        """Return True while a Jupiter position must prevent reinforcement.

        Jupiter represents holdings by ``marketId`` and side.  The safety rule
        is intentionally stricter: any still-held position on a market blocks
        both the same side (reinforcement) and the opposite side (accidental
        hedging).  Claimed/empty positions no longer block future markets.
        """
        if not str(position.get("market_id") or "").strip():
            return False
        if bool(position.get("claimed")):
            return False
        try:
            shares = float(position.get("shares") or 0.0)
        except (TypeError, ValueError):
            shares = 0.0
        status = str(position.get("status") or "open").strip().casefold()
        inactive = {"closed", "sold", "claimed", "lost", "empty", "cancelled", "canceled"}
        return shares > 0.0 and status not in inactive

    def maintenance(self, *, auto_claim: bool = True) -> dict:
        positions, synced = self.sync_positions()
        reconcile = self.reconcile_orders(positions)
        claims = {"claimable": 0, "claimed": 0, "errors": []}
        guards: list[dict] = []
        if positions is not None:
            claims["claimable"] = sum(1 for p in positions if p.get("claimable") and not p.get("claimed"))
            guards = [
                {
                    "market_id": str(p.get("market_id") or ""),
                    "event_id": str(p.get("event_id") or ""),
                    "event_title": str(p.get("event_title") or ""),
                    "outcome": str(p.get("outcome") or "").upper(),
                    "position_key": str(p.get("position_key") or ""),
                    "question": str(p.get("question") or ""),
                    "shares": float(p.get("shares") or 0.0),
                    "cost_usd": float(p.get("cost_usd") or 0.0),
                }
                for p in positions
                if self._position_blocks_new_buy(p)
            ]
        if auto_claim and self.s.auto_claim_enabled and self.s.trading_mode == "live" and self.s.auto_execute and self.s.live_confirmation == "I_ACCEPT_REAL_MONEY_RISK":
            claims = self.claim_all(positions)
        exits = self.manage_positions(positions)
        return {
            "positions": synced,
            "positions_snapshot_complete": positions is not None,
            "open_position_guards": guards,
            "reconcile": reconcile,
            "claims": claims,
            "exits": exits,
            "summary": self.db.live_summary(),
        }
