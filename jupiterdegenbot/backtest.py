from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .crypto_data import Candle
from .features import TF_SECONDS, build_timeframe_features, flatten_features, log_returns
from .research_ml import NeuralModelManager
from .storage import now

SOURCE_PRIORITY = ("binance", "bybit", "coinbase", "hyperliquid", "kraken")


@dataclass(slots=True)
class DatasetRow:
    asset: str
    timeframe: str
    ts: int
    features: dict[str, float]
    label: int


def grouped_walk_forward_folds(rows: list[DatasetRow], min_train: int, test_size: int, step: int):
    """Yield non-overlapping, timestamp-grouped, purged chronological folds.

    Rows sharing the same anchor timestamp never cross the train/test boundary.
    Training labels whose future horizon reaches into the test period are purged.
    This prevents the most common form of temporal leakage in threshold datasets.
    """
    if not rows:
        return
    groups: dict[int, list[DatasetRow]] = defaultdict(list)
    for row in sorted(rows, key=lambda x: (x.ts, x.asset)):
        groups[int(row.ts)].append(row)
    timestamps = sorted(groups)
    cumulative = 0
    start_group = 0
    while start_group < len(timestamps) and cumulative < int(min_train):
        cumulative += len(groups[timestamps[start_group]])
        start_group += 1
    while start_group < len(timestamps):
        test_end_group = start_group
        test_rows = 0
        while test_end_group < len(timestamps) and test_rows < int(test_size):
            test_rows += len(groups[timestamps[test_end_group]])
            test_end_group += 1
        test_timestamps = timestamps[start_group:test_end_group]
        if not test_timestamps:
            break
        test_start_ts = test_timestamps[0]
        test = [row for ts in test_timestamps for row in groups[ts]]
        train = []
        for ts in timestamps[:start_group]:
            for row in groups[ts]:
                label_end = row.ts + int(float(row.features.get("horizon_hours", 0.0)) * 3600.0)
                if label_end < test_start_ts:
                    train.append(row)
        if len(train) >= int(min_train) and len(test) >= 10:
            yield train, test
        # Never overlap test windows. WALK_FORWARD_STEP can add an embargo gap,
        # but cannot move backwards into the test window just evaluated.
        advance_target = max(int(test_size), int(step), 1)
        advanced = 0
        next_group = start_group
        while next_group < len(timestamps) and advanced < advance_target:
            advanced += len(groups[timestamps[next_group]])
            next_group += 1
        start_group = max(test_end_group, next_group)


