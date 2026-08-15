from types import SimpleNamespace

import pytest

from jupiterdegenbot.engine import BotEngine


class _StartRunReached(RuntimeError):
    pass


class _DB:
    def __init__(self):
        self.called = False

    def start_run(self, kind):
        self.called = True
        assert kind == "timed_fast"
        raise _StartRunReached()


def test_timed_fast_learning_still_runs_when_no_asset_is_live_ready(monkeypatch):
    """Regression: live_ready must gate LIVE, never TIMED learning itself."""
    engine = BotEngine.__new__(BotEngine)
    engine._timed_fast_seen = {}
    engine._timed_fast_attempts = {}
    engine.s = SimpleNamespace(trading_mode="live")
    engine.db = _DB()
    market = SimpleNamespace(id="POLY-TEST-1")
    engine.jupiter = SimpleNamespace(live_degen_markets=lambda: [market])
    engine._timed_fast_live_ready_assets = lambda: set()
    engine._is_active_short_timed_spec = lambda spec, now_ts=None: True

    fake_spec = SimpleNamespace(asset="BTC")
    monkeypatch.setattr("jupiterdegenbot.engine.parse_crypto_market", lambda market: fake_spec)

    # The old ready-only implementation returned before start_run() here.
    # V2.5 must continue into the learning run even with zero LIVE-ready assets.
    with pytest.raises(_StartRunReached):
        engine.scan_timed_fast_once()

    assert engine.db.called is True
