from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import requests

from .market_parser import looks_like_crypto_market
from .models import Market

log = logging.getLogger(__name__)


def _price(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number / 1_000_000 if number > 1.5 else number


def _money(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number / 1_000_000 if abs(number) >= 100_000 else number


def _unix(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return int(number)
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        return None


def search_query(market: Market) -> str:
    raw = f"{market.event_title} {market.question}".replace("___", " ")
    raw = raw.replace("↓", " below ").replace("↑", " above ")
    raw = re.sub(r"(?<=\d),(?=\d)", "", raw)
    raw = re.sub(r"[^A-Za-zÀ-ÿ0-9$%.'+-]+", " ", raw)
    tokens = re.findall(r"\$?\d+(?:\.\d+)?%?|[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9.'+-]{1,}", raw)
    stop = {"the", "and", "will", "with", "from", "this", "that", "market", "yes", "no"}
    out, seen = [], set()
    for token in tokens:
        key = token.casefold()
        if key in stop or key in seen:
            continue
        seen.add(key)
        out.append(token)
    return " ".join(out[:14]) or market.event_title[:140]


class JupiterClient:
    def __init__(self, settings):
        self.s = settings
        self.http = requests.Session()
        self.http.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "JupiterDegenEdgeBotPC/1.0.0",
        })
        self._last_request = 0.0
        self._timed_cache: tuple[float, list[dict]] = (0.0, [])
        # Instance-local bounded GET mode for the independent short-TIMED worker.
        # POST/DELETE order mutations keep the original timeout/retry behavior.
        self._fast_read_timeout_seconds: float | None = None
        self._fast_read_max_retries: int | None = None

    def enable_fast_read_mode(self, timeout_seconds: float = 6.0, max_retries: int = 0) -> None:
        """Bound Jupiter GET latency for the dedicated 5m/15m worker only."""
        self._fast_read_timeout_seconds = max(3.0, min(12.0, float(timeout_seconds)))
        self._fast_read_max_retries = max(0, min(1, int(max_retries)))

    @property
    def headers(self) -> dict[str, str]:
        if not self.s.jupiter_api_key:
            raise RuntimeError("JUPITER_API_KEY absente")
        return {"x-api-key": self.s.jupiter_api_key, "Content-Type": "application/json"}

    def _throttle(self) -> None:
        wait = self.s.jupiter_request_interval_seconds - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    def request(self, method: str, path: str, base_url: str | None = None, **kwargs):
        last_error = None
        is_fast_get = method.upper() == "GET" and self._fast_read_timeout_seconds is not None
        max_retries = int(self.s.jupiter_max_retries)
        timeout = 40.0
        if is_fast_get:
            max_retries = min(max_retries, int(self._fast_read_max_retries or 0))
            timeout = float(self._fast_read_timeout_seconds)
        for attempt in range(max_retries + 1):
            self._throttle()
            try:
                response = self.http.request(
                    method,
                    (base_url or self.s.jupiter_base_url).rstrip("/") + path,
                    headers=self.headers,
                    timeout=timeout,
                    **kwargs,
                )
                self._last_request = time.monotonic()
            except requests.RequestException as exc:
                last_error = RuntimeError(f"Jupiter réseau {method} {path}: {exc}")
                if method.upper() != "GET" or attempt >= max_retries:
                    raise last_error from exc
                time.sleep(min(20, 2 ** (attempt + 1)))
                continue
            if response.status_code == 429:
                retry = response.headers.get("Retry-After")
                try:
                    delay = float(retry)
                except (TypeError, ValueError):
                    delay = min(20, 2 ** (attempt + 1))
                last_error = RuntimeError(f"Jupiter HTTP 429: {response.text[:300]}")
                if attempt >= max_retries:
                    break
                log.warning("Jupiter 429, attente %.1fs", delay)
                time.sleep(delay)
                continue
            if response.status_code in {500, 502, 503, 504} and method.upper() == "GET":
                last_error = RuntimeError(f"Jupiter HTTP {response.status_code}: {response.text[:300]}")
                if attempt >= max_retries:
                    break
                time.sleep(min(20, 2 ** (attempt + 1)))
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"Jupiter HTTP {response.status_code}: {response.text[:900]}")
            if not response.content:
                return {}
            return response.json()
        raise last_error or RuntimeError("Jupiter indisponible")

    def get(self, path: str, params: dict | None = None, base_url: str | None = None):
        return self.request("GET", path, params=params, base_url=base_url)

    def post(self, path: str, data: dict):
        return self.request("POST", path, json=data)

    def delete(self, path: str, data: dict):
        return self.request("DELETE", path, json=data)

    def status(self):
        return self.get("/trading-status")

    @staticmethod
    def _payload_events(payload) -> list[dict]:
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if not isinstance(payload, dict):
            return []
        data = payload.get("data") or payload.get("events") or []
        return [row for row in data if isinstance(row, dict)]

    def closed_events(self, since: int | None = None) -> list[dict]:
        """Fetch recently closed events with bounded pagination and de-duplication."""
        rows: dict[str, dict] = {}
        page_size = 100
        max_pages = max(1, min(20, int(getattr(self.s, "max_event_pages", 12))))
        for page in range(max_pages):
            start = page * page_size
            params = {"provider": self.s.jupiter_provider, "start": start, "end": start + page_size}
            if since is not None:
                params["since"] = max(1, int(since))
            payload = self.get("/events/closed", params=params, base_url=self.s.jupiter_degen_base_url)
            data = self._payload_events(payload)
            for event in data:
                event_id = str(event.get("eventId") or (event.get("metadata") or {}).get("eventId") or "")
                if event_id:
                    rows[event_id] = event
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            if not data or (isinstance(pagination, dict) and pagination.get("hasNext") is False):
                break
        return list(rows.values())

    def _timed_events(self) -> list[dict]:
        """Fetch 5m/15m crypto events with a conservative in-memory cache.

        Jupiter documents one endpoint per subcategory and interval. Fourteen GETs
        every cycle would be wasteful, so the cache is shared across cycles and all
        calls still pass through the existing 1.2 s throttle/429 backoff.
        """
        if not bool(getattr(self.s, "include_timed_crypto_events", True)):
            return []
        now_mono = time.monotonic()
        cached_at, cached_rows = self._timed_cache
        assets = list(getattr(self.s, "timed_crypto_assets", self.s.crypto_assets))
        tags = list(getattr(self.s, "timed_crypto_tags", ["5m", "15m"]))
        ttl = max(60.0, float(getattr(self.s, "jupiter_timed_cache_seconds", 240)))
        # A 10-minute cache (the old .env value is commonly 600 s) can hide an
        # entire 5-minute contract from a 5-minute scanner. V2 therefore caps the
        # timed-event cache below one scan window whenever 5m markets are enabled.
        if "5m" in tags and bool(getattr(self.s, "timed_direction_model_enabled", True)):
            ttl = min(ttl, 240.0)
        if cached_rows and now_mono - cached_at < ttl:
            return list(cached_rows)
        rows: dict[str, dict] = {}
        for asset in assets:
            for tag in tags:
                try:
                    payload = self.get(
                        "/events/crypto/timed",
                        params={"provider": self.s.jupiter_provider, "subcategory": asset.casefold(), "tags": tag},
                        base_url=self.s.jupiter_degen_base_url,
                    )
                    for event in self._payload_events(payload):
                        event_id = str(event.get("eventId") or (event.get("metadata") or {}).get("eventId") or "")
                        if event_id:
                            rows[event_id] = event
                except Exception as exc:
                    log.warning("Jupiter timed %s/%s indisponible: %s", asset, tag, exc)
        values = list(rows.values())
        self._timed_cache = (now_mono, values)
        return values

    def events(self) -> list[dict]:
        output: dict[str, dict] = {}

        # Dedicated Jupiter Degen endpoint: current 5m/15m crypto events. Try
        # the configured trading API first, then the current reference host.
        if self.s.include_live_degen_events:
            bases: list[str | None] = [None]
            alternate = str(self.s.jupiter_degen_base_url or "").strip()
            if alternate and alternate.rstrip("/") != self.s.jupiter_base_url.rstrip("/"):
                bases.append(alternate)
            for base in bases:
                try:
                    payload = self.get("/events/degen", base_url=base)
                    rows = self._payload_events(payload)
                    for event in rows:
                        event_id = str(event.get("eventId") or (event.get("metadata") or {}).get("eventId") or "")
                        if event_id:
                            output[event_id] = event
                    if rows:
                        break
                except Exception as exc:
                    log.warning("Endpoint Degen Jupiter indisponible sur %s: %s", base or self.s.jupiter_base_url, exc)

        # Explicit timed history/future feed. /events/degen only returns the
        # current live set; /events/crypto/timed also exposes the recent 5 hours
        # and future intervals, which is essential for fast shadow settlement.
        for event in self._timed_events():
            event_id = str(event.get("eventId") or (event.get("metadata") or {}).get("eventId") or "")
            if event_id and event_id not in output:
                output[event_id] = event

        filters = self.s.event_filters or ["trending"]
        categories = self.s.categories or [None]
        for category in categories:
            for event_filter in filters:
                for page in range(self.s.max_event_pages):
                    start = page * self.s.max_events_per_page
                    end = start + self.s.max_events_per_page
                    params = {
                        "filter": event_filter,
                        "includeMarkets": "true",
                        "includeAllMarkets": "true",
                        "provider": self.s.jupiter_provider,
                        "start": start,
                        "end": end,
                    }
                    if category:
                        params["category"] = category
                    try:
                        payload = self.get("/events", params)
                    except Exception as exc:
                        log.warning("Événements %s/%s page %d: %s", category or "all", event_filter, page + 1, exc)
                        break
                    data = self._payload_events(payload)
                    for event in data:
                        event_id = str(event.get("eventId") or (event.get("metadata") or {}).get("eventId") or "")
                        if event_id and event_id not in output:
                            output[event_id] = event
                    if not data or (payload.get("pagination") or {}).get("hasNext") is False:
                        break
        return list(output.values())

    def _market_from_raw(
        self, raw: dict, *, event_id: str = "", event_title: str = "",
        category: str = "", subcategory: str = "", event_context: str = "",
        event_begin_at: int | None = None,
        is_live: bool = False, now_ts: float | None = None,
    ) -> Market | None:
        if not isinstance(raw, dict):
            return None
        now_ts = time.time() if now_ts is None else now_ts
        meta = raw.get("metadata") or {}
        pricing = raw.get("pricing") or {}
        market_id = str(raw.get("marketId") or meta.get("marketId") or raw.get("id") or "")
        if market_id and not market_id.startswith("POLY-") and str(raw.get("provider") or "").casefold() == "polymarket":
            market_id = f"POLY-{market_id}"
        title = str(raw.get("title") or meta.get("title") or raw.get("question") or meta.get("question") or event_title)
        event_id = str(raw.get("eventId") or meta.get("eventId") or event_id or "")
        if event_id and not event_id.startswith("POLY-") and market_id.startswith("POLY-"):
            event_id = f"POLY-{event_id}"
        event_title = str(raw.get("eventTitle") or meta.get("eventTitle") or event_title or title)
        category = str(raw.get("category") or meta.get("category") or category or "")
        status = str(raw.get("status") or meta.get("status") or "open").casefold()
        yes = _price(pricing.get("buyYesPriceUsd") if pricing else raw.get("yesPrice"))
        no = _price(pricing.get("buyNoPriceUsd") if pricing else raw.get("noPrice"))

        # /events/crypto/timed exposes Up and Down as two separate YES-buyable
        # marketIds.  buyNoPriceUsd is therefore legitimately zero.  Preserve
        # that shape instead of fabricating a complementary NO quote.
        market_options = raw.get("marketOptions") or meta.get("marketOptions") or []
        option_label = ""
        if isinstance(market_options, list):
            for option in market_options:
                if not isinstance(option, dict) or option.get("buyYes") is not True:
                    continue
                label = str(option.get("label") or "").strip()
                if label.casefold() in {"up", "down"}:
                    option_label = label
                    break
        if not option_label and title.strip().casefold() in {"up", "down"}:
            option_label = title.strip()
        timed_direction = option_label.upper() if (
            option_label.casefold() in {"up", "down"}
            and re.search(r"\bup\s+or\s+down\b", event_title, re.I)
        ) else ""
        one_sided_yes = bool(timed_direction)

        if not market_id or status != "open" or raw.get("tradable", True) is False:
            return None
        if not (self.s.min_trade_price <= yes <= self.s.max_trade_price):
            return None
        if not one_sided_yes and not (self.s.min_trade_price <= no <= self.s.max_trade_price):
            return None

        close_time = _unix(
            raw.get("closeTime") or meta.get("closeTime") or raw.get("endDate") or raw.get("end_date_iso")
        )
        if close_time is None:
            return None
        begin_at = _unix(event_begin_at)
        hours = (close_time - now_ts) / 3600
        mode = str(getattr(self.s, "trading_mode", "paper")).casefold()
        paper_learning = mode == "paper" or (
            mode == "live" and bool(getattr(self.s, "paper_parallel_live_enabled", False))
        )
        if one_sided_yes:
            # TIMED 5m/15m contracts cannot use the generic LIVE minimum horizon
            # (commonly 0.50 h). Their own calibration/risk gate later decides
            # whether a real order is allowed. Discovery must remain possible in
            # PAPER, LIVE and LIVE+parallel modes so V2 can learn start-anchored
            # labels consistently.
            minimum_hours = float(getattr(
                self.s, "timed_direction_discovery_min_hours_to_close",
                getattr(self.s, "paper_min_hours_to_close", 0.02),
            ))
        else:
            minimum_hours = (
                float(getattr(self.s, "paper_min_hours_to_close", self.s.min_hours_to_close))
                if mode == "paper" else float(self.s.min_hours_to_close)
            )
        if hours < minimum_hours or hours > self.s.max_hours_to_close:
            return None

        rules = " ".join(filter(None, [
            str(raw.get("rulesPrimary") or meta.get("rulesPrimary") or ""),
            str(raw.get("rulesSecondary") or meta.get("rulesSecondary") or ""),
            str(raw.get("rules") or meta.get("rules") or ""),
            str(raw.get("description") or meta.get("description") or ""),
            str(raw.get("resolutionCriteria") or meta.get("resolutionCriteria") or ""),
            str(raw.get("resolutionSource") or meta.get("resolutionSource") or ""),
            str(raw.get("source") or meta.get("source") or ""),
            str(event_context or ""),
        ]))[:8000]
        outcomes = raw.get("outcomes") or meta.get("outcomes") or []
        if one_sided_yes:
            yes_label = timed_direction.title()
            no_label = "Down" if timed_direction == "UP" else "Up"
        else:
            yes_label = str(outcomes[0]) if isinstance(outcomes, list) and len(outcomes) >= 1 else "YES"
            no_label = str(outcomes[1]) if isinstance(outcomes, list) and len(outcomes) >= 2 else "NO"
        market = Market(
            id=market_id, event_id=event_id, event_title=event_title, question=title,
            yes_price=yes, no_price=no,
            sell_yes_price=_price(pricing.get("sellYesPriceUsd") if pricing else raw.get("sellYesPrice")),
            sell_no_price=_price(pricing.get("sellNoPriceUsd") if pricing else raw.get("sellNoPrice")),
            volume_usd=_money(pricing.get("volume") if pricing else raw.get("volume")),
            liquidity_usd=_money(pricing.get("liquidity") if pricing else raw.get("liquidity")),
            rules=rules, close_time=close_time, category=category,
            subcategory=str(raw.get("subcategory") or meta.get("subcategory") or subcategory or ""),
            is_live=bool(raw.get("isLive") or meta.get("isLive") or is_live),
            resolution_source=str(raw.get("resolutionSource") or meta.get("resolutionSource") or raw.get("source") or meta.get("source") or ""),
            yes_label=yes_label, no_label=no_label,
            one_sided_yes=one_sided_yes,
            timed_direction=timed_direction,
            event_begin_at=begin_at,
        )
        market.search_query = search_query(market)
        return market if looks_like_crypto_market(market) else None

    def live_degen_markets(self) -> list[Market]:
        """Return only Jupiter's current live Up/Down Degen contracts.

        Unlike ``markets()``, this deliberately does not walk generic event
        pages or the timed-history feed. It is the low-latency discovery path
        used by the independent 5m/15m worker and normally costs one GET.
        """
        now_ts = time.time()
        rows: list[dict] = []
        bases: list[str | None] = [None]
        alternate = str(self.s.jupiter_degen_base_url or "").strip()
        if alternate and alternate.rstrip("/") != self.s.jupiter_base_url.rstrip("/"):
            bases.append(alternate)
        for base in bases:
            try:
                payload = self.get("/events/degen", base_url=base)
                rows = self._payload_events(payload)
                if rows:
                    break
            except Exception as exc:
                log.warning("Endpoint Degen FAST indisponible sur %s: %s", base or self.s.jupiter_base_url, exc)

        output: dict[str, Market] = {}
        for event in rows:
            event_meta = event.get("metadata") or {}
            event_id = str(event.get("eventId") or "")
            event_title = str(event_meta.get("title") or event.get("title") or event_id)
            category = str(event.get("category") or event_meta.get("category") or "")
            subcategory = str(event.get("subcategory") or event_meta.get("subcategory") or "")
            is_live = bool(event.get("isLive") or event_meta.get("isLive"))
            event_begin_at = _unix(event.get("beginAt") or event_meta.get("beginAt") or event.get("openTime"))
            tags = event.get("tags") or event_meta.get("tags") or []
            live_score = event.get("liveScore") or {}
            event_context = " ".join(filter(None, [
                str(event_meta.get("subtitle") or event.get("subtitle") or ""),
                str(event.get("closeCondition") or event_meta.get("closeCondition") or ""),
                "tags=" + ",".join(str(x) for x in tags) if isinstance(tags, list) and tags else "",
                str(live_score.get("score") or "") if isinstance(live_score, dict) else "",
            ]))
            for raw in event.get("markets") or []:
                market = self._market_from_raw(
                    raw, event_id=event_id, event_title=event_title,
                    category=category, subcategory=subcategory,
                    event_context=event_context, event_begin_at=event_begin_at,
                    is_live=is_live, now_ts=now_ts,
                )
                if market is None or not bool(getattr(market, "one_sided_yes", False)):
                    continue
                if str(getattr(market, "timed_direction", "") or "").upper() not in {"UP", "DOWN"}:
                    continue
                output[market.id] = market
                if len(output) >= self.s.max_markets_fetched:
                    break
            if len(output) >= self.s.max_markets_fetched:
                break

        markets = list(output.values())
        markets.sort(key=lambda market: (market.close_time or 10**12, market.id))
        return markets

    def markets(self, extra_market_ids: list[str] | None = None) -> list[Market]:
        """Return configured crypto price/Degen markets that Jupiter confirms as tradable.

        Optional identifiers are always re-read from Jupiter before analysis.
        """
        now_ts = time.time()
        output: dict[str, Market] = {}
        for event in self.events():
            event_meta = event.get("metadata") or {}
            event_id = str(event.get("eventId") or "")
            event_title = str(event_meta.get("title") or event.get("title") or event_id)
            category = str(event.get("category") or event_meta.get("category") or "")
            subcategory = str(event.get("subcategory") or event_meta.get("subcategory") or "")
            is_live = bool(event.get("isLive") or event_meta.get("isLive"))
            event_begin_at = _unix(event.get("beginAt") or event_meta.get("beginAt") or event.get("openTime"))
            tags = event.get("tags") or event_meta.get("tags") or []
            live_score = event.get("liveScore") or {}
            event_context = " ".join(filter(None, [
                str(event_meta.get("subtitle") or event.get("subtitle") or ""),
                str(event.get("closeCondition") or event_meta.get("closeCondition") or ""),
                "tags=" + ",".join(str(x) for x in tags) if isinstance(tags, list) and tags else "",
                str(live_score.get("score") or "") if isinstance(live_score, dict) else "",
            ]))
            for raw in event.get("markets") or []:
                market = self._market_from_raw(
                    raw, event_id=event_id, event_title=event_title,
                    category=category, subcategory=subcategory, event_context=event_context,
                    event_begin_at=event_begin_at, is_live=is_live, now_ts=now_ts,
                )
                if market is not None:
                    output[market.id] = market
                    if len(output) >= self.s.max_markets_fetched:
                        break
            if len(output) >= self.s.max_markets_fetched:
                break

        missing = [mid for mid in (extra_market_ids or []) if mid not in output]
        for market_id in missing[: max(0, int(self.s.max_markets_fetched))]:
            try:
                raw = self.market(market_id)
                market = self._market_from_raw(raw or {}, now_ts=now_ts)
                if market is not None:
                    output[market.id] = market
            except Exception as exc:
                log.debug("Réconciliation Jupiter impossible pour %s: %s", market_id, exc)

        markets = list(output.values())
        markets.sort(key=lambda m: (m.close_time or 10**12, -m.volume_usd))
        return markets

    def market(self, market_id: str) -> dict | None:
        try:
            data = self.get(f"/markets/{market_id}")
            if isinstance(data, dict):
                return data.get("market") or data.get("data") or data
        except Exception:
            return None
        return None

    def current_buy_price(self, market_id: str, is_yes: bool) -> float:
        raw = self.market(market_id)
        if not isinstance(raw, dict):
            raise RuntimeError("marché Jupiter introuvable pour contrôle de dérive")
        pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
        key = "buyYesPriceUsd" if is_yes else "buyNoPriceUsd"
        value = pricing.get(key)
        if value in (None, ""):
            raise RuntimeError(f"{key} absent du marché Jupiter")
        result = _price(value)
        if not (0 < result < 1):
            raise RuntimeError(f"prix Jupiter invalide: {result}")
        return result

    def trade_quote(self, market_id: str, is_yes: bool) -> dict:
        """Return a side-specific executable buy/sell quote from Jupiter."""
        raw = self.market(market_id)
        if not isinstance(raw, dict):
            raise RuntimeError("marché Jupiter introuvable pour contrôle de liquidité")
        pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
        buy_key = "buyYesPriceUsd" if is_yes else "buyNoPriceUsd"
        sell_key = "sellYesPriceUsd" if is_yes else "sellNoPriceUsd"
        buy = _price(pricing.get(buy_key))
        sell_value = pricing.get(sell_key)
        sell = _price(sell_value) if sell_value not in (None, "") else 0.0
        volume = _money(pricing.get("volume"))
        liquidity = _money(pricing.get("liquidity"))
        if not (0 < buy < 1):
            raise RuntimeError(f"{buy_key} invalide ou absent")
        spread = buy - sell if sell > 0 else None
        return {
            "buy": buy,
            "sell": sell,
            "spread": spread,
            "volume_usd": volume,
            "liquidity_usd": liquidity,
            "market": raw,
        }

    def orderbook(self, market_id: str) -> dict:
        payload = self.get(f"/orderbook/{market_id}")
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise RuntimeError("format orderbook Jupiter inattendu")
        return payload.get("data") if isinstance(payload.get("data"), dict) else payload

    def create_order(self, owner: str, market_id: str, is_yes: bool, usd: float, deposit_mint: str):
        return self.post(
            "/orders",
            {
                "ownerPubkey": owner,
                "marketId": market_id,
                "isYes": bool(is_yes),
                "isBuy": True,
                "depositAmount": str(int(round(usd * 1_000_000))),
                "depositMint": deposit_mint,
            },
        )

    def create_sell_order(
        self, owner: str, position_pubkey: str, market_id: str,
        is_yes: bool, contracts_decimal: float,
    ):
        quantity = float(contracts_decimal)
        if quantity <= 0:
            raise RuntimeError("quantité de contrats à vendre invalide")
        # Current Prediction API requires POST /orders with isBuy=false and the
        # exact positionPubkey. contractsDecimal preserves fractional contracts.
        return self.post(
            "/orders",
            {
                "ownerPubkey": owner,
                "marketId": market_id,
                "positionPubkey": position_pubkey,
                "isYes": bool(is_yes),
                "isBuy": False,
                "contractsDecimal": format(quantity, ".6f").rstrip("0").rstrip("."),
            },
        )

    def order_status(self, order_pubkey: str):
        # Current API reference uses /orders/{pubkey}. Keep the legacy
        # /orders/status/{pubkey} fallback because the beta API has changed
        # paths in the past.
        try:
            return self.get(f"/orders/{order_pubkey}")
        except Exception as first:
            text = str(first)
            if "404" not in text and "not found" not in text.casefold():
                raise
            return self.get(f"/orders/status/{order_pubkey}")

    def orders(self, owner: str, start: int | None = None):
        params = {"ownerPubkey": owner}
        if start is not None:
            params["start"] = int(start)
        return self.get("/orders", params)

    def history(
        self,
        owner: str,
        start: int | None = None,
        position_pubkey: str | None = None,
    ):
        # FINAL_POSITION_RECONCILE_V1:
        # Jupiter documents positionPubkey as a history filter. Keeping it
        # optional preserves every existing caller.
        params = {"ownerPubkey": owner}
        if position_pubkey:
            params["positionPubkey"] = str(position_pubkey)
        if start is not None:
            params["start"] = int(start)
        return self.get("/history", params)

    def positions(self, owner: str, start: int | None = None):
        params = {"ownerPubkey": owner}
        if start is not None:
            params["start"] = int(start)
        return self.get("/positions", params)

    def claim(self, owner: str, position_pubkey: str):
        return self.post(f"/positions/{position_pubkey}/claim", {"ownerPubkey": owner})

    def close_position(self, owner: str, position_pubkey: str):
        raise RuntimeError(
            "fermeture héritée refusée: utilise create_sell_order avec marketId, côté et quantité exacts"
        )
