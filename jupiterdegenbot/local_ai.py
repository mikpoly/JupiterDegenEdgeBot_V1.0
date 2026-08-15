from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import requests

from .market_parser import parse_crypto_market
from .models import EngineEstimate, Market, Signal
from .probability import clamp
from .dashboard_utils import decision_summary_fr


@dataclass(slots=True)
class AIReview:
    model: str
    verdict: str
    explanation_fr: str
    risk_flags: list[str] = field(default_factory=list)
    confidence_penalty: float = 0.0
    reliability_penalty: float = 0.0
    available: bool = True
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    def dict(self) -> dict[str, Any]:
        return asdict(self)


_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "caution", "reject"]},
        "explanation_fr": {"type": "string"},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "confidence_penalty": {"type": "number", "minimum": 0, "maximum": 0.25},
        "reliability_penalty": {"type": "number", "minimum": 0, "maximum": 0.25},
    },
    "required": [
        "verdict", "explanation_fr", "risk_flags",
        "confidence_penalty", "reliability_penalty",
    ],
}

_HARD_FLAGS = {
    "asset_mismatch", "comparator_mismatch", "threshold_mismatch", "expiry_mismatch",
    "resolution_source_mismatch", "rules_ambiguous", "missing_critical_data",
    "probability_direction_contradiction",
}


