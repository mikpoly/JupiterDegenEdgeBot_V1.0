from __future__ import annotations

import logging
import time
from typing import Any

from .paper import _winner

log = logging.getLogger(__name__)


def _event_markets(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for event in events:
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            market_id = str(market.get("marketId") or market.get("id") or "")
            provider = str(market.get("provider") or "").casefold()
            if market_id:
                out[market_id] = market
                if not market_id.startswith("POLY-"):
                    out[f"POLY-{market_id}"] = market
                elif market_id.startswith("POLY-"):
                    out[market_id[5:]] = market
            if market_id and provider == "polymarket" and not market_id.startswith("POLY-"):
                out[f"POLY-{market_id}"] = market
    return out


class ShadowPredictionTracker:
    """Resolve non-traded predictions in bulk so learning never depends on order count."""

    def __init__(self, settings, db, jupiter):
        self.s, self.db, self.jupiter = settings, db, jupiter

    def refresh(self) -> dict[str, Any]:
        result = {"checked": 0, "resolved": 0, "errors": [], "summary": self.db.shadow_summary()}
        if not bool(getattr(self.s, "shadow_learning_enabled", True)):
            return result
        rows = self.db.open_shadow_predictions(
            limit=getattr(self.s, "shadow_refresh_limit", 300),
            grace_minutes=getattr(self.s, "shadow_resolution_grace_minutes", 2),
        )
        if not rows:
            return result

        since = max(0, int(min(int(row["expiry"] or time.time()) for row in rows)) - 6 * 3600)
        bulk: dict[str, dict[str, Any]] = {}
        try:
            bulk = _event_markets(self.jupiter.closed_events(since=since))
        except Exception as exc:
            result["errors"].append(f"closed-events: {exc}")

        individual_budget = max(0, int(getattr(self.s, "shadow_individual_fallback_limit", 5)))
        for row in rows:
            result["checked"] += 1
            market_id = str(row["market_id"])
            raw = bulk.get(market_id)
            source = "Jupiter /events/closed"
            if raw is None and individual_budget > 0:
                individual_budget -= 1
                try:
                    raw = self.jupiter.market(market_id)
                    source = "Jupiter /markets/{id}"
                except Exception as exc:
                    result["errors"].append(f"{market_id}: {exc}")
                    continue
            if not isinstance(raw, dict):
                continue
            winner = _winner(raw)
            if winner not in {"YES", "NO"}:
                continue
            self.db.resolve_shadow_prediction(int(row["id"]), 1 if winner == "YES" else 0, source)
            result["resolved"] += 1
        result["summary"] = self.db.shadow_summary()
        return result
