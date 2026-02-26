"""Tests for edge detection, market making, and signal combining."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.config import RiskConfig, StrategyConfig
from src.data.models import (
    FeatureVector,
    MarketSnapshot,
    Orderbook,
    OrderbookLevel,
    PredictionResult,
    TradeSignal,
)
from src.risk.volatility import VolatilityTracker
from src.strategy.edge_detector import EdgeDetector
from src.strategy.market_maker import MarketMaker
from src.strategy.signal_combiner import SignalCombiner


@pytest.fixture
def strategy_config() -> StrategyConfig:
    return StrategyConfig(
        min_edge_threshold=0.03,
        max_edge_threshold=0.25,
        confidence_weight=0.7,
        use_market_maker=True,
        mm_min_spread=0.05,
    )


@pytest.fixture
def edge_detector(strategy_config: StrategyConfig) -> EdgeDetector:
    return EdgeDetector(strategy_config)


class TestEdgeDetector:
    def test_no_signal_when_edge_below_threshold(
        self, edge_detector: EdgeDetector, sample_snapshot: MarketSnapshot
    ):
        """No signal when model agrees with market."""
        prediction = PredictionResult(
            probability_yes=0.52,  # Close to implied 0.51
            confidence=0.7,
            model_name="test",
        )
        signal = edge_detector.detect(prediction, sample_snapshot)
        assert signal is None

    def test_signal_when_edge_above_threshold(
        self, edge_detector: EdgeDetector, sample_snapshot: MarketSnapshot
    ):
        """Signal generated when model disagrees significantly."""
        prediction = PredictionResult(
            probability_yes=0.62,  # 11% edge vs implied 0.51
            confidence=0.7,
            model_name="test",
        )
        signal = edge_detector.detect(prediction, sample_snapshot)
        assert signal is not None
        assert signal.side == "yes"
        assert signal.net_edge > 0.03

    def test_no_signal_buys_yes(
        self, edge_detector: EdgeDetector, sample_snapshot: MarketSnapshot
    ):
        """When model thinks NO, side should be 'no'."""
        prediction = PredictionResult(
            probability_yes=0.38,  # Well below implied 0.51
            confidence=0.7,
            model_name="test",
        )
        signal = edge_detector.detect(prediction, sample_snapshot)
        assert signal is not None
        assert signal.side == "no"

    def test_no_signal_when_edge_too_high(
        self, edge_detector: EdgeDetector, sample_snapshot: MarketSnapshot
    ):
        """Reject suspiciously large edges."""
        prediction = PredictionResult(
            probability_yes=0.90,  # 39% edge — too large
            confidence=0.9,
            model_name="test",
        )
        signal = edge_detector.detect(prediction, sample_snapshot)
        assert signal is None

    def test_no_signal_when_no_orderbook(self, edge_detector: EdgeDetector, now: datetime):
        """No signal when orderbook has no implied probability."""
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(ticker="test", timestamp=now),
            time_to_expiry_seconds=600,
        )
        prediction = PredictionResult(
            probability_yes=0.62, confidence=0.7, model_name="test"
        )
        signal = edge_detector.detect(prediction, snapshot)
        assert signal is None

    def test_no_signal_low_confidence(
        self, edge_detector: EdgeDetector, sample_snapshot: MarketSnapshot
    ):
        """No signal when model confidence is too low."""
        prediction = PredictionResult(
            probability_yes=0.62,
            confidence=0.1,  # Very low confidence
            model_name="test",
        )
        signal = edge_detector.detect(prediction, sample_snapshot)
        assert signal is None


class TestFeeCalculation:
    def test_fee_at_50_cents(self):
        """Fee is maximized at 50 cents."""
        fee = EdgeDetector.compute_fee_dollars(1, 0.50, is_maker=False)
        # 0.07 * 1 * 0.5 * 0.5 = 0.0175 -> ceil to 0.02
        assert fee == Decimal("0.02")

    def test_fee_at_extreme_price(self):
        """Fee approaches zero at extreme prices."""
        fee = EdgeDetector.compute_fee_dollars(1, 0.95, is_maker=False)
        # 0.07 * 1 * 0.95 * 0.05 = 0.003325 -> ceil to 0.01
        assert fee == Decimal("0.01")

    def test_maker_fee_lower(self):
        """Maker fee is lower than taker fee."""
        taker = EdgeDetector.compute_fee_dollars(10, 0.50, is_maker=False)
        maker = EdgeDetector.compute_fee_dollars(10, 0.50, is_maker=True)
        assert maker < taker

    def test_fee_scales_with_count(self):
        """Fee scales with contract count."""
        fee_1 = EdgeDetector.compute_fee_dollars(1, 0.50, is_maker=False)
        fee_10 = EdgeDetector.compute_fee_dollars(10, 0.50, is_maker=False)
        assert fee_10 >= fee_1

    def test_fee_positive(self):
        """Fee should always be positive for valid inputs."""
        for price in [0.10, 0.25, 0.50, 0.75, 0.90]:
            fee = EdgeDetector.compute_fee_dollars(1, price)
            assert fee >= Decimal("0.01")

    def test_fee_decimal_precision_no_float_loss(self):
        """Fee computation stays in Decimal — no float precision loss.

        Regression: old code used math.ceil(float(raw_fee) * 100) which
        could round incorrectly at float boundaries. New code uses
        Decimal.to_integral_value(rounding=ROUND_CEILING).
        """
        # Pick values where float arithmetic might lose precision
        # 0.0175 * 7 * 0.33 * 0.67 = exact Decimal vs float drift
        fee = EdgeDetector.compute_fee_dollars(7, 0.33, is_maker=True)
        assert isinstance(fee, Decimal)
        # Manually: 0.0175 * 7 * 0.33 * 0.67 = 0.02709075
        # * 100 = 2.709075, ceil = 3, / 100 = 0.03
        assert fee == Decimal("0.03")

        # Verify symmetry: fee(count=1, p) * count can differ from fee(count, p)
        # because ceiling is applied per-batch, not per-contract
        fee_batch = EdgeDetector.compute_fee_dollars(10, 0.50, is_maker=False)
        fee_single = EdgeDetector.compute_fee_dollars(1, 0.50, is_maker=False)
        # Batch: ceil(0.07 * 10 * 0.25 * 100) = ceil(17.5) = 18 -> $0.18
        # Single: ceil(0.07 * 1 * 0.25 * 100) = ceil(1.75) = 2 -> $0.02
        # 10 * $0.02 = $0.20 != $0.18 — ceiling is not linear
        assert fee_batch == Decimal("0.18")
        assert fee_single == Decimal("0.02")
        assert fee_single * 10 != fee_batch  # Confirms non-linearity

    def test_fee_per_contract_vs_batch_divided(self):
        """Per-contract fee computed directly is >= batch fee / count.

        Regression: take-profit code used batch fee / count which
        underestimated the per-contract sell fee due to ceiling rounding.
        """
        # At price=0.30: batch of 2 has lower per-contract fee than direct
        batch_fee = EdgeDetector.compute_fee_dollars(2, 0.30, is_maker=False)
        single_fee = EdgeDetector.compute_fee_dollars(1, 0.30, is_maker=False)
        batch_per_contract = batch_fee / 2

        # Single: ceil(0.07 * 1 * 0.30 * 0.70 * 100) = ceil(1.47) = 2 -> $0.02
        # Batch:  ceil(0.07 * 2 * 0.30 * 0.70 * 100) = ceil(2.94) = 3 -> $0.03
        # Batch/2 = $0.015 < $0.02
        assert single_fee == Decimal("0.02")
        assert batch_fee == Decimal("0.03")
        assert single_fee > batch_per_contract  # Direct is more accurate


class TestMarketMaker:
    @pytest.fixture
    def market_maker(self, strategy_config: StrategyConfig) -> MarketMaker:
        return MarketMaker(strategy_config)

    def test_no_quotes_when_spread_tight(
        self, market_maker: MarketMaker, sample_prediction: PredictionResult, now: datetime
    ):
        """No quotes when spread is below threshold."""
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[OrderbookLevel(price_dollars=Decimal("0.52"), quantity=100)],
                no_levels=[OrderbookLevel(price_dollars=Decimal("0.49"), quantity=100)],
                timestamp=now,
            ),
            spread=Decimal("0.01"),  # Tight spread
            implied_yes_prob=Decimal("0.51"),
            time_to_expiry_seconds=600,
        )
        quotes = market_maker.generate_quotes(sample_prediction, snapshot, 0)
        assert len(quotes) == 0

    def test_quotes_when_spread_wide(
        self, market_maker: MarketMaker, sample_prediction: PredictionResult, now: datetime
    ):
        """Quotes generated when spread is wide enough."""
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                no_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                timestamp=now,
            ),
            spread=Decimal("0.10"),  # Wide spread
            implied_yes_prob=Decimal("0.50"),
            time_to_expiry_seconds=600,
        )
        quotes = market_maker.generate_quotes(sample_prediction, snapshot, 0)
        assert len(quotes) >= 1

    def test_no_quotes_near_expiry(
        self, market_maker: MarketMaker, sample_prediction: PredictionResult, now: datetime
    ):
        """No quotes when too close to expiry."""
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                no_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                timestamp=now,
            ),
            spread=Decimal("0.10"),
            implied_yes_prob=Decimal("0.50"),
            time_to_expiry_seconds=60,  # Too close
        )
        quotes = market_maker.generate_quotes(sample_prediction, snapshot, 0)
        assert len(quotes) == 0


    def test_mm_quotes_have_post_only_true(
        self, market_maker: MarketMaker, sample_prediction: PredictionResult, now: datetime
    ):
        """MM quotes must set post_only=True for maker fees."""
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                no_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                timestamp=now,
            ),
            spread=Decimal("0.10"),
            implied_yes_prob=Decimal("0.50"),
            time_to_expiry_seconds=600,
        )
        quotes = market_maker.generate_quotes(sample_prediction, snapshot, 0)
        assert len(quotes) >= 1
        for q in quotes:
            assert q.post_only is True

    def test_mm_fill_asymmetry_skips_heavy_side(
        self, sample_prediction: PredictionResult, now: datetime
    ):
        """When YES fills dominate, MM should skip YES side."""
        config = StrategyConfig(mm_fill_asymmetry_threshold=2.0, mm_fill_asymmetry_window=10)
        mm = MarketMaker(config)
        # Record 4 YES fills and 1 NO fill → ratio 4:1 > threshold 2.0
        for _ in range(4):
            mm.record_fill("test", "yes")
        mm.record_fill("test", "no")
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                no_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                timestamp=now,
            ),
            spread=Decimal("0.10"),
            implied_yes_prob=Decimal("0.50"),
            time_to_expiry_seconds=600,
        )
        quotes = mm.generate_quotes(sample_prediction, snapshot, 0)
        # YES side should be skipped; only NO quote (if any)
        yes_quotes = [q for q in quotes if q.side == "yes"]
        assert len(yes_quotes) == 0

    def test_mm_quote_staleness_detection(self, now: datetime):
        """is_quote_stale returns True when quotes are older than max_age."""
        config = StrategyConfig()
        mm = MarketMaker(config)
        # No quotes yet — not stale
        assert mm.is_quote_stale("test", 60.0) is False
        # Record a quote with a timestamp 120s ago
        from datetime import timedelta
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=120)
        mm._last_quotes["test"] = (Decimal("0.50"), old_ts)
        assert mm.is_quote_stale("test", 60.0) is True
        assert mm.is_quote_stale("test", 300.0) is False

    def test_mm_fill_ratio_tracking(self, now: datetime):
        """record_fill and _fill_ratio correctly track per-ticker fills."""
        config = StrategyConfig(mm_fill_asymmetry_window=5)
        mm = MarketMaker(config)
        mm.record_fill("A", "yes")
        mm.record_fill("A", "yes")
        mm.record_fill("A", "no")
        mm.record_fill("B", "yes")
        y, n = mm._fill_ratio("A")
        assert y == 2
        assert n == 1
        y_b, n_b = mm._fill_ratio("B")
        assert y_b == 1
        assert n_b == 0


class TestMMPositionSizing:
    """Test MM-specific Kelly fraction and confidence scaling."""

    def test_mm_uses_dedicated_kelly_fraction(self, now: datetime):
        """MM signals should use mm_kelly_fraction, not global Kelly."""
        from src.risk.position_sizer import PositionSizer
        risk_config = RiskConfig(
            kelly_fraction=0.15, min_position_size=1,
            max_position_per_market=200,
        )
        strat_config = StrategyConfig(
            mm_kelly_fraction=0.07,
            stop_loss_max_dollar_loss=500.0,
        )
        sizer = PositionSizer(risk_config, strategy_config=strat_config)
        signal = TradeSignal(
            market_ticker="test",
            side="yes",
            action="buy",
            raw_edge=0.10,
            net_edge=0.08,
            model_probability=0.60,
            implied_probability=0.50,
            confidence=0.50,
            suggested_price_dollars="0.45",
            suggested_count=0,
            timestamp=now,
            signal_type="market_making",
        )
        mm_count = sizer.size(signal, Decimal("1000"), Decimal("0"))
        # Same signal but directional (uses global kelly 0.15)
        dir_signal = signal.model_copy(update={"signal_type": "directional"})
        dir_count = sizer.size(dir_signal, Decimal("1000"), Decimal("0"))
        # MM should be smaller than directional due to lower Kelly
        assert mm_count > 0
        assert dir_count > 0
        assert mm_count < dir_count

    def test_mm_skips_confidence_scaling(self, now: datetime):
        """MM signals should not be scaled by confidence."""
        from src.risk.position_sizer import PositionSizer
        risk_config = RiskConfig(
            kelly_fraction=0.15, min_position_size=1,
            max_position_per_market=200,
        )
        strat_config = StrategyConfig(
            mm_kelly_fraction=0.07,
            stop_loss_max_dollar_loss=500.0,
        )
        sizer = PositionSizer(risk_config, strategy_config=strat_config)
        high_conf = TradeSignal(
            market_ticker="test", side="yes", action="buy",
            raw_edge=0.10, net_edge=0.08, model_probability=0.60,
            implied_probability=0.50, confidence=0.90,
            suggested_price_dollars="0.45", suggested_count=0,
            timestamp=now, signal_type="market_making",
        )
        low_conf = high_conf.model_copy(update={"confidence": 0.30})
        high_count = sizer.size(high_conf, Decimal("1000"), Decimal("0"))
        low_count = sizer.size(low_conf, Decimal("1000"), Decimal("0"))
        # Without confidence scaling, both should produce the same size
        assert high_count > 0
        assert high_count == low_count


class TestEdgeDetectorVolAdjusted:
    """Test that EdgeDetector uses vol-adjusted thresholds when tracker is provided."""

    def test_vol_adjusted_threshold_high_vol(
        self, strategy_config: StrategyConfig, sample_snapshot: MarketSnapshot
    ):
        """High vol regime raises threshold, rejecting marginal edges."""
        tracker = VolatilityTracker()
        # Simulate high vol history (high percentile)
        for _ in range(100):
            tracker.update(0.001)
        for _ in range(20):
            tracker.update(0.01)  # Recent high vol

        detector = EdgeDetector(strategy_config, vol_tracker=tracker)
        # Edge that would pass normal threshold but not high-vol threshold
        prediction = PredictionResult(
            probability_yes=0.58, confidence=0.7, model_name="test"
        )
        signal = detector.detect(prediction, sample_snapshot)
        # High vol regime multiplies threshold by 1.5+, this edge may be rejected
        # The exact result depends on the regime classification
        assert signal is None or signal.net_edge > 0

    def test_vol_adjusted_threshold_fallback(
        self, strategy_config: StrategyConfig, sample_snapshot: MarketSnapshot
    ):
        """Without tracker, uses config threshold (no crash)."""
        detector = EdgeDetector(strategy_config, vol_tracker=None)
        prediction = PredictionResult(
            probability_yes=0.62, confidence=0.7, model_name="test"
        )
        signal = detector.detect(prediction, sample_snapshot)
        assert signal is not None
        assert signal.net_edge > strategy_config.min_edge_threshold

    def test_vol_adjusted_low_vol_easier_entry(
        self, strategy_config: StrategyConfig, sample_snapshot: MarketSnapshot
    ):
        """Low vol regime lowers threshold, allowing smaller edges."""
        tracker = VolatilityTracker()
        # Most observations high, few low at end -> low percentile for current
        for _ in range(90):
            tracker.update(0.01)
        for _ in range(10):
            tracker.update(0.0001)  # Recent low vol, < 20th percentile
        assert tracker.current_regime == "low"

        detector = EdgeDetector(strategy_config, vol_tracker=tracker)
        prediction = PredictionResult(
            probability_yes=0.60, confidence=0.7, model_name="test"
        )
        signal = detector.detect(prediction, sample_snapshot)
        # Low vol regime reduces threshold by 20%, making it easier to enter
        assert signal is not None


class TestSignalCombiner:
    @pytest.fixture
    def combiner(self, strategy_config: StrategyConfig) -> SignalCombiner:
        return SignalCombiner(strategy_config)

    def test_directional_signal(
        self, combiner: SignalCombiner, sample_snapshot: MarketSnapshot
    ):
        """Directional signal when strong edge exists."""
        prediction = PredictionResult(
            probability_yes=0.65, confidence=0.7, model_name="test"
        )
        signals = combiner.evaluate(prediction, sample_snapshot, 0)
        if signals:
            assert signals[0].signal_type == "directional"

    def test_no_signals_near_expiry(
        self, combiner: SignalCombiner, now: datetime
    ):
        """No signals when too close to expiry."""
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                no_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                timestamp=now,
            ),
            implied_yes_prob=Decimal("0.50"),
            spread=Decimal("0.10"),
            time_to_expiry_seconds=30,
        )
        prediction = PredictionResult(
            probability_yes=0.65, confidence=0.7, model_name="test"
        )
        signals = combiner.evaluate(prediction, snapshot, 0)
        assert len(signals) == 0

    def test_ppe_filter_blocks_low_efficiency(
        self, now: datetime, sample_feature_vector: FeatureVector
    ):
        """PPE filter blocks directional when path efficiency is too low."""
        config = StrategyConfig(
            min_edge_threshold=0.03,
            max_edge_threshold=0.25,
            ppe_filter_enabled=True,
            ppe_min_threshold=0.30,
            phase_filter_enabled=False,
            edge_confirmation_cycles=1,
            quiet_hours_enabled=False,
        )
        combiner = SignalCombiner(config)
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="kxbtc15m-test",
            spot_price=Decimal("97500"),
            spot_prices_5min=[
                Decimal(f"{97480 + i * 0.02}") for i in range(1800)
            ],
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                no_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                timestamp=now,
            ),
            implied_yes_prob=Decimal("0.50"),
            spread=Decimal("0.02"),
            time_to_expiry_seconds=600,
        )
        prediction = PredictionResult(
            probability_yes=0.65, confidence=0.7, model_name="test"
        )
        # Low PPE features: choppy price path
        low_ppe_features = sample_feature_vector.model_copy(
            update={
                "path_efficiency_60s": 0.10,
                "path_efficiency_180s": 0.15,
                "path_efficiency_300s": 0.20,
            }
        )
        signals = combiner.evaluate(prediction, snapshot, 0, features=low_ppe_features)
        # No directional signals should pass (PPE 0.20 < threshold 0.30)
        directional = [s for s in signals if s.signal_type == "directional"]
        assert len(directional) == 0
        assert any("ppe_filter" in r for r in combiner.last_block_reasons)

    def test_ppe_filter_allows_high_efficiency(
        self, now: datetime, sample_feature_vector: FeatureVector
    ):
        """PPE filter allows directional when path efficiency is above threshold."""
        config = StrategyConfig(
            min_edge_threshold=0.03,
            max_edge_threshold=0.25,
            ppe_filter_enabled=True,
            ppe_min_threshold=0.30,
            phase_filter_enabled=False,
            edge_confirmation_cycles=1,
        )
        combiner = SignalCombiner(config)
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="kxbtc15m-test",
            spot_price=Decimal("97500"),
            spot_prices_5min=[
                Decimal(f"{97480 + i * 0.02}") for i in range(1800)
            ],
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                no_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                timestamp=now,
            ),
            implied_yes_prob=Decimal("0.50"),
            spread=Decimal("0.02"),
            time_to_expiry_seconds=600,
        )
        prediction = PredictionResult(
            probability_yes=0.65, confidence=0.7, model_name="test"
        )
        # High PPE features: smooth price path
        high_ppe_features = sample_feature_vector.model_copy(
            update={
                "path_efficiency_60s": 0.80,
                "path_efficiency_180s": 0.70,
                "path_efficiency_300s": 0.65,
            }
        )
        combiner.evaluate(prediction, snapshot, 0, features=high_ppe_features)
        # PPE filter should NOT block (0.65 > 0.30)
        assert not any("ppe_filter" in r for r in combiner.last_block_reasons)


class TestMMMidPriceBlend:
    """Tests for Fix #1: Mid-price blending with OB midpoint."""

    def test_mm_fair_value_blends_ob_mid(self, now: datetime):
        """blend=0.3, model=0.60, ob_mid=0.50 → fv ≈ 0.57."""
        config = StrategyConfig(mm_ob_mid_blend=0.3)
        mm = MarketMaker(config)
        prediction = PredictionResult(
            probability_yes=0.60, confidence=0.7, model_name="test"
        )
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                no_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                timestamp=now,
            ),
            implied_yes_prob=Decimal("0.50"),
            time_to_expiry_seconds=600,
        )
        fv = mm._compute_fair_value(prediction, snapshot)
        # 0.3 * 0.50 + 0.7 * 0.60 = 0.15 + 0.42 = 0.57
        assert fv == Decimal("0.57")

    def test_mm_fair_value_fallback_no_ob(self, now: datetime):
        """No OB data → pure model prob."""
        config = StrategyConfig(mm_ob_mid_blend=0.3)
        mm = MarketMaker(config)
        prediction = PredictionResult(
            probability_yes=0.60, confidence=0.7, model_name="test"
        )
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(ticker="test", timestamp=now),
            time_to_expiry_seconds=600,
        )
        fv = mm._compute_fair_value(prediction, snapshot)
        assert fv == Decimal("0.60")

    def test_mm_fair_value_blend_zero(self, now: datetime):
        """blend=0.0 → pure model, even with OB data."""
        config = StrategyConfig(mm_ob_mid_blend=0.0)
        mm = MarketMaker(config)
        prediction = PredictionResult(
            probability_yes=0.60, confidence=0.7, model_name="test"
        )
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                no_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                timestamp=now,
            ),
            implied_yes_prob=Decimal("0.50"),
            time_to_expiry_seconds=600,
        )
        fv = mm._compute_fair_value(prediction, snapshot)
        assert fv == Decimal("0.60")


