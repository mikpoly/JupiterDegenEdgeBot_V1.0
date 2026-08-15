from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .crypto_data import Candle, PAIR, TARGETED_ALT_ASSETS
from .storage import now as utc_now

log = logging.getLogger(__name__)

TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


@dataclass(slots=True)
class QualityReport:
    asset: str
    source: str
    timeframe: str
    row_count: int
    missing_ratio: float
    duplicate_count: int
    stale: bool
    incomplete_dropped: int
    first_ts: int | None
    last_ts: int | None
    passed: bool
    detail: dict[str, Any]

    def dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset, "source": self.source, "timeframe": self.timeframe,
            "row_count": self.row_count, "missing_ratio": self.missing_ratio,
            "duplicate_count": self.duplicate_count, "stale": self.stale,
            "incomplete_dropped": self.incomplete_dropped, "first_ts": self.first_ts,
            "last_ts": self.last_ts, "passed": self.passed, "detail": self.detail,
        }


def _pair(source: str, asset: str) -> str:
    try:
        return PAIR[source][asset]
    except KeyError as exc:
        raise RuntimeError(f"{source}: paire indisponible pour {asset}") from exc


def _f(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _aggregate(candles: Iterable[Candle], source_tf: str, target_tf: str) -> list[Candle]:
    source_seconds = TF_SECONDS[source_tf]
    target_seconds = TF_SECONDS[target_tf]
    if target_seconds % source_seconds:
        raise ValueError(f"agrégation impossible {source_tf}->{target_tf}")
    factor = target_seconds // source_seconds
    buckets: dict[int, list[Candle]] = {}
    for candle in candles:
        bucket = candle.ts - candle.ts % target_seconds
        buckets.setdefault(bucket, []).append(candle)
    output: list[Candle] = []
    for ts, rows in sorted(buckets.items()):
        rows = sorted(rows, key=lambda x: x.ts)
        expected = [ts + i * source_seconds for i in range(factor)]
        if [x.ts for x in rows] != expected:
            continue
        output.append(Candle(
            ts=ts, open=rows[0].open, high=max(x.high for x in rows),
            low=min(x.low for x in rows), close=rows[-1].close,
            volume=sum(x.volume for x in rows), source=rows[0].source,
            asset=rows[0].asset, timeframe=target_tf,
        ))
    return output


def clean_candles(candles: Iterable[Candle], *, now_ts: int | None = None,
                  drop_incomplete: bool = True, max_missing_ratio: float = 0.015,
                  max_stale_intervals: int = 3) -> tuple[list[Candle], QualityReport]:
    rows = list(candles)
    if not rows:
        return [], QualityReport("", "", "", 0, 1.0, 0, True, 0, None, None, False, {"reason": "empty"})
    asset, source, timeframe = rows[0].asset, rows[0].source, rows[0].timeframe
    seconds = TF_SECONDS[timeframe]
    now_ts = int(now_ts or time.time())
    valid: list[Candle] = []
    incomplete_dropped = 0
    seen: set[int] = set()
    duplicates = 0
    invalid_ohlc = 0
    for candle in sorted(rows, key=lambda x: x.ts):
        if candle.ts in seen:
            duplicates += 1
            continue
        seen.add(candle.ts)
        if min(candle.open, candle.high, candle.low, candle.close) <= 0:
            invalid_ohlc += 1
            continue
        if candle.low > min(candle.open, candle.close) or candle.high < max(candle.open, candle.close) or candle.low > candle.high:
            invalid_ohlc += 1
            continue
        if drop_incomplete and candle.ts + seconds > now_ts:
            incomplete_dropped += 1
            continue
        valid.append(candle)
    if not valid:
        report = QualityReport(asset, source, timeframe, 0, 1.0, duplicates, True,
                               incomplete_dropped, None, None, False,
                               {"invalid_ohlc": invalid_ohlc, "reason": "no_valid_rows"})
        return [], report
    first_ts, last_ts = valid[0].ts, valid[-1].ts
    expected = max(1, int((last_ts - first_ts) / seconds) + 1)
    missing = max(0, expected - len(valid))
    missing_ratio = missing / expected
    stale = now_ts - (last_ts + seconds) > max_stale_intervals * seconds
    passed = missing_ratio <= max_missing_ratio and not stale and invalid_ohlc == 0
    report = QualityReport(
        asset, source, timeframe, len(valid), missing_ratio, duplicates, stale,
        incomplete_dropped, first_ts, last_ts, passed,
        {"expected_rows": expected, "missing_rows": missing, "invalid_ohlc": invalid_ohlc},
    )
    return valid, report


class HistoricalDataManager:
    """Rate-limited, resumable, multi-source historical OHLCV collector."""

    def __init__(self, settings, http, db):
        self.s, self.http, self.db = settings, http, db

    def _get_json_from_bases(self, bases: list[str], path: str, *,
                             params: dict[str, Any] | None = None,
                             require_data: bool = False) -> Any:
        errors: list[str] = []
        for raw_base in bases:
            base = str(raw_base or "").strip().rstrip("/")
            if not base:
                continue
            try:
                payload = self.http.get_json(f"{base}{path}", params=params)
                if require_data:
                    data = payload.get("data") if isinstance(payload, dict) else None
                    if data in (None, [], {}):
                        errors.append(f"{base}: réponse vide")
                        continue
                return payload
            except Exception as exc:
                errors.append(f"{base}: {exc}")
        raise RuntimeError(" | ".join(errors)[:1400] or f"aucune API disponible pour {path}")

    def _existing_bounds(self, source: str, asset: str, timeframe: str) -> tuple[int | None, int | None]:
        with self.db.connect(readonly=True) as conn:
            row = conn.execute(
                "SELECT MIN(ts) lo,MAX(ts) hi FROM candles WHERE source=? AND asset=? AND timeframe=?",
                (source, asset, timeframe),
            ).fetchone()
        return (int(row["lo"]) if row and row["lo"] is not None else None,
                int(row["hi"]) if row and row["hi"] is not None else None)

    def _load_range(self, source: str, asset: str, timeframe: str, start: int, end: int) -> list[Candle]:
        with self.db.connect(readonly=True) as conn:
            rows = conn.execute(
                """SELECT ts,open,high,low,close,volume FROM candles
                   WHERE source=? AND asset=? AND timeframe=? AND ts>=? AND ts<=? ORDER BY ts""",
                (source, asset, timeframe, int(start), int(end)),
            ).fetchall()
        return [Candle(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]),
                       float(r["close"]), float(r["volume"] or 0.0), source, asset, timeframe)
                for r in rows]

    @staticmethod
    def _requested_expected_rows(start: int, end: int, timeframe: str) -> int:
        seconds = TF_SECONDS[timeframe]
        first = ((int(start) + seconds - 1) // seconds) * seconds
        last = (int(end) // seconds) * seconds
        if last + seconds > int(end):
            last -= seconds
        if last < first:
            return 0
        return int((last - first) // seconds) + 1

    def _segments_to_sync(self, source: str, asset: str, timeframe: str, start: int, end: int) -> list[tuple[int, int]]:
        """Return only missing edges plus a small refresh overlap.

        This makes long research downloads resumable and avoids repeatedly
        consuming exchange quotas after a successful initial backfill.
        """
        lo, hi = self._existing_bounds(source, asset, timeframe)
        seconds = TF_SECONDS[timeframe]
        if lo is None or hi is None:
            return [(start, end)]
        segments: list[tuple[int, int]] = []
        if start < lo:
            backward_end = min(end, lo - seconds)
            if start <= backward_end:
                segments.append((start, backward_end))
        # Re-fetch a three-candle overlap so late exchange corrections are
        # upserted, then append genuinely new candles.
        forward_start = max(start, hi - 2 * seconds)
        if forward_start <= end:
            segments.append((forward_start, end))
        # Merge overlapping ranges defensively.
        merged: list[tuple[int, int]] = []
        for left, right in sorted(segments):
            if not merged or left > merged[-1][1] + seconds:
                merged.append((left, right))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        return merged

    def _write(self, rows: Iterable[Candle]) -> int:
        payload = [(c.source, c.asset, c.timeframe, int(c.ts), c.open, c.high, c.low, c.close, c.volume)
                   for c in rows]
        if not payload:
            return 0
        with self.db.connect() as conn:
            conn.executemany(
                """INSERT INTO candles(source,asset,timeframe,ts,open,high,low,close,volume)
                   VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source,asset,timeframe,ts) DO UPDATE SET
                   open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume""",
                payload,
            )
        return len(payload)

    def _record_quality(self, report: QualityReport) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO data_quality(asset,source,timeframe,row_count,missing_ratio,duplicate_count,
                   stale,incomplete_dropped,first_ts,last_ts,passed,detail_json,checked_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (report.asset, report.source, report.timeframe, report.row_count, report.missing_ratio,
                 report.duplicate_count, int(report.stale), report.incomplete_dropped,
                 report.first_ts, report.last_ts, int(report.passed),
                 json.dumps(report.detail, ensure_ascii=False), utc_now()),
            )

    def _record_sync(self, asset: str, source: str, timeframe: str, start: int, end: int,
                     rows: int, pages: int, status: str, detail: str, started_at: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO research_sync(asset,source,timeframe,requested_start,requested_end,
                   rows_written,pages,status,detail,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (asset, source, timeframe, start, end, rows, pages, status, detail[:2000], started_at, utc_now()),
            )

    def sync(self, asset: str, timeframe: str, *, days: int | None = None,
             sources: list[str] | None = None, end_ts: int | None = None) -> dict[str, Any]:
        asset = asset.upper(); end_ts = int(end_ts or time.time())
        days = max(1, int(days or self.s.research_history_days))
        start_ts = end_ts - days * 86400
        sources = sources or [s for s in self.s.crypto_sources if s in {"binance", "coinbase", "bybit", "hyperliquid", "okx", "kucoin"}]
        if asset not in TARGETED_ALT_ASSETS:
            sources = [s for s in sources if s not in {"okx", "kucoin"}]
        result: dict[str, Any] = {"asset": asset, "timeframe": timeframe, "days": days, "sources": {}}
        for source in sources:
            started = utc_now()
            try:
                segments = self._segments_to_sync(source, asset, timeframe, start_ts, end_ts)
                pages = 0; written = 0
                for segment_start, segment_end in segments:
                    fetched, segment_pages = getattr(self, f"_fetch_{source}")(
                        asset, timeframe, segment_start, segment_end
                    )
                    pages += int(segment_pages)
                    # Segment-level cleaning removes duplicate, invalid and open
                    # candles without falsely failing an old backfill as stale.
                    safe_rows, _ = clean_candles(
                        fetched, now_ts=end_ts,
                        drop_incomplete=self.s.research_drop_incomplete_candle,
                        max_missing_ratio=1.0, max_stale_intervals=10**9,
                    )
                    written += self._write(safe_rows)
                full_rows = self._load_range(source, asset, timeframe, start_ts, end_ts)
                cleaned, quality = clean_candles(
                    full_rows, now_ts=end_ts,
                    drop_incomplete=self.s.research_drop_incomplete_candle,
                    max_missing_ratio=self.s.research_max_missing_ratio,
                    max_stale_intervals=self.s.research_max_stale_intervals,
                )
                expected_requested = self._requested_expected_rows(start_ts, end_ts, timeframe)
                coverage = len(cleaned) / max(1, expected_requested)
                quality.detail.update({
                    "requested_start": start_ts, "requested_end": end_ts,
                    "requested_expected_rows": expected_requested,
                    "coverage_ratio": coverage,
                    "min_coverage_ratio": float(self.s.research_min_coverage_ratio),
                    "incremental_segments": segments,
                })
                quality.passed = bool(quality.passed and coverage >= float(self.s.research_min_coverage_ratio))
                self._record_quality(quality)
                status = "ok" if quality.passed else "quality_failed"
                self._record_sync(asset, source, timeframe, start_ts, end_ts, written, pages, status,
                                  json.dumps(quality.dict(), ensure_ascii=False), started)
                result["sources"][source] = {
                    "rows": written, "total_rows": len(cleaned), "pages": pages,
                    "segments": segments, "quality": quality.dict(),
                }
            except Exception as exc:
                log.warning("Historique %s/%s/%s indisponible: %s", source, asset, timeframe, exc)
                self._record_sync(asset, source, timeframe, start_ts, end_ts, 0, 0, "error", str(exc), started)
                result["sources"][source] = {"error": str(exc)}
        result["ok_sources"] = sum(1 for x in result["sources"].values() if x.get("quality", {}).get("passed"))
        result["ok"] = result["ok_sources"] >= int(self.s.crypto_min_sources)
        return result

    def sync_all(self, *, days: int | None = None, assets: list[str] | None = None,
                 timeframes: list[str] | None = None) -> dict[str, Any]:
        assets = assets or self.s.research_history_assets
        timeframes = timeframes or self.s.research_primary_timeframes
        reports = []
        for asset in assets:
            for timeframe in timeframes:
                reports.append(self.sync(asset, timeframe, days=days))
        requested_assets = {str(asset).upper() for asset in assets}
        core_assets = set(self.s.research_core_assets) & requested_assets
        # When a user explicitly requests only an extended asset (for example
        # HYPE), that asset becomes required for this command. During a full
        # portfolio sync, BTC/ETH/SOL are the mandatory research core and the
        # remaining assets are best-effort until enough deep sources exist.
        required_assets = core_assets or requested_assets
        required_reports = [r for r in reports if str(r.get("asset", "")).upper() in required_assets]
        failed_extended = [r for r in reports if not r.get("ok") and str(r.get("asset", "")).upper() not in required_assets]
        return {
            "reports": reports,
            "ok": bool(required_reports) and all(r.get("ok") for r in required_reports),
            "successful": sum(bool(r.get("ok")) for r in reports),
            "required_successful": sum(bool(r.get("ok")) for r in required_reports),
            "required_total": len(required_reports),
            "extended_warnings": len(failed_extended),
            "required_assets": sorted(required_assets),
            "total": len(reports),
        }

    def _fetch_binance(self, asset: str, timeframe: str, start: int, end: int) -> tuple[list[Candle], int]:
        symbol = _pair("binance", asset)
        base = "https://data-api.binance.vision/api/v3/klines"
        current = start * 1000; end_ms = end * 1000; rows: list[Candle] = []; pages = 0
        while current < end_ms and pages < self.s.research_max_pages_per_source:
            payload = self.http.get_json(base, params={"symbol": symbol, "interval": timeframe,
                                                       "startTime": current, "endTime": end_ms, "limit": 1000})
            pages += 1
            if not payload:
                break
            batch = [Candle(int(r[0] // 1000), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[5]),
                            "binance", asset, timeframe) for r in payload if len(r) >= 6]
            rows.extend(batch)
            next_ms = int(payload[-1][0]) + TF_SECONDS[timeframe] * 1000
            if next_ms <= current:
                break
            current = next_ms
            if len(payload) < 1000:
                break
            time.sleep(self.s.research_page_pause_seconds)
        return rows, pages

    def _fetch_coinbase(self, asset: str, timeframe: str, start: int, end: int) -> tuple[list[Candle], int]:
        pair = _pair("coinbase", asset)
        requested_tf = "1h" if timeframe == "4h" else timeframe
        granularity = {"5m": 300, "15m": 900, "1h": 3600, "1d": 86400}.get(requested_tf)
        if granularity is None:
            raise RuntimeError(f"coinbase: timeframe {timeframe} non supporté")
        rows: list[Candle] = []; pages = 0; current = start
        window = granularity * 299
        while current < end and pages < self.s.research_max_pages_per_source:
            page_end = min(end, current + window)
            payload = self.http.get_json(
                f"https://api.exchange.coinbase.com/products/{pair}/candles",
                params={"granularity": granularity,
                        "start": datetime.fromtimestamp(current, timezone.utc).isoformat(),
                        "end": datetime.fromtimestamp(page_end, timezone.utc).isoformat()},
            )
            pages += 1
            rows.extend(Candle(int(r[0]), _f(r[3]), _f(r[2]), _f(r[1]), _f(r[4]),
                               _f(r[5] if len(r) > 5 else 0), "coinbase", asset, requested_tf)
                        for r in payload if isinstance(r, list) and len(r) >= 5)
            current = page_end + granularity
            time.sleep(self.s.research_page_pause_seconds)
        if timeframe == "4h":
            rows = _aggregate(rows, "1h", "4h")
        return rows, pages

    def _fetch_bybit(self, asset: str, timeframe: str, start: int, end: int) -> tuple[list[Candle], int]:
        symbol = _pair("bybit", asset)
        interval = {"5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}[timeframe]
        current = start * 1000; end_ms = end * 1000; rows: list[Candle] = []; pages = 0
        step_ms = TF_SECONDS[timeframe] * 1000
        while current < end_ms and pages < self.s.research_max_pages_per_source:
            page_end = min(end_ms, current + step_ms * 999)
            payload = self.http.get_json("https://api.bybit.com/v5/market/kline",
                                         params={"category": "linear", "symbol": symbol,
                                                 "interval": interval, "start": current,
                                                 "end": page_end, "limit": 1000})
            pages += 1
            raw = ((payload.get("result") or {}).get("list") or [])
            batch = [Candle(int(int(r[0]) // 1000), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]), _f(r[5]),
                            "bybit", asset, timeframe) for r in raw if len(r) >= 6]
            rows.extend(batch)
            current = page_end + step_ms
            if not raw:
                break
            time.sleep(self.s.research_page_pause_seconds)
        return rows, pages


    def _fetch_okx(self, asset: str, timeframe: str, start: int, end: int) -> tuple[list[Candle], int]:
        symbol = _pair("okx", asset)
        bases = list(getattr(self.s, "okx_api_base_urls", [])) or ["https://www.okx.com"]
        bar = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc"}[timeframe]
        start_ms = start * 1000
        cursor = end * 1000
        rows: list[Candle] = []
        pages = 0
        seen_earliest: int | None = None
        while cursor > start_ms and pages < self.s.research_max_pages_per_source:
            payload = self._get_json_from_bases(
                bases, "/api/v5/market/history-candles",
                params={"instId": symbol, "bar": bar, "after": str(cursor), "limit": "300"},
                require_data=False,
            )
            pages += 1
            raw = payload.get("data") or [] if isinstance(payload, dict) else []
            batch = [Candle(
                int(int(r[0]) // 1000), _f(r[1]), _f(r[2]), _f(r[3]),
                _f(r[4]), _f(r[5]), "okx", asset, timeframe,
            ) for r in raw if isinstance(r, list) and len(r) >= 6]
            rows.extend(c for c in batch if start <= c.ts <= end)
            if not raw:
                break
            earliest = min(int(r[0]) for r in raw if isinstance(r, list) and r)
            if seen_earliest is not None and earliest >= seen_earliest:
                break
            seen_earliest = earliest
            cursor = earliest - 1
            if earliest <= start_ms:
                break
            time.sleep(self.s.research_page_pause_seconds)
        return rows, pages

    def _fetch_kucoin(self, asset: str, timeframe: str, start: int, end: int) -> tuple[list[Candle], int]:
        symbol = _pair("kucoin", asset)
        bases = list(getattr(self.s, "kucoin_api_base_urls", [])) or [
            "https://api.kucoin.com", "https://api.kucoin.eu",
        ]
        kind = {"5m": "5min", "15m": "15min", "1h": "1hour", "4h": "4hour", "1d": "1day"}[timeframe]
        step = TF_SECONDS[timeframe]
        current = start
        rows: list[Candle] = []
        pages = 0
        while current < end and pages < self.s.research_max_pages_per_source:
            page_end = min(end, current + step * 1499)
            payload = self._get_json_from_bases(
                bases, "/api/v1/market/candles",
                params={"symbol": symbol, "type": kind, "startAt": current, "endAt": page_end},
                require_data=False,
            )
            pages += 1
            raw = payload.get("data") or [] if isinstance(payload, dict) else []
            rows.extend(Candle(
                int(r[0]), _f(r[1]), _f(r[3]), _f(r[4]), _f(r[2]),
                _f(r[5]), "kucoin", asset, timeframe,
            ) for r in raw if isinstance(r, list) and len(r) >= 6)
            current = page_end + step
            time.sleep(self.s.research_page_pause_seconds)
        return rows, pages

    def _fetch_hyperliquid(self, asset: str, timeframe: str, start: int, end: int) -> tuple[list[Candle], int]:
        coin = _pair("hyperliquid", asset)
        payload = self.http.post_json("https://api.hyperliquid.xyz/info",
                                      {"type": "candleSnapshot", "req": {"coin": coin,
                                       "interval": timeframe, "startTime": start * 1000, "endTime": end * 1000}})
        rows = [Candle(int(int(r["t"]) // 1000), _f(r["o"]), _f(r["h"]), _f(r["l"]), _f(r["c"]), _f(r.get("v")),
                       "hyperliquid", asset, timeframe) for r in payload if isinstance(r, dict)]
        return rows, 1
