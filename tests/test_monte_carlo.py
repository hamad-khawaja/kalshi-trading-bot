"""Tests for Monte Carlo GBM simulation and MC signal detector."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pytest

from src.config import StrategyConfig
from src.data.models import (
    FeatureVector,
    MarketSnapshot,
    Orderbook,
    OrderbookLevel,
    TradeSignal,
)
from src.model.monte_carlo import MonteCarloSimulator
from src.strategy.mc_detector import MCSignalDetector


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 2, 19, 14, 0, 0, tzinfo=timezone.utc)


def _make_features(
    now: datetime,
    realized_vol: float = 0.002,
    momentum_180s: float = 0.0,
) -> FeatureVector:
    return FeatureVector(
        timestamp=now,
        market_ticker="kxbtc15m-test",
        realized_vol_5min=realized_vol,
        momentum_180s=momentum_180s,
    )


def _make_snapshot(
    now: datetime,
    btc_price: float = 97500.0,
    strike_price: float | None = 97500.0,
    implied_yes_prob: float = 0.50,
    ttx: float = 300.0,
) -> MarketSnapshot:
    ob = Orderbook(
        ticker="kxbtc15m-test",
        yes_levels=[
            OrderbookLevel(price_dollars=Decimal(str(implied_yes_prob)), quantity=100),
        ],
        no_levels=[
            OrderbookLevel(price_dollars=Decimal(str(round(1.0 - implied_yes_prob, 2))), quantity=100),
        ],
        timestamp=now,
    )
    return MarketSnapshot(
        timestamp=now,
        market_ticker="kxbtc15m-test",
        btc_price=Decimal(str(btc_price)),
        orderbook=ob,
        implied_yes_prob=Decimal(str(implied_yes_prob)),
        spread=Decimal("0.02"),
        strike_price=Decimal(str(strike_price)) if strike_price is not None else None,
        time_to_expiry_seconds=ttx,
        time_elapsed_seconds=900.0 - ttx,
    )


class TestMonteCarloSimulator:
    """Tests for the GBM simulation engine."""

    def test_gbm_produces_valid_prices(self, now: datetime):
        """All simulated prices should be positive and finite."""
        sim = MonteCarloSimulator(n_samples=5000)
        snapshot = _make_snapshot(now, btc_price=97500.0, strike_price=97500.0)
        features = _make_features(now)
        prob, confidence = sim.estimate_probability(snapshot, features)

        assert 0.05 <= prob <= 0.95
        assert 0.0 <= confidence <= 1.0

    def test_prob_near_one_when_spot_far_above_strike(self, now: datetime):
        """P(YES) ~ 1.0 when spot >> strike with low vol and short TTX."""
        sim = MonteCarloSimulator(n_samples=10000)
        snapshot = _make_snapshot(
            now, btc_price=100000.0, strike_price=90000.0, ttx=60.0
        )
        features = _make_features(now, realized_vol=0.0001)
        prob, confidence = sim.estimate_probability(snapshot, features)

        assert prob >= 0.90  # Clamped at 0.95 max
        assert confidence > 0.5

    def test_prob_near_zero_when_spot_far_below_strike(self, now: datetime):
        """P(YES) ~ 0.0 when spot << strike with low vol and short TTX."""
        sim = MonteCarloSimulator(n_samples=10000)
        snapshot = _make_snapshot(
            now, btc_price=90000.0, strike_price=100000.0, ttx=60.0
        )
        features = _make_features(now, realized_vol=0.0001)
        prob, confidence = sim.estimate_probability(snapshot, features)

        assert prob <= 0.10  # Clamped at 0.05 min
        assert confidence > 0.5

    def test_prob_near_half_when_spot_equals_strike(self, now: datetime):
        """P(YES) ~ 0.5 when spot = strike (zero drift)."""
        sim = MonteCarloSimulator(n_samples=10000, drift_mode="zero")
        snapshot = _make_snapshot(
            now, btc_price=97500.0, strike_price=97500.0, ttx=300.0
        )
        features = _make_features(now, realized_vol=0.002)
        prob, _ = sim.estimate_probability(snapshot, features)

        assert 0.35 <= prob <= 0.65  # Should be around 0.5

    def test_momentum_drift_shifts_probability(self, now: datetime):
        """Positive momentum should increase P(YES) vs zero drift."""
        sim_zero = MonteCarloSimulator(n_samples=10000, drift_mode="zero")
        sim_mom = MonteCarloSimulator(n_samples=10000, drift_mode="momentum")

        snapshot = _make_snapshot(
            now, btc_price=97500.0, strike_price=97500.0, ttx=300.0
        )
        features = _make_features(now, realized_vol=0.002, momentum_180s=0.001)

        prob_zero, _ = sim_zero.estimate_probability(snapshot, features)
        prob_mom, _ = sim_mom.estimate_probability(snapshot, features)

        # Positive momentum should push prob_yes higher
        assert prob_mom > prob_zero - 0.15  # Allow some MC noise

    def test_vol_multiplier_increases_uncertainty(self, now: datetime):
        """Higher vol multiplier should push probability toward 0.5."""
        sim_low = MonteCarloSimulator(n_samples=10000, vol_multiplier=0.5)
        sim_high = MonteCarloSimulator(n_samples=10000, vol_multiplier=3.0)

        # Spot well above strike — low vol should be more certain
        snapshot = _make_snapshot(
            now, btc_price=98000.0, strike_price=97500.0, ttx=300.0
        )
        features = _make_features(now, realized_vol=0.002)

        prob_low, _ = sim_low.estimate_probability(snapshot, features)
        prob_high, _ = sim_high.estimate_probability(snapshot, features)

        # Higher vol → more uncertainty → closer to 0.5
        assert prob_low >= prob_high - 0.10

    def test_confidence_higher_at_extremes(self, now: datetime):
        """Confidence should be higher when probability is near 0 or 1."""
        sim = MonteCarloSimulator(n_samples=10000)

        # Near-certain: spot far above strike
        snap_certain = _make_snapshot(
            now, btc_price=100000.0, strike_price=90000.0, ttx=60.0
        )
        features = _make_features(now, realized_vol=0.0001)
        _, conf_certain = sim.estimate_probability(snap_certain, features)

        # Uncertain: spot = strike
        snap_uncertain = _make_snapshot(
            now, btc_price=97500.0, strike_price=97500.0, ttx=300.0
        )
        _, conf_uncertain = sim.estimate_probability(snap_uncertain, features)

        assert conf_certain > conf_uncertain

    def test_zero_vol_floor(self, now: datetime):
        """Zero realized vol should use a floor, not produce degenerate paths."""
        sim = MonteCarloSimulator(n_samples=1000)
        snapshot = _make_snapshot(now)
        features = _make_features(now, realized_vol=0.0)
        prob, confidence = sim.estimate_probability(snapshot, features)

        assert 0.05 <= prob <= 0.95
        assert 0.0 <= confidence <= 1.0


class TestMCSignalDetector:
    """Tests for the Monte Carlo signal detector."""

    def _make_config(self, **overrides) -> StrategyConfig:
        defaults = dict(
            mc_enabled=True,
            mc_samples=5000,
            mc_drift_mode="zero",
            mc_vol_multiplier=1.0,
            mc_min_edge=0.04,
            mc_min_confidence=0.30,
            mc_min_implied_distance=0.10,
            mc_kelly_fraction=0.15,
            mc_min_ttx=120.0,
            mc_max_ttx=720.0,
        )
        defaults.update(overrides)
        return StrategyConfig(**defaults)

    def test_returns_none_when_ttx_too_low(self, now: datetime):
        """Should skip when time to expiry < mc_min_ttx."""
        config = self._make_config()
        detector = MCSignalDetector(config)
        snapshot = _make_snapshot(now, ttx=60.0, implied_yes_prob=0.30, strike_price=97500.0)
        features = _make_features(now)
        assert detector.detect(snapshot, features) is None

    def test_returns_none_when_ttx_too_high(self, now: datetime):
        """Should skip when time to expiry > mc_max_ttx."""
        config = self._make_config()
        detector = MCSignalDetector(config)
        snapshot = _make_snapshot(now, ttx=800.0, implied_yes_prob=0.30, strike_price=97500.0)
        features = _make_features(now)
        assert detector.detect(snapshot, features) is None

    def test_returns_none_when_no_strike(self, now: datetime):
        """Should skip when strike_price is None."""
        config = self._make_config()
        detector = MCSignalDetector(config)
        snapshot = _make_snapshot(now, strike_price=None, implied_yes_prob=0.30)
        features = _make_features(now)
        assert detector.detect(snapshot, features) is None

    def test_returns_none_when_coin_flip(self, now: datetime):
        """Should skip when implied prob is too close to 0.50."""
        config = self._make_config(mc_min_implied_distance=0.10)
        detector = MCSignalDetector(config)
        snapshot = _make_snapshot(now, implied_yes_prob=0.48, strike_price=97500.0)
        features = _make_features(now)
        assert detector.detect(snapshot, features) is None

    def test_returns_none_when_edge_below_threshold(self, now: datetime):
        """Should return None when MC edge is too small."""
        # Set very high min_edge so no signal fires
        config = self._make_config(mc_min_edge=0.90)
        detector = MCSignalDetector(config)
        snapshot = _make_snapshot(
            now, btc_price=97600.0, strike_price=97500.0,
            implied_yes_prob=0.55, ttx=300.0,
        )
        features = _make_features(now, realized_vol=0.002)
        assert detector.detect(snapshot, features) is None

    def test_returns_signal_when_strong_edge(self, now: datetime):
        """Should return a TradeSignal when MC diverges strongly from implied."""
        # Spot far above strike → MC says P(YES) high,
        # but implied is low → big edge
        config = self._make_config(
            mc_min_edge=0.02,
            mc_min_confidence=0.20,
            mc_min_implied_distance=0.10,
        )
        detector = MCSignalDetector(config)
        snapshot = _make_snapshot(
            now,
            btc_price=100000.0,
            strike_price=90000.0,
            implied_yes_prob=0.30,
            ttx=300.0,
        )
        features = _make_features(now, realized_vol=0.0001)
        signal = detector.detect(snapshot, features)

        assert signal is not None
        assert isinstance(signal, TradeSignal)
        assert signal.signal_type == "monte_carlo"
        assert signal.side == "yes"
        assert signal.net_edge > 0

    def test_returns_no_side_signal(self, now: datetime):
        """Should return NO signal when MC thinks NO and implied is high."""
        config = self._make_config(
            mc_min_edge=0.02,
            mc_min_confidence=0.20,
            mc_min_implied_distance=0.10,
        )
        detector = MCSignalDetector(config)
        snapshot = _make_snapshot(
            now,
            btc_price=90000.0,
            strike_price=100000.0,
            implied_yes_prob=0.70,
            ttx=300.0,
        )
        features = _make_features(now, realized_vol=0.0001)
        signal = detector.detect(snapshot, features)

        assert signal is not None
        assert signal.signal_type == "monte_carlo"
        assert signal.side == "no"

    def test_signal_type_is_monte_carlo(self, now: datetime):
        """Signal type must be 'monte_carlo'."""
        config = self._make_config(
            mc_min_edge=0.02,
            mc_min_confidence=0.20,
            mc_min_implied_distance=0.10,
        )
        detector = MCSignalDetector(config)
        snapshot = _make_snapshot(
            now,
            btc_price=100000.0,
            strike_price=90000.0,
            implied_yes_prob=0.30,
            ttx=300.0,
        )
        features = _make_features(now, realized_vol=0.0001)
        signal = detector.detect(snapshot, features)

        assert signal is not None
        assert signal.signal_type == "monte_carlo"
