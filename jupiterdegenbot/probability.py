from __future__ import annotations

import math
import statistics
import time
from statistics import NormalDist

from .adaptive import AdaptiveMemory, horizon_bucket
from .crypto_data import Candle, CryptoDataClient, SourceSeries
from .derivatives import DerivativesClient
from .features import (
    TF_SECONDS, TimeframeFeatures, build_timeframe_features, clamp, flatten_features,
    log_returns, probability_models, timeframe_weight,
)
from .market_parser import parse_crypto_market
from .market_context import MarketContextClient
from .models import EngineEstimate, Market, SourceObservation
from .research_ml import NeuralModelManager


TIMED_DIRECTION_MODEL_NAME = "DEGEN_QUANT_V6_TIMED_DIRECTION_V2"
TIMED_DIRECTION_MODEL_VERSION = "START_ANCHORED_REMAINING_TIME_V2"


def _probability(comparator: str, low: float, high: float | None, spot: float,
                 mu: float, sigma: float) -> float:
    sigma = max(1e-9, sigma)
    normal = NormalDist(mu=mu, sigma=sigma)
    log_low = math.log(low / spot)
    if comparator in {"above", "above_or_equal"}:
        return 1.0 - normal.cdf(log_low)
    if comparator in {"below", "below_or_equal"}:
        return normal.cdf(log_low)
    if comparator == "between" and high is not None:
        log_high = math.log(high / spot)
        return max(0.0, normal.cdf(log_high) - normal.cdf(log_low))
    raise ValueError(f"comparateur probabiliste non supporté: {comparator}")


def _timeframe(hours: float) -> tuple[str, float]:
    if hours <= 1:
        return "5m", 5 / 60
    if hours <= 6:
        return "15m", 0.25
    if hours <= 36:
        return "1h", 1.0
    if hours <= 120:
        return "4h", 4.0
    return "1d", 24.0


def _drift(features, horizon_steps: float, sigma_horizon: float) -> float:
    mean_proxy = 0.35 * features.trend_slope * horizon_steps
    raw = (mean_proxy + 0.24 * features.momentum_fast + 0.18 * features.momentum_slow +
           0.13 * features.ma_signal - 0.04 * (features.rsi - 50.0) / 50.0 * sigma_horizon +
           0.03 * clamp(features.volume_z, -3.0, 3.0) * sigma_horizon)
    return clamp(raw, -0.70 * sigma_horizon, 0.70 * sigma_horizon)


def _timeframe_model(item: SourceSeries, timeframe: str, horizon_hours: float,
                     comparator: str, low: float, high: float | None, settings) -> dict:
    candles = item.candles.get(timeframe) or []
    features = build_timeframe_features(
        candles, max_missing_ratio=settings.research_max_missing_ratio,
        max_stale_intervals=settings.research_max_stale_intervals,
    )
    returns = log_returns(candles)
    bucket_hours = TF_SECONDS[timeframe] / 3600.0
    steps_float = max(0.05, horizon_hours / bucket_hours)
    steps = max(1, int(round(steps_float)))
    base_sigma = 0.45 * features.sigma + 0.55 * features.ewma_sigma
    asymmetry = features.upside_sigma if comparator in {"above", "above_or_equal"} else features.downside_sigma
    base_sigma = 0.75 * base_sigma + 0.25 * asymmetry
    sigma_horizon = clamp(base_sigma * math.sqrt(steps_float), settings.probability_min_sigma,
                          settings.probability_max_sigma)
    mu = _drift(features, steps_float, sigma_horizon)
    models = probability_models(comparator, low, high, item.spot, mu, sigma_horizon, returns, steps)
    probability = 0.35 * models["normal"] + 0.35 * models["student"] + 0.30 * models["empirical"]
    return {
        "source": item.source, "timeframe": timeframe, "probability": clamp(probability, 0.001, 0.999),
        "model_probabilities": models, "spot": item.spot, "sample_count": features.sample_count,
        "sigma_horizon": sigma_horizon, "mu_horizon": mu, "timeframe_weight": timeframe_weight(timeframe, horizon_hours),
        "quality_passed": features.quality_passed, "features": features.dict(),
        "reliability": item.reliability,
    }


def _source_model(item: SourceSeries, timeframe: str, bucket_hours: float,
                  horizon_hours: float, comparator: str, low: float, high: float | None,
                  settings) -> dict:
    """Backward-compatible single-timeframe wrapper used by older integrations."""
    return _timeframe_model(item, timeframe, horizon_hours, comparator, low, high, settings)




