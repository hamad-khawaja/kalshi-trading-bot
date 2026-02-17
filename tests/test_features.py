"""Tests for indicators and feature engine."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pytest

from src.data.models import MarketSnapshot, Orderbook, OrderbookLevel
from src.features.feature_engine import FeatureEngine
from src.features.indicators import (
    bollinger_band_position,
    macd_signal,
    mean_reversion_z_score,
    momentum,
    momentum_divergence,
    order_flow_imbalance,
    orderbook_depth_imbalance,
    rate_of_change_acceleration,
    rsi,
    spread_ratio,
    time_decay_factor,
    volatility_realized,
    volume_weighted_momentum,
    vwap,
    vwap_deviation,
)


class TestMomentum:
    def test_positive_momentum(self):
        prices = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
        result = momentum(prices, 4)
        # prices[-4]=101, prices[-1]=104: (104-101)/101 ≈ 0.0297
        assert result == pytest.approx(0.0297, abs=0.001)

    def test_negative_momentum(self):
        prices = np.array([104.0, 103.0, 102.0, 101.0, 100.0])
        result = momentum(prices, 4)
        assert result < 0

    def test_flat_momentum(self):
        prices = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        result = momentum(prices, 4)
        assert result == 0.0

    def test_insufficient_data(self):
        prices = np.array([100.0, 101.0])
        result = momentum(prices, 5)
        assert result == 0.0

    def test_zero_window(self):
        prices = np.array([100.0, 101.0])
        assert momentum(prices, 0) == 0.0

    def test_window_one(self):
        # window=1: prices[-1] vs prices[-1] = 0 (same element)
        prices = np.array([100.0, 102.0])
        result = momentum(prices, 1)
        assert result == 0.0

    def test_window_two(self):
        prices = np.array([100.0, 102.0])
        result = momentum(prices, 2)
        assert result == pytest.approx(0.02, abs=0.001)


class TestVolatility:
    def test_zero_on_constant_prices(self):
        prices = np.array([100.0] * 20)
        assert volatility_realized(prices) == 0.0

    def test_increases_with_variance(self):
        calm = np.array([100.0 + 0.01 * i for i in range(50)])
        volatile = np.array([100.0 + (-1) ** i * 2.0 for i in range(50)])
        vol_calm = volatility_realized(calm)
        vol_volatile = volatility_realized(volatile)
        assert vol_volatile > vol_calm

    def test_single_price(self):
        assert volatility_realized(np.array([100.0])) == 0.0

    def test_empty_array(self):
        assert volatility_realized(np.array([])) == 0.0

    def test_with_window(self):
        prices = np.array([100.0 + i * 0.1 for i in range(100)])
        vol_full = volatility_realized(prices)
        vol_window = volatility_realized(prices, window=20)
        # Both should be positive
        assert vol_full > 0
        assert vol_window > 0


class TestRSI:
    def test_overbought(self):
        # Continuously rising prices
        prices = np.array([100.0 + i for i in range(20)])
        result = rsi(prices, period=14)
        assert result > 70

    def test_oversold(self):
        # Continuously falling prices
        prices = np.array([120.0 - i for i in range(20)])
        result = rsi(prices, period=14)
        assert result < 30

    def test_neutral_insufficient_data(self):
        prices = np.array([100.0, 101.0])
        result = rsi(prices, period=14)
        assert result == 50.0  # Default when insufficient data

    def test_range(self):
        prices = np.array([100.0 + np.sin(i * 0.5) for i in range(30)])
        result = rsi(prices)
        assert 0 <= result <= 100


class TestVWAP:
    def test_basic(self):
        prices = np.array([100.0, 102.0, 104.0])
        volumes = np.array([10.0, 20.0, 10.0])
        result = vwap(prices, volumes)
        expected = (100 * 10 + 102 * 20 + 104 * 10) / 40
        assert result == pytest.approx(expected, abs=0.01)

    def test_empty(self):
        assert vwap(np.array([]), np.array([])) == 0.0

    def test_zero_volume(self):
        prices = np.array([100.0, 102.0])
        volumes = np.array([0.0, 0.0])
        # Should return mean price
        result = vwap(prices, volumes)
        assert result == pytest.approx(101.0, abs=0.01)


class TestVWAPDeviation:
    def test_above(self):
        assert vwap_deviation(102.0, 100.0) == pytest.approx(0.02, abs=0.001)

    def test_below(self):
        assert vwap_deviation(98.0, 100.0) == pytest.approx(-0.02, abs=0.001)

    def test_zero_vwap(self):
        assert vwap_deviation(100.0, 0.0) == 0.0


class TestOrderFlowImbalance:
    def test_balanced(self):
        assert order_flow_imbalance(100, 100) == 0.0

    def test_all_yes(self):
        assert order_flow_imbalance(100, 0) == 1.0

    def test_all_no(self):
        assert order_flow_imbalance(0, 100) == -1.0

    def test_range(self):
        result = order_flow_imbalance(150, 100)
        assert -1 <= result <= 1
        assert result > 0

    def test_zero_total(self):
        assert order_flow_imbalance(0, 0) == 0.0


class TestSpreadRatio:
    def test_narrow_spread(self):
        result = spread_ratio(0.02, 0.50)
        assert 0 <= result <= 1

    def test_wide_spread(self):
        # max_possible_spread at prob=0.5 is min(0.5, 0.5)*2 = 1.0
        # 0.50 / 1.0 = 0.5
        result = spread_ratio(0.50, 0.50)
        assert result == 0.5

    def test_max_spread(self):
        # Spread equals max possible -> ratio = 1.0
        result = spread_ratio(1.0, 0.50)
        assert result == 1.0

    def test_extreme_prob(self):
        assert spread_ratio(0.02, 0.0) == 0.0
        assert spread_ratio(0.02, 1.0) == 0.0


class TestTimeDecayFactor:
    def test_full_window(self):
        assert time_decay_factor(900, 900) == 1.0

    def test_at_expiry(self):
        assert time_decay_factor(0, 900) == 0.0

    def test_half_window(self):
        assert time_decay_factor(450, 900) == 0.5

    def test_negative_clamp(self):
        assert time_decay_factor(-10, 900) == 0.0

    def test_over_clamp(self):
        assert time_decay_factor(1800, 900) == 1.0


class TestMeanReversionZScore:
    def test_at_mean(self):
        prices = np.array([100.0] * 60)
        assert mean_reversion_z_score(prices) == 0.0

    def test_above_mean(self):
        prices = np.array([100.0] * 59 + [102.0])
        result = mean_reversion_z_score(prices)
        assert result > 0


class TestMomentumDivergence:
    def test_accelerating(self):
        assert momentum_divergence(0.02, 0.01) == pytest.approx(0.01)

    def test_decelerating(self):
        assert momentum_divergence(0.01, 0.02) == pytest.approx(-0.01)


class TestBollingerBandPosition:
    def test_at_middle(self):
        """Price at SMA should return ~0."""
        prices = np.array([100.0] * 20)
        result = bollinger_band_position(prices)
        assert result == 0.0

    def test_above_middle(self):
        """Price above SMA should be positive."""
        prices = np.array([100.0] * 19 + [105.0])
        result = bollinger_band_position(prices)
        assert result > 0

    def test_below_middle(self):
        """Price below SMA should be negative."""
        prices = np.array([100.0] * 19 + [95.0])
        result = bollinger_band_position(prices)
        assert result < 0

    def test_clamped_to_range(self):
        """Result should be clamped to [-1, 1]."""
        prices = np.array([100.0] * 19 + [200.0])  # Extreme outlier
        result = bollinger_band_position(prices)
        assert -1.0 <= result <= 1.0

    def test_insufficient_data(self):
        prices = np.array([100.0, 101.0])
        assert bollinger_band_position(prices, window=20) == 0.0


class TestMACDSignal:
    def test_returns_three_values(self):
        prices = np.array([100.0 + i * 0.1 for i in range(250)])
        macd_line, signal_line, histogram = macd_signal(prices)
        assert isinstance(macd_line, float)
        assert isinstance(signal_line, float)
        assert isinstance(histogram, float)

    def test_insufficient_data(self):
        prices = np.array([100.0] * 10)
        result = macd_signal(prices)
        assert result == (0.0, 0.0, 0.0)

    def test_trending_up(self):
        """Uptrend should produce positive MACD histogram."""
        prices = np.array([100.0 + i * 0.5 for i in range(300)])
        _, _, histogram = macd_signal(prices)
        assert histogram > 0


class TestRateOfChangeAcceleration:
    def test_accelerating(self):
        """Accelerating momentum should be positive."""
        # Prices that accelerate upward
        prices = np.array([100.0 + i ** 1.5 * 0.01 for i in range(100)])
        result = rate_of_change_acceleration(prices, window=30)
        assert result > 0

    def test_insufficient_data(self):
        prices = np.array([100.0] * 10)
        assert rate_of_change_acceleration(prices, window=30) == 0.0

    def test_constant_speed(self):
        """Linear trend should have near-zero acceleration."""
        prices = np.array([100.0 + i * 0.1 for i in range(100)])
        result = rate_of_change_acceleration(prices, window=30)
        assert abs(result) < 0.001


class TestVolumeWeightedMomentum:
    def test_high_volume_move(self):
        """Big-volume moves should produce a stronger signal."""
        prices = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
        high_vol = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        low_vol = np.array([1.0, 1.0, 1.0, 1.0, 1.0])
        result_high = volume_weighted_momentum(prices, high_vol)
        result_low = volume_weighted_momentum(prices, low_vol)
        # Same returns, both should be positive
        assert result_high > 0
        assert result_low > 0
        # With equal per-tick returns, weighting shouldn't matter
        assert result_high == pytest.approx(result_low, abs=0.001)

    def test_empty_data(self):
        assert volume_weighted_momentum(np.array([]), np.array([])) == 0.0

    def test_zero_volume(self):
        prices = np.array([100.0, 101.0])
        volumes = np.array([0.0, 0.0])
        assert volume_weighted_momentum(prices, volumes) == 0.0


class TestOrderbookDepthImbalance:
    def test_balanced(self):
        """Equal depth should return 0."""
        yes = [OrderbookLevel(price_dollars=Decimal("0.50"), quantity=100)]
        no = [OrderbookLevel(price_dollars=Decimal("0.50"), quantity=100)]
        result = orderbook_depth_imbalance(yes, no)
        assert result == 0.0

    def test_yes_dominant(self):
        """More YES depth should be positive."""
        yes = [OrderbookLevel(price_dollars=Decimal("0.50"), quantity=200)]
        no = [OrderbookLevel(price_dollars=Decimal("0.50"), quantity=50)]
        result = orderbook_depth_imbalance(yes, no)
        assert result > 0

    def test_no_dominant(self):
        """More NO depth should be negative."""
        yes = [OrderbookLevel(price_dollars=Decimal("0.50"), quantity=50)]
        no = [OrderbookLevel(price_dollars=Decimal("0.50"), quantity=200)]
        result = orderbook_depth_imbalance(yes, no)
        assert result < 0

    def test_empty_levels(self):
        assert orderbook_depth_imbalance([], []) == 0.0

    def test_weighting(self):
        """Top of book should be weighted more heavily (3x)."""
        # YES: 100 at top (3x=300), NO: 100 at second level (2x=200)
        yes = [OrderbookLevel(price_dollars=Decimal("0.52"), quantity=100)]
        no = [
            OrderbookLevel(price_dollars=Decimal("0.50"), quantity=0),
            OrderbookLevel(price_dollars=Decimal("0.48"), quantity=100),
        ]
        result = orderbook_depth_imbalance(yes, no)
        # YES weighted: 300, NO weighted: 0 + 200 = 200 -> (300-200)/500 = 0.2
        assert result > 0

    def test_range(self):
        """Result should always be in [-1, 1]."""
        yes = [OrderbookLevel(price_dollars=Decimal("0.50"), quantity=1000)]
        no = [OrderbookLevel(price_dollars=Decimal("0.50"), quantity=1)]
        result = orderbook_depth_imbalance(yes, no)
        assert -1.0 <= result <= 1.0


class TestFeatureEngine:
    def test_compute_returns_all_fields(self, sample_snapshot: MarketSnapshot):
        from src.config import FeatureConfig

        engine = FeatureEngine(FeatureConfig())
        fv = engine.compute(sample_snapshot)

        assert fv.market_ticker == sample_snapshot.market_ticker
        assert fv.timestamp == sample_snapshot.timestamp
        assert isinstance(fv.momentum_15s, float)
        assert isinstance(fv.momentum_60s, float)
        assert isinstance(fv.realized_vol_5min, float)
        assert isinstance(fv.rsi_14, float)
        assert isinstance(fv.order_flow_imbalance, float)
        assert isinstance(fv.spread, float)
        assert isinstance(fv.time_to_expiry_normalized, float)
        assert fv.implied_probability > 0
        assert isinstance(fv.bollinger_position, float)
        assert isinstance(fv.macd_histogram, float)
        assert isinstance(fv.roc_acceleration, float)
        assert isinstance(fv.volume_weighted_momentum, float)
        assert isinstance(fv.orderbook_depth_imbalance, float)

    def test_compute_handles_empty_prices(self, now: datetime):
        from src.config import FeatureConfig

        engine = FeatureEngine(FeatureConfig())
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            btc_price=Decimal("97500"),
            orderbook=Orderbook(ticker="test", timestamp=now),
            time_to_expiry_seconds=600,
        )
        fv = engine.compute(snapshot)
        # Should not crash, return defaults
        assert fv.momentum_15s == 0.0
        assert fv.rsi_14 == 50.0
        assert fv.bollinger_position == 0.0
        assert fv.macd_histogram == 0.0
        assert fv.roc_acceleration == 0.0

    def test_to_array_shape(self, sample_feature_vector):
        arr = sample_feature_vector.to_array()
        names = sample_feature_vector.feature_names()
        assert len(arr) == len(names)
        assert len(arr) == 26