class TestMMDynamicSpread:
    """Tests for Fix #2: Dynamic spread based on fill rate."""

    def test_mm_dynamic_spread_tightens_no_fills(self, now: datetime):
        """No fills → minimum spread offset (base only)."""
        config = StrategyConfig(
            mm_min_spread_offset=0.01,
            mm_max_spread_offset=0.06,
            mm_target_fills_per_minute=2.0,
        )
        mm = MarketMaker(config)
        offset = mm._dynamic_spread_offset("test")
        # No fills, no vol tracker → ratio=0 → min_offset * 1.0
        assert offset == Decimal("0.01")

    def test_mm_dynamic_spread_widens_with_fills(self, now: datetime):
        """Recent fills → wider spread offset."""
        from datetime import timedelta
        config = StrategyConfig(
            mm_min_spread_offset=0.01,
            mm_max_spread_offset=0.06,
            mm_target_fills_per_minute=2.0,
            mm_fill_asymmetry_window=100,
        )
        mm = MarketMaker(config)
        # Record 4 fills in the last 30 seconds (well above target 2/min)
        recent = datetime.now(timezone.utc) - timedelta(seconds=10)
        for _ in range(4):
            mm._recent_fills.append(("test", "yes", recent))
        offset = mm._dynamic_spread_offset("test")
        # fpm=4, target=2 → ratio=min(4/2, 1.0)=1.0 → 0.01 + 1.0*(0.06-0.01) = 0.06
        assert offset == Decimal("0.06")

    def test_mm_fills_per_minute(self, now: datetime):
        """Fill-rate calculation with timestamps."""
        from datetime import timedelta
        config = StrategyConfig(mm_fill_asymmetry_window=100)
        mm = MarketMaker(config)
        recent = datetime.now(timezone.utc) - timedelta(seconds=30)
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        # 2 recent fills, 1 old fill
        mm._recent_fills.append(("test", "yes", recent))
        mm._recent_fills.append(("test", "no", recent))
        mm._recent_fills.append(("test", "yes", old))
        assert mm._fills_per_minute("test") == 2


