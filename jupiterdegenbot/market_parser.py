from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .models import CryptoMarketSpec, Market

# Jupiter currently exposes timed/Degen subcategories for these symbols.  The
# parser remains fail-closed: exactly one asset must be identified.
ASSET_PATTERNS = {
    "BTC": r"\b(?:BTC|BITCOIN)\b",
    "ETH": r"\b(?:ETH|ETHEREUM)\b",
    "SOL": r"\b(?:SOL|SOLANA)\b",
    "XRP": r"\b(?:XRP|RIPPLE)\b",
    "HYPE": r"\b(?:HYPE|HYPERLIQUID)\b",
    "DOGE": r"\b(?:DOGE|DOGECOIN)\b",
    "BNB": r"\b(?:BNB|BINANCE\s+COIN)\b",
}
MONEY = r"\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([KMB])?"


def _number(raw: str, suffix: str = "") -> float:
    value = float(raw.replace(",", ""))
    scale = {"K": 1_000.0, "M": 1_000_000.0, "B": 1_000_000_000.0}.get(suffix.upper(), 1.0)
    return value * scale


def _headline(market: Market) -> str:
    event = str(market.event_title or "").strip()
    question = str(market.question or "").strip()
    if "___" in event and question:
        event = event.replace("___", question, 1)
        question = ""
    text = " ".join(x for x in (event, question) if x)
    return text.replace("↑", " above_or_equal ").replace("↓", " below_or_equal ")


def market_text(market: Market | str) -> str:
    if isinstance(market, Market):
        labels = f"YES={getattr(market, 'yes_label', 'YES')} NO={getattr(market, 'no_label', 'NO')}"
        return " ".join((
            _headline(market), market.rules, market.category, market.subcategory,
            market.resolution_source, labels,
        ))
    return str(market or "")


def detect_asset(text: str) -> str | None:
    matches = [asset for asset, pattern in ASSET_PATTERNS.items() if re.search(pattern, text, re.I)]
    return matches[0] if len(matches) == 1 else None


def _detect_market_asset(market: Market) -> str | None:
    headline = _headline(market)
    headline_matches = [asset for asset, pattern in ASSET_PATTERNS.items() if re.search(pattern, headline, re.I)]
    if len(headline_matches) > 1:
        return None
    if len(headline_matches) == 1:
        return headline_matches[0]
    sub = str(market.subcategory or "").strip().upper()
    if sub in ASSET_PATTERNS:
        return sub
    return detect_asset(market_text(market))


def looks_like_crypto_market(market: Market | str) -> bool:
    text = market_text(market)
    asset = _detect_market_asset(market) if isinstance(market, Market) else detect_asset(text)
    if asset is None:
        return False
    price_words = (
        r"\b(?:price|trading|trade|close|closes|closing|settle|settles|settlement|"
        r"above|below|higher|lower|between|reach|hit|up\s+or\s+down|price\s+to\s+beat)\b"
    )
    money_or_direction = rf"(?:{MONEY}|\bup\s+or\s+down\b|[↑↓])"
    return bool(re.search(price_words, text, re.I) and re.search(money_or_direction, text, re.I))


def _explicit_timezone(text: str) -> str:
    upper = text.upper()
    mapping = (
        (r"\b(?:UTC|GMT)\b", "UTC"),
        (r"\b(?:ET|EST|EDT)\b", "America/New_York"),
        (r"\b(?:PT|PST|PDT)\b", "America/Los_Angeles"),
        (r"\b(?:CET|CEST)\b", "Europe/Brussels"),
    )
    for pattern, zone in mapping:
        if re.search(pattern, upper):
            return zone
    return "UTC"


MONTHS = {
    "JAN": 1, "JANUARY": 1, "FEB": 2, "FEBRUARY": 2, "MAR": 3, "MARCH": 3,
    "APR": 4, "APRIL": 4, "MAY": 5, "JUN": 6, "JUNE": 6, "JUL": 7,
    "JULY": 7, "AUG": 8, "AUGUST": 8, "SEP": 9, "SEPT": 9,
    "SEPTEMBER": 9, "OCT": 10, "OCTOBER": 10, "NOV": 11,
    "NOVEMBER": 11, "DEC": 12, "DECEMBER": 12,
}


