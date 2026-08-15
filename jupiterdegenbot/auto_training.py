from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .backtest import WalkForwardResearch
from .memory import available_gb

log = logging.getLogger(__name__)


def _ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


class AutoNeuralTrainer:
    """Daily, label-triggered retraining with the same walk-forward promotion gate."""

    def __init__(self, settings, db):
        self.s, self.db = settings, db

    def status(self) -> dict[str, Any]:
        latest = self.db.latest_auto_training()
        last_finished = _ts(latest["finished_at"] if latest else None)
        with self.db.connect(readonly=True) as conn:
            active = int(conn.execute("SELECT COUNT(*) n FROM neural_models WHERE active=1").fetchone()["n"] or 0)
            history_rows = int(conn.execute("SELECT COUNT(*) n FROM candles").fetchone()["n"] or 0)
            if last_finished > 0:
                new_labels = int(conn.execute(
                    "SELECT COUNT(*) n FROM shadow_predictions WHERE status='RESOLVED' AND resolved_at>?",
                    (datetime.fromtimestamp(last_finished, timezone.utc).isoformat(),),
                ).fetchone()["n"] or 0)
            else:
                new_labels = int(conn.execute(
                    "SELECT COUNT(*) n FROM shadow_predictions WHERE status='RESOLVED'"
                ).fetchone()["n"] or 0)
        elapsed_hours = (datetime.now(timezone.utc).timestamp() - last_finished) / 3600.0 if last_finished else None
        due_time = last_finished == 0 or (elapsed_hours or 0) >= float(self.s.auto_neural_train_interval_hours)
        due_labels = new_labels >= int(self.s.auto_neural_train_min_new_labels)
        min_history_rows = max(1000, int(getattr(self.s, "research_min_history_candles", 240)) * 5)
        history_ready = history_rows >= min_history_rows
        return {
            "enabled": bool(self.s.auto_neural_train_enabled), "active_models": active,
            "new_labels": new_labels, "last_finished_at": latest["finished_at"] if latest else None,
            "last_status": latest["status"] if latest else None, "elapsed_hours": elapsed_hours,
            "due_time": due_time, "due_labels": due_labels,
            "history_rows": history_rows, "history_ready": history_ready,
            "minimum_history_rows": min_history_rows,
            "ready": bool(self.s.auto_neural_train_enabled and history_ready and due_time and (due_labels or active == 0)),
        }

    def maybe_run(self) -> dict[str, Any]:
        state = self.status()
        if not state["ready"]:
            return {"ran": False, **state}
        if available_gb() < float(self.s.auto_neural_train_required_free_gb):
            return {"ran": False, "reason": "mémoire libre insuffisante", **state}
        reason = "aucun champion actif" if state["active_models"] == 0 else f"{state['new_labels']} nouveaux labels"
        run_id = self.db.start_auto_training(reason, int(state["new_labels"]))
        try:
            result = WalkForwardResearch(self.s, self.db).run(
                max_rows_per_asset=int(self.s.auto_neural_train_max_rows_per_asset)
            )
            self.db.finish_auto_training(run_id, "ok", result)
            log.info("AUTO NEURAL TRAIN terminé: %s", result.get("status"))
            return {"ran": True, "status": "ok", "reason": reason, "result": result}
        except Exception as exc:
            payload = {"error": str(exc)}
            self.db.finish_auto_training(run_id, "failed", payload)
            log.exception("AUTO NEURAL TRAIN en erreur")
            return {"ran": True, "status": "failed", "reason": reason, **payload}