def _ensemble_feature_rows(source_models: list[dict]) -> list[TimeframeFeatures]:
    """Median feature vector per timeframe across independent sources.

    The neural member is trained on true multi-timeframe vectors. Feeding only
    the primary timeframe at inference would silently zero the other features,
    so every available timeframe is aggregated here before prediction.
    """
    grouped: dict[str, list[dict]] = {}
    for source in source_models:
        for model in source.get("timeframes", []):
            grouped.setdefault(str(model.get("timeframe")), []).append(dict(model.get("features") or {}))
    rows: list[TimeframeFeatures] = []
    for timeframe, values in sorted(grouped.items(), key=lambda item: TF_SECONDS.get(item[0], 10**9)):
        if not values:
            continue
        def med(key: str, default: float = 0.0) -> float:
            nums = []
            for value in values:
                try:
                    number = float(value.get(key, default))
                    if math.isfinite(number):
                        nums.append(number)
                except (TypeError, ValueError):
                    pass
            return statistics.median(nums) if nums else default
        rows.append(TimeframeFeatures(
            source="ensemble", asset=str(values[0].get("asset") or ""), timeframe=timeframe,
            sample_count=int(min(med("sample_count", 0.0), 10_000_000)), spot=med("spot"),
            rsi=med("rsi", 50.0), atr_pct=med("atr_pct"), sigma=med("sigma"),
            ewma_sigma=med("ewma_sigma"), downside_sigma=med("downside_sigma"),
            upside_sigma=med("upside_sigma"), momentum_fast=med("momentum_fast"),
            momentum_slow=med("momentum_slow"), ma_signal=med("ma_signal"),
            volume_z=med("volume_z"), trend_slope=med("trend_slope"),
            return_skew=med("return_skew"), return_kurtosis=med("return_kurtosis"),
            missing_ratio=max(float(v.get("missing_ratio", 0.0) or 0.0) for v in values),
            quality_passed=all(bool(v.get("quality_passed", False)) for v in values),
        ))
    return rows


def _derivative_adjustment(rows: list[dict]) -> tuple[float, dict]:
    if not rows:
        return 0.0, {}
    funding = statistics.median(float(x.get("funding_rate", 0.0)) for x in rows)
    oi_change = statistics.median(float(x.get("oi_change", 0.0)) for x in rows)
    imbalance = statistics.median(float(x.get("book_imbalance", 0.0)) for x in rows)
    basis_bps = statistics.median(float(x.get("basis_bps", 0.0)) for x in rows)
    # Contrarian funding/basis, confirming order-book/OI. The total is capped to
    # 2.5 probability points so derivatives can never dominate price history.
    raw = (-clamp(funding * 100.0, -1.0, 1.0) * 0.008 +
           clamp(oi_change, -0.15, 0.15) * 0.04 +
           clamp(imbalance, -1.0, 1.0) * 0.015 -
           clamp(basis_bps / 100.0, -1.0, 1.0) * 0.004)
    return clamp(raw, -0.025, 0.025), {
        "funding_rate": funding, "oi_change": oi_change,
        "book_imbalance": imbalance, "basis_bps": basis_bps,
    }


def _touch_source_probability(item: SourceSeries, spec, horizon_hours: float, settings) -> dict:
    threshold = float(spec.threshold_low or 0.0)
    if threshold <= 0 or item.spot <= 0:
        raise ValueError("seuil/spot invalide")
    # Choose the finest series that covers the complete barrier window when possible.
    window_start = int(spec.window_start_ts or max(0, spec.expiry_ts - 24 * 3600))
    chosen_tf = ""
    chosen: list[Candle] = []
    full_path = False
    for tf in ("5m", "15m", "1h", "4h"):
        rows = sorted(item.candles.get(tf) or [], key=lambda c: c.ts)
        if len(rows) < 30:
            continue
        covers = rows[0].ts <= window_start + TF_SECONDS.get(tf, 0)
        if covers:
            chosen_tf, chosen, full_path = tf, rows, True
            break
        if not chosen:
            chosen_tf, chosen = tf, rows
    if len(chosen) < 30:
        raise ValueError("historique touch insuffisant")
    relevant = [c for c in chosen if c.ts >= window_start]
    if not relevant:
        relevant = chosen
    already_touched = (
        any(c.high >= threshold for c in relevant)
        if spec.settlement_kind == "touch_high"
        else any(c.low <= threshold for c in relevant)
    )
    if already_touched:
        probability = 0.999
        sigma_remaining = 0.0
    else:
        returns = log_returns(chosen)
        if returns.size < 20:
            raise ValueError("rendements touch insuffisants")
        sigma_step = max(float(settings.probability_min_sigma) / 10.0, float(statistics.pstdev(returns[-min(240, returns.size):])))
        step_hours = TF_SECONDS[chosen_tf] / 3600.0
        remaining_steps = max(0.05, horizon_hours / step_hours)
        sigma_remaining = clamp(sigma_step * math.sqrt(remaining_steps), settings.probability_min_sigma, settings.probability_max_sigma)
        distance = abs(math.log(threshold / item.spot))
        z = distance / max(sigma_remaining, 1e-9)
        probability = clamp(2.0 * (1.0 - NormalDist().cdf(z)), 0.001, 0.995)
        # If price already lies beyond the barrier, the touch condition is met now.
        if spec.settlement_kind == "touch_high" and item.spot >= threshold:
            probability = 0.999
        if spec.settlement_kind == "touch_low" and item.spot <= threshold:
            probability = 0.999
    return {
        "source": item.source, "probability": probability, "spot": item.spot,
        "timeframe": chosen_tf, "sample_count": len(chosen), "full_path": full_path,
        "already_touched": already_touched, "sigma_remaining": sigma_remaining,
        "reliability": item.reliability,
    }