def _text_date(text: str) -> tuple[int, int, int] | None:
    upper = text.upper()
    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    patterns = (
        rf"\b({month_names})\s+(\d{{1,2}})(?:ST|ND|RD|TH)?(?:,)?\s+(20\d{{2}})\b",
        rf"\b(\d{{1,2}})(?:ST|ND|RD|TH)?\s+({month_names})(?:,)?\s+(20\d{{2}})\b",
        r"\b(20\d{2})-(\d{2})-(\d{2})\b",
    )
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, upper)
        if not match:
            continue
        if index == 0:
            month, day, year = MONTHS[match.group(1)], int(match.group(2)), int(match.group(3))
        elif index == 1:
            day, month, year = int(match.group(1)), MONTHS[match.group(2)], int(match.group(3))
        else:
            year, month, day = map(int, match.groups())
        try:
            datetime(year, month, day)
        except ValueError:
            return None
        return year, month, day
    return None


def _text_time(text: str) -> tuple[int, int] | None:
    upper = text.upper()
    if re.search(r"\bNOON\b", upper):
        return 12, 0
    if re.search(r"\bMIDNIGHT\b", upper):
        return 0, 0
    match = re.search(r"\b(\d{1,2}):(\d{2})\s*(AM|PM)?\b", upper)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        meridiem = match.group(3)
        if minute > 59 or hour > (12 if meridiem else 23) or hour == 0 and meridiem:
            return None
        if meridiem:
            hour = hour % 12 + (12 if meridiem == "PM" else 0)
        return hour, minute
    match = re.search(r"\b(\d{1,2})\s*(AM|PM)\b", upper)
    if match:
        hour = int(match.group(1))
        if not 1 <= hour <= 12:
            return None
        return hour % 12 + (12 if match.group(2) == "PM" else 0), 0
    return None


def _parse_expiry(text: str, close_time: int | None) -> tuple[int | None, str, bool]:
    zone_name = _explicit_timezone(text)
    if close_time is None:
        return None, zone_name, True
    try:
        close_ts = int(close_time)
        close_dt_utc = datetime.fromtimestamp(close_ts, tz=timezone.utc)
        close_local = close_dt_utc.astimezone(ZoneInfo(zone_name))
    except (TypeError, ValueError, OSError):
        return None, zone_name, True

    date_parts = _text_date(text)
    time_parts = _text_time(text)
    if date_parts:
        year, month, day = date_parts
        if (close_local.year, close_local.month, close_local.day) != (year, month, day):
            named = datetime(year, month, day, tzinfo=ZoneInfo(zone_name))
            if abs((close_local.date() - named.date()).days) > 0:
                return close_ts, zone_name, True
        if time_parts:
            hour, minute = time_parts
            try:
                text_dt = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(zone_name))
            except ValueError:
                return close_ts, zone_name, True
            if abs(text_dt.timestamp() - close_ts) > 6 * 3600:
                return close_ts, zone_name, True
            return int(text_dt.timestamp()), zone_name, False
    else:
        years = [int(y) for y in re.findall(r"\b(20\d{2})\b", text)]
        if years and close_local.year not in years:
            return close_ts, zone_name, True
    return close_ts, zone_name, False


