from types import SimpleNamespace

import pytest

import jupiterdegenbot.probability as probability


class DummySource:
    def __init__(self, *, spot=110.0):
        self.source = "dummy"
        self.spot = spot
        self.reliability = 1.0
        history = [
            SimpleNamespace(ts=700 - i * 300, open=99.0, high=101.0, low=98.0, close=100.0, volume=1.0)
            for i in range(40)
        ]
        history[0] = SimpleNamespace(ts=700, open=99.0, high=101.0, low=98.0, close=100.0, volume=1.0)
        self.candles = {"5m": history, "15m": [], "1h": []}


def settings():
    return SimpleNamespace(
        timed_direction_reference_boundary_tolerance_seconds=3,
        timed_direction_reference_spot_grace_seconds=20,
        research_max_missing_ratio=0.05,
        research_max_stale_intervals=3,
        probability_min_sigma=0.002,
        probability_max_sigma=0.35,
    )


def spec(comparator="above"):
    return SimpleNamespace(window_start_ts=1000, expiry_ts=1300, comparator=comparator)


def test_reference_uses_previous_candle_close_at_exact_start_boundary():
    item = DummySource()
    ref = probability._timed_reference_price(item, spec(), settings(), now_ts=1120)
    assert ref["price"] == pytest.approx(100.0)
    assert ref["method"] == "previous_candle_close"
    assert ref["boundary_error_seconds"] == 0


def test_pre_start_is_rejected():
    item = DummySource()
    with pytest.raises(ValueError, match="pas encore commencée"):
        probability._timed_reference_price(item, spec(), settings(), now_ts=999)


def test_remaining_horizon_and_start_threshold_are_used(monkeypatch):
    item = DummySource(spot=110.0)
    calls = []

    def fake_model(item_arg, timeframe, horizon_hours, comparator, low, high, settings_arg):
        calls.append((timeframe, horizon_hours, comparator, low, float(item_arg.spot)))
        return {
            "timeframe": timeframe,
            "probability": 0.70,
            "timeframe_weight": 1.0,
            "sample_count": 100,
            "quality_passed": True,
            "sigma_horizon": 0.01,
        }

    monkeypatch.setattr(probability, "_timeframe_model", fake_model)
    result = probability._timed_direction_source_probability(
        item, spec("above"), settings(), now_ts=1120
    )

    assert calls
    assert calls[0][1] == pytest.approx(180.0 / 3600.0)
    assert calls[0][3] == pytest.approx(100.0)
    assert calls[0][4] == pytest.approx(110.0)
    assert result["probability"] == pytest.approx(0.70)
    assert result["remaining_seconds"] == pytest.approx(180.0)

    result_down = probability._timed_direction_source_probability(
        item, spec("below"), settings(), now_ts=1120
    )
    assert result_down["probability"] == pytest.approx(0.30)
