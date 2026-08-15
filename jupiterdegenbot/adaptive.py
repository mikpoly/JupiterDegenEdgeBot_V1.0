from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(slots=True)
class AdaptiveProfile:
    asset: str
    horizon: str
    comparator: str
    sample_count: int = 0
    brier_score: float | None = None
    residual_bias: float = 0.0
    probability_adjustment: float = 0.0
    confidence_multiplier: float = 1.0
    source_weights: dict[str, float] = field(default_factory=dict)
    active: bool = False

    def dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "horizon": self.horizon,
            "comparator": self.comparator,
            "sample_count": self.sample_count,
            "brier_score": self.brier_score,
            "residual_bias": self.residual_bias,
            "probability_adjustment": self.probability_adjustment,
            "confidence_multiplier": self.confidence_multiplier,
            "source_weights": dict(self.source_weights),
            "active": self.active,
        }


def horizon_bucket(expiry: int | float | None, created_at: str | None = None) -> str:
    try:
        if created_at:
            created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            start = created.timestamp()
        else:
            start = datetime.now(timezone.utc).timestamp()
        hours = (float(expiry) - start) / 3600.0
    except (TypeError, ValueError, OSError):
        return "unknown"
    if hours <= 1:
        return "5m-1h"
    if hours <= 24:
        return "1h-24h"
    return "1d-7d"


def _actual_yes(outcome: str, result: str) -> int | None:
    side = str(outcome or "").upper()
    settled = str(result or "").upper()
    if side not in {"YES", "NO"} or settled not in {"WON", "LOST"}:
        return None
    won = settled == "WON"
    return int((side == "YES" and won) or (side == "NO" and not won))


def _safe_json(value: str | dict | None) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


