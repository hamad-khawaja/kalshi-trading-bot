"""Tests for indicators and feature engine."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pytest

from src.data.models import MarketSnapshot, Orderbook, OrderbookLevel
from src.features.feature_engine import FeatureEngine
from src.features.indicators import (
    funding_rate_z_score,
    mean_reversion_z_score,
    momentum,
    momentum_divergence,
    order_flow_imbalance,
    rsi,
    spread_ratio,
    time_decay_factor,
    volatility_realized,
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


class TestFundingRateZScore:
    def test_at_mean(self):
        history = np.array([0.01, 0.01, 0.01, 0.01])
        assert funding_rate_z_score(0.01, history) == 0.0

    def test_above_mean(self):
        # Need some variance in history for non-zero std
        history = np.array([0.01, 0.012, 0.008, 0.011, 0.009])
        result = funding_rate_z_score(0.02, history)
        assert result > 0

    def test_insufficient_data(self):
        assert funding_rate_z_score(0.01, np.array([0.01])) == 0.0


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

    def test_to_array_shape(self, sample_feature_vector):
        arr = sample_feature_vector.to_array()
        names = sample_feature_vector.feature_names()
        assert len(arr) == len(names)
        assert len(arr) == 17
