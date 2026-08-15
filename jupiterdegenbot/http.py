from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

from .cache import JsonCache
from .rate_limit import RateLimitCoordinator

log = logging.getLogger(__name__)


class HttpClient:
    def __init__(self, settings):
        self.s = settings
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "JupiterDegenEdgeBotPC/1.0.0 contact=local-user",
            "Accept-Encoding": "gzip, deflate",
        })
        self.cache = JsonCache(settings.cache_dir)
        self.rate_limits = RateLimitCoordinator(settings)
        # Per-instance overrides used only by the dedicated TIMED worker.
        # The normal scanner keeps the original settings unchanged.
        self._fast_timeout_seconds: float | None = None
        self._fast_max_attempts: int | None = None
        self._fast_max_rate_wait_seconds: float | None = None
        self._fast_min_cache_seconds: float = 0.0

    def enable_fast_read_mode(
        self, *, timeout_seconds: float = 3.0, max_attempts: int = 1,
        max_rate_wait_seconds: float = 0.75, min_cache_seconds: float = 2.0,
    ) -> None:
        """Bound read latency on this HttpClient instance only.

        Real order submission does not use this client.  The worker may abandon
        a slow public market-data source, but it never relaxes a LIVE guard.
        """
        self._fast_timeout_seconds = max(0.75, float(timeout_seconds))
        self._fast_max_attempts = max(1, int(max_attempts))
        self._fast_max_rate_wait_seconds = max(0.0, float(max_rate_wait_seconds))
        self._fast_min_cache_seconds = max(0.0, float(min_cache_seconds))

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        last = None
        attempts = (
            self._fast_max_attempts if self._fast_max_attempts is not None
            else max(1, int(getattr(self.s, "http_max_attempts", 4)))
        )
        timeout = (
            self._fast_timeout_seconds if self._fast_timeout_seconds is not None
            else max(3.0, float(getattr(self.s, "http_timeout_seconds", 25.0)))
        )
        source = self.rate_limits.source_for(url)
        for attempt in range(attempts):
            # A FAST worker must not sit behind a long host cooldown.  It skips
            # that source for this poll and can retry on the next short poll.
            if self._fast_max_rate_wait_seconds is not None:
                state = self.rate_limits.snapshot().get(source, {})
                pending = max(
                    float(state.get("next_request_in_seconds", 0.0) or 0.0),
                    float(state.get("circuit_in_seconds", 0.0) or 0.0),
                )
                if pending > self._fast_max_rate_wait_seconds:
                    raise TimeoutError(
                        f"FAST source {source} en attente {pending:.2f}s "
                        f"> {self._fast_max_rate_wait_seconds:.2f}s"
                    )
            self.rate_limits.acquire(url)
            try:
                response = self.session.request(method, url, timeout=timeout, **kwargs)
                if response.status_code in {418, 429}:
                    retry = response.headers.get("Retry-After")
                    try:
                        retry_seconds = float(retry)
                    except (TypeError, ValueError):
                        retry_seconds = min(120.0, 2.0 ** (attempt + 2))
                    delay = self.rate_limits.penalize(source, retry_seconds)
                    log.warning("Rate limit %s sur %s: pause %.1fs", response.status_code, source, delay)
                    if attempt < attempts - 1:
                        time.sleep(delay)
                        continue
                if response.status_code in {500, 502, 503, 504} and attempt < attempts - 1:
                    time.sleep(min(30.0, 2 ** (attempt + 1)) + random.uniform(0.0, 0.25))
                    continue
                if response.status_code >= 400:
                    body = (response.text or "").strip().replace("\n", " ")[:700]
                    raise requests.HTTPError(
                        f"HTTP {response.status_code} {response.url}: {body or response.reason}",
                        response=response,
                    )
                return response
            except requests.RequestException as exc:
                last = exc
                if attempt >= attempts - 1:
                    raise
                time.sleep(min(30.0, 2 ** (attempt + 1)) + random.uniform(0.0, 0.25))
        raise last or RuntimeError(f"requête impossible: {url}")

    def get_json(self, url: str, params: dict | None = None, cache_seconds: float = 0, headers: dict | None = None) -> Any:
        if self._fast_min_cache_seconds > 0:
            cache_seconds = max(float(cache_seconds or 0.0), self._fast_min_cache_seconds)
        key = "json:" + url + "?" + repr(sorted((params or {}).items()))
        if cache_seconds > 0:
            cached = self.cache.get(key, cache_seconds)
            if cached is not None:
                return cached
        response = self._request("GET", url, params=params, headers=headers)
        data = response.json()
        if cache_seconds > 0:
            self.cache.set(key, data)
        return data

    def get_text(self, url: str, params: dict | None = None, cache_seconds: float = 0, headers: dict | None = None) -> str:
        key = "text:" + url + "?" + repr(sorted((params or {}).items()))
        if cache_seconds > 0:
            cached = self.cache.get(key, cache_seconds)
            if isinstance(cached, dict) and "text" in cached:
                return str(cached["text"])
        response = self._request("GET", url, params=params, headers=headers)
        text = response.text
        if cache_seconds > 0:
            self.cache.set(key, {"text": text})
        return text

    def post_json(self, url: str, payload: dict, cache_seconds: float = 0, headers: dict | None = None) -> Any:
        if self._fast_min_cache_seconds > 0:
            cache_seconds = max(float(cache_seconds or 0.0), self._fast_min_cache_seconds)
        key = "post:" + url + "?" + repr(sorted(payload.items()))
        if cache_seconds > 0:
            cached = self.cache.get(key, cache_seconds)
            if cached is not None:
                return cached
        response = self._request("POST", url, json=payload, headers=headers)
        data = response.json()
        if cache_seconds > 0:
            self.cache.set(key, data)
        return data