def _touch_estimate(engine, market: Market, spec, snapshot, run_id: int | None) -> EngineEstimate:
    horizon_hours = (spec.expiry_ts - time.time()) / 3600.0
    if not bool(getattr(engine.s, "touch_model_enabled", True)):
        return engine._reject("modèle touch désactivé", spec.asset)
    if horizon_hours <= 0 or horizon_hours > float(getattr(engine.s, "touch_model_max_horizon_hours", 168.0)):
        return engine._reject("horizon touch hors limite", spec.asset)
    models, errors = [], []
    for item in snapshot.sources:
        try:
            models.append(_touch_source_probability(item, spec, horizon_hours, engine.s))
        except Exception as exc:
            errors.append(f"{item.source}: {exc}")
    minimum = max(int(engine.s.crypto_min_sources), int(getattr(engine.s, "touch_model_min_sources", 2)))
    if len(models) < minimum:
        estimate = engine._reject(f"modèle touch: {len(models)}/{minimum} source(s)", spec.asset)
        estimate.reasoning = " | ".join(errors)[:900]
        estimate.observations = snapshot.observations
        return estimate
    probabilities = [float(m["probability"]) for m in models]
    p_yes = statistics.median(probabilities)
    dispersion = max(probabilities) - min(probabilities)
    probability_agreement = clamp(1.0 - dispersion / 0.30, 0.0, 1.0)
    full_ratio = sum(1 for m in models if m["full_path"]) / len(models)
    source_agreement = clamp(0.55 * snapshot.source_agreement + 0.45 * probability_agreement, 0.0, 1.0)
    sample_score = clamp(min(m["sample_count"] for m in models) / 240.0, 0.0, 1.0)
    confidence = clamp(0.38 * source_agreement + 0.32 * sample_score + 0.30 * full_ratio, 0.0, 0.97)
    reliability = clamp(statistics.fmean(float(m["reliability"]) for m in models) * source_agreement, 0.0, 0.97)
    reasoning = (
        f"{spec.asset} {spec.settlement_kind} {float(spec.threshold_low):,.4f}$; "
        f"horizon restant {horizon_hours:.2f} h; P(toucher)={p_yes:.3f}; "
        f"{len(models)} sources; chemin complet {full_ratio:.0%}; dispersion={dispersion:.3f}."
    )
    evidence = [
        f"{m['source']}: p={m['probability']:.3f}, TF={m['timeframe']}, "
        f"chemin={'complet' if m['full_path'] else 'partiel'}, touché={m['already_touched']}"
        for m in models
    ]
    evidence_json = {
        "spec": spec.dict(), "touch_model": "GBM_FIRST_PASSAGE_V1", "horizon_hours": horizon_hours,
        "models": models, "errors": errors, "spot_median": snapshot.spot_median,
        "path_coverage_ratio": full_ratio,
    }
    observations = list(snapshot.observations)
    for model in models:
        observations.append(SourceObservation(
            source=model["source"], value=model["probability"], observed_at=snapshot.observed_at,
            kind="touch_probability_yes", reliability=model["reliability"],
            metadata={"asset": spec.asset, "timeframe": model["timeframe"], "quantitative": True},
        ))
    estimate = EngineEstimate(
        engine="DEGEN_QUANT_V4_CONTINUOUS_TOUCH", probability_yes=p_yes,
        confidence=confidence, reliability=reliability, source_agreement=source_agreement,
        reasoning=reasoning, evidence=evidence, observations=observations, asset=spec.asset,
        volatility=statistics.median(float(m["sigma_remaining"]) for m in models),
        liquidity=market.liquidity_usd, entry_price=market.yes_price,
        exit_price=market.sell_yes_price, spread=max(0.0, market.yes_price-market.sell_yes_price),
        evidence_json=evidence_json,
    )
    if engine.db is not None and run_id is not None:
        engine.db.add_model_prediction(run_id, market, estimate, spec)
    return estimate