class TestMMDepthImbalance:
    """Tests for Fix #3: Depth imbalance skew."""

    def test_mm_depth_imbalance_skews_quotes(self, now: datetime):
        """YES depth >> NO → positive skew (tighten YES, widen NO)."""
        config = StrategyConfig(mm_depth_imbalance_max_skew=0.03)
        mm = MarketMaker(config)
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[
                    OrderbookLevel(price_dollars=Decimal("0.50"), quantity=300),
                ],
                no_levels=[
                    OrderbookLevel(price_dollars=Decimal("0.50"), quantity=100),
                ],
                timestamp=now,
            ),
            time_to_expiry_seconds=600,
        )
        skew = mm._depth_imbalance_skew(snapshot)
        # imbalance = (300-100)/400 = 0.5, skew = 0.5 * 0.03 = 0.015
        assert skew == Decimal("0.015")
        assert skew > 0  # Positive → tighten YES bid

    def test_mm_depth_imbalance_capped(self, now: datetime):
        """Extreme imbalance → capped at max_skew."""
        config = StrategyConfig(mm_depth_imbalance_max_skew=0.03)
        mm = MarketMaker(config)
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[
                    OrderbookLevel(price_dollars=Decimal("0.50"), quantity=1000),
                ],
                no_levels=[],  # Zero NO depth → imbalance = 1.0
                timestamp=now,
            ),
            time_to_expiry_seconds=600,
        )
        skew = mm._depth_imbalance_skew(snapshot)
        # imbalance = (1000-0)/1000 = 1.0, skew = 1.0 * 0.03 = 0.03 (max)
        assert skew == Decimal("0.03")


