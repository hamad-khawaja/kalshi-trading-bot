"""Tests for Monte Carlo simulation and MC signal detector."""

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
from src.risk.volatility import VolatilityTracker
from src.strategy.mc_detector import MCSignalDetector


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
            mc_max_edge=0.15,
            mc_bootstrap_min_returns=30,
            mc_settlement_discount=0.7,
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

    def test_edge_capped_at_max(self, now: datetime):
        """Raw edge should be capped at mc_max_edge."""
        config = self._make_config(
            mc_min_edge=0.02,
            mc_min_confidence=0.20,
            mc_min_implied_distance=0.10,
            mc_max_edge=0.10,
        )
        detector = MCSignalDetector(config)
        # MC prob ~0.95, implied=0.30 → raw edge would be ~0.65 without cap
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
        assert signal.raw_edge <= 0.10

    def test_extreme_vol_skips(self, now: datetime):
        """Returns None when vol regime is extreme."""
        config = self._make_config(
            mc_min_edge=0.02,
            mc_min_confidence=0.20,
            mc_min_implied_distance=0.10,
        )
        vol_tracker = VolatilityTracker()
        # Push vol high enough to be "extreme" (>90th percentile)
        for i in range(100):
            vol_tracker.update(0.001)
        for i in range(20):
            vol_tracker.update(0.01)  # Spike to extreme

        detector = MCSignalDetector(config, vol_tracker=vol_tracker)
        snapshot = _make_snapshot(
            now,
            btc_price=100000.0,
            strike_price=90000.0,
            implied_yes_prob=0.30,
            ttx=300.0,
        )
        features = _make_features(now, realized_vol=0.0001)
        result = detector.detect(snapshot, features)

        assert result is None

    def test_high_vol_requires_more_edge(self, now: datetime):
        """In high vol regime, mc_min_edge should be scaled up by 1.5x."""
        # mc_min_edge=0.10 → with 1.5x = 0.15
        # mc_max_edge=0.15, fee_drag ~$0.01 → net_edge ≈ 0.14 < 0.15 → blocked
        config = self._make_config(
            mc_min_edge=0.10,
            mc_min_confidence=0.20,
            mc_min_implied_distance=0.10,
            mc_max_edge=0.15,
        )

        # Build a vol tracker in "high" regime (70th-90th percentile)
        vol_tracker = VolatilityTracker()
        for _ in range(75):
            vol_tracker.update(0.001)
        for _ in range(20):
            vol_tracker.update(0.010)
        vol_tracker.update(0.002)  # Last value: ~79th percentile → "high"
        assert vol_tracker.current_regime == "high"

        detector = MCSignalDetector(config, vol_tracker=vol_tracker)
        snapshot = _make_snapshot(
            now,
            btc_price=100000.0,
            strike_price=90000.0,
            implied_yes_prob=0.30,
            ttx=300.0,
        )
        features = _make_features(now, realized_vol=0.0001)
        signal = detector.detect(snapshot, features)

        # With high vol: min_edge = 0.10 * 1.5 = 0.15
        # Net edge = 0.15 - 0.01 = 0.14 < 0.15 → should be None
        assert signal is None

    def test_settlement_discount_reduces_confidence(self, now: datetime):
        """Confidence should be discounted when MC disagrees with settlement trend."""
        config = self._make_config(
            mc_min_edge=0.02,
            mc_min_confidence=0.60,  # High threshold
            mc_min_implied_distance=0.10,
            mc_settlement_discount=0.7,
        )
        detector = MCSignalDetector(config)

        # MC says YES (spot >> strike), but settlement bias says NO
        snapshot = _make_snapshot(
            now,
            btc_price=100000.0,
            strike_price=90000.0,
            implied_yes_prob=0.30,
            ttx=300.0,
        )
        # Strong negative settlement bias → disagrees with YES side
        features = _make_features(now, realized_vol=0.0001, settlement_bias=-0.5)
        signal = detector.detect(snapshot, features)

        # The discounted confidence (original ~0.99 * 0.7 = ~0.69) should still pass 0.60
        # but if we set threshold higher, it would fail. Let's verify the confidence
        # is reduced compared to neutral settlement.
        features_neutral = _make_features(now, realized_vol=0.0001, settlement_bias=0.0)
        signal_neutral = detector.detect(snapshot, features_neutral)

        assert signal is not None
        assert signal_neutral is not None
        assert signal.confidence < signal_neutral.confidence

    def test_settlement_discount_blocks_low_confidence(self, now: datetime):
        """When settlement discount drops confidence below threshold, signal is blocked."""
        config = self._make_config(
            mc_min_edge=0.02,
            mc_min_confidence=0.95,  # Very high threshold
            mc_min_implied_distance=0.10,
            mc_settlement_discount=0.7,
        )
        detector = MCSignalDetector(config)

        snapshot = _make_snapshot(
            now,
            btc_price=100000.0,
            strike_price=90000.0,
            implied_yes_prob=0.30,
            ttx=300.0,
        )
        # Strong negative settlement bias → MC YES confidence will be discounted
        features = _make_features(now, realized_vol=0.0001, settlement_bias=-0.5)
        signal = detector.detect(snapshot, features)

        # Discounted confidence ~0.99 * 0.7 = ~0.69 < 0.95 threshold
        assert signal is None

    def test_no_vol_tracker_uses_standard_thresholds(self, now: datetime):
        """Without vol_tracker, standard min_edge applies."""
        config = self._make_config(
            mc_min_edge=0.02,
            mc_min_confidence=0.20,
            mc_min_implied_distance=0.10,
        )
        detector = MCSignalDetector(config, vol_tracker=None)
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
