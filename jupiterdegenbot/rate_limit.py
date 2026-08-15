from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(slots=True)
class SourcePolicy:
    min_interval: float
    burst: int = 1
    cooldown_after_429: float = 30.0


class RateLimitCoordinator:
    """Thread-safe conservative host limiter.

    It intentionally stays below published limits. The bot also honors
    Retry-After and opens a temporary circuit after HTTP 429/418 responses.
    """

    HOST_SOURCE = {
        "api.exchange.coinbase.com": "coinbase",
        "api.coinbase.com": "coinbase",
        "data-api.binance.vision": "binance",
        "api.binance.com": "binance",
        "api.kraken.com": "kraken",
        "api.bybit.com": "bybit",
        "api.hyperliquid.xyz": "hyperliquid",
        "api.coingecko.com": "coingecko",
        "pro-api.coinmarketcap.com": "coinmarketcap",
        "api.jup.ag": "jupiter",
        "prediction-market-api.jup.ag": "jupiter",
    }

    def __init__(self, settings):
        self._lock = threading.RLock()
        self._next_at: dict[str, float] = {}
        self._blocked_until: dict[str, float] = {}
        self._settings = settings

    def source_for(self, url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        return self.HOST_SOURCE.get(host, host or "unknown")

    def policy(self, source: str) -> SourcePolicy:
        default = float(getattr(self._settings, "source_default_min_interval_seconds", 0.35))
        value = float(getattr(self._settings, f"rate_{source}_min_interval_seconds", default))
        cooldown = float(getattr(self._settings, "rate_limit_cooldown_seconds", 30.0))
        return SourcePolicy(min_interval=max(0.0, value), cooldown_after_429=max(1.0, cooldown))

    def acquire(self, url: str) -> str:
        source = self.source_for(url)
        policy = self.policy(source)
        while True:
            with self._lock:
                now = time.monotonic()
                ready = max(self._next_at.get(source, 0.0), self._blocked_until.get(source, 0.0))
                delay = ready - now
                if delay <= 0:
                    jitter = random.uniform(0.0, min(0.08, policy.min_interval * 0.15))
                    self._next_at[source] = now + policy.min_interval + jitter
                    return source
            time.sleep(min(max(delay, 0.01), 2.0))

    def penalize(self, source: str, retry_after: float | None = None) -> float:
        policy = self.policy(source)
        delay = max(policy.cooldown_after_429, float(retry_after or 0.0))
        with self._lock:
            self._blocked_until[source] = max(self._blocked_until.get(source, 0.0), time.monotonic() + delay)
        return delay

    def snapshot(self) -> dict[str, dict[str, float]]:
        now = time.monotonic()
        with self._lock:
            keys = set(self._next_at) | set(self._blocked_until)
            return {
                key: {
                    "next_request_in_seconds": round(max(0.0, self._next_at.get(key, 0.0) - now), 3),
                    "circuit_in_seconds": round(max(0.0, self._blocked_until.get(key, 0.0) - now), 3),
                }
                for key in sorted(keys)
            }