class LocalAIReviewer:
    """Local Ollama guard that can only reduce risk, never create an edge."""

    def __init__(self, settings):
        self.s = settings
        self.base_url = str(getattr(settings, "ollama_base_url", "http://127.0.0.1:11434")).rstrip("/")
        self.model = str(getattr(settings, "ollama_model", "qwen2.5:1.5b-instruct-q4_K_M"))
        self.session = requests.Session()
        self.review_count = 0

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.s, "local_ai_enabled", True))

    def reset_cycle(self) -> None:
        self.review_count = 0

    def status(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "available": False, "model": self.model, "detail": "LOCAL_AI_ENABLED=false"}
        timeout = min(10.0, max(2.0, float(getattr(self.s, "local_ai_timeout_seconds", 180.0))))
        try:
            response = self.session.get(f"{self.base_url}/api/tags", timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            names = [str(row.get("name") or row.get("model") or "") for row in payload.get("models", []) if isinstance(row, dict)]
            installed = any(name == self.model for name in names)
            return {
                "enabled": True, "available": True, "model": self.model,
                "installed": installed, "models": names,
                "detail": "Ollama répond" if installed else "Ollama répond mais le modèle demandé est absent",
            }
        except Exception as exc:
            return {"enabled": True, "available": False, "model": self.model, "installed": False, "detail": str(exc)}

    def _deterministic_review(self, market: Market, estimate: EngineEstimate, signal: Signal) -> AIReview | None:
        spec = parse_crypto_market(market)
        flags: list[str] = []
        evidence_spec = estimate.evidence_json.get("spec") if isinstance(estimate.evidence_json, dict) else None
        if isinstance(evidence_spec, dict):
            if str(evidence_spec.get("asset") or "") != spec.asset:
                flags.append("asset_mismatch")
            if str(evidence_spec.get("comparator") or "") != spec.comparator:
                flags.append("comparator_mismatch")
            for key, expected in (("threshold_low", spec.threshold_low), ("threshold_high", spec.threshold_high)):
                actual = evidence_spec.get(key)
                if expected is None and actual is None:
                    continue
                try:
                    if abs(float(actual) - float(expected)) > max(0.01, abs(float(expected)) * 1e-8):
                        flags.append("threshold_mismatch")
                except (TypeError, ValueError):
                    flags.append("threshold_mismatch")
            try:
                if abs(int(evidence_spec.get("expiry_ts")) - int(spec.expiry_ts)) > 60:
                    flags.append("expiry_mismatch")
            except (TypeError, ValueError):
                flags.append("expiry_mismatch")
        if not (0.0 < estimate.probability_yes < 1.0):
            flags.append("missing_critical_data")
        if signal.outcome == "YES" and abs(signal.probability - estimate.probability_yes) > 1e-6:
            flags.append("probability_direction_contradiction")
        if signal.outcome == "NO" and abs(signal.probability - (1.0 - estimate.probability_yes)) > 1e-6:
            flags.append("probability_direction_contradiction")
        if flags:
            return AIReview(
                model="deterministic_guard", verdict="reject",
                explanation_fr="Incohérence interne vérifiable entre le marché, le modèle quantitatif et le signal.",
                risk_flags=sorted(set(flags)), confidence_penalty=0.25,
                reliability_penalty=0.25, available=True,
                raw={"deterministic": True},
            )
        return None

    def review(self, market: Market, estimate: EngineEstimate, signal: Signal) -> AIReview:
        deterministic = self._deterministic_review(market, estimate, signal)
        if deterministic is not None:
            return deterministic
        if not self.enabled:
            return AIReview(self.model, "unavailable", "IA locale désactivée.", available=False)
        remaining_minutes = max(0.0, (float(market.close_time or 0) - time.time()) / 60.0)
        minimum_for_ollama = max(0.0, float(getattr(self.s, "local_ai_min_horizon_minutes", 20.0)))
        if remaining_minutes < minimum_for_ollama:
            summary = decision_summary_fr({
                "outcome": signal.outcome, "selected_probability": signal.probability,
                "entry_price": signal.price, "edge": signal.edge,
                "event_title": market.event_title, "signal_question": market.question,
                "market_id": market.id,
            })
            return AIReview(
                model="deterministic_fast_guard", verdict="pass",
                explanation_fr=(summary + " Contrôle rapide déterministe validé; Ollama ignoré "
                                "pour ne pas dépasser l'échéance courte."),
                available=True, latency_ms=0,
                raw={"deterministic": True, "short_horizon_minutes": remaining_minutes},
            )
        max_reviews = max(0, int(getattr(self.s, "local_ai_max_reviews_per_cycle", 3)))
        if max_reviews and self.review_count >= max_reviews:
            return AIReview(self.model, "unavailable", "Limite de revues IA du cycle atteinte.", available=False)
        self.review_count += 1

        spec = parse_crypto_market(market)
        evidence = estimate.evidence_json if isinstance(estimate.evidence_json, dict) else {}
        compact_models = []
        for row in (evidence.get("models") or [])[:4]:
            if isinstance(row, dict):
                compact_models.append({
                    "source": row.get("source"), "spot": row.get("spot"),
                    "probability": row.get("probability"), "sigma_horizon": row.get("sigma_horizon"),
                    "mu_horizon": row.get("mu_horizon"), "rsi": row.get("rsi"),
                    "atr_pct": row.get("atr_pct"), "sample_count": row.get("sample_count"),
                })
        input_payload = {
            "market": {
                "event_title": market.event_title, "question": market.question,
                "rules": market.rules[:1800], "resolution_source": market.resolution_source,
                "yes_price": market.yes_price, "no_price": market.no_price,
                "sell_yes_price": market.sell_yes_price, "sell_no_price": market.sell_no_price,
                "close_time": market.close_time,
            },
            "parsed_spec": spec.dict(),
            "quant": {
                "probability_yes": estimate.probability_yes,
                "confidence": estimate.confidence,
                "reliability": estimate.reliability,
                "source_agreement": estimate.source_agreement,
                "reasoning": estimate.reasoning,
                "models": compact_models,
                "spot_median": evidence.get("spot_median"),
                "spot_dispersion": evidence.get("spot_dispersion"),
            },
            "candidate": {
                "outcome": signal.outcome, "entry_price": signal.price,
                "exit_price": signal.exit_price, "probability": signal.probability,
                "edge": signal.edge, "stake_usd": signal.stake_usd,
            },
        }
        system = (
            "Tu es un contrôleur de cohérence pour un bot PAPER de marchés prédictifs crypto. "
            "Tu ne calcules jamais une nouvelle probabilité, tu ne proposes jamais un pari et tu n'augmentes jamais la confiance. "
            "Vérifie les contradictions explicites entre question, règles, actif, seuil, échéance, source de résolution, "
            "sens YES/NO et données quantitatives. Dans explanation_fr, parle obligatoirement du côté réellement sélectionné, "
            "de sa probabilité, de son prix d'entrée et de l'edge fournis dans candidate. Explique clairement qu'un côté peut être "
            "acheté pour sa sous-évaluation même s'il n'est pas l'issue la plus probable. Ne remplace jamais ces valeurs et ne les "
            "recalcule pas. PASS si cohérent. CAUTION pour une faiblesse réelle mais non bloquante. REJECT uniquement pour une "
            "contradiction claire ou une donnée critique manquante. Réponds strictement selon le schéma JSON."
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))},
            ],
            "stream": False,
            "format": _SCHEMA,
            "keep_alive": str(getattr(self.s, "ollama_keep_alive", "2m")),
            "options": {
                "temperature": 0,
                "seed": 42,
                "num_ctx": max(1024, int(getattr(self.s, "local_ai_num_ctx", 2048))),
                "num_predict": max(128, int(getattr(self.s, "local_ai_num_predict", 320))),
            },
        }
        started = time.monotonic()
        try:
            response = self.session.post(
                f"{self.base_url}/api/chat", json=payload,
                timeout=max(10.0, float(getattr(self.s, "local_ai_timeout_seconds", 180.0))),
            )
            response.raise_for_status()
            outer = response.json()
            content = ((outer.get("message") or {}).get("content") or "{}").strip()
            parsed = json.loads(content)
            verdict = str(parsed.get("verdict") or "caution").casefold()
            if verdict not in {"pass", "caution", "reject"}:
                verdict = "caution"
            flags = [str(x).strip().casefold() for x in (parsed.get("risk_flags") or []) if str(x).strip()][:12]
            if verdict == "reject" and not set(flags).intersection(_HARD_FLAGS):
                verdict = "caution"
            confidence_penalty = clamp(float(parsed.get("confidence_penalty") or 0.0), 0.0, 0.25)
            reliability_penalty = clamp(float(parsed.get("reliability_penalty") or 0.0), 0.0, 0.25)
            if verdict == "pass":
                confidence_penalty = 0.0
                reliability_penalty = 0.0
            elif verdict == "caution":
                confidence_penalty = min(confidence_penalty, 0.12)
                reliability_penalty = min(reliability_penalty, 0.12)
            model_explanation = str(parsed.get("explanation_fr") or "Revue IA sans explication.").strip()[:900]
            deterministic_summary = decision_summary_fr({
                "outcome": signal.outcome,
                "selected_probability": signal.probability,
                "entry_price": signal.price,
                "edge": signal.edge,
                "event_title": market.event_title,
                "signal_question": market.question,
                "market_id": market.id,
            })
            explanation = f"{deterministic_summary} Contrôle IA : {model_explanation}"[:1800]
            return AIReview(
                model=self.model, verdict=verdict, explanation_fr=explanation,
                risk_flags=flags, confidence_penalty=confidence_penalty,
                reliability_penalty=reliability_penalty, available=True,
                latency_ms=int((time.monotonic() - started) * 1000), raw=outer,
            )
        except Exception as exc:
            return AIReview(
                model=self.model, verdict="unavailable",
                explanation_fr=f"Ollama indisponible: {exc}", available=False,
                latency_ms=int((time.monotonic() - started) * 1000), raw={"error": str(exc)},
            )


