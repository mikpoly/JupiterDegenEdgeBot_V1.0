from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from .storage import now
from .research_ml import MODEL_SCHEMA_VERSION


class LiveValidationGate:
    """Statistical gate evaluated from real PAPER outcomes and walk-forward tests."""

    def __init__(self, settings, db):
        self.s, self.db = settings, db

    def evaluate(self, persist: bool = True) -> dict[str, Any]:
        reasons: list[str] = []
        with self.db.connect(readonly=True) as conn:
            paper = conn.execute(
                """SELECT COUNT(*) settled,
                   COALESCE(SUM(CASE WHEN paper_result='WON' THEN 1 ELSE 0 END),0) won,
                   COALESCE(SUM(paper_pnl_usd),0) pnl,
                   COALESCE(SUM(amount_usd),0) staked
                   FROM orders WHERE mode='paper' AND paper_result IN ('WON','LOST')"""
            ).fetchone()
            cal = conn.execute(
                "SELECT * FROM calibration_results WHERE asset='ALL' AND horizon='ALL' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            paper_predictions = conn.execute(
                """SELECT s.probability,s.price,o.paper_result,o.asset
                   FROM orders o JOIN signals s ON s.id=o.signal_id
                   WHERE o.mode='paper' AND o.paper_result IN ('WON','LOST')
                     AND s.probability IS NOT NULL AND s.price IS NOT NULL"""
            ).fetchall()
            neural = conn.execute(
                "SELECT model_key,brier_score,log_loss FROM neural_models WHERE active=1 AND version=?",
                (MODEL_SCHEMA_VERSION,),
            ).fetchall()
            validation = conn.execute(
                "SELECT * FROM validation_runs WHERE kind='walk_forward_neural' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            critical = conn.execute(
                "SELECT COUNT(*) n FROM incidents WHERE severity IN ('critical','fatal') AND created_at>=datetime('now','-30 day')"
            ).fetchone()
            quality = conn.execute(
                """SELECT asset,source,timeframe,MAX(id) id FROM data_quality
                   GROUP BY asset,source,timeframe"""
            ).fetchall()
            required_quality_assets = set(self.s.research_history_assets if self.s.live_gate_require_all_assets
                                          else self.s.research_core_assets)
            quality_by_key: dict[tuple[str, str], dict[str, int]] = {}
            ignored_extended_quality = 0
            for item in quality:
                asset = str(item["asset"] or "").upper()
                timeframe = str(item["timeframe"] or "")
                q = conn.execute("SELECT passed FROM data_quality WHERE id=?", (item["id"],)).fetchone()
                passed = int(bool(q["passed"])) if q else 0
                if asset in required_quality_assets:
                    bucket = quality_by_key.setdefault((asset, timeframe), {"passed": 0, "total": 0})
                    bucket["passed"] += passed
                    bucket["total"] += 1
                elif not passed:
                    ignored_extended_quality += 1
            required_timeframes = list(self.s.research_primary_timeframes)
            quality_failures = []
            quality_quorum = {}
            for asset in sorted(required_quality_assets):
                for timeframe in required_timeframes:
                    bucket = quality_by_key.get((asset, timeframe), {"passed": 0, "total": 0})
                    quality_quorum[f"{asset}:{timeframe}"] = bucket
                    if int(bucket["passed"]) < int(self.s.live_gate_min_quality_sources):
                        quality_failures.append(
                            f"{asset}/{timeframe}: {bucket['passed']}/{self.s.live_gate_min_quality_sources} sources valides"
                        )
            failed_quality = len(quality_failures)
        settled = int(paper["settled"] or 0)
        staked = float(paper["staked"] or 0.0)
        paper_roi = float(paper["pnl"] or 0.0) / staked if staked > 0 else 0.0
        brier = float(cal["brier_score"]) if cal and cal["brier_score"] is not None else None
        logloss = float(cal["log_loss"]) if cal and cal["log_loss"] is not None else None
        def score(rows, key):
            if not rows:
                return None, None
            brier_total = 0.0; log_total = 0.0
            for row in rows:
                probability = max(1e-6, min(1 - 1e-6, float(row[key])))
                actual = 1 if str(row["paper_result"]) == "WON" else 0
                brier_total += (probability - actual) ** 2
                log_total += -(actual * math.log(probability) + (1 - actual) * math.log(1 - probability))
            return brier_total / len(rows), log_total / len(rows)
        model_brier_direct, model_log_direct = score(paper_predictions, "probability")
        market_brier, market_logloss = score(paper_predictions, "price")
        brier_skill = ((market_brier - model_brier_direct)
                       if market_brier is not None and model_brier_direct is not None else None)
        active_neural = len(neural)
        active_neural_assets = {str(row["model_key"]).split(":", 1)[0].upper() for row in neural}
        if settled < self.s.live_gate_min_settled:
            reasons.append(f"PAPER réglés {settled}/{self.s.live_gate_min_settled}")
        if brier is None or brier > self.s.live_gate_max_brier:
            reasons.append(f"Brier PAPER {brier if brier is not None else 'absent'} > {self.s.live_gate_max_brier}")
        if logloss is None or logloss > self.s.live_gate_max_log_loss:
            reasons.append(f"log-loss PAPER {logloss if logloss is not None else 'absente'} > {self.s.live_gate_max_log_loss}")
        if paper_roi < self.s.live_gate_min_paper_roi:
            reasons.append(
                f"ROI PAPER {paper_roi:.2%} < {self.s.live_gate_min_paper_roi:.2%}"
            )
        if brier_skill is None or brier_skill < self.s.live_gate_min_brier_skill:
            reasons.append(
                f"skill Brier vs prix marché {brier_skill if brier_skill is not None else 'absent'} "
                f"< {self.s.live_gate_min_brier_skill}"
            )
        if self.s.live_gate_require_neural_model and active_neural < self.s.live_gate_min_active_neural_models:
            reasons.append(
                f"modèles neuronaux actifs {active_neural}/{self.s.live_gate_min_active_neural_models}"
            )
        missing_core_neural = []
        if self.s.live_gate_require_neural_model and self.s.live_gate_require_core_neural_models:
            missing_core_neural = sorted(set(self.s.research_core_assets) - active_neural_assets)
            if missing_core_neural:
                reasons.append("modèles neuronaux core absents: " + ", ".join(missing_core_neural))
        if not validation:
            reasons.append("aucun backtest walk-forward")
            validation_age = None
        else:
            if str(validation["status"]) != "passed":
                reasons.append("dernier walk-forward non validé")
            if int(validation["trade_count"] or 0) < self.s.live_gate_min_backtest_trades:
                reasons.append(f"trades backtest {int(validation['trade_count'] or 0)}/{self.s.live_gate_min_backtest_trades}")
            if float(validation["brier_score"] or 1.0) > self.s.live_gate_max_brier:
                reasons.append("Brier walk-forward trop élevé")
            if float(validation["log_loss"] or 99.0) > self.s.live_gate_max_log_loss:
                reasons.append("log-loss walk-forward trop élevée")
            # The historical dataset creates correlated pseudo-markets at a fixed
            # synthetic price of 0.50. Its ROI and drawdown are diagnostics only and
            # must never unlock real trading. PAPER Jupiter outcomes remain the
            # source of truth for ROI and drawdown validation.
            try:
                validation_metrics = json.loads(str(validation["metrics_json"] or "{}"))
            except Exception:
                validation_metrics = {}
            per_asset_validation = validation_metrics.get("per_asset", {}) if isinstance(validation_metrics, dict) else {}
            failed_core_walkforward = [
                asset for asset in self.s.research_core_assets
                if not bool((per_asset_validation.get(asset) or {}).get("passed", False))
            ]
            if failed_core_walkforward:
                reasons.append("walk-forward core non validé: " + ", ".join(failed_core_walkforward))
            try:
                finished = datetime.fromisoformat(str(validation["finished_at"]).replace("Z", "+00:00"))
                validation_age = (datetime.now(timezone.utc) - finished).total_seconds() / 3600.0
                if validation_age > self.s.live_gate_max_validation_age_hours:
                    reasons.append("validation walk-forward trop ancienne")
            except Exception:
                validation_age = None
                reasons.append("date de validation invalide")
        if int(critical["n"] or 0) > 0:
            reasons.append("incident critique récent")
        if failed_quality > 0:
            reasons.append("quorum qualité historique insuffisant: " + " | ".join(quality_failures[:12]))
        if not bool(getattr(self.s, "release_live_capable", False)):
            reasons.append("release_live_capable=false: verrou codé de la v1.0.0 MICRO-LIVE")
        if not self.s.live_allowed_by_version:
            reasons.append("LIVE_ALLOWED_BY_VERSION=false pour la v1.0.0 MICRO-LIVE")
        metrics = {
            "settled_paper": settled, "paper_won": int(paper["won"] or 0), "paper_roi": paper_roi,
            "paper_brier": brier, "paper_log_loss": logloss,
            "paper_model_brier_direct": model_brier_direct,
            "paper_model_log_loss_direct": model_log_direct,
            "paper_market_brier": market_brier, "paper_market_log_loss": market_logloss,
            "paper_brier_skill_vs_market": brier_skill,
            "active_neural_models": active_neural,
            "active_neural_assets": sorted(active_neural_assets),
            "missing_core_neural_assets": missing_core_neural,
            "validation_id": int(validation["id"]) if validation else None,
            "validation_age_hours": validation_age,
            "failed_data_quality": failed_quality,
            "quality_failures": quality_failures,
            "quality_quorum": quality_quorum,
            "ignored_extended_data_quality_failures": ignored_extended_quality,
            "required_quality_assets": sorted(required_quality_assets),
            "critical_incidents_30d": int(critical["n"] or 0),
        }
        result = {"passed": not reasons, "reasons": reasons, "metrics": metrics, "checked_at": now()}
        if persist:
            with self.db.connect() as conn:
                conn.execute("INSERT INTO live_gate_checks(passed,reasons_json,metrics_json,checked_at) VALUES(?,?,?,?)",
                             (int(result["passed"]), json.dumps(reasons, ensure_ascii=False),
                              json.dumps(metrics, ensure_ascii=False), result["checked_at"]))
        return result

    def require(self) -> None:
        result = self.evaluate(persist=True)
        if not result["passed"]:
            raise RuntimeError("Verrou statistique LIVE: " + " | ".join(result["reasons"]))