class WalkForwardResearch:
    def __init__(self, settings, db):
        self.s, self.db = settings, db
        self.neural = NeuralModelManager(settings, db)

    def _best_source(self, asset: str, timeframe: str = "1h") -> str | None:
        with self.db.connect(readonly=True) as conn:
            rows = conn.execute(
                "SELECT source,COUNT(*) n FROM candles WHERE asset=? AND timeframe=? GROUP BY source ORDER BY n DESC",
                (asset, timeframe),
            ).fetchall()
        counts = {str(r["source"]): int(r["n"]) for r in rows}
        eligible = [s for s in SOURCE_PRIORITY if counts.get(s, 0) >= self.s.research_min_history_candles]
        if eligible:
            return max(eligible, key=lambda s: counts[s])
        return max(counts, key=counts.get) if counts else None

    def _load(self, asset: str, source: str, timeframe: str) -> list[Candle]:
        with self.db.connect(readonly=True) as conn:
            rows = conn.execute(
                "SELECT ts,open,high,low,close,volume FROM candles WHERE asset=? AND source=? AND timeframe=? ORDER BY ts",
                (asset, source, timeframe),
            ).fetchall()
        return [Candle(int(r["ts"]), float(r["open"]), float(r["high"]), float(r["low"]),
                       float(r["close"]), float(r["volume"] or 0), source, asset, timeframe) for r in rows]

    def _aggregate(self, rows: list[Candle], target: str) -> list[Candle]:
        target_seconds = TF_SECONDS[target]
        buckets: dict[int, list[Candle]] = defaultdict(list)
        for row in rows:
            buckets[row.ts - row.ts % target_seconds].append(row)
        output = []
        expected_count = target_seconds // TF_SECONDS[rows[0].timeframe]
        for ts, group in sorted(buckets.items()):
            group = sorted(group, key=lambda x: x.ts)
            if len(group) != expected_count:
                continue
            output.append(Candle(ts, group[0].open, max(x.high for x in group), min(x.low for x in group),
                                 group[-1].close, sum(x.volume for x in group), group[0].source,
                                 group[0].asset, target))
        return output

    def build_dataset(self, asset: str, max_rows: int = 6000) -> list[DatasetRow]:
        asset = asset.upper(); source = self._best_source(asset, "1h")
        if not source:
            return []
        series: dict[str, list[Candle]] = {}
        for tf in ("15m", "1h", "4h", "1d"):
            rows = self._load(asset, source, tf)
            if rows:
                series[tf] = rows
        if "1h" not in series:
            return []
        if "4h" not in series:
            series["4h"] = self._aggregate(series["1h"], "4h")
        if "1d" not in series:
            series["1d"] = self._aggregate(series["1h"], "1d")
        if len(series) < 3:
            return []
        times = {tf: [x.ts for x in rows] for tf, rows in series.items()}
        base = series["1h"]
        base_times = times["1h"]
        horizons = (6, 24, 72)
        threshold_zs = (-1.0, -0.5, 0.0, 0.5, 1.0)
        step = max(1, len(base) // max(1, max_rows // (len(horizons) * len(threshold_zs))))
        output: list[DatasetRow] = []
        for idx in range(240, len(base) - max(horizons), step):
            anchor = base[idx]
            tf_features = []
            for tf, rows in series.items():
                pos = bisect_right(times[tf], anchor.ts) - 1
                if pos < 40:
                    continue
                lookback = rows[max(0, pos - 800):pos + 1]
                try:
                    tf_features.append(build_timeframe_features(
                        lookback, now_ts=anchor.ts + TF_SECONDS[tf], max_missing_ratio=0.04,
                        max_stale_intervals=2,
                    ))
                except ValueError:
                    continue
            if len(tf_features) < 3:
                continue
            returns = log_returns(base[max(0, idx - 500):idx + 1])
            sigma_1h = float(np.std(returns[-min(240, returns.size):], ddof=1)) if returns.size >= 20 else 0.0
            if sigma_1h <= 1e-8:
                continue
            for horizon in horizons:
                future = base[idx + horizon].close
                sigma_h = sigma_1h * math.sqrt(horizon)
                for threshold_z in threshold_zs:
                    threshold = anchor.close * math.exp(threshold_z * sigma_h)
                    threshold_distance = math.log(threshold / anchor.close) if anchor.close > 0 else 0.0
                    features = flatten_features(tf_features, [], threshold_distance, float(horizon))
                    # Keep the standardized distance as a separate feature. The live
                    # path computes the same value from the current horizon sigma.
                    features["threshold_z"] = float(threshold_z)
                    # Metadata used for strict chronological grouping/purging.
                    # It is explicitly excluded from the model feature matrix.
                    features["anchor_ts"] = float(anchor.ts)
                    label = int(future >= threshold)
                    output.append(DatasetRow(asset, "multi", anchor.ts, features, label))
                    if len(output) >= max_rows:
                        return output
        return output

    @staticmethod
    def _feature_names(rows: list[DatasetRow]) -> list[str]:
        return sorted({key for row in rows for key, value in row.features.items()
                       if isinstance(value, (int, float, bool)) and key not in {"anchor_ts"}})

    @staticmethod
    def _matrix(rows: list[DatasetRow], names: list[str]) -> np.ndarray:
        return np.asarray([[float(r.features.get(n, 0.0)) for n in names] for r in rows], dtype=float)

    def run(self, assets: list[str] | None = None, max_rows_per_asset: int = 6000) -> dict[str, Any]:
        assets = assets or self.s.research_history_assets
        started = now()
        all_rows: list[DatasetRow] = []
        by_asset: dict[str, list[DatasetRow]] = {}
        for asset in assets:
            rows = self.build_dataset(asset, max_rows_per_asset)
            by_asset[asset] = rows
            all_rows.extend(rows)
        all_rows.sort(key=lambda x: (x.ts, x.asset))
        if all_rows:
            with self.db.connect() as conn:
                conn.executemany(
                    """INSERT INTO feature_vectors(asset,timeframe,ts,horizon_steps,threshold_z,feature_json,label,split,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(asset,timeframe,ts,horizon_steps,threshold_z) DO UPDATE SET
                       feature_json=excluded.feature_json,label=excluded.label,split=excluded.split,created_at=excluded.created_at""",
                    [(row.asset, row.timeframe, row.ts, int(row.features.get("horizon_hours", 0)),
                      float(row.features.get("threshold_distance", 0.0)), json.dumps(row.features), row.label,
                      "research", now()) for row in all_rows],
                )
        if len(all_rows) < self.s.walk_forward_min_train + self.s.walk_forward_test_size:
            return {"ok": False, "reason": "historique insuffisant", "samples": len(all_rows)}
        names = self._feature_names(all_rows)
        predictions: list[tuple[DatasetRow, float]] = []
        fold_train_sizes: list[int] = []
        fold_count = 0
        for train, test in grouped_walk_forward_folds(
            all_rows, int(self.s.walk_forward_min_train),
            int(self.s.walk_forward_test_size), int(self.s.walk_forward_step),
        ):
            X_train = self._matrix(train, names); y_train = np.asarray([r.label for r in train], dtype=int)
            if np.unique(y_train).size < 2:
                continue
            model = Pipeline([
                ("imputer", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("mlp", MLPClassifier(hidden_layer_sizes=self.s.neural_hidden_layer_sizes,
                                      alpha=0.003, max_iter=min(160, self.s.neural_max_iter),
                                      early_stopping=True, n_iter_no_change=12,
                                      random_state=42)),
            ])
            model.fit(X_train, y_train)
            probs = model.predict_proba(self._matrix(test, names))[:, 1]
            predictions.extend(zip(test, probs.tolist()))
            fold_train_sizes.append(len(train))
            fold_count += 1
        if not predictions:
            return {"ok": False, "reason": "aucune fenêtre walk-forward valide"}
        y = np.asarray([row.label for row, _ in predictions], dtype=int)
        p = np.clip(np.asarray([prob for _, prob in predictions], dtype=float), 1e-6, 1 - 1e-6)
        baseline = np.full_like(p, 0.5)
        cost = float(self.s.walk_forward_cost_bps) / 10000.0
        pnl = []
        selected = []
        for actual, prob in zip(y, p):
            choose = abs(prob - 0.5) >= 0.10
            selected.append(choose)
            if not choose:
                pnl.append(0.0); continue
            correct = (prob >= 0.5 and actual == 1) or (prob < 0.5 and actual == 0)
            pnl.append((0.5 - cost) if correct else -(0.5 + cost))
        pnl_arr = np.asarray(pnl)
        equity = np.cumsum(pnl_arr)
        peak = np.maximum.accumulate(np.concatenate([[0.0], equity]))[1:]
        drawdown = peak - equity
        trade_count = int(np.sum(selected))
        roi = float(pnl_arr.sum() / max(1.0, trade_count * 0.5))
        per_asset: dict[str, dict[str, float | int]] = {}
        for asset in sorted({row.asset for row, _ in predictions}):
            indices = [i for i, (row, _) in enumerate(predictions) if row.asset == asset]
            if not indices:
                continue
            ay = y[indices]; ap = p[indices]; apnl = pnl_arr[indices]
            atrades = int(sum(bool(selected[i]) for i in indices))
            asset_brier = float(brier_score_loss(ay, ap))
            asset_log = float(log_loss(ay, ap, labels=[0, 1]))
            asset_base_brier = float(brier_score_loss(ay, np.full_like(ap, 0.5)))
            asset_base_log = float(log_loss(ay, np.full_like(ap, 0.5), labels=[0, 1]))
            asset_passed = bool(asset_brier < asset_base_brier and asset_log < asset_base_log and atrades > 0)
            per_asset[asset] = {
                "samples": int(len(indices)),
                "trades": atrades,
                "brier_score": asset_brier,
                "log_loss": asset_log,
                "baseline_brier": asset_base_brier,
                "baseline_log_loss": asset_base_log,
                "roi_after_costs": float(apnl.sum() / max(1.0, atrades * 0.5)),
                "passed": asset_passed,
            }

        metrics = {
            "brier_score": float(brier_score_loss(y, p)),
            "log_loss": float(log_loss(y, p, labels=[0, 1])),
            "baseline_brier": float(brier_score_loss(y, baseline)),
            "baseline_log_loss": float(log_loss(y, baseline, labels=[0, 1])),
            "roi_after_costs": roi,
            "max_drawdown": float(drawdown.max() / max(1.0, peak.max() + 1.0)) if drawdown.size else 0.0,
            "win_rate": float(np.mean([((prob >= 0.5) == bool(actual)) for actual, prob in zip(y, p)])),
            "trade_count": trade_count,
            "test_samples": int(len(y)),
            "train_samples": int(min(fold_train_sizes) if fold_train_sizes else 0),
            "max_train_samples": int(max(fold_train_sizes) if fold_train_sizes else 0),
            "fold_count": int(fold_count),
            "purged_temporal_split": True,
            "per_asset": per_asset,
            "synthetic_roi_at_050": roi,
            "synthetic_drawdown_at_050": float(drawdown.max() / max(1.0, peak.max() + 1.0)) if drawdown.size else 0.0,
            "note": "ROI/drawdown synthétiques à prix 0,50 sur pseudo-marchés corrélés; métriques informatives, jamais utilisées pour déverrouiller le LIVE.",
        }
        status = "passed" if (metrics["brier_score"] < metrics["baseline_brier"] and
                              metrics["log_loss"] < metrics["baseline_log_loss"] and trade_count > 0) else "failed"
        with self.db.connect() as conn:
            cur = conn.execute(
                """INSERT INTO validation_runs(kind,status,assets_json,train_samples,test_samples,trade_count,
                   brier_score,log_loss,baseline_brier,baseline_log_loss,roi_after_costs,max_drawdown,
                   win_rate,metrics_json,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("walk_forward_neural", status, json.dumps(assets), metrics["train_samples"], metrics["test_samples"],
                 trade_count, metrics["brier_score"], metrics["log_loss"], metrics["baseline_brier"],
                 metrics["baseline_log_loss"], roi, metrics["max_drawdown"], metrics["win_rate"],
                 json.dumps(metrics, ensure_ascii=False), started, now()),
            )
            validation_id = int(cur.lastrowid)
            conn.executemany(
                """INSERT INTO validation_predictions(validation_run_id,asset,timeframe,ts,probability,actual,
                   market_price,selected,pnl,feature_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                [(validation_id, row.asset, row.timeframe, row.ts, float(prob), row.label, 0.5,
                  int(abs(prob - 0.5) >= 0.10), float(pnl_value), json.dumps(row.features))
                 for (row, prob), pnl_value in zip(predictions, pnl)],
            )
        neural_training = {}
        for asset, rows in by_asset.items():
            if rows:
                asset_validation = per_asset.get(asset, {})
                neural_training[asset] = self.neural.train(
                    f"{asset}:threshold", [x.features for x in rows], [x.label for x in rows],
                    allow_activation=(status == "passed" and bool(asset_validation.get("passed", False))),
                    external_validation=asset_validation,
                )
        return {"ok": status == "passed", "validation_id": validation_id,
                "status": status, "metrics": metrics, "neural_training": neural_training,
                "dataset_samples": len(all_rows)}