def _timed_reference_price(item: SourceSeries, spec, settings, now_ts: float) -> dict:
    """Return a source-local proxy for the price at the exact timed-window start.

    We prefer a candle boundary that exactly matches ``window_start_ts``.  If the
    current candle is intentionally excluded by the data-quality layer, the close
    of the immediately preceding candle is the same boundary and is used instead.
    A tiny spot grace exists only for starts observed essentially in real time.
    No future candle and no post-start arbitrary spot are accepted as the anchor.
    """
    start = int(spec.window_start_ts or 0)
    if start <= 0:
        raise ValueError("début timed invalide")
    if now_ts + 1e-6 < start:
        raise ValueError("fenêtre timed pas encore commencée")

    boundary_tolerance = max(1, int(getattr(settings, "timed_direction_reference_boundary_tolerance_seconds", 3)))
    for timeframe in ("5m", "15m", "1h"):
        seconds = int(TF_SECONDS.get(timeframe, 0) or 0)
        if seconds <= 0:
            continue
        rows = sorted(item.candles.get(timeframe) or [], key=lambda candle: int(candle.ts))
        if not rows:
            continue

        # Preferred case: candle opens at the market window start.
        exact = min(rows, key=lambda candle: abs(int(candle.ts) - start))
        exact_delta = abs(int(exact.ts) - start)
        if exact_delta <= boundary_tolerance:
            value = float(exact.open)
            if value > 0:
                return {
                    "price": value, "timeframe": timeframe, "method": "candle_open",
                    "candle_ts": int(exact.ts), "boundary_error_seconds": exact_delta,
                }

        # Quality code often drops the still-open candle.  The previous close is
        # then the last non-lookahead observation at the same boundary.
        previous = [candle for candle in rows if int(candle.ts) < start]
        if previous:
            candle = previous[-1]
            close_boundary = int(candle.ts) + seconds
            delta = abs(close_boundary - start)
            if delta <= boundary_tolerance:
                value = float(candle.close)
                if value > 0:
                    return {
                        "price": value, "timeframe": timeframe, "method": "previous_candle_close",
                        "candle_ts": int(candle.ts), "boundary_error_seconds": delta,
                    }

    # Very small startup/API timing gap only.  This is deliberately strict: a
    # spot observed minutes after the start must never masquerade as start price.
    grace = max(0, int(getattr(settings, "timed_direction_reference_spot_grace_seconds", 20)))
    seconds_after_start = max(0.0, now_ts - start)
    if seconds_after_start <= grace and float(item.spot or 0.0) > 0:
        return {
            "price": float(item.spot), "timeframe": "spot", "method": "spot_start_grace",
            "candle_ts": None, "boundary_error_seconds": seconds_after_start,
        }

    raise ValueError("prix de référence du début de fenêtre indisponible sans look-ahead")


def _timed_direction_source_probability(item: SourceSeries, spec, settings, now_ts: float) -> dict:
    """Estimate P(YES) for a timed Up/Down market after its window has begun.

    V2 fixes the V1 phase error.  The contract is anchored to the price at the
    beginning of the Jupiter window, while the stochastic horizon is only the
    time still remaining until expiry.  Once price has already moved during the
    window, that realised move is therefore carried into the probability through
    the fixed start reference instead of being discarded.
    """
    start = int(spec.window_start_ts or 0)
    end = int(spec.expiry_ts or 0)
    full_interval_hours = (end - start) / 3600.0 if start > 0 and end > start else 0.0
    if start <= 0 or end <= start or full_interval_hours <= 0 or full_interval_hours > 1.0:
        raise ValueError("fenêtre timed invalide")
    if now_ts < start:
        raise ValueError("fenêtre timed pas encore commencée")
    if now_ts >= end:
        raise ValueError("fenêtre timed terminée")
    if item.spot <= 0:
        raise ValueError("spot timed invalide")

    reference = _timed_reference_price(item, spec, settings, now_ts)
    reference_price = float(reference["price"])
    remaining_seconds = max(1.0, float(end) - float(now_ts))
    remaining_hours = remaining_seconds / 3600.0

    models: list[dict] = []
    for timeframe in ("5m", "15m", "1h"):
        candles = item.candles.get(timeframe) or []
        if len(candles) < 30:
            continue
        try:
            model = _timeframe_model(
                item, timeframe, remaining_hours,
                "above", reference_price, None, settings,
            )
        except Exception:
            continue
        if not bool(model.get("quality_passed", False)):
            continue
        models.append(model)

    if not models:
        raise ValueError("historique timed insuffisant")

    weights = [max(1e-6, float(model.get("timeframe_weight") or 0.0)) for model in models]
    total_weight = sum(weights)
    p_up = sum(float(model["probability"]) * weight for model, weight in zip(models, weights)) / total_weight
    p_up = clamp(p_up, 0.01, 0.99)
    p_yes = p_up if spec.comparator == "above" else 1.0 - p_up
    realised_log_move = math.log(max(1e-12, float(item.spot)) / max(1e-12, reference_price))
    return {
        "source": item.source,
        "probability": clamp(p_yes, 0.01, 0.99),
        "probability_up": p_up,
        "spot": item.spot,
        "reference_price": reference_price,
        "reference_method": reference["method"],
        "reference_timeframe": reference["timeframe"],
        "reference_boundary_error_seconds": reference["boundary_error_seconds"],
        "full_interval_hours": full_interval_hours,
        "remaining_hours": remaining_hours,
        "remaining_seconds": remaining_seconds,
        "seconds_after_start": max(0.0, now_ts - start),
        "realised_log_move_since_start": realised_log_move,
        "timeframes": [model["timeframe"] for model in models],
        "sample_count": min(int(model.get("sample_count") or 0) for model in models),
        "reliability": item.reliability,
        "models": models,
    }


