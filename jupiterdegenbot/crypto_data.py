from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import SourceObservation

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str
    asset: str
    timeframe: str

    def dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts, "open": self.open, "high": self.high, "low": self.low,
            "close": self.close, "volume": self.volume, "source": self.source,
            "asset": self.asset, "timeframe": self.timeframe,
        }


@dataclass(slots=True)
class SourceSeries:
    source: str
    asset: str
    spot: float
    bid: float
    ask: float
    volume_24h: float
    candles: dict[str, list[Candle]] = field(default_factory=dict)
    reliability: float = 0.8


@dataclass(slots=True)
class CryptoSnapshot:
    asset: str
    observed_at: str
    sources: list[SourceSeries]
    spot_median: float
    spot_dispersion: float
    source_agreement: float
    observations: list[SourceObservation]


PAIR = {
    # Unsupported combinations are intentionally absent. fetch() will continue
    # with the other independent sources and still requires CRYPTO_MIN_SOURCES.
    "coinbase": {
        "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
        "XRP": "XRP-USD", "DOGE": "DOGE-USD",
    },
    "binance": {
        "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
        "XRP": "XRPUSDT", "DOGE": "DOGEUSDT", "BNB": "BNBUSDT",
    },
    "kraken": {
        "BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD",
        "XRP": "XRPUSD", "DOGE": "XDGUSD",
    },
    "bybit": {
        "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
        "XRP": "XRPUSDT", "HYPE": "HYPEUSDT", "DOGE": "DOGEUSDT",
        "BNB": "BNBUSDT",
    },
    "hyperliquid": {
        "BTC": "BTC", "ETH": "ETH", "SOL": "SOL", "XRP": "XRP",
        "HYPE": "HYPE", "DOGE": "DOGE", "BNB": "BNB",
    },
    "okx": {
        "HYPE": "HYPE-USDT", "DOGE": "DOGE-USDT", "BNB": "BNB-USDT",
    },
    "kucoin": {
        "HYPE": "HYPE-USDT", "DOGE": "DOGE-USDT", "BNB": "BNB-USDT",
    },
}

# This patch is intentionally limited to the three assets that were missing
# on v1.0.0. BTC/ETH/SOL/XRP keep exactly their original source set.
TARGETED_ALT_ASSETS = {"HYPE", "DOGE", "BNB"}


def _pair(source: str, asset: str) -> str:
    try:
        return PAIR[source][asset]
    except KeyError as exc:
        raise RuntimeError(f"{source}: paire indisponible pour {asset}") from exc