def _resolution_source(market: Market, text: str) -> str:
    if market.resolution_source.strip():
        return market.resolution_source.strip()
    patterns = (
        r"resolution\s+source(?:\s+for\s+this\s+market)?\s+(?:is|will\s+be)\s+([^.;\n]{3,180})",
        r"(?:resolution|resolved|settled)\s+(?:using|according\s+to|by|from)\s+([^.;\n]{3,180})",
        r"source\s*[:\-]\s*([^.;\n]{3,180})",
        r"according\s+to\s+([^.;\n]{3,120})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
    return ""


def _event_family(asset: str, expiry: int, question: str) -> str:
    normalized = question.upper()
    normalized = re.sub(MONEY, "#", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    bucket = int(expiry // 900) * 900
    return f"{asset}:{bucket}:{normalized[:120]}"


def _single_money(text: str) -> float | None:
    match = re.fullmatch(rf"\s*{MONEY}\s*", text, re.I)
    return _number(match.group(1), match.group(2) or "") if match else None


def _bare_range(text: str) -> tuple[float, float] | None:
    match = re.fullmatch(rf"\s*{MONEY}\s*(?:-|–|—|TO)\s*{MONEY}\s*", text, re.I)
    if not match:
        return None
    low = _number(match.group(1), match.group(2) or "")
    high = _number(match.group(3), match.group(4) or "")
    return (low, high) if low <= high else (high, low)


def _reference_price(text: str) -> float | None:
    patterns = (
        rf"\bprice\s+to\s+beat\s*[:=]?\s*{MONEY}",
        rf"\b(?:reference|starting|start|opening)\s+price\s*[:=]?\s*{MONEY}",
        rf"\bbeat\s+{MONEY}\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _number(match.group(1), match.group(2) or "")
    return None


def _touch_window_start(text: str, expiry: int, zone_name: str) -> int | None:
    """Infer the beginning of a daily/range barrier window from its title."""
    upper = text.upper()
    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    matches = list(re.finditer(rf"\b({month_names})\s+(\d{{1,2}})(?:ST|ND|RD|TH)?", upper))
    try:
        end_local = datetime.fromtimestamp(expiry, tz=ZoneInfo(zone_name))
    except Exception:
        return None
    if matches:
        month = MONTHS[matches[0].group(1)]
        day = int(matches[0].group(2))
        year = end_local.year
        # A December-to-January range belongs to the previous year at the start.
        if month > end_local.month + 6:
            year -= 1
        try:
            return int(datetime(year, month, day, 0, 0, tzinfo=ZoneInfo(zone_name)).timestamp())
        except ValueError:
            return None
    # Timed touch contracts without a written date are treated as a 24h window.
    if re.search(r"\b(?:TODAY|DURING THE DAY|DAILY)\b", upper):
        return int((end_local.replace(hour=0, minute=0, second=0, microsecond=0)).timestamp())
    return None


def parse_crypto_market(market: Market) -> CryptoMarketSpec:
    text = market_text(market)
    headline = _headline(market)
    asset = _detect_market_asset(market)
    if not asset:
        return CryptoMarketSpec("", "", None, None, 0, "UTC", "", ambiguous=True,
                                reject_reason="actif crypto absent ou multiple")

    expiry, zone_name, expiry_ambiguous = _parse_expiry(text, market.close_time)
    if not expiry:
        return CryptoMarketSpec(asset, "", None, None, 0, zone_name, "", ambiguous=True,
                                reject_reason="échéance Jupiter absente ou invalide")
    if expiry_ambiguous:
        return CryptoMarketSpec(asset, "", None, None, expiry, zone_name, "", ambiguous=True,
                                reject_reason="date textuelle contradictoire avec closeTime Jupiter")

    question = str(market.question or "").strip()
    event = str(market.event_title or "").strip()
    rules = str(market.rules or "")

    # Jupiter /events/crypto/timed publishes two independent YES-buyable
    # instruments per event: one marketId for Up and one for Down.  There is no
    # numeric price-to-beat in the payload because the proposition is the return
    # direction over the timed interval itself.  Preserve the true marketId and
    # let the dedicated probability model estimate that direction directly.
    if bool(getattr(market, "one_sided_yes", False)):
        direction = str(
            getattr(market, "timed_direction", "")
            or getattr(market, "yes_label", "")
            or question
        ).strip().casefold()
        begin_at = getattr(market, "event_begin_at", None)
        try:
            begin_at = int(begin_at) if begin_at not in (None, "") else None
        except (TypeError, ValueError):
            begin_at = None
        if direction not in {"up", "down"}:
            return CryptoMarketSpec(asset, "", None, None, expiry, zone_name, "", ambiguous=True,
                                    reject_reason="direction timed Up/Down absente")
        if begin_at is None or begin_at >= expiry:
            return CryptoMarketSpec(asset, "", None, None, expiry, zone_name, "", ambiguous=True,
                                    reject_reason="début de fenêtre timed absent ou invalide")
        source = _resolution_source(market, text)
        comparator = "above" if direction == "up" else "below"
        return CryptoMarketSpec(
            asset=asset, comparator=comparator, threshold_low=None, threshold_high=None,
            expiry_ts=expiry, timezone_name=zone_name, settlement_kind="timed_direction",
            resolution_source=source,
            event_family=_event_family(asset, expiry, event or f"{asset} timed"),
            window_start_ts=begin_at,
        )

    candidates: list[tuple[str, float | None, float | None, str]] = []

    # Jupiter multi-option bucket: event title describes a price fixing and the
    # market title itself is only the numerical range.
    range_value = _bare_range(question)
    if range_value and re.search(r"\b(?:price|close|closing)\b", event + " " + rules, re.I):
        candidates.append(("between", range_value[0], range_value[1], "close_range"))

    # Arrow markets are barrier-touch contracts.  The arrow and market title
    # carry the threshold; rules determine whether equality counts.
    arrow_match = re.fullmatch(rf"\s*([↑↓])\s*{MONEY}\s*", question, re.I)
    if arrow_match:
        value = _number(arrow_match.group(2), arrow_match.group(3) or "")
        if arrow_match.group(1) == "↑":
            candidates.append(("above_or_equal", value, value, "touch_high"))
        else:
            candidates.append(("below_or_equal", value, value, "touch_low"))

    # Direct live Degen contracts. YES/NO labels are read from Jupiter instead
    # of assuming that YES always means Up.
    if re.search(r"\bup\s+or\s+down\b", text, re.I):
        reference = _reference_price(text)
        yes_label = str(getattr(market, "yes_label", "YES") or "YES").strip().casefold()
        if reference is not None:
            if yes_label == "up":
                candidates.append(("above", reference, reference, "degen_direction"))
            elif yes_label == "down":
                candidates.append(("below", reference, reference, "degen_direction"))

    # Explicit comparator and placeholder forms are evaluated on the headline,
    # never on unrelated timestamps/numbers in the long rules body.
    comparator_patterns = (
        ("above_or_equal", rf"\babove_or_equal\s+{MONEY}", "close_or_price"),
        ("below_or_equal", rf"\bbelow_or_equal\s+{MONEY}", "close_or_price"),
        ("above_or_equal", rf"{MONEY}\s+(?:or\s+higher|or\s+above|or\s+more)\b", "close_or_price"),
        ("below_or_equal", rf"{MONEY}\s+(?:or\s+lower|or\s+below|or\s+less)\b", "close_or_price"),
        ("above", rf"\b(?:above|over|greater\s+than|more\s+than)\s+{MONEY}", "close_or_price"),
        ("below", rf"\b(?:below|under|less\s+than)\s+{MONEY}", "close_or_price"),
        ("exact", rf"\b(?:exactly|equal\s+to|at\s+exactly)\s+{MONEY}", "exact"),
    )
    for comparator, pattern, settlement in comparator_patterns:
        match = re.search(pattern, headline, re.I)
        if match:
            value = _number(match.group(1), match.group(2) or "")
            if re.search(r"\bfinal\s+[\"']?close|\bcloses?\b", rules + " " + event, re.I):
                settlement = "close_above" if comparator.startswith("above") else (
                    "close_below" if comparator.startswith("below") else settlement
                )
            candidates.append((comparator, value, value, settlement))

    # Fallback for a numerical market option under an event-level comparator.
    value = _single_money(question)
    if value is not None and not range_value and not arrow_match:
        if re.search(r"\babove\b", event, re.I):
            candidates.append(("above", value, value, "close_above"))
        elif re.search(r"\bbelow\b", event, re.I):
            candidates.append(("below", value, value, "close_below"))

    # Traditional prose range, e.g. "between $X and $Y".
    for pattern in (
        rf"\bbetween\s+{MONEY}\s+(?:and|to|-)\s+{MONEY}",
        rf"\bfrom\s+{MONEY}\s+(?:to|through|-)\s+{MONEY}",
    ):
        match = re.search(pattern, headline, re.I)
        if match:
            low = _number(match.group(1), match.group(2) or "")
            high = _number(match.group(3), match.group(4) or "")
            if high < low:
                low, high = high, low
            candidates.append(("between", low, high, "close_range"))
            break

    unique = {(c[0], c[1], c[2], c[3]) for c in candidates}
    # The same threshold can be found both by the filled placeholder and the
    # numerical fallback. Deduplicate semantically before deciding ambiguity.
    semantic = {(c[0], c[1], c[2]) for c in candidates}
    if len(semantic) != 1:
        reason = "comparateur absent" if not semantic else "plusieurs comparateurs incompatibles"
        if re.search(r"\bup\s+or\s+down\b", text, re.I) and _reference_price(text) is None:
            reason = "prix de référence Degen absent"
        return CryptoMarketSpec(asset, "", None, None, expiry, zone_name, "", ambiguous=True,
                                reject_reason=reason)

    comparator, low, high = next(iter(semantic))
    settlement_candidates = [c[3] for c in candidates if c[:3] == (comparator, low, high)]
    settlement = next((x for x in settlement_candidates if x != "close_or_price"), settlement_candidates[0])
    source = _resolution_source(market, text)
    spec = CryptoMarketSpec(
        asset=asset,
        comparator=comparator,
        threshold_low=low,
        threshold_high=high,
        expiry_ts=expiry,
        timezone_name=zone_name,
        settlement_kind=settlement,
        resolution_source=source,
        event_family=_event_family(asset, expiry, f"{market.event_title} {market.question}"),
        window_start_ts=_touch_window_start(f"{market.event_title} {market.question} {market.rules}", expiry, zone_name)
        if settlement in {"touch_high", "touch_low"} else None,
    )
    if low is None or low <= 0 or (high is not None and high <= 0):
        spec.ambiguous = True
        spec.reject_reason = "seuil monétaire invalide"
    return spec
