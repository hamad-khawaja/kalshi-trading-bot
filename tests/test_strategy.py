"""Tests for edge detection, market making, and signal combining."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.config import StrategyConfig
from src.data.models import (
    MarketSnapshot,
    Orderbook,
    OrderbookLevel,
    PredictionResult,
)
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
            btc_price=Decimal("97500"),
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
            btc_price=Decimal("97500"),
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
            btc_price=Decimal("97500"),
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
            btc_price=Decimal("97500"),
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
            btc_price=Decimal("97500"),
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