def apply_ai_review(settings, estimate: EngineEstimate, review: AIReview) -> EngineEstimate:
    required = bool(getattr(settings, "local_ai_required_for_new_signal", False))
    if not review.available:
        if required:
            return replace(
                estimate, supported=False, reject_reason="revue IA locale obligatoire indisponible",
                reasoning=estimate.reasoning + f" IA locale: {review.explanation_fr}",
                evidence=[*estimate.evidence, f"IA locale: {review.explanation_fr}"],
                evidence_json={**estimate.evidence_json, "local_ai_review": review.dict()},
            )
        return replace(
            estimate,
            reasoning=estimate.reasoning + f" IA locale non disponible; quantitatif conservé: {review.explanation_fr}",
            evidence=[*estimate.evidence, f"IA locale indisponible: {review.explanation_fr}"],
            evidence_json={**estimate.evidence_json, "local_ai_review": review.dict()},
        )

    if review.verdict == "reject" and bool(getattr(settings, "local_ai_allow_veto", True)):
        return replace(
            estimate, supported=False, reject_reason="incohérence détectée par le garde IA local",
            confidence=max(0.0, estimate.confidence - review.confidence_penalty),
            reliability=max(0.0, estimate.reliability - review.reliability_penalty),
            reasoning=estimate.reasoning + f" IA locale REJECT: {review.explanation_fr}",
            evidence=[*estimate.evidence, f"IA locale REJECT: {review.explanation_fr}"],
            evidence_json={**estimate.evidence_json, "local_ai_review": review.dict()},
        )

    return replace(
        estimate,
        confidence=max(0.0, estimate.confidence - review.confidence_penalty),
        reliability=max(0.0, estimate.reliability - review.reliability_penalty),
        reasoning=estimate.reasoning + f" IA locale {review.verdict.upper()}: {review.explanation_fr}",
        evidence=[*estimate.evidence, f"IA locale {review.verdict.upper()}: {review.explanation_fr}"],
        evidence_json={**estimate.evidence_json, "local_ai_review": review.dict()},
    )
