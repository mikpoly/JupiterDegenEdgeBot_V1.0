from __future__ import annotations
from pathlib import Path

from jupiterdegenbot import __version__
from jupiterdegenbot.storage import DB

ROOT = Path(__file__).resolve().parents[1]


def test_public_version():
    assert __version__ == "1.0.0"


def test_public_env_is_paper_and_live_locked():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    required = {
        "TRADING_MODE=paper",
        "AUTO_EXECUTE=false",
        "LIVE_RELEASE_ENABLED=false",
        "MICRO_LIVE_ENABLED=false",
        "PERSISTENT_LIVE_ENABLED=false",
        "TIMED_DIRECTION_LIVE_ENABLED=false",
        "AUTO_CLAIM_ENABLED=false",
    }
    for line in required:
        assert line in text


def test_fresh_database_schema_initializes(tmp_path):
    path = tmp_path / "fresh.db"
    db = DB(str(path))
    assert path.exists()
    with db.connect(readonly=True) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "markets", "signals", "orders", "shadow_predictions"}.issubset(names)


def test_current_timed_and_claim_safety_markers_present():
    engine = (ROOT / "jupiterdegenbot" / "engine.py").read_text(encoding="utf-8")
    lifecycle = (ROOT / "jupiterdegenbot" / "lifecycle.py").read_text(encoding="utf-8")
    assert "TIMED_FAST_LEARNING_ALL_ASSETS_V2_5" in engine
    assert "CLAIM_FALLBACK_SINGLE_POSITION_V1" in lifecycle
