from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, asdict
from typing import Any

from .crypto_data import PAIR
from .storage import now

log = logging.getLogger(__name__)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _imbalance(bids: list, asks: list, levels: int) -> tuple[float, float]:
    bids = bids[:levels]; asks = asks[:levels]
    bid_size = sum(_f(row[1]) for row in bids if len(row) >= 2)
    ask_size = sum(_f(row[1]) for row in asks if len(row) >= 2)
    total = bid_size + ask_size
    imbalance = (bid_size - ask_size) / total if total > 0 else 0.0
    best_bid = _f(bids[0][0]) if bids else 0.0
    best_ask = _f(asks[0][0]) if asks else 0.0
    mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0.0
    spread_bps = (best_ask - best_bid) / mid * 10000 if mid > 0 else 0.0
    return imbalance, spread_bps


@dataclass(slots=True)
class DerivativeSnapshot:
    asset: str
    source: str
    funding_rate: float = 0.0
    open_interest: float = 0.0
    oi_change: float = 0.0
    book_imbalance: float = 0.0
    book_spread_bps: float = 0.0
    liquidation_bias: float = 0.0
    basis_bps: float = 0.0
    observed_at: str = ""
    raw: dict | None = None

    def dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["raw"] = self.raw or {}
        return result


class DerivativesClient:
    def __init__(self, settings, http, db=None):
        self.s, self.http, self.db = settings, http, db

    def fetch(self, asset: str) -> list[DerivativeSnapshot]:
        if not self.s.derivatives_enabled:
            return []
        rows: list[DerivativeSnapshot] = []
        for source in ("bybit", "hyperliquid"):
            if source not in self.s.crypto_sources:
                continue
            try:
                snap = getattr(self, f"_fetch_{source}")(asset.upper())
                rows.append(snap)
                self._persist(snap)
            except Exception as exc:
                log.warning("Dérivés %s/%s indisponibles: %s", source, asset, exc)
        return rows

    def _persist(self, snap: DerivativeSnapshot) -> None:
        if self.db is None:
            return
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO derived_metrics(asset,source,observed_at,funding_rate,open_interest,
                   oi_change,book_imbalance,book_spread_bps,liquidation_bias,basis_bps,raw_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (snap.asset, snap.source, snap.observed_at, snap.funding_rate, snap.open_interest,
                 snap.oi_change, snap.book_imbalance, snap.book_spread_bps, snap.liquidation_bias,
                 snap.basis_bps, json.dumps(snap.raw or {}, ensure_ascii=False, default=str)),
            )

    def _fetch_bybit(self, asset: str) -> DerivativeSnapshot:
        symbol = PAIR["bybit"][asset]
        base = "https://api.bybit.com/v5/market"
        ticker = self.http.get_json(f"{base}/tickers", params={"category": "linear", "symbol": symbol}, cache_seconds=20)
        ticker_row = (((ticker.get("result") or {}).get("list") or [{}])[0])
        funding = _f(ticker_row.get("fundingRate"))
        oi = _f(ticker_row.get("openInterest"))
        mark = _f(ticker_row.get("markPrice")); index = _f(ticker_row.get("indexPrice"))
        basis = (mark - index) / index * 10000 if index > 0 else 0.0
        book = self.http.get_json(f"{base}/orderbook", params={"category": "linear", "symbol": symbol,
                                                                  "limit": min(200, max(1, self.s.orderbook_depth_levels))},
                                  cache_seconds=10)
        result = book.get("result") or {}
        imbalance, spread_bps = _imbalance(result.get("b") or [], result.get("a") or [], self.s.orderbook_depth_levels)
        oi_history = self.http.get_json(f"{base}/open-interest",
                                        params={"category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": 2},
                                        cache_seconds=60)
        oi_rows = ((oi_history.get("result") or {}).get("list") or [])
        oi_change = 0.0
        if len(oi_rows) >= 2:
            newest, previous = _f(oi_rows[0].get("openInterest")), _f(oi_rows[1].get("openInterest"))
            oi_change = (newest - previous) / previous if previous > 0 else 0.0
        return DerivativeSnapshot(asset, "bybit", funding, oi, oi_change, imbalance, spread_bps,
                                  0.0, basis, now(), {"ticker": ticker_row, "book_time": result.get("ts")})

    def _fetch_hyperliquid(self, asset: str) -> DerivativeSnapshot:
        coin = PAIR["hyperliquid"][asset]
        meta_ctx = self.http.post_json("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"}, cache_seconds=20)
        universe = ((meta_ctx[0] or {}).get("universe") or []) if isinstance(meta_ctx, list) and len(meta_ctx) >= 2 else []
        contexts = meta_ctx[1] if isinstance(meta_ctx, list) and len(meta_ctx) >= 2 else []
        context = {}
        for idx, item in enumerate(universe):
            if str(item.get("name")) == coin and idx < len(contexts):
                context = contexts[idx] or {}
                break
        book = self.http.post_json("https://api.hyperliquid.xyz/info", {"type": "l2Book", "coin": coin}, cache_seconds=10)
        levels = book.get("levels") or [[], []]
        bids = [[x.get("px"), x.get("sz")] for x in (levels[0] if len(levels) > 0 else [])]
        asks = [[x.get("px"), x.get("sz")] for x in (levels[1] if len(levels) > 1 else [])]
        imbalance, spread_bps = _imbalance(bids, asks, self.s.orderbook_depth_levels)
        mark = _f(context.get("markPx")); oracle = _f(context.get("oraclePx"))
        basis = (mark - oracle) / oracle * 10000 if oracle > 0 else 0.0
        return DerivativeSnapshot(
            asset, "hyperliquid", _f(context.get("funding")), _f(context.get("openInterest")),
            0.0, imbalance, spread_bps, 0.0, basis, now(), {"context": context, "book_time": book.get("time")},
        )