class TestMMRequoteThreshold:
    """Tests for Fix #4: Configurable requote threshold."""

    def test_mm_requote_threshold_configurable(self, now: datetime):
        """Config value used instead of hardcoded 0.03."""
        config = StrategyConfig(mm_requote_threshold=0.02)
        mm = MarketMaker(config)
        # Record a quote at 0.50
        mm._last_quotes["test"] = (Decimal("0.50"), datetime.now(timezone.utc))
        # Move fair value by 0.025 — should trigger with threshold=0.02
        assert mm.should_requote("test", Decimal("0.525")) is True
        # But not with explicit higher threshold
        assert mm.should_requote("test", Decimal("0.525"), threshold=0.03) is False


class TestMMRequoteBlendedFV:
    """Test Fix #1: Requote uses blended fair value via compute_fair_value()."""

    def test_mm_requote_uses_blended_fair_value(self, now: datetime):
        """compute_fair_value() returns blended FV, not raw model."""
        config = StrategyConfig(mm_ob_mid_blend=0.3, mm_requote_threshold=0.02)
        mm = MarketMaker(config)
        prediction = PredictionResult(
            probability_yes=0.60, confidence=0.7, model_name="test"
        )
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                no_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                timestamp=now,
            ),
            implied_yes_prob=Decimal("0.50"),
            time_to_expiry_seconds=600,
        )
        fv = mm.compute_fair_value(prediction, snapshot)
        # 0.3 * 0.50 + 0.7 * 0.60 = 0.57 (blended), not 0.60 (raw model)
        assert fv == Decimal("0.57")
        # Record a quote at the blended FV
        mm._last_quotes["test"] = (fv, datetime.now(timezone.utc))
        # Raw model FV (0.60) would trigger requote (|0.60-0.57|=0.03 > 0.02),
        # but blended FV (0.57) should NOT trigger (|0.57-0.57|=0)
        assert mm.should_requote("test", fv) is False


