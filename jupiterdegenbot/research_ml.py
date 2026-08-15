from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .storage import now

MODEL_SCHEMA_VERSION = "0.2.1-feature-v2"


@dataclass(slots=True)
class NeuralArtifact:
    feature_names: list[str]
    pipeline: Any
    calibrator: Any
    metrics: dict[str, float]
    trained_at: str

    def predict(self, features: dict[str, float]) -> float:
        row = np.asarray([[float(features.get(name, 0.0)) for name in self.feature_names]], dtype=float)
        raw = float(self.pipeline.predict_proba(row)[0, 1])
        raw = min(1 - 1e-6, max(1e-6, raw))
        logit = math.log(raw / (1 - raw))
        calibrated = float(self.calibrator.predict_proba([[logit]])[0, 1]) if self.calibrator is not None else raw
        return min(0.999, max(0.001, calibrated))


def _matrix(rows: list[dict[str, Any]], feature_names: list[str]) -> np.ndarray:
    return np.asarray([[float(row.get(name, 0.0) or 0.0) for name in feature_names] for row in rows], dtype=float)


class NeuralModelManager:
    """Small bounded neural ensemble member.

    The network is never allowed to replace the quantitative model. It becomes
    active only after enough chronological samples and out-of-sample metrics.
    """

    def __init__(self, settings, db):
        self.s, self.db = settings, db
        self.model_dir = Path(settings.neural_model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, NeuralArtifact] = {}
        # v0.2.0 trained threshold_distance as a z-score while live inference
        # supplied a log-distance and also omitted two training-only features.
        # Those artifacts are incompatible and must never remain active.
        with self.db.connect() as conn:
            conn.execute("UPDATE neural_models SET active=0 WHERE active=1 AND version<>?", (MODEL_SCHEMA_VERSION,))

    def train(self, model_key: str, rows: list[dict[str, Any]], labels: list[int],
              *, allow_activation: bool = True,
              external_validation: dict[str, Any] | None = None) -> dict[str, Any]:
        n = len(rows)
        if n < int(self.s.neural_min_train_samples):
            return {"active": False, "reason": f"{n} échantillons < {self.s.neural_min_train_samples}"}
        y = np.asarray(labels, dtype=int)
        if np.unique(y).size < 2:
            return {"active": False, "reason": "une seule classe"}
        feature_names = sorted({key for row in rows for key, value in row.items()
                                if isinstance(value, (int, float, bool)) and key not in {"anchor_ts"}})
        X = _matrix(rows, feature_names)
        # Strict chronological calibration split, grouped by anchor timestamp.
        # Labels whose horizon reaches the calibration window are purged.
        anchor = np.asarray([float(row.get("anchor_ts", i)) for i, row in enumerate(rows)], dtype=float)
        unique_ts = np.unique(anchor)
        if unique_ts.size >= 10 and all("anchor_ts" in row for row in rows):
            split_ts = float(unique_ts[min(unique_ts.size - 1, max(1, int(unique_ts.size * 0.8)))])
            horizon_seconds = np.asarray([float(row.get("horizon_hours", 0.0)) * 3600.0 for row in rows])
            train_mask = anchor + horizon_seconds < split_ts
            cal_mask = anchor >= split_ts
            X_train, X_cal = X[train_mask], X[cal_mask]
            y_train, y_cal = y[train_mask], y[cal_mask]
        else:
            split = max(int(n * 0.8), int(self.s.neural_min_train_samples * 0.7))
            split = min(split, n - max(30, int(n * 0.1)))
            X_train, X_cal = X[:split], X[split:]
            y_train, y_cal = y[:split], y[split:]
        if np.unique(y_train).size < 2 or np.unique(y_cal).size < 2:
            return {"active": False, "reason": "classes insuffisantes dans le découpage chronologique"}
        pipeline = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("mlp", MLPClassifier(
                hidden_layer_sizes=self.s.neural_hidden_layer_sizes,
                activation="relu", solver="adam", alpha=0.002,
                learning_rate_init=0.001, max_iter=int(self.s.neural_max_iter),
                early_stopping=True, validation_fraction=0.15,
                n_iter_no_change=20, random_state=42,
            )),
        ])
        pipeline.fit(X_train, y_train)
        raw = np.clip(pipeline.predict_proba(X_cal)[:, 1], 1e-6, 1 - 1e-6)
        logits = np.log(raw / (1 - raw)).reshape(-1, 1)
        calibrator = LogisticRegression(C=0.5, random_state=42).fit(logits, y_cal)
        calibrated = np.clip(calibrator.predict_proba(logits)[:, 1], 1e-6, 1 - 1e-6)
        baseline = np.full_like(calibrated, y_train.mean(), dtype=float)
        metrics = {
            "brier": float(brier_score_loss(y_cal, calibrated)),
            "log_loss": float(log_loss(y_cal, calibrated, labels=[0, 1])),
            "baseline_brier": float(brier_score_loss(y_cal, baseline)),
            "baseline_log_loss": float(log_loss(y_cal, baseline, labels=[0, 1])),
            "auc": float(roc_auc_score(y_cal, calibrated)),
            "samples": float(n), "positive_rate": float(y.mean()),
        }
        baseline_pass = (metrics["brier"] < metrics["baseline_brier"] and
                         metrics["log_loss"] < metrics["baseline_log_loss"])
        with self.db.connect(readonly=True) as conn:
            champion = conn.execute(
                "SELECT brier_score,log_loss,artifact_path FROM neural_models "
                "WHERE model_key=? AND active=1 ORDER BY id DESC LIMIT 1", (model_key,),
            ).fetchone()
        beats_champion = True
        if champion is not None:
            old_brier = float(champion["brier_score"] or 1.0)
            old_log = float(champion["log_loss"] or 99.0)
            beats_champion = (metrics["brier"] <= old_brier and metrics["log_loss"] <= old_log and
                              (metrics["brier"] < old_brier or metrics["log_loss"] < old_log))
        external_validation = dict(external_validation or {})
        external_pass = bool(external_validation.get("passed", allow_activation))
        promoted = bool(allow_activation and external_pass and baseline_pass and beats_champion)
        metrics["walk_forward_activation_allowed"] = bool(allow_activation)
        metrics["external_walk_forward_pass"] = bool(external_pass)
        metrics["external_walk_forward"] = external_validation
        metrics["baseline_pass"] = bool(baseline_pass)
        metrics["beats_champion"] = bool(beats_champion)
        artifact = NeuralArtifact(feature_names, pipeline, calibrator, metrics, now())
        safe_key = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in model_key)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = "champion" if promoted else "candidate"
        path = self.model_dir / f"{safe_key}_{stamp}_{suffix}.joblib"
        joblib.dump(artifact, path)
        with self.db.connect() as conn:
            if promoted:
                conn.execute("UPDATE neural_models SET active=0 WHERE model_key=?", (model_key,))
            conn.execute(
                """INSERT INTO neural_models(model_key,model_type,version,train_samples,positive_rate,
                   brier_score,log_loss,auc,artifact_path,feature_names_json,metrics_json,active,trained_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (model_key, "MLP+PLATT", MODEL_SCHEMA_VERSION, n, float(y.mean()), metrics["brier"], metrics["log_loss"],
                 metrics["auc"], str(path), json.dumps(feature_names), json.dumps(metrics), int(promoted), now()),
            )
        if promoted:
            self._cache[model_key] = artifact
        return {"active": promoted, "promoted": promoted, "model_key": model_key, "path": str(path),
                "baseline_pass": baseline_pass, "beats_champion": beats_champion,
                "activation_allowed": bool(allow_activation), "external_validation_pass": bool(external_pass), **metrics}

    def _load(self, model_key: str) -> NeuralArtifact | None:
        if model_key in self._cache:
            return self._cache[model_key]
        with self.db.connect(readonly=True) as conn:
            row = conn.execute(
                "SELECT * FROM neural_models WHERE model_key=? AND active=1 AND version=? ORDER BY id DESC LIMIT 1",
                (model_key, MODEL_SCHEMA_VERSION),
            ).fetchone()
        if not row:
            return None
        path = Path(str(row["artifact_path"] or ""))
        if not path.exists():
            # A version migration can move the project while preserving the
            # compatible model files. Resolve by filename inside the current
            # configured model directory and repair the stored absolute path.
            relocated = self.model_dir / path.name if path.name else Path()
            if not relocated.is_file():
                return None
            path = relocated
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE neural_models SET artifact_path=? WHERE id=?",
                    (str(path), int(row["id"])),
                )
        artifact = joblib.load(path)
        if not isinstance(artifact, NeuralArtifact):
            return None
        self._cache[model_key] = artifact
        return artifact

    def predict(self, model_key: str, features: dict[str, float]) -> dict[str, Any]:
        if not self.s.neural_enabled:
            return {"available": False, "reason": "désactivé"}
        artifact = self._load(model_key)
        if artifact is None:
            return {"available": False, "reason": "aucun modèle neuronal actif"}
        probability = artifact.predict(features)
        improvement = max(0.0, artifact.metrics.get("baseline_brier", 0.25) - artifact.metrics.get("brier", 0.25))
        weight = min(float(self.s.neural_weight_max), 0.05 + improvement * 2.0)
        return {"available": True, "probability": probability, "weight": weight,
                "metrics": artifact.metrics, "trained_at": artifact.trained_at}
