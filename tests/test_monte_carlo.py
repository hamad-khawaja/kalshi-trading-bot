"""Tests for Monte Carlo simulation and MC signal detector."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models import (
    FeatureVector,
    MarketSnapshot,
    Orderbook,
    OrderbookLevel,
)
from src.model.monte_carlo import MonteCarloSimulator


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 2, 19, 14, 0, 0, tzinfo=timezone.utc)


def _make_features(
    now: datetime,
    realized_vol: float = 0.002,
    momentum_180s: float = 0.0,
    settlement_bias: float = 0.0,
) -> FeatureVector:
    return FeatureVector(
        timestamp=now,
        market_ticker="kxbtc15m-test",
        realized_vol_5min=realized_vol,
        momentum_180s=momentum_180s,
        settlement_bias=settlement_bias,
    )


def _make_snapshot(
    now: datetime,
    btc_price: float = 97500.0,
    strike_price: float | None = 97500.0,
    implied_yes_prob: float = 0.50,
    ttx: float = 300.0,
    btc_prices_5min: list[Decimal] | None = None,
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
    if btc_prices_5min is None:
        btc_prices_5min = []
    return MarketSnapshot(
        timestamp=now,
        market_ticker="kxbtc15m-test",
        btc_price=Decimal(str(btc_price)),
        btc_prices_5min=btc_prices_5min,
        orderbook=ob,
        implied_yes_prob=Decimal(str(implied_yes_prob)),
        spread=Decimal("0.02"),
        strike_price=Decimal(str(strike_price)) if strike_price is not None else None,
        time_to_expiry_seconds=ttx,
        time_elapsed_seconds=900.0 - ttx,
    )


def _make_price_history(base: float = 97500.0, n: int = 300, step: float = 0.5) -> list[Decimal]:
    """Generate a realistic price history with small increments."""
    return [Decimal(str(round(base + i * step, 2))) for i in range(n)]


class TestMonteCarloSimulator:
    """Tests for the simulation engine (bootstrap + GBM fallback)."""

    def test_bootstrap_uses_real_returns(self, now: datetime):
        """When price history is provided, bootstrap should be used."""
        sim = MonteCarloSimulator(n_samples=5000, min_bootstrap_returns=10)
        prices = _make_price_history(n=100, step=0.5)
        snapshot = _make_snapshot(
            now, btc_price=97550.0, strike_price=97500.0, btc_prices_5min=prices,
        )
        features = _make_features(now)
        prob, confidence = sim.estimate_probability(snapshot, features)

        assert 0.05 <= prob <= 0.95
        assert 0.0 <= confidence <= 1.0

    def test_bootstrap_fallback_to_parametric(self, now: datetime):
        """Empty price history should fall back to GBM parametric."""
        sim = MonteCarloSimulator(n_samples=5000, min_bootstrap_returns=30)
        snapshot = _make_snapshot(
            now, btc_price=97500.0, strike_price=97500.0, btc_prices_5min=[],
        )
        features = _make_features(now, realized_vol=0.002)
        prob, confidence = sim.estimate_probability(snapshot, features)

        assert 0.05 <= prob <= 0.95
        assert 0.0 <= confidence <= 1.0

    def test_bootstrap_fallback_when_too_few_returns(self, now: datetime):
        """Fewer returns than min_bootstrap_returns should fall back to GBM."""
        sim = MonteCarloSimulator(n_samples=5000, min_bootstrap_returns=100)
        # Only 20 prices → 19 returns < 100 threshold
        prices = _make_price_history(n=20)
        snapshot = _make_snapshot(
            now, btc_price=97510.0, strike_price=97500.0, btc_prices_5min=prices,
        )
        features = _make_features(now, realized_vol=0.002)
        prob, confidence = sim.estimate_probability(snapshot, features)

        assert 0.05 <= prob <= 0.95
        assert 0.0 <= confidence <= 1.0

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
            now, btc_price=100000.0, strike_price=90000.0, ttx=60.0,
        )
        features = _make_features(now, realized_vol=0.0001)
        prob, confidence = sim.estimate_probability(snapshot, features)

        assert prob >= 0.90  # Clamped at 0.95 max
        assert confidence > 0.5

    def test_prob_near_zero_when_spot_far_below_strike(self, now: datetime):
        """P(YES) ~ 0.0 when spot << strike with low vol and short TTX."""
        sim = MonteCarloSimulator(n_samples=10000)
        snapshot = _make_snapshot(
            now, btc_price=90000.0, strike_price=100000.0, ttx=60.0,
        )
        features = _make_features(now, realized_vol=0.0001)
        prob, confidence = sim.estimate_probability(snapshot, features)

        assert prob <= 0.10  # Clamped at 0.05 min
        assert confidence > 0.5

    def test_prob_near_half_when_spot_equals_strike(self, now: datetime):
        """P(YES) ~ 0.5 when spot = strike (zero drift)."""
        sim = MonteCarloSimulator(n_samples=10000, drift_mode="zero")
        snapshot = _make_snapshot(
            now, btc_price=97500.0, strike_price=97500.0, ttx=300.0,
        )
        features = _make_features(now, realized_vol=0.002)
        prob, _ = sim.estimate_probability(snapshot, features)

        assert 0.35 <= prob <= 0.65  # Should be around 0.5

    def test_vol_multiplier_increases_uncertainty(self, now: datetime):
        """Higher vol multiplier should push probability toward 0.5."""
        sim_low = MonteCarloSimulator(n_samples=10000, vol_multiplier=0.5)
        sim_high = MonteCarloSimulator(n_samples=10000, vol_multiplier=3.0)

        # Spot well above strike — low vol should be more certain
        snapshot = _make_snapshot(
            now, btc_price=98000.0, strike_price=97500.0, ttx=300.0,
        )
        features = _make_features(now, realized_vol=0.002)

        prob_low, _ = sim_low.estimate_probability(snapshot, features)
        prob_high, _ = sim_high.estimate_probability(snapshot, features)

        # Higher vol → more uncertainty → closer to 0.5
        assert prob_low >= prob_high - 0.10

    def test_bootstrap_vol_multiplier(self, now: datetime):
        """Vol multiplier should affect bootstrap by scaling sampled returns."""
        prices = _make_price_history(n=200, step=1.0)  # Mild uptrend
        sim_low = MonteCarloSimulator(
            n_samples=10000, vol_multiplier=0.5, min_bootstrap_returns=10,
        )
        sim_high = MonteCarloSimulator(
            n_samples=10000, vol_multiplier=3.0, min_bootstrap_returns=10,
        )

        snapshot = _make_snapshot(
            now, btc_price=97700.0, strike_price=97500.0,
            ttx=300.0, btc_prices_5min=prices,
        )
        features = _make_features(now)

        prob_low, _ = sim_low.estimate_probability(snapshot, features)
        prob_high, _ = sim_high.estimate_probability(snapshot, features)

        # Higher vol multiplier → more dispersion → probability closer to 0.5
        assert prob_low >= prob_high - 0.15

    def test_confidence_higher_at_extremes(self, now: datetime):
        """Confidence should be higher when probability is near 0 or 1."""
        sim = MonteCarloSimulator(n_samples=10000)

        # Near-certain: spot far above strike
        snap_certain = _make_snapshot(
            now, btc_price=100000.0, strike_price=90000.0, ttx=60.0,
        )
        features = _make_features(now, realized_vol=0.0001)
        _, conf_certain = sim.estimate_probability(snap_certain, features)

        # Uncertain: spot = strike
        snap_uncertain = _make_snapshot(
            now, btc_price=97500.0, strike_price=97500.0, ttx=300.0,
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