def _timed_direction_estimate(engine, market: Market, spec, snapshot, run_id: int | None) -> EngineEstimate:
    if not bool(getattr(engine.s, "timed_direction_model_enabled", True)):
        return engine._reject("modèle direction timed désactivé", spec.asset)

    start = int(spec.window_start_ts or 0)
    end = int(spec.expiry_ts or 0)
    interval_hours = (end - start) / 3600.0 if start and end > start else 0.0
    if interval_hours <= 0 or interval_hours > 1.0:
        return engine._reject("fenêtre direction timed hors limite", spec.asset)

    now_ts = time.time()
    if now_ts < start:
        estimate = engine._reject(
            f"direction timed V2 en attente du début de fenêtre ({start - now_ts:.0f}s)",
            spec.asset,
        )
        estimate.observations = snapshot.observations
        estimate.evidence_json = {
            "spec": spec.dict(), "timed_model": TIMED_DIRECTION_MODEL_VERSION,
            "phase": "pre_start_wait", "seconds_to_start": start - now_ts,
        }
        return estimate
    if now_ts >= end:
        estimate = engine._reject("direction timed V2: fenêtre déjà terminée", spec.asset)
        estimate.observations = snapshot.observations
        return estimate

    models, errors = [], []
    for item in snapshot.sources:
        try:
            models.append(_timed_direction_source_probability(item, spec, engine.s, now_ts))
        except Exception as exc:
            errors.append(f"{item.source}: {exc}")

    minimum = max(
        int(engine.s.crypto_min_sources),
        int(getattr(engine.s, "timed_direction_min_sources", 2)),
    )
    if len(models) < minimum:
        estimate = engine._reject(f"direction timed V2: {len(models)}/{minimum} source(s)", spec.asset)
        estimate.reasoning = " | ".join(errors)[:900]
        estimate.observations = snapshot.observations
        estimate.evidence_json = {
            "spec": spec.dict(), "timed_model": TIMED_DIRECTION_MODEL_VERSION,
            "phase": "in_window_reference_unavailable",
            "seconds_after_start": now_ts - start, "seconds_before_expiry": end - now_ts,
            "errors": errors,
        }
        return estimate

    probabilities = [float(model["probability"]) for model in models]
    p_yes = statistics.median(probabilities)
    dispersion = max(probabilities) - min(probabilities)
    probability_agreement = clamp(1.0 - dispersion / 0.30, 0.0, 1.0)
    source_agreement = clamp(
        0.60 * float(snapshot.source_agreement) + 0.40 * probability_agreement,
        0.0, 1.0,
    )
    sample_score = clamp(min(model["sample_count"] for model in models) / 180.0, 0.0, 1.0)
    timeframe_score = clamp(
        statistics.fmean(min(1.0, len(model["timeframes"]) / 2.0) for model in models),
        0.0, 1.0,
    )
    reference_score = clamp(
        statistics.fmean(
            1.0 if model["reference_method"] in {"candle_open", "previous_candle_close"} else 0.85
            for model in models
        ),
        0.0, 1.0,
    )
    confidence = clamp(
        0.40 * source_agreement + 0.25 * sample_score + 0.20 * timeframe_score + 0.15 * reference_score,
        0.0, 0.90,
    )
    reliability = clamp(
        statistics.fmean(float(model["reliability"]) for model in models) * source_agreement * reference_score,
        0.0, 0.92,
    )
    direction = "UP" if spec.comparator == "above" else "DOWN"
    seconds_after_start = max(0.0, now_ts - start)
    seconds_before_expiry = max(0.0, end - now_ts)
    references = [float(model["reference_price"]) for model in models]
    reference_median = statistics.median(references)
    reasoning = (
        f"{spec.asset} timed {direction} V2; fenêtre {interval_hours * 60:.0f} min; "
        f"phase +{seconds_after_start:.0f}s, reste {seconds_before_expiry:.0f}s; "
        f"référence début médiane {reference_median:,.6f}$; P(YES)={p_yes:.3f}; "
        f"{len(models)} sources; dispersion={dispersion:.3f}."
    )
    evidence = [
        f"{model['source']}: pYES={model['probability']:.3f}, pUP={model['probability_up']:.3f}, "
        f"start={model['reference_price']:.6f} ({model['reference_method']}/{model['reference_timeframe']}), "
        f"spot={model['spot']:.6f}, reste={model['remaining_seconds']:.0f}s"
        for model in models
    ]
    evidence_json = {
        "spec": spec.dict(),
        "timed_model": TIMED_DIRECTION_MODEL_VERSION,
        "model_name": TIMED_DIRECTION_MODEL_NAME,
        "phase": "in_window_start_anchored",
        "interval_hours": interval_hours,
        "remaining_hours": seconds_before_expiry / 3600.0,
        "seconds_after_start": seconds_after_start,
        "seconds_before_expiry": seconds_before_expiry,
        "reference_price_median": reference_median,
        "models": models,
        "errors": errors,
        "spot_median": snapshot.spot_median,
        "resolution_proxy_warning": "exchange spot/candles proxy the Jupiter resolution feed; start anchor is never fabricated from a late spot",
    }
    observations = list(snapshot.observations)
    for model in models:
        observations.append(SourceObservation(
            source=model["source"], value=model["probability"], observed_at=snapshot.observed_at,
            kind="timed_direction_v2_probability_yes", reliability=model["reliability"],
            metadata={
                "asset": spec.asset, "direction": direction,
                "timeframes": model["timeframes"], "quantitative": True,
                "reference_price": model["reference_price"],
                "reference_method": model["reference_method"],
                "seconds_after_start": seconds_after_start,
                "seconds_before_expiry": seconds_before_expiry,
            },
        ))
    estimate = EngineEstimate(
        engine=TIMED_DIRECTION_MODEL_NAME,
        probability_yes=clamp(p_yes, 0.01, 0.99),
        confidence=confidence,
        reliability=reliability,
        source_agreement=source_agreement,
        reasoning=reasoning,
        evidence=evidence,
        observations=observations,
        asset=spec.asset,
        volatility=statistics.median(
            max(float(x.get("sigma_horizon") or 0.0) for x in model["models"])
            for model in models
        ),
        liquidity=market.liquidity_usd,
        entry_price=market.yes_price,
        exit_price=market.sell_yes_price,
        spread=max(0.0, market.yes_price - market.sell_yes_price),
        evidence_json=evidence_json,
    )
    if engine.db is not None and run_id is not None:
        engine.db.add_model_prediction(run_id, market, estimate, spec)
    return estimate



