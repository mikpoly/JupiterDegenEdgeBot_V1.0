from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _price(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    if number > 2:
        number /= 1_000_000.0
    return number if 0 <= number <= 1 else None


def _first(mapping: dict, *keys):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _market_parts(raw: dict) -> list[dict]:
    output = [raw]
    for key in ("market", "data", "result", "metadata", "marketMetadata", "pricing"):
        value = raw.get(key)
        if isinstance(value, dict):
            output.append(value)
            pricing = value.get("pricing")
            if isinstance(pricing, dict):
                output.append(pricing)
    return output


def _winner(raw: dict) -> str:
    # Timed Up/Down instruments are one-sided YES markets. Jupiter may expose
    # the resolved direction as the literal word "Up"/"Down" rather than
    # YES/NO. Recover the YES label from marketOptions/title first.
    yes_label = ""
    for part in _market_parts(raw):
        options = part.get("marketOptions") if isinstance(part, dict) else None
        if isinstance(options, list):
            for option in options:
                if isinstance(option, dict) and option.get("buyYes") is True:
                    label = str(option.get("label") or "").strip().upper()
                    if label in {"UP", "DOWN"}:
                        yes_label = label
                        break
        if yes_label:
            break
        title = str(part.get("title") or "").strip().upper() if isinstance(part, dict) else ""
        if title in {"UP", "DOWN"}:
            yes_label = title
            break

    for part in _market_parts(raw):
        yes_flag = _first(part, "isYesWinner", "yesWon", "yesWinner")
        if yes_flag is True:
            return "YES"
        if yes_flag is False and any(k in part for k in ("isYesWinner", "yesWon", "yesWinner")):
            return "NO"
        value = _first(
            part, "winningOutcome", "resolvedOutcome", "winner", "resolution",
            "result", "outcome", "settlementOutcome",
        )
        if isinstance(value, bool):
            return "YES" if value else "NO"
        text = str(value or "").strip().upper()
        if text in {"YES", "Y", "TRUE", "1"}:
            return "YES"
        if text in {"NO", "N", "FALSE", "0"}:
            return "NO"
        if text in {"UP", "DOWN"} and yes_label in {"UP", "DOWN"}:
            return "YES" if text == yes_label else "NO"
        if text in {"CANCELLED", "CANCELED", "REFUNDED", "VOID", "INVALID"}:
            return "REFUND"
    return ""


def _status(raw: dict) -> str:
    for part in _market_parts(raw):
        value = _first(part, "status", "marketStatus", "resolutionStatus", "state")
        if value not in (None, ""):
            return str(value).strip().casefold()
    return ""


def _mark_price(raw: dict, outcome: str) -> float | None:
    yes = outcome.upper() == "YES"
    keys = (
        ("sellYesPriceUsd", "buyYesPriceUsd", "yesPriceUsd", "markYesPriceUsd", "yesPrice")
        if yes else
        ("sellNoPriceUsd", "buyNoPriceUsd", "noPriceUsd", "markNoPriceUsd", "noPrice")
    )
    for part in _market_parts(raw):
        for key in keys:
            value = _price(part.get(key))
            if value is not None:
                return value
    return None


class PaperTracker:
    """Refresh simulated orders from Jupiter without signing any transaction."""

    def __init__(self, settings, db, jupiter):
        self.s = settings
        self.db = db
        self.jupiter = jupiter

    def refresh(self) -> dict:
        result = {
            "checked": 0, "updated": 0, "won": 0, "lost": 0,
            "refunded": 0, "errors": [], "summary": self.db.paper_summary(),
        }
        if not bool(getattr(self.s, "paper_tracking_enabled", True)):
            return result
        rows = self.db.paper_orders_for_refresh(
            getattr(self.s, "paper_refresh_limit", 20),
            getattr(self.s, "paper_refresh_interval_minutes", 10),
        )
        for row in rows:
            result["checked"] += 1
            try:
                raw = self.jupiter.market(str(row["market_id"]))
                if not isinstance(raw, dict):
                    raise RuntimeError("marché Jupiter introuvable")
                entry = float(row["paper_entry_price"] or row["signal_price"] or 0)
                amount = float(row["amount_usd"] or 0)
                shares = float(row["paper_shares"] or (amount / entry if entry > 0 else 0))
                if shares <= 0:
                    raise RuntimeError("prix d'entrée PAPER invalide")

                winner = _winner(raw)
                status_text = _status(raw)
                closed = any(x in status_text for x in ("resolved", "settled", "closed", "final", "cancel", "void"))
                mark = _mark_price(raw, str(row["outcome"]))

                if winner == "REFUND" or any(x in status_text for x in ("cancel", "refund", "void", "invalid")):
                    order_status = "paper_refunded"
                    paper_result = "REFUNDED"
                    mark = entry
                    value = amount
                    result["refunded"] += 1
                elif winner in {"YES", "NO"}:
                    won = winner == str(row["outcome"]).upper()
                    order_status = "paper_won" if won else "paper_lost"
                    paper_result = "WON" if won else "LOST"
                    mark = 1.0 if won else 0.0
                    value = shares * mark
                    result["won" if won else "lost"] += 1
                else:
                    # Some feeds expose no explicit winner but converge to 0/1.
                    if closed and mark is not None and (mark >= 0.995 or mark <= 0.005):
                        won = mark >= 0.995
                        order_status = "paper_won" if won else "paper_lost"
                        paper_result = "WON" if won else "LOST"
                        mark = 1.0 if won else 0.0
                        value = shares * mark
                        result["won" if won else "lost"] += 1
                    else:
                        order_status = "paper_filled"
                        paper_result = "OPEN"
                        mark = entry if mark is None else mark
                        value = shares * mark

                pnl = value - amount
                self.db.update_paper_order(
                    int(row["id"]), status=order_status, mark_price=float(mark),
                    value_usd=float(value), pnl_usd=float(pnl), result=paper_result,
                    entry_price=entry, shares=shares,
                    response_patch={
                        "market_status": status_text, "winner": winner,
                        "mark_price": mark, "value_usd": value, "pnl_usd": pnl,
                    },
                )
                result["updated"] += 1
            except Exception as exc:
                text = f"order {row['id']} / {row['market_id']}: {exc}"
                result["errors"].append(text)
                self.db.log("paper_tracking_error", str(row["market_id"]), text)
                log.debug("Suivi PAPER indisponible: %s", text)
        result["summary"] = self.db.paper_summary()
        return result