def _f(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _aggregate(candles: list[Candle], factor: int, timeframe: str) -> list[Candle]:
    if factor <= 1:
        return candles
    rows = sorted(candles, key=lambda c: c.ts)
    out: list[Candle] = []
    for i in range(0, len(rows), factor):
        group = rows[i:i + factor]
        if len(group) < factor:
            continue
        out.append(Candle(
            ts=group[0].ts, open=group[0].open, high=max(c.high for c in group),
            low=min(c.low for c in group), close=group[-1].close,
            volume=sum(c.volume for c in group), source=group[0].source,
            asset=group[0].asset, timeframe=timeframe,
        ))
    return out


class CryptoDataClient:
    def __init__(self, settings, http, db=None):
        self.s = settings
        self.http = http
        self.db = db
        self._timeframes_override: tuple[str, ...] | None = None

    def enable_timed_fast_mode(self) -> None:
        # TIMED V2 consumes only 5m/15m/1h.  Avoid downloading 4h/1d in the
        # short worker; the normal engine remains unchanged.
        self._timeframes_override = ("5m", "15m", "1h")

    def _timeframes(self) -> tuple[str, ...]:
        values = self._timeframes_override or tuple(self.s.crypto_timeframes)
        return tuple(str(x) for x in values)

    def _get_json_from_bases(self, bases: list[str], path: str, *, params: dict[str, Any] | None = None,
                             cache_seconds: float = 0, require_data: bool = False) -> Any:
        """Try configured public API domains in order.

        A HTTP 200 response with an empty ``data`` field is not considered a
        success when ``require_data`` is true. This matters for KuCoin EU,
        which can answer ``code=200000`` with ``data=null`` for some symbols.
        """
        errors: list[str] = []
        for raw_base in bases:
            base = str(raw_base or "").strip().rstrip("/")
            if not base:
                continue
            try:
                payload = self.http.get_json(
                    f"{base}{path}", params=params,
                    cache_seconds=cache_seconds,
                )
                if require_data:
                    data = payload.get("data") if isinstance(payload, dict) else None
                    if data in (None, [], {}):
                        errors.append(f"{base}: réponse vide")
                        continue
                return payload
            except Exception as exc:
                errors.append(f"{base}: {exc}")
        raise RuntimeError(" | ".join(errors)[:1400] or f"aucune API disponible pour {path}")

    def fetch(self, asset: str, *, persist: bool = True) -> CryptoSnapshot:
        asset = asset.upper()
        if asset not in self.s.crypto_assets:
            raise ValueError(f"actif non autorisé: {asset}")
        series: list[SourceSeries] = []
        errors: list[str] = []
        for source in self.s.crypto_sources:
            if source in {"okx", "kucoin"} and asset not in TARGETED_ALT_ASSETS:
                continue
            try:
                item = getattr(self, f"_fetch_{source}")(asset)
                if item.spot > 0:
                    series.append(item)
            except Exception as exc:
                errors.append(f"{source}: {exc}")
                log.warning("Source crypto %s/%s indisponible: %s", source, asset, exc)
        if len(series) < int(self.s.crypto_min_sources):
            raise RuntimeError(
                f"{asset}: {len(series)} source(s) disponible(s), minimum {self.s.crypto_min_sources}; "
                + " | ".join(errors)[:800]
            )
        spots = [x.spot for x in series if x.spot > 0]
        median = statistics.median(spots)
        dispersion = (max(spots) - min(spots)) / median if median else 1.0
        max_dispersion = max(1e-9, float(self.s.crypto_max_price_dispersion))
        agreement = max(0.0, min(1.0, 1.0 - dispersion / max_dispersion))
        stamp = datetime.now(timezone.utc).isoformat()
        observations: list[SourceObservation] = []
        for item in series:
            observations.append(SourceObservation(
                source=item.source,
                value=item.spot,
                observed_at=stamp,
                kind="spot_price",
                reliability=item.reliability,
                metadata={
                    "asset": asset, "bid": item.bid, "ask": item.ask,
                    "volume_24h": item.volume_24h, "quantitative": True,
                },
            ))
        snapshot = CryptoSnapshot(asset, stamp, series, median, dispersion, agreement, observations)
        if self.db is not None and persist:
            self.db.add_crypto_snapshot(snapshot)
        return snapshot

    def _fetch_coinbase(self, asset: str) -> SourceSeries:
        pair = _pair("coinbase", asset)
        ticker = self.http.get_json(
            f"https://api.exchange.coinbase.com/products/{pair}/ticker",
            cache_seconds=self.s.crypto_source_cache_seconds,
        )
        result = SourceSeries(
            source="coinbase", asset=asset, spot=_f(ticker.get("price")),
            bid=_f(ticker.get("bid")), ask=_f(ticker.get("ask")),
            volume_24h=_f(ticker.get("volume")), reliability=0.88,
        )
        granularity = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}
        for tf in self._timeframes():
            requested = "1h" if tf == "4h" else tf
            if requested not in granularity:
                continue
            rows = self.http.get_json(
                f"https://api.exchange.coinbase.com/products/{pair}/candles",
                params={"granularity": granularity[requested]},
                cache_seconds=self.s.crypto_source_cache_seconds,
            )
            candles = [Candle(
                ts=int(r[0]), low=_f(r[1]), high=_f(r[2]), open=_f(r[3]),
                close=_f(r[4]), volume=_f(r[5] if len(r) > 5 else 0),
                source="coinbase", asset=asset, timeframe=requested,
            ) for r in rows if isinstance(r, list) and len(r) >= 5]
            candles = sorted(candles, key=lambda c: c.ts)[-self.s.crypto_candle_limit:]
            result.candles[tf] = _aggregate(candles, 4, "4h") if tf == "4h" else candles
        return result

    def _fetch_binance(self, asset: str) -> SourceSeries:
        symbol = _pair("binance", asset)
        base = "https://data-api.binance.vision"
        book = self.http.get_json(
            f"{base}/api/v3/ticker/bookTicker", params={"symbol": symbol},
            cache_seconds=self.s.crypto_source_cache_seconds,
        )
        stats = self.http.get_json(
            f"{base}/api/v3/ticker/24hr", params={"symbol": symbol},
            cache_seconds=self.s.crypto_source_cache_seconds,
        )
        result = SourceSeries(
            source="binance", asset=asset, spot=_f(stats.get("lastPrice")),
            bid=_f(book.get("bidPrice")), ask=_f(book.get("askPrice")),
            volume_24h=_f(stats.get("quoteVolume")), reliability=0.90,
        )
        for tf in self._timeframes():
            rows = self.http.get_json(
                f"{base}/api/v3/klines",
                params={"symbol": symbol, "interval": tf, "limit": min(1000, self.s.crypto_candle_limit)},
                cache_seconds=self.s.crypto_source_cache_seconds,
            )
            result.candles[tf] = [Candle(
                ts=int(r[0] // 1000), open=_f(r[1]), high=_f(r[2]), low=_f(r[3]),
                close=_f(r[4]), volume=_f(r[5]), source="binance", asset=asset, timeframe=tf,
            ) for r in rows if isinstance(r, list) and len(r) >= 6]
        return result

    def _fetch_kraken(self, asset: str) -> SourceSeries:
        pair = _pair("kraken", asset)
        base = "https://api.kraken.com/0/public"
        ticker_payload = self.http.get_json(
            f"{base}/Ticker", params={"pair": pair},
            cache_seconds=self.s.crypto_source_cache_seconds,
        )
        if ticker_payload.get("error"):
            raise RuntimeError(str(ticker_payload["error"]))
        ticker = next(iter((ticker_payload.get("result") or {}).values()))
        result = SourceSeries(
            source="kraken", asset=asset, spot=_f((ticker.get("c") or [0])[0]),
            bid=_f((ticker.get("b") or [0])[0]), ask=_f((ticker.get("a") or [0])[0]),
            volume_24h=_f((ticker.get("v") or [0, 0])[-1]), reliability=0.88,
        )
        intervals = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
        for tf in self._timeframes():
            payload = self.http.get_json(
                f"{base}/OHLC", params={"pair": pair, "interval": intervals[tf]},
                cache_seconds=self.s.crypto_source_cache_seconds,
            )
            if payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            data = payload.get("result") or {}
            rows = next((v for k, v in data.items() if k != "last" and isinstance(v, list)), [])
            result.candles[tf] = [Candle(
                ts=int(r[0]), open=_f(r[1]), high=_f(r[2]), low=_f(r[3]),
                close=_f(r[4]), volume=_f(r[6] if len(r) > 6 else 0),
                source="kraken", asset=asset, timeframe=tf,
            ) for r in rows[-self.s.crypto_candle_limit:] if isinstance(r, list) and len(r) >= 5]
        return result

    def _fetch_bybit(self, asset: str) -> SourceSeries:
        symbol = _pair("bybit", asset)
        base = "https://api.bybit.com/v5/market"
        payload = self.http.get_json(
            f"{base}/tickers", params={"category": "spot", "symbol": symbol},
            cache_seconds=self.s.crypto_source_cache_seconds,
        )
        rows = ((payload.get("result") or {}).get("list") or []) if isinstance(payload, dict) else []
        if not rows:
            raise RuntimeError(f"Bybit {symbol}: ticker absent")
        ticker = rows[0]
        result = SourceSeries(
            source="bybit", asset=asset, spot=_f(ticker.get("lastPrice")),
            bid=_f(ticker.get("bid1Price")), ask=_f(ticker.get("ask1Price")),
            volume_24h=_f(ticker.get("turnover24h")), reliability=0.88,
        )
        intervals = {"5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
        for tf in self._timeframes():
            candle_payload = self.http.get_json(
                f"{base}/kline",
                params={
                    "category": "spot", "symbol": symbol, "interval": intervals[tf],
                    "limit": min(1000, self.s.crypto_candle_limit),
                },
                cache_seconds=self.s.crypto_source_cache_seconds,
            )
            raw_rows = ((candle_payload.get("result") or {}).get("list") or [])
            candles = [Candle(
                ts=int(int(r[0]) // 1000), open=_f(r[1]), high=_f(r[2]), low=_f(r[3]),
                close=_f(r[4]), volume=_f(r[5]), source="bybit", asset=asset, timeframe=tf,
            ) for r in raw_rows if isinstance(r, list) and len(r) >= 6]
            result.candles[tf] = sorted(candles, key=lambda c: c.ts)[-self.s.crypto_candle_limit:]
        return result


    def _fetch_okx(self, asset: str) -> SourceSeries:
        symbol = _pair("okx", asset)
        bases = list(getattr(self.s, "okx_api_base_urls", [])) or ["https://www.okx.com"]
        payload = self._get_json_from_bases(
            bases, "/api/v5/market/ticker", params={"instId": symbol},
            cache_seconds=self.s.crypto_source_cache_seconds, require_data=True,
        )
        rows = payload.get("data") or []
        ticker = rows[0]
        result = SourceSeries(
            source="okx", asset=asset, spot=_f(ticker.get("last")),
            bid=_f(ticker.get("bidPx")), ask=_f(ticker.get("askPx")),
            volume_24h=_f(ticker.get("volCcyQuote24h") or ticker.get("volCcy24h")),
            reliability=0.89,
        )
        if result.spot <= 0:
            raise RuntimeError(f"OKX {symbol}: prix spot nul ou invalide")
        bars = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}
        for tf in self._timeframes():
            try:
                candle_payload = self._get_json_from_bases(
                    bases, "/api/v5/market/candles",
                    params={"instId": symbol, "bar": bars[tf],
                            "limit": min(300, self.s.crypto_candle_limit)},
                    cache_seconds=self.s.crypto_source_cache_seconds, require_data=True,
                )
                raw = candle_payload.get("data") or []
                candles = [Candle(
                    ts=int(int(r[0]) // 1000), open=_f(r[1]), high=_f(r[2]),
                    low=_f(r[3]), close=_f(r[4]), volume=_f(r[5]),
                    source="okx", asset=asset, timeframe=tf,
                ) for r in raw if isinstance(r, list) and len(r) >= 6]
                result.candles[tf] = sorted(candles, key=lambda c: c.ts)[-self.s.crypto_candle_limit:]
            except Exception as exc:
                log.warning("Bougies OKX %s/%s indisponibles: %s", asset, tf, exc)
        return result

    def _fetch_kucoin(self, asset: str) -> SourceSeries:
        symbol = _pair("kucoin", asset)
        # Global first: the EU endpoint can return HTTP 200/code 200000 with data=null.
        bases = list(getattr(self.s, "kucoin_api_base_urls", [])) or [
            "https://api.kucoin.com", "https://api.kucoin.eu",
        ]
        payload = self._get_json_from_bases(
            bases, "/api/v1/market/orderbook/level1", params={"symbol": symbol},
            cache_seconds=self.s.crypto_source_cache_seconds, require_data=True,
        )
        level1 = payload.get("data") or {}
        stats: dict[str, Any] = {}
        try:
            stats_payload = self._get_json_from_bases(
                bases, "/api/v1/market/stats", params={"symbol": symbol},
                cache_seconds=self.s.crypto_source_cache_seconds, require_data=True,
            )
            stats = stats_payload.get("data") or {}
        except Exception as exc:
            log.warning("Statistiques KuCoin %s indisponibles: %s", asset, exc)
        spot = _f(level1.get("price") or stats.get("last"))
        if spot <= 0:
            raise RuntimeError(f"KuCoin {symbol}: prix spot nul ou invalide")
        result = SourceSeries(
            source="kucoin", asset=asset, spot=spot,
            bid=_f(level1.get("bestBid") or stats.get("buy"), spot),
            ask=_f(level1.get("bestAsk") or stats.get("sell"), spot),
            volume_24h=_f(stats.get("volValue")), reliability=0.88,
        )
        types = {"5m": "5min", "15m": "15min", "1h": "1hour", "4h": "4hour", "1d": "1day"}
        for tf in self._timeframes():
            try:
                candle_payload = self._get_json_from_bases(
                    bases, "/api/v1/market/candles",
                    params={"symbol": symbol, "type": types[tf]},
                    cache_seconds=self.s.crypto_source_cache_seconds, require_data=True,
                )
                raw = candle_payload.get("data") or []
                candles = [Candle(
                    ts=int(r[0]), open=_f(r[1]), high=_f(r[3]), low=_f(r[4]),
                    close=_f(r[2]), volume=_f(r[5]), source="kucoin",
                    asset=asset, timeframe=tf,
                ) for r in raw if isinstance(r, list) and len(r) >= 6]
                result.candles[tf] = sorted(candles, key=lambda c: c.ts)[-self.s.crypto_candle_limit:]
            except Exception as exc:
                log.warning("Bougies KuCoin %s/%s indisponibles: %s", asset, tf, exc)
        return result

    def _fetch_hyperliquid(self, asset: str) -> SourceSeries:
        coin = _pair("hyperliquid", asset)
        base = "https://api.hyperliquid.xyz/info"
        meta_payload = self.http.post_json(
            base, {"type": "metaAndAssetCtxs"},
            cache_seconds=self.s.crypto_source_cache_seconds,
        )
        if not isinstance(meta_payload, list) or len(meta_payload) < 2:
            raise RuntimeError("Hyperliquid: metaAndAssetCtxs invalide")
        meta, contexts = meta_payload[0] or {}, meta_payload[1] or []
        universe = meta.get("universe") or []
        index = next((i for i, row in enumerate(universe) if str(row.get("name") or "").upper() == coin), None)
        if index is None or index >= len(contexts):
            raise RuntimeError(f"Hyperliquid: actif {coin} absent")
        ctx = contexts[index] or {}
        spot = _f(ctx.get("midPx") or ctx.get("markPx") or ctx.get("oraclePx"))
        bid = ask = spot
        try:
            book = self.http.post_json(
                base, {"type": "l2Book", "coin": coin},
                cache_seconds=self.s.crypto_source_cache_seconds,
            )
            levels = (book or {}).get("levels") or []
            if len(levels) >= 2:
                if levels[0]:
                    bid = _f(levels[0][0].get("px"), spot)
                if levels[1]:
                    ask = _f(levels[1][0].get("px"), spot)
        except Exception:
            pass
        result = SourceSeries(
            source="hyperliquid", asset=asset, spot=spot, bid=bid, ask=ask,
            volume_24h=_f(ctx.get("dayNtlVlm")), reliability=0.86,
        )
        seconds = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        for tf in self._timeframes():
            start_ms = now_ms - int(seconds[tf] * max(30, self.s.crypto_candle_limit + 5) * 1000)
            rows = self.http.post_json(
                base,
                {"type": "candleSnapshot", "req": {
                    "coin": coin, "interval": tf, "startTime": start_ms, "endTime": now_ms,
                }},
                cache_seconds=self.s.crypto_source_cache_seconds,
            )
            candles = [Candle(
                ts=int(int(r.get("t") or 0) // 1000), open=_f(r.get("o")),
                high=_f(r.get("h")), low=_f(r.get("l")), close=_f(r.get("c")),
                volume=_f(r.get("v")), source="hyperliquid", asset=asset, timeframe=tf,
            ) for r in (rows or []) if isinstance(r, dict) and r.get("t") is not None]
            result.candles[tf] = sorted(candles, key=lambda c: c.ts)[-self.s.crypto_candle_limit:]
        return result

