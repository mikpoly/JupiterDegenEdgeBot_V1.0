from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
from scipy.stats import kurtosis, t as student_t

from .crypto_data import Candle
from .history import TF_SECONDS, clean_candles


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def log_returns(candles: list[Candle]) -> np.ndarray:
    closes = np.asarray([x.close for x in sorted(candles, key=lambda x: x.ts) if x.close > 0], dtype=float)
    if closes.size < 2:
        return np.asarray([], dtype=float)
    return np.diff(np.log(closes))


def rsi(candles: list[Candle], period: int = 14) -> float:
    closes = np.asarray([x.close for x in sorted(candles, key=lambda x: x.ts) if x.close > 0], dtype=float)
    if closes.size < period + 1:
        return 50.0
    diffs = np.diff(closes)[-period:]
    gains = np.maximum(diffs, 0).mean(); losses = np.maximum(-diffs, 0).mean()
    if losses <= 1e-12:
        return 100.0 if gains > 0 else 50.0
    return float(100 - 100 / (1 + gains / losses))


def atr_pct(candles: list[Candle], period: int = 14) -> float:
    rows = sorted(candles, key=lambda x: x.ts)
    if len(rows) < period + 1:
        return 0.0
    values = []
    for prev, row in zip(rows[:-1], rows[1:]):
        tr = max(row.high - row.low, abs(row.high - prev.close), abs(row.low - prev.close))
        values.append(tr / max(prev.close, 1e-12))
    return float(np.mean(values[-period:]))


def ewma_sigma(returns: np.ndarray, decay: float = 0.94) -> float:
    if returns.size < 2:
        return 0.0
    weights = (1.0 - decay) * decay ** np.arange(returns.size - 1, -1, -1)
    weights /= weights.sum()
    mean = float(np.sum(weights * returns))
    variance = float(np.sum(weights * (returns - mean) ** 2))
    return math.sqrt(max(variance, 0.0))


def _rolling_horizon_returns(returns: np.ndarray, steps: int) -> np.ndarray:
    if steps <= 0 or returns.size < steps:
        return np.asarray([], dtype=float)
    cs = np.concatenate([[0.0], np.cumsum(returns)])
    return cs[steps:] - cs[:-steps]


def probability_models(comparator: str, low: float, high: float | None, spot: float,
                       mu: float, sigma: float, returns: np.ndarray, steps: int) -> dict[str, float]:
    sigma = max(1e-9, sigma)
    z_low = (math.log(low / spot) - mu) / sigma

    def cdf_normal(z: float) -> float:
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    excess = float(kurtosis(returns[-min(500, returns.size):], fisher=True, bias=False)) if returns.size >= 20 else 0.0
    df = clamp(6.0 / max(excess, 0.25) + 4.0, 4.0, 30.0)
    scale = sigma * math.sqrt((df - 2.0) / df)
    zt_low = (math.log(low / spot) - mu) / max(scale, 1e-9)

    def evaluate(cdf, zl: float, zh: float | None = None) -> float:
        if comparator in {"above", "above_or_equal"}:
            return 1.0 - cdf(zl)
        if comparator in {"below", "below_or_equal"}:
            return cdf(zl)
        if comparator == "between" and zh is not None:
            return max(0.0, cdf(zh) - cdf(zl))
        raise ValueError(comparator)

    z_high = ((math.log(high / spot) - mu) / sigma) if high else None
    zt_high = ((math.log(high / spot) - mu) / max(scale, 1e-9)) if high else None
    p_normal = evaluate(cdf_normal, z_low, z_high)
    p_student = evaluate(lambda z: float(student_t.cdf(z, df)), zt_low, zt_high)

    empirical = _rolling_horizon_returns(returns, max(1, steps))
    if empirical.size >= 30:
        threshold_low = math.log(low / spot)
        if comparator in {"above", "above_or_equal"}:
            p_empirical = float(np.mean(empirical >= threshold_low))
        elif comparator in {"below", "below_or_equal"}:
            p_empirical = float(np.mean(empirical <= threshold_low))
        elif comparator == "between" and high:
            threshold_high = math.log(high / spot)
            p_empirical = float(np.mean((empirical >= threshold_low) & (empirical <= threshold_high)))
        else:
            p_empirical = p_normal
    else:
        p_empirical = p_normal
    return {"normal": clamp(p_normal, 0.001, 0.999),
            "student": clamp(p_student, 0.001, 0.999),
            "empirical": clamp(p_empirical, 0.001, 0.999), "student_df": df}


