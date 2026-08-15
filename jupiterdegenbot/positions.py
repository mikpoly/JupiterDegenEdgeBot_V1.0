from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _f(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _micro(value) -> float:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0
    return float(number / Decimal("1000000")) if abs(number) > Decimal("2") else float(number)


def _first(mapping: dict, *keys, default=None):
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return mapping[key]
    return default


def parse_position(raw: dict) -> dict:
    market = (
        raw.get("marketMetadata") if isinstance(raw.get("marketMetadata"), dict)
        else raw.get("market") if isinstance(raw.get("market"), dict)
        else {}
    )
    pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    metadata = (
        raw.get("eventMetadata") if isinstance(raw.get("eventMetadata"), dict)
        else raw.get("metadata") if isinstance(raw.get("metadata"), dict)
        else {}
    )
    key = str(_first(raw, "positionPubkey", "positionKey", "pubkey", "id", default=""))
    market_id = str(_first(raw, "marketId", default=market.get("marketId") or metadata.get("marketId") or ""))
    event_id = str(_first(raw, "eventId", default=market.get("eventId") or metadata.get("eventId") or ""))
    is_yes = _first(raw, "isYes", default=None)
    outcome = str(_first(raw, "outcome", "side", default="YES" if is_yes is True else "NO" if is_yes is False else ""))
    shares = _micro(_first(raw, "contractsMicro", "sharesMicro", default=0))
    if shares == 0:
        shares = _f(_first(raw, "contractsDecimal", "shares", "contracts", default=0))
    entry = _micro(_first(raw, "avgPriceUsd", "entryPriceUsd", "averagePriceUsd", default=0))
    cost = _micro(_first(raw, "totalCostUsd", "costUsd", "sizeUsd", "investedUsd", default=0))
    value = _micro(_first(raw, "valueUsd", "currentValueUsd", "payoutValueUsd", default=0))
    if value == 0:
        mark = _micro(_first(raw, "markPriceUsd", "sellPriceUsd", default=pricing.get("markPriceUsd") or 0))
        if mark > 0 and shares > 0:
            value = mark * shares
    pnl = _micro(_first(raw, "pnlUsd", "profitLossUsd", default=value - cost))
    pnl_after_fees = _micro(_first(raw, "pnlUsdAfterFees", default=pnl))
    fees_paid = _micro(_first(raw, "feesPaidUsd", default=0))
    mark_price = _micro(_first(raw, "markPriceUsd", default=pricing.get("markPriceUsd") or 0))
    sell_raw = _first(raw, "sellPriceUsd", default=pricing.get("sellPriceUsd"))
    sell_price = _micro(sell_raw) if sell_raw not in (None, "") else 0.0
    payout_usd = _micro(_first(raw, "payoutUsd", default=0))
    realized_pnl = _micro(_first(raw, "realizedPnlUsd", default=0))
    status = str(_first(raw, "status", default="open")).casefold()
    claimable = bool(_first(raw, "claimable", "isClaimable", default=False))
    claimed = bool(_first(raw, "claimed", "isClaimed", default=False))
    question = str(_first(raw, "question", "title", default=market.get("title") or metadata.get("title") or market_id))
    event_title = str(_first(metadata, "title", default=""))
    close_time = _first(metadata, "closeTime", default=market.get("closeTime"))
    try:
        close_time = int(float(close_time)) if close_time not in (None, "") else None
    except (TypeError, ValueError):
        close_time = None
    return {
        "position_key": key,
        "market_id": market_id,
        "event_id": event_id,
        "event_title": event_title,
        "outcome": outcome.upper(),
        "shares": shares,
        "entry_price": entry,
        "mark_price": mark_price,
        "sell_price": sell_price,
        "cost_usd": cost,
        "value_usd": value,
        "pnl_usd": pnl,
        "pnl_after_fees_usd": pnl_after_fees,
        "fees_paid_usd": fees_paid,
        "realized_pnl_usd": realized_pnl,
        "payout_usd": payout_usd,
        "no_exit_price": sell_price <= 0.0,
        "close_time": close_time,
        "status": status,
        "question": question,
        "claimable": claimable,
        "claimed": claimed,
        "raw": raw,
    }


def extract_position_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("positions", "data", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            for nested in ("positions", "items", "data"):
                rows = value.get(nested)
                if isinstance(rows, list):
                    return [x for x in rows if isinstance(x, dict)]
    return []