class CryptoProbabilityEngine:
    name = "DEGEN_QUANT_V4_CONTINUOUS_ENSEMBLE"

    def __init__(self, settings, http, db=None):
        self.s, self.db = settings, db
        self.data = CryptoDataClient(settings, http, db)
        self.derivatives = DerivativesClient(settings, http, db)
        self.context = MarketContextClient(settings, http)
        self.memory = AdaptiveMemory(settings, db) if db is not None else None
        self.neural = NeuralModelManager(settings, db) if db is not None else None

    def enable_timed_fast_mode(self) -> None:
        self.data.enable_timed_fast_mode()

    def estimate_timed_from_snapshot(
        self, market: Market, snapshot: CryptoSnapshot, run_id: int | None = None
    ) -> EngineEstimate:
        """TIMED V2 estimate reusing one already-fetched asset snapshot.

        This is the same TIMED probability path as estimate(), but it reuses the
        already-fetched asset snapshot.  The V1 fail-fast checks for future and
        invalid TIMED windows are intentionally kept in estimate().
        """
        spec = parse_crypto_market(market)
        if spec.ambiguous:
            return self._reject(spec.reject_reason, spec.asset)
        if self.s.require_resolution_source and not spec.resolution_source:
            return self._reject("source de résolution absente", spec.asset)
        if spec.comparator == "exact":
            return self._reject("marché exact sans tolérance explicite", spec.asset)
        now_ts = time.time()
        horizon_hours = (spec.expiry_ts - now_ts) / 3600.0
        if horizon_hours <= 0:
            return self._reject("échéance passée", spec.asset)
        if spec.settlement_kind != "timed_direction":
            return self._reject("marché non direction timed pour FAST", spec.asset)
        try:
            start = int(spec.window_start_ts or 0)
            end = int(spec.expiry_ts or 0)
        except (TypeError, ValueError):
            return self._reject("fenêtre direction timed hors limite", spec.asset)
        interval_hours = (end - start) / 3600.0 if start and end > start else 0.0
        if interval_hours <= 0 or interval_hours > 1.0:
            return self._reject("fenêtre direction timed hors limite", spec.asset)
        if now_ts < start:
            return self._reject(
                f"direction timed V2 en attente du début de fenêtre ({start - now_ts:.0f}s)",
                spec.asset,
            )
        return _timed_direction_estimate(self, market, spec, snapshot, run_id)

    def supports(self, market: Market) -> bool:
        spec = parse_crypto_market(market)
        return not spec.ambiguous and spec.asset in self.s.crypto_assets

    def unsupported_reason(self, market: Market) -> str:
        spec = parse_crypto_market(market)
        return spec.reject_reason or "marché crypto non supporté"

    def _reject(self, reason: str, asset: str = "") -> EngineEstimate:
        return EngineEstimate(engine=self.name, probability_yes=0.5, confidence=0.0, reliability=0.0,
                              source_agreement=0.0, reasoning=reason, evidence=[], observations=[],
                              supported=False, reject_reason=reason, asset=asset)

    def estimate(self, market: Market, run_id: int | None = None) -> EngineEstimate:
        spec = parse_crypto_market(market)
        if spec.ambiguous:
            return self._reject(spec.reject_reason, spec.asset)
        if self.s.require_resolution_source and not spec.resolution_source:
            return self._reject("source de résolution absente", spec.asset)
        if spec.comparator == "exact":
            return self._reject("marché exact sans tolérance explicite", spec.asset)
        now_ts = time.time()
        horizon_hours = (spec.expiry_ts - now_ts) / 3600.0
        if horizon_hours <= 0:
            return self._reject("échéance passée", spec.asset)

        # Fail fast for future/invalid TIMED windows before downloading any
        # exchange candles.  These markets are useful for discovery/storage but
        # cannot be traded yet, and fetching all sources for them used to make a
        # 5-minute scan last far longer than the contract itself.
        if spec.settlement_kind == "timed_direction":
            try:
                start = int(spec.window_start_ts or 0)
                end = int(spec.expiry_ts or 0)
            except (TypeError, ValueError):
                return self._reject("fenêtre direction timed hors limite", spec.asset)
            interval_hours = (end - start) / 3600.0 if start and end > start else 0.0
            if interval_hours <= 0 or interval_hours > 1.0:
                return self._reject("fenêtre direction timed hors limite", spec.asset)
            if now_ts < start:
                estimate = self._reject(
                    f"direction timed V2 en attente du début de fenêtre ({start - now_ts:.0f}s)",
                    spec.asset,
                )
                estimate.evidence_json = {
                    "spec": spec.dict(), "timed_model": TIMED_DIRECTION_MODEL_VERSION,
                    "phase": "pre_start_wait", "seconds_to_start": start - now_ts,
                    "fast_reject_before_market_data": True,
                }
                return estimate

        snapshot = self.data.fetch(spec.asset)
        if spec.settlement_kind == "timed_direction":
            return _timed_direction_estimate(self, market, spec, snapshot, run_id)
        if spec.settlement_kind in {"touch_high", "touch_low"}:
            return _touch_estimate(self, market, spec, snapshot, run_id)
        derivative_objects = self.derivatives.fetch(spec.asset)
        market_context = [x.dict() for x in self.context.fetch(spec.asset)]
        derivative_rows = [x.dict() for x in derivative_objects]
        source_models: list[dict] = []
        model_errors: list[str] = []
        all_tf_features = []
        all_timeframes: set[str] = set()
        threshold = float(spec.threshold_low or 0.0)
        for item in snapshot.sources:
            tf_models = []
            for timeframe in self.s.crypto_timeframes:
                if timeframe not in item.candles:
                    continue
                try:
                    model = _timeframe_model(item, timeframe, horizon_hours, spec.comparator,
                                             threshold, spec.threshold_high, self.s)
                    if model["quality_passed"]:
                        tf_models.append(model)
                        all_timeframes.add(timeframe)
                        all_tf_features.append(model["features"])
                except Exception as exc:
                    model_errors.append(f"{item.source}/{timeframe}: {exc}")
            if not tf_models:
                continue
            weights = [max(0.01, float(x["timeframe_weight"])) for x in tf_models]
            p_source = sum(x["probability"] * w for x, w in zip(tf_models, weights)) / sum(weights)
            source_models.append({
                "source": item.source, "probability": p_source, "spot": item.spot,
                "timeframes": tf_models, "timeframe_count": len(tf_models),
                "sample_count": min(x["sample_count"] for x in tf_models),
                "reliability": item.reliability,
            })
        if len(source_models) < int(self.s.crypto_min_sources):
            estimate = self._reject("modèles quantitatifs multi-sources insuffisants", spec.asset)
            estimate.reasoning = " | ".join(model_errors)[:900]
            estimate.observations = snapshot.observations
            return estimate
        if len(all_timeframes) < int(self.s.ensemble_min_timeframes):
            estimate = self._reject(
                f"ensemble multi-timeframe insuffisant: {len(all_timeframes)}/{self.s.ensemble_min_timeframes}", spec.asset)
            estimate.observations = snapshot.observations
            return estimate

        probabilities = [x["probability"] for x in source_models]
        learned_horizon = horizon_bucket(spec.expiry_ts)
        profile = self.memory.profile(spec.asset, learned_horizon, spec.comparator) if self.memory else None
        source_weights = profile.source_weights if profile and profile.active else {}
        weights = [max(0.01, float(source_weights.get(x["source"].casefold(), 1.0))) for x in source_models]
        quant_probability = sum(x["probability"] * w for x, w in zip(source_models, weights)) / sum(weights)
        derivative_adjustment, derivative_summary = _derivative_adjustment(derivative_rows)
        p_after_derivatives = clamp(quant_probability + derivative_adjustment, 0.001, 0.999)

        primary_tf, _ = _timeframe(horizon_hours)
        ensemble_feature_rows = _ensemble_feature_rows(source_models)
        threshold_distance = math.log(threshold / snapshot.spot_median) if threshold > 0 and snapshot.spot_median > 0 else 0.0
        sigma_candidates = [
            float(tf.get("sigma_horizon", 0.0))
            for source in source_models for tf in source.get("timeframes", [])
            if str(tf.get("timeframe")) == primary_tf and float(tf.get("sigma_horizon", 0.0)) > 0
        ]
        if not sigma_candidates:
            sigma_candidates = [
                float(tf.get("sigma_horizon", 0.0))
                for source in source_models for tf in source.get("timeframes", [])
                if float(tf.get("sigma_horizon", 0.0)) > 0
            ]
        neural_sigma_horizon = statistics.median(sigma_candidates) if sigma_candidates else 0.0
        threshold_z = threshold_distance / max(neural_sigma_horizon, 1e-9)
        neural_features = flatten_features(ensemble_feature_rows, derivative_rows, threshold_distance, horizon_hours) if ensemble_feature_rows else {
            "threshold_distance": threshold_distance, "horizon_hours": horizon_hours,
        }
        neural_features["threshold_z"] = clamp(threshold_z, -8.0, 8.0)
        neural_result = self.neural.predict(f"{spec.asset}:threshold", neural_features) if self.neural else {"available": False}
        if neural_result.get("available"):
            nw = clamp(float(neural_result["weight"]), 0.0, self.s.neural_weight_max)
            p_after_neural = (1.0 - nw) * p_after_derivatives + nw * float(neural_result["probability"])
        else:
            nw = 0.0; p_after_neural = p_after_derivatives
        prior_weight = clamp(float(self.s.model_prior_weight), 0.0, 0.25)
        market_prior = clamp(float(market.yes_price), 0.001, 0.999)
        p_before_learning = (1.0 - prior_weight) * p_after_neural + prior_weight * market_prior
        learned_adjustment = profile.probability_adjustment if profile and profile.active else 0.0
        p_yes = clamp(p_before_learning + learned_adjustment, 0.001, 0.999)

        dispersion = max(probabilities) - min(probabilities)
        probability_agreement = clamp(1.0 - dispersion / max(0.05, self.s.ensemble_max_probability_spread), 0.0, 1.0)
        source_agreement = clamp(0.5 * snapshot.source_agreement + 0.5 * probability_agreement, 0.0, 1.0)
        sample_score = clamp(min(x["sample_count"] for x in source_models) / 300.0, 0.0, 1.0)
        timeframe_score = clamp(len(all_timeframes) / max(3.0, len(self.s.crypto_timeframes)), 0.0, 1.0)
        horizon_score = 1.0 if horizon_hours <= 72 else clamp(1.0 - (horizon_hours - 72) / 168, 0.45, 1.0)
        confidence = clamp(0.27 * source_agreement + 0.25 * sample_score + 0.25 * timeframe_score + 0.23 * horizon_score, 0.0, 0.99)
        if profile and profile.active:
            confidence = clamp(confidence * profile.confidence_multiplier, 0.0, 0.99)
        reliability = clamp(statistics.fmean(x["reliability"] for x in source_models) * source_agreement, 0.0, 0.99)
        volatility = statistics.median(
            tf["sigma_horizon"] for source in source_models for tf in source["timeframes"]
        )
        entry, exit_price = market.yes_price, market.sell_yes_price
        spread = entry - exit_price if exit_price > 0 else 0.0
        threshold_text = (f"{spec.threshold_low:,.2f}–{spec.threshold_high:,.2f}$"
                          if spec.comparator == "between" and spec.threshold_high is not None
                          else f"{threshold:,.2f}$")
        reasoning = (
            f"{spec.asset} {spec.comparator} {threshold_text}; horizon {horizon_hours:.2f} h; "
            f"spot médian {snapshot.spot_median:,.2f}$; ensemble {len(all_timeframes)} TF/{len(source_models)} sources; "
            f"Pquant={quant_probability:.3f}; dérivés={derivative_adjustment:+.3f}; neural poids={nw:.2f}; "
            f"prior marché={market_prior:.3f} poids={prior_weight:.2f}; mémoire={learned_adjustment:+.3f}; "
            f"P(YES)={p_yes:.3f}; dispersion sources={dispersion:.3f}."
        )
        evidence = [f"{x['source']}: p={x['probability']:.3f}, TF={x['timeframe_count']}, spot={x['spot']:.2f}"
                    for x in source_models]
        evidence_json = {
            "spec": spec.dict(), "horizon_hours": horizon_hours, "timeframe": primary_tf,
            "timeframes": sorted(all_timeframes), "spot_median": snapshot.spot_median,
            "spot_dispersion": snapshot.spot_dispersion, "quant_probability": quant_probability,
            "derivative_adjustment": derivative_adjustment, "derivatives": derivative_summary,
            "market_context": market_context,
            "neural": neural_result, "neural_weight": nw, "market_prior": market_prior,
            "market_prior_weight": prior_weight, "probability_before_learning": p_before_learning,
            "adaptive_profile": profile.dict() if profile else {}, "models": source_models,
            "model_errors": model_errors, "feature_vector": neural_features,
        }
        observations: list[SourceObservation] = list(snapshot.observations)
        for model in source_models:
            observations.append(SourceObservation(
                source=model["source"], value=model["probability"], observed_at=snapshot.observed_at,
                kind="multitimeframe_probability_yes", reliability=model["reliability"],
                metadata={"asset": spec.asset, "timeframes": model["timeframe_count"], "quantitative": True},
            ))
        estimate = EngineEstimate(
            engine=self.name, probability_yes=p_yes, confidence=confidence, reliability=reliability,
            source_agreement=source_agreement, reasoning=reasoning, evidence=evidence,
            observations=observations, asset=spec.asset, volatility=volatility,
            liquidity=market.liquidity_usd, entry_price=entry, exit_price=exit_price,
            spread=spread, evidence_json=evidence_json,
        )
        if self.db is not None and run_id is not None:
            self.db.add_model_prediction(run_id, market, estimate, spec)
        return estimate