@dataclass(slots=True)
class TimeframeFeatures:
    source: str
    asset: str
    timeframe: str
    sample_count: int
    spot: float
    rsi: float
    atr_pct: float
    sigma: float
    ewma_sigma: float
    downside_sigma: float
    upside_sigma: float
    momentum_fast: float
    momentum_slow: float
    ma_signal: float
    volume_z: float
    trend_slope: float
    return_skew: float
    return_kurtosis: float
    missing_ratio: float
    quality_passed: bool

    def dict(self) -> dict[str, Any]:
        return asdict(self)


def build_timeframe_features(candles: list[Candle], *, now_ts: int | None = None,
                             max_missing_ratio: float = 0.015,
                             max_stale_intervals: int = 3) -> TimeframeFeatures:
    cleaned, quality = clean_candles(candles, now_ts=now_ts, drop_incomplete=True,
                                     max_missing_ratio=max_missing_ratio,
                                     max_stale_intervals=max_stale_intervals)
    if len(cleaned) < 35:
        raise ValueError(f"{quality.source}: seulement {len(cleaned)} bougies closes {quality.timeframe}")
    returns = log_returns(cleaned)
    closes = np.asarray([x.close for x in cleaned], dtype=float)
    volumes = np.asarray([x.volume for x in cleaned], dtype=float)
    recent = returns[-min(500, returns.size):]
    sigma = float(np.std(recent, ddof=1)) if recent.size >= 2 else 0.0
    down = recent[recent < 0]; up = recent[recent > 0]
    fast_idx = max(0, closes.size - 6); slow_idx = max(0, closes.size - 21)
    momentum_fast = float(math.log(closes[-1] / closes[fast_idx]))
    momentum_slow = float(math.log(closes[-1] / closes[slow_idx]))
    ma_fast = float(np.mean(closes[-min(8, closes.size):]))
    ma_slow = float(np.mean(closes[-min(34, closes.size):]))
    ma_signal = math.log(ma_fast / ma_slow) if ma_fast > 0 and ma_slow > 0 else 0.0
    vol_recent = volumes[-min(60, volumes.size):]
    volume_z = (float(volumes[-1] - vol_recent.mean()) / float(vol_recent.std(ddof=1))) if vol_recent.size >= 5 and vol_recent.std(ddof=1) > 0 else 0.0
    y = np.log(closes[-min(60, closes.size):])
    x = np.arange(y.size, dtype=float)
    trend_slope = float(np.polyfit(x, y, 1)[0]) if y.size >= 5 else 0.0
    skew = float(((recent - recent.mean()) ** 3).mean() / max(recent.std() ** 3, 1e-12)) if recent.size >= 20 else 0.0
    kurt = float(kurtosis(recent, fisher=True, bias=False)) if recent.size >= 20 else 0.0
    return TimeframeFeatures(
        cleaned[0].source, cleaned[0].asset, cleaned[0].timeframe, int(returns.size),
        float(closes[-1]), rsi(cleaned), atr_pct(cleaned), sigma, ewma_sigma(recent),
        float(np.std(down, ddof=1)) if down.size >= 2 else sigma,
        float(np.std(up, ddof=1)) if up.size >= 2 else sigma,
        momentum_fast, momentum_slow, ma_signal, clamp(volume_z, -8.0, 8.0),
        trend_slope, clamp(skew, -8.0, 8.0), clamp(kurt, -5.0, 30.0),
        quality.missing_ratio, quality.passed,
    )


def timeframe_weight(timeframe: str, horizon_hours: float) -> float:
    tf_hours = TF_SECONDS[timeframe] / 3600.0
    ideal = clamp(horizon_hours / 12.0, 5 / 60, 24.0)
    distance = abs(math.log(max(tf_hours, 1e-9) / ideal))
    return math.exp(-0.72 * distance)


def flatten_features(features: list[TimeframeFeatures], derivatives: list[dict[str, Any]] | None,
                     threshold_distance: float, horizon_hours: float) -> dict[str, float]:
    result: dict[str, float] = {"threshold_distance": threshold_distance, "horizon_hours": horizon_hours}
    for row in features:
        prefix = row.timeframe
        for key, value in row.dict().items():
            if isinstance(value, (int, float, bool)) and key not in {"spot"}:
                result[f"{prefix}_{key}"] = float(value)
    derivatives = derivatives or []
    if derivatives:
        for key in ("funding_rate", "oi_change", "book_imbalance", "book_spread_bps", "basis_bps"):
            values = [float(x.get(key, 0.0)) for x in derivatives]
            result[f"deriv_{key}"] = float(statistics.median(values)) if values else 0.0
    return result
