from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Any

log = logging.getLogger(__name__)

COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "HYPE": "hyperliquid", "DOGE": "dogecoin", "BNB": "binancecoin",
}


@dataclass(slots=True)
class MarketContext:
    source: str
    asset: str
    price_usd: float = 0.0
    change_24h_pct: float = 0.0
    volume_24h_usd: float = 0.0
    btc_dominance: float = 0.0
    total_market_cap_change_24h_pct: float = 0.0

    def dict(self) -> dict[str, Any]:
        return asdict(self)


class MarketContextClient:
    def __init__(self, settings, http):
        self.s, self.http = settings, http

    def fetch(self, asset: str) -> list[MarketContext]:
        rows: list[MarketContext] = []
        if self.s.coingecko_enabled:
            try:
                rows.append(self._coingecko(asset))
            except Exception as exc:
                log.warning("Contexte CoinGecko indisponible: %s", exc)
        if self.s.coinmarketcap_enabled and self.s.coinmarketcap_api_key:
            try:
                rows.append(self._coinmarketcap(asset))
            except Exception as exc:
                log.warning("Contexte CoinMarketCap indisponible: %s", exc)
        return rows

    def _coingecko(self, asset: str) -> MarketContext:
        coin_id = COINGECKO_IDS[asset]
        headers = {"x-cg-demo-api-key": self.s.coingecko_api_key} if self.s.coingecko_api_key else None
        simple = self.http.get_json(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd", "include_24hr_change": "true", "include_24hr_vol": "true"},
            headers=headers, cache_seconds=300,
        )
        global_data = self.http.get_json("https://api.coingecko.com/api/v3/global", headers=headers, cache_seconds=600)
        quote = simple.get(coin_id) or {}
        data = global_data.get("data") or {}
        return MarketContext(
            "coingecko", asset, float(quote.get("usd") or 0.0),
            float(quote.get("usd_24h_change") or 0.0), float(quote.get("usd_24h_vol") or 0.0),
            float((data.get("market_cap_percentage") or {}).get("btc") or 0.0),
            float(data.get("market_cap_change_percentage_24h_usd") or 0.0),
        )

    def _coinmarketcap(self, asset: str) -> MarketContext:
        headers = {"X-CMC_PRO_API_KEY": self.s.coinmarketcap_api_key}
        quote = self.http.get_json(
            "https://pro-api.coinmarketcap.com/v3/cryptocurrency/quotes/latest",
            params={"symbol": asset, "convert": "USD"}, headers=headers, cache_seconds=300,
        )
        global_data = self.http.get_json(
            "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest",
            headers=headers, cache_seconds=600,
        )
        item = (quote.get("data") or {}).get(asset) or {}
        if isinstance(item, list):
            item = item[0] if item else {}
        usd = (item.get("quote") or {}).get("USD") or {}
        gdata = global_data.get("data") or {}
        gusd = (gdata.get("quote") or {}).get("USD") or {}
        return MarketContext(
            "coinmarketcap", asset, float(usd.get("price") or 0.0),
            float(usd.get("percent_change_24h") or 0.0), float(usd.get("volume_24h") or 0.0),
            float(gdata.get("btc_dominance") or 0.0), float(gusd.get("total_market_cap_yesterday_percentage_change") or 0.0),
        )
