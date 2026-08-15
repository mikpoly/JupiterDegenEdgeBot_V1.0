from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone


def _horizon(expiry, created_at: str) -> str:
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        hours = (float(expiry) - created.timestamp()) / 3600.0
    except (TypeError, ValueError, OSError):
        return "unknown"
    if hours <= 1:
        return "5m-1h"
    if hours <= 24:
        return "1h-24h"
    return "1d-7d"


def _metrics(rows: list[dict]) -> dict:
    if not rows:
        return {"sample_count": 0, "brier_score": None, "log_loss": None, "bins": []}
    brier = 0.0
    log_loss = 0.0
    bins: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for row in rows:
        p = max(1e-6, min(1 - 1e-6, float(row["probability"])))
        y = int(row["actual"])
        brier += (p - y) ** 2
        log_loss += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        bins[min(9, int(p * 10))].append((p, y))
    payload = []
    for idx in sorted(bins):
        values = bins[idx]
        payload.append({
            "bin_low": idx / 10,
            "bin_high": (idx + 1) / 10,
            "count": len(values),
            "mean_probability": sum(x[0] for x in values) / len(values),
            "observed_frequency": sum(x[1] for x in values) / len(values),
        })
    n = len(rows)
    return {
        "sample_count": n,
        "brier_score": brier / n,
        "log_loss": log_loss / n,
        "bins": payload,
    }


def calculate_calibration(db, *, persist: bool = True) -> dict:
    with db.connect(readonly=True) as conn:
        source = conn.execute(
            """SELECT o.id,o.asset,o.paper_result,o.created_at,s.probability,s.expiry
               FROM orders o JOIN signals s ON s.id=o.signal_id
               WHERE o.mode='paper' AND o.paper_result IN ('WON','LOST')
               ORDER BY o.id"""
        ).fetchall()
    rows = [
        {
            "asset": str(row["asset"] or "UNKNOWN"),
            "probability": float(row["probability"]),
            "actual": 1 if row["paper_result"] == "WON" else 0,
            "horizon": _horizon(row["expiry"], row["created_at"]),
        }
        for row in source
        if row["probability"] is not None
    ]
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    groups[("ALL", "ALL")].extend(rows)
    for row in rows:
        groups[(row["asset"], row["horizon"])].append(row)
        groups[(row["asset"], "ALL")].append(row)

    results = []
    for (asset, horizon), items in sorted(groups.items()):
        metrics = _metrics(items)
        results.append({"asset": asset, "horizon": horizon, **metrics})

    if persist:
        with db.connect() as conn:
            conn.execute("DELETE FROM calibration_results")
            for result in results:
                conn.execute(
                    """INSERT INTO calibration_results(asset,horizon,sample_count,brier_score,log_loss,
                       calibration_json,calculated_at) VALUES(?,?,?,?,?,?,datetime('now'))""",
                    (result["asset"], result["horizon"], result["sample_count"],
                     result["brier_score"], result["log_loss"],
                     json.dumps(result["bins"], ensure_ascii=False)),
                )
    overall = next((r for r in results if r["asset"] == "ALL" and r["horizon"] == "ALL"),
                   {"sample_count": 0, "brier_score": None, "log_loss": None, "bins": []})
    return {"overall": overall, "groups": results}
