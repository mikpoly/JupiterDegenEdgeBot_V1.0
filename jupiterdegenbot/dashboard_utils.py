from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd


def ensure_unique_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy whose column labels are always unique.

    PyArrow rejects duplicate labels even when pandas accepts them. The
    dashboard uses this as a final safety net after SQL joins and selections.
    """
    if df is None:
        return pd.DataFrame()
    result = df.copy()
    seen: dict[str, int] = {}
    labels: list[str] = []
    for raw in result.columns:
        label = str(raw)
        count = seen.get(label, 0)
        labels.append(label if count == 0 else f"{label}__{count + 1}")
        seen[label] = count + 1
    result.columns = labels
    return result


def display_frame(df: pd.DataFrame, columns: Iterable[str], rename: Mapping[str, str] | None = None) -> pd.DataFrame:
    """Select existing columns once, preserving the requested order."""
    safe = ensure_unique_columns(df)
    selected: list[str] = []
    used: set[str] = set()
    for raw in columns:
        name = str(raw)
        if name in safe.columns and name not in used:
            selected.append(name)
            used.add(name)
    result = safe.loc[:, selected].copy() if selected else pd.DataFrame(index=safe.index)
    if rename:
        result = result.rename(columns=dict(rename))
    return ensure_unique_columns(result)


def parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if pd.isna(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        number = int(float(value))
        return number
    except (TypeError, ValueError):
        return default


def learning_progress(sample_count: int, target: int) -> float:
    target = max(1, int(target))
    return max(0.0, min(1.0, int(sample_count) / target))


def learning_stage(sample_count: int, target: int, active_profiles: int = 0) -> str:
    if active_profiles > 0:
        return "ADAPTATIF ACTIF"
    progress = learning_progress(sample_count, target)
    if progress <= 0:
        return "COLLECTE EN ATTENTE"
    if progress < 0.5:
        return "COLLECTE INITIALE"
    if progress < 1.0:
        return "CALIBRATION EN COURS"
    return "PRÊT À ACTIVER UN PROFIL"


def selected_probability_breakdown(outcome: str, selected_probability: float) -> tuple[float, float]:
    selected = max(0.0, min(1.0, as_float(selected_probability)))
    if str(outcome or "").upper() == "NO":
        return 1.0 - selected, selected
    return selected, 1.0 - selected


def decision_summary_fr(row: Mapping[str, Any] | pd.Series) -> str:
    data = dict(row)
    outcome = str(data.get("outcome") or "?").upper()
    selected_probability = as_float(data.get("selected_probability", data.get("probability")))
    yes_probability, no_probability = selected_probability_breakdown(outcome, selected_probability)
    entry = as_float(data.get("entry_price", data.get("paper_entry_price", data.get("price"))))
    edge = as_float(data.get("edge"), selected_probability - entry)
    title = str(data.get("event_title") or data.get("question") or data.get("market_id") or "marché")
    signal_question = str(data.get("signal_question") or "").strip()
    if signal_question and signal_question.casefold() not in title.casefold():
        title = f"{title} — {signal_question}"
    likelihood_note = ""
    opposite = no_probability if outcome == "YES" else yes_probability
    if selected_probability < opposite:
        likelihood_note = (
            f" {outcome} n'est pas l'issue la plus probable, mais elle est retenue parce que "
            "sa probabilité estimée reste supérieure à son prix de marché."
        )
    else:
        likelihood_note = (
            f" {outcome} est aussi l'issue la plus probable selon le modèle et son prix de marché "
            "reste inférieur à cette estimation."
        )
    return (
        f"Position {outcome} sur « {title} ». "
        f"Probabilité modèle : YES {yes_probability * 100:.2f} %, NO {no_probability * 100:.2f} %. "
        f"Prix d'entrée {outcome} : {entry * 100:.2f} %. "
        f"Edge estimé : {edge * 100:+.2f} points.{likelihood_note}"
    )


def ai_explanation_from_row(row: Mapping[str, Any] | pd.Series) -> str:
    data = dict(row)
    explicit = str(data.get("ai_explanation") or "").strip()
    if explicit:
        return explicit
    evidence = parse_json_dict(data.get("evidence_json"))
    review = evidence.get("local_ai_review")
    if isinstance(review, dict):
        return str(review.get("explanation_fr") or "").strip()
    return ""


def model_sources_from_row(row: Mapping[str, Any] | pd.Series) -> list[str]:
    evidence = parse_json_dict(dict(row).get("evidence_json"))
    sources: list[str] = []
    for item in evidence.get("models") or []:
        if isinstance(item, dict):
            source = str(item.get("source") or "").strip()
            if source and source not in sources:
                sources.append(source)
    return sources


def settled_counts_by_asset(orders_paper: pd.DataFrame, assets: Iterable[str]) -> dict[str, int]:
    result = {str(asset).upper(): 0 for asset in assets}
    if orders_paper is None or orders_paper.empty or "asset" not in orders_paper.columns:
        return result
    frame = ensure_unique_columns(orders_paper)
    result_column = "paper_result" if "paper_result" in frame.columns else None
    if result_column:
        frame = frame[frame[result_column].astype(str).str.upper().isin({"WON", "LOST"})]
    counts = frame["asset"].astype(str).str.upper().value_counts()
    for asset in result:
        result[asset] = int(counts.get(asset, 0))
    return result