class AdaptiveMemory:
    """Conservative learning from settled shadow predictions and PAPER positions.

    This component never trains a language model and never learns from an
    unresolved market. Timed-direction labels are intentionally excluded because
    their short-horizon V2 calibration is isolated from generic threshold-market
    memory. Other supported Jupiter markets contribute settled labels; it estimates historical source quality and calibration
    bias, then applies bounded corrections with strong shrinkage.
    """

    def __init__(self, settings, db):
        self.s = settings
        self.db = db
        self._cache: dict[tuple[str, str, str], AdaptiveProfile] = {}

    def _history_rows(self) -> list[dict[str, Any]]:
        limit = max(1, int(getattr(self.s, "adaptive_lookback", 2000)))
        result: list[dict[str, Any]] = []
        seen_markets: set[str] = set()
        with self.db.connect(readonly=True) as conn:
            shadow_rows = conn.execute(
                """SELECT market_id,asset,horizon,comparator,probability_yes,actual_yes,evidence_json
                   FROM shadow_predictions
                   WHERE status='RESOLVED' AND actual_yes IS NOT NULL
                     AND COALESCE(settlement_kind,'') <> 'timed_direction'
                   ORDER BY id DESC LIMIT ?""", (limit,),
            ).fetchall()
            for row in shadow_rows:
                market_id = str(row["market_id"] or "")
                if market_id:
                    seen_markets.add(market_id)
                result.append({
                    "asset": str(row["asset"] or "UNKNOWN").upper(),
                    "horizon": str(row["horizon"] or "unknown"),
                    "comparator": str(row["comparator"] or "ALL"),
                    "probability_yes": clamp(float(row["probability_yes"]), 0.001, 0.999),
                    "actual_yes": int(row["actual_yes"]),
                    "evidence": _safe_json(row["evidence_json"]),
                })
            remaining = max(0, limit - len(result))
            if remaining:
                rows = conn.execute(
                    """SELECT o.id,o.market_id,o.asset,o.outcome,o.paper_result,o.created_at,
                              s.expiry,m.comparator,mp.probability_yes,mp.evidence_json
                       FROM orders o
                       JOIN signals s ON s.id=o.signal_id
                       LEFT JOIN markets m ON m.market_id=o.market_id
                       LEFT JOIN model_predictions mp ON mp.id=(
                           SELECT MAX(mp2.id) FROM model_predictions mp2
                           WHERE mp2.run_id=o.run_id AND mp2.market_id=o.market_id
                       )
                       WHERE o.mode='paper' AND o.paper_result IN ('WON','LOST')
                             AND mp.probability_yes IS NOT NULL
                       ORDER BY o.id DESC LIMIT ?""", (remaining,),
                ).fetchall()
                for row in rows:
                    if str(row["market_id"] or "") in seen_markets:
                        continue
                    actual = _actual_yes(row["outcome"], row["paper_result"])
                    if actual is None:
                        continue
                    result.append({
                        "asset": str(row["asset"] or "UNKNOWN").upper(),
                        "horizon": horizon_bucket(row["expiry"], row["created_at"]),
                        "comparator": str(row["comparator"] or "ALL"),
                        "probability_yes": clamp(float(row["probability_yes"]), 0.001, 0.999),
                        "actual_yes": actual,
                        "evidence": _safe_json(row["evidence_json"]),
                    })
        return result

    def rebuild(self) -> list[AdaptiveProfile]:
        rows = self._history_rows()
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            keys = {
                (row["asset"], row["horizon"], row["comparator"]),
                (row["asset"], row["horizon"], "ALL"),
                (row["asset"], "ALL", "ALL"),
            }
            for key in keys:
                grouped.setdefault(key, []).append(row)

        profiles: list[AdaptiveProfile] = []
        min_samples = max(5, int(getattr(self.s, "adaptive_min_settled", 20)))
        max_adjustment = clamp(float(getattr(self.s, "adaptive_max_probability_adjustment", 0.06)), 0.0, 0.15)
        source_min = clamp(float(getattr(self.s, "adaptive_source_weight_min", 0.65)), 0.1, 1.0)
        source_max = clamp(float(getattr(self.s, "adaptive_source_weight_max", 1.35)), 1.0, 3.0)

        for (asset, horizon, comparator), items in sorted(grouped.items()):
            n = len(items)
            residual = sum(row["actual_yes"] - row["probability_yes"] for row in items) / n
            brier = sum((row["probability_yes"] - row["actual_yes"]) ** 2 for row in items) / n
            active = n >= min_samples
            shrink = n / (n + 2.0 * min_samples)
            adjustment = clamp(residual * shrink, -max_adjustment, max_adjustment) if active else 0.0
            confidence_multiplier = 1.0
            if active:
                confidence_multiplier = clamp(1.04 - max(0.0, brier - 0.16) * 1.6, 0.72, 1.02)

            source_scores: dict[str, list[tuple[float, int]]] = {}
            for row in items:
                models = row["evidence"].get("models") or []
                if not isinstance(models, list):
                    continue
                for model in models:
                    if not isinstance(model, dict):
                        continue
                    source = str(model.get("source") or "").casefold()
                    try:
                        probability = clamp(float(model.get("probability")), 0.001, 0.999)
                    except (TypeError, ValueError):
                        continue
                    if source:
                        source_scores.setdefault(source, []).append((probability, int(row["actual_yes"])))

            source_weights: dict[str, float] = {}
            for source, values in source_scores.items():
                source_n = len(values)
                if source_n < max(5, min_samples // 2):
                    source_weights[source] = 1.0
                    continue
                source_brier = sum((p - y) ** 2 for p, y in values) / source_n
                quality = math.sqrt(0.25 / max(source_brier, 0.04))
                source_shrink = source_n / (source_n + min_samples)
                source_weights[source] = clamp(1.0 + (quality - 1.0) * source_shrink, source_min, source_max)

            profile = AdaptiveProfile(
                asset=asset,
                horizon=horizon,
                comparator=comparator,
                sample_count=n,
                brier_score=brier,
                residual_bias=residual,
                probability_adjustment=adjustment,
                confidence_multiplier=confidence_multiplier,
                source_weights=source_weights,
                active=active,
            )
            profiles.append(profile)
            self.db.upsert_learning_profile(profile)

        self._cache = {(p.asset, p.horizon, p.comparator): p for p in profiles}
        return profiles

    def profile(self, asset: str, horizon: str, comparator: str) -> AdaptiveProfile:
        if not bool(getattr(self.s, "adaptive_learning_enabled", True)):
            return AdaptiveProfile(asset, horizon, comparator)
        keys = [
            (asset.upper(), horizon, comparator),
            (asset.upper(), horizon, "ALL"),
            (asset.upper(), "ALL", "ALL"),
        ]
        for key in keys:
            if key in self._cache:
                return self._cache[key]
            row = self.db.get_learning_profile(*key)
            if row is not None:
                profile = AdaptiveProfile(
                    asset=str(row["asset"]), horizon=str(row["horizon"]), comparator=str(row["comparator"]),
                    sample_count=int(row["sample_count"] or 0),
                    brier_score=float(row["brier_score"]) if row["brier_score"] is not None else None,
                    residual_bias=float(row["residual_bias"] or 0.0),
                    probability_adjustment=float(row["probability_adjustment"] or 0.0),
                    confidence_multiplier=float(row["confidence_multiplier"] or 1.0),
                    source_weights=_safe_json(row["source_weights_json"]),
                    active=bool(row["active"]),
                )
                self._cache[key] = profile
                return profile
        return AdaptiveProfile(asset.upper(), horizon, comparator)