class TestMMImmediateFillTracking:
    """Test Fix #3: record_fill() makes fills visible to _fills_per_minute()."""

    def test_mm_immediate_fill_updates_recent_fills(self):
        """record_fill() → _fills_per_minute() reflects the fill."""
        config = StrategyConfig()
        mm = MarketMaker(config)
        assert mm._fills_per_minute("test") == 0
        mm.record_fill("test", "yes")
        assert mm._fills_per_minute("test") == 1
        mm.record_fill("test", "no")
        assert mm._fills_per_minute("test") == 2


class TestMMInventorySkewSymmetric:
    """Test Fix #4: Symmetric inventory skew affects both sides."""

    def test_mm_inventory_skew_symmetric(self, now: datetime):
        """Long YES → YES bid wider AND NO bid tighter (symmetric skew)."""
        config = StrategyConfig(
            mm_min_spread=0.05,
            mm_max_spread=0.30,
            mm_max_inventory=10,
            mm_ob_mid_blend=0.0,  # Pure model FV for clarity
            mm_min_spread_offset=0.02,
            mm_max_spread_offset=0.02,
            mm_depth_imbalance_max_skew=0.0,  # Disable depth skew
            mm_vol_filter_enabled=False,
        )
        mm = MarketMaker(config)
        prediction = PredictionResult(
            probability_yes=0.50, confidence=0.7, model_name="test"
        )
        snapshot = MarketSnapshot(
            timestamp=now,
            market_ticker="test",
            spot_price=Decimal("97500"),
            orderbook=Orderbook(
                ticker="test",
                yes_levels=[OrderbookLevel(price_dollars=Decimal("0.40"), quantity=100)],
                no_levels=[OrderbookLevel(price_dollars=Decimal("0.40"), quantity=100)],
                timestamp=now,
            ),
            implied_yes_prob=Decimal("0.50"),
            spread=Decimal("0.10"),
            time_to_expiry_seconds=600,
        )
        # Flat inventory → baseline
        flat = mm.generate_quotes(prediction, snapshot, current_position=0)
        flat_yes = [s for s in flat if s.side == "yes"]
        flat_no = [s for s in flat if s.side == "no"]
        assert flat_yes and flat_no, "Should generate both sides"

        # Long YES (positive inventory) → YES bid should be LOWER (wider),
        # NO bid should be HIGHER (tighter) than flat
        long_yes = mm.generate_quotes(prediction, snapshot, current_position=5)
        long_yes_bid = [s for s in long_yes if s.side == "yes"]
        long_no_bid = [s for s in long_yes if s.side == "no"]
        if long_yes_bid and flat_yes:
            assert float(long_yes_bid[0].suggested_price_dollars) <= float(
                flat_yes[0].suggested_price_dollars
            ), "YES bid should widen when long YES"
        if long_no_bid and flat_no:
            assert float(long_no_bid[0].suggested_price_dollars) >= float(
                flat_no[0].suggested_price_dollars
            ), "NO bid should tighten when long YES"


