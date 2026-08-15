from __future__ import annotations

import csv
import json
from pathlib import Path

from .storage import DB


def _rows(conn, query: str, params=()) -> list[dict]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def export_audit(db: DB, run_id: int | None = None, export_root: str = "exports") -> dict:
    root = Path(export_root)
    if not root.is_absolute():
        root = db.path.parent.parent / root
    root.mkdir(parents=True, exist_ok=True)
    with db.connect(readonly=True) as conn:
        if run_id is None:
            row = conn.execute("SELECT id FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
            run_id = int(row["id"]) if row else 0
        runs = _rows(conn, "SELECT * FROM runs WHERE id=?", (run_id,))
        markets = _rows(conn, """SELECT DISTINCT m.* FROM markets m
            JOIN market_snapshots ms ON ms.market_id=m.market_id WHERE ms.run_id=?""", (run_id,))
        predictions = _rows(conn, "SELECT * FROM model_predictions WHERE run_id=? ORDER BY id", (run_id,))
        signals = _rows(conn, "SELECT * FROM signals WHERE run_id=? ORDER BY id", (run_id,))
        orders = _rows(conn, "SELECT * FROM orders WHERE run_id=? ORDER BY id", (run_id,))
        observations = _rows(conn, "SELECT * FROM observations WHERE run_id=? ORDER BY id", (run_id,))
        logs = _rows(conn, "SELECT * FROM lifecycle_log WHERE detail LIKE ? ORDER BY id", (f"%run={run_id}%",))
    folder = root / f"run_{run_id:06d}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, rows in {
        "run": runs, "markets": markets, "model_predictions": predictions,
        "signals": signals, "orders": orders, "observations": observations,
        "lifecycle": logs,
    }.items():
        _write_csv(folder / f"{name}.csv", rows)
    summary = {
        "run_id": run_id, "markets": len(markets), "predictions": len(predictions),
        "signals": len(signals), "orders": len(orders), "observations": len(observations),
        "folder": str(folder.resolve()),
    }
    (folder / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
