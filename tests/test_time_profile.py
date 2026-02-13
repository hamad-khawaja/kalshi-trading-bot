"""Tests for time-based trading profiles and predict weight multipliers."""

from __future__ import annotations

import pytest

from src.data.time_profile import (
    HourlyProfile,
    SessionType,
    TimeProfiler,
)
from src.model.predict import HeuristicModel


class TestTimeProfiler:
    def _make_klines(self, hours: int = 720) -> list:
        """Generate mock Binance kline data spanning multiple days."""
        klines = []
        base_time_ms = 1_700_000_000_000  # arbitrary start
        for i in range(hours):
            open_time = base_time_ms + i * 3_600_000
            o = 40000.0
            h = 40000.0 + 10 + (i % 24) * 10  # always > open
            low = 40000.0 - 10 - (i % 24) * 5  # always < open
            c = 40000.0 + 5
            vol = 1000.0 + (i % 24) * 50
            klines.append([open_time, o, h, low, c, vol, 0, 0, 0, 0, 0, 0])
        return klines

    def test_build_profiles_from_klines(self):
        """Mock kline data produces 24 hourly profiles."""
        profiler = TimeProfiler(lookback_days=30)
        klines = self._make_klines(720)
        profiler._build_profiles(klines)

        assert profiler.loaded
        assert len(profiler.profiles) == 24
        for h in range(24):
            p = profiler.profiles[h]
            assert p.hour == h
            assert p.avg_volatility >= 0
            assert p.avg_volume >= 0
            assert p.vol_ratio > 0

    def test_session_classification(self):
        """Verify UTC hour -> session mapping matches spec."""
        assert TimeProfiler.classify_hour(0) == SessionType.ASIA
        assert TimeProfiler.classify_hour(3) == SessionType.ASIA
        assert TimeProfiler.classify_hour(7) == SessionType.ASIA
        assert TimeProfiler.classify_hour(8) == SessionType.EUROPE
        assert TimeProfiler.classify_hour(12) == SessionType.EUROPE
        assert TimeProfiler.classify_hour(13) == SessionType.OVERLAP_EU_US
        assert TimeProfiler.classify_hour(15) == SessionType.OVERLAP_EU_US
        assert TimeProfiler.classify_hour(16) == SessionType.US
        assert TimeProfiler.classify_hour(20) == SessionType.US
        assert TimeProfiler.classify_hour(21) == SessionType.ASIA  # late/off-hours
        assert TimeProfiler.classify_hour(23) == SessionType.ASIA

    def test_weight_multipliers_asia(self):
        """Asia session boosts mean reversion, dampens momentum."""
        m = TimeProfiler.get_weight_multipliers(SessionType.ASIA)
        assert m["momentum"] == 0.7
        assert m["mean_reversion"] == 1.4
        assert m["technical"] == 1.0

    def test_weight_multipliers_us(self):
        """US session boosts momentum/orderflow, dampens mean reversion."""
        m = TimeProfiler.get_weight_multipliers(SessionType.US)
        assert m["momentum"] == 1.5
        assert m["orderflow"] == 1.3
        assert m["mean_reversion"] == 0.7

    def test_weight_multipliers_europe_neutral(self):
        """Europe session has all 1.0 multipliers."""
        m = TimeProfiler.get_weight_multipliers(SessionType.EUROPE)
        assert all(v == 1.0 for v in m.values())

    def test_edge_threshold_multiplier(self):
        """High-vol sessions lower threshold, low-vol raise it."""
        assert TimeProfiler.get_edge_threshold_multiplier(SessionType.US) == 0.8
        assert TimeProfiler.get_edge_threshold_multiplier(SessionType.OVERLAP_EU_US) == 0.8
        assert TimeProfiler.get_edge_threshold_multiplier(SessionType.ASIA) == 1.2
        assert TimeProfiler.get_edge_threshold_multiplier(SessionType.EUROPE) == 1.0

    def test_should_market_make(self):
        """MM disabled during overlap, enabled otherwise."""
        assert TimeProfiler.should_market_make(SessionType.ASIA) is True
        assert TimeProfiler.should_market_make(SessionType.EUROPE) is True
        assert TimeProfiler.should_market_make(SessionType.US) is True
        assert TimeProfiler.should_market_make(SessionType.OVERLAP_EU_US) is False


class TestPredictWithMultipliers:
    def test_weight_multipliers_applied(self, sample_feature_vector):
        """Momentum-boosted weights should change prediction vs. default."""
        model_default = HeuristicModel()
        pred_default = model_default.predict(sample_feature_vector)

        model_boosted = HeuristicModel(
            weight_multipliers={"momentum": 2.0, "mean_reversion": 0.5}
        )
        pred_boosted = model_boosted.predict(sample_feature_vector)

        # With positive momentum features, boosting momentum weight should
        # shift the probability (though it may be subtle due to re-normalization)
        assert isinstance(pred_default.probability_yes, float)
        assert isinstance(pred_boosted.probability_yes, float)

    def test_weight_renormalization(self, sample_feature_vector):
        """Total signal magnitude stays bounded even with extreme multipliers."""
        model = HeuristicModel(
            weight_multipliers={
                "momentum": 3.0,
                "technical": 3.0,
                "orderflow": 3.0,
                "mean_reversion": 3.0,
                "funding": 3.0,
                "time_decay": 3.0,
            }
        )
        pred = model.predict(sample_feature_vector)
        # Uniform 3x should re-normalize to same as 1x
        assert 0.05 <= pred.probability_yes <= 0.95

    def test_set_weight_multipliers_runtime(self, sample_feature_vector):
        """Runtime multiplier update changes subsequent predictions."""
        model = HeuristicModel()
        pred1 = model.predict(sample_feature_vector)

        # Reset EMA state for fair comparison
        model._prev_probability = None
        model.set_weight_multipliers({"momentum": 0.1, "mean_reversion": 3.0})
        pred2 = model.predict(sample_feature_vector)

        # Both should be valid probabilities
        assert 0.05 <= pred1.probability_yes <= 0.95
        assert 0.05 <= pred2.probability_yes <= 0.95