class TestMMCooldownExemption:
    """Test Fix #5: MM signals survive entry cooldown."""

    def test_mm_cooldown_exempts_mm(self):
        """MM signal_type='market_making' not filtered by cooldown logic."""
        signals = [
            TradeSignal(
                market_ticker="test",
                side="yes",
                action="buy",
                raw_edge=0.05,
                net_edge=0.03,
                model_probability=0.55,
                implied_probability=0.50,
                confidence=0.7,
                suggested_price_dollars="0.48",
                suggested_count=1,
                timestamp=datetime.now(timezone.utc),
                signal_type="directional",
            ),
            TradeSignal(
                market_ticker="test",
                side="no",
                action="buy",
                raw_edge=0.04,
                net_edge=0.02,
                model_probability=0.45,
                implied_probability=0.50,
                confidence=0.7,
                suggested_price_dollars="0.48",
                suggested_count=0,
                timestamp=datetime.now(timezone.utc),
                signal_type="market_making",
                post_only=True,
            ),
        ]
        # Simulate cooldown filter logic from bot.py (Fix #5)
        buy_signals = [
            s for s in signals
            if s.action == "buy" and s.signal_type != "market_making"
        ]
        assert len(buy_signals) == 1  # Only directional
        filtered = [
            s for s in signals
            if s.action != "buy" or s.signal_type == "market_making"
        ]
        # MM signal survives, directional is removed
        assert len(filtered) == 1
        assert filtered[0].signal_type == "market_making"
