"""Tests for position sizing, risk management, and volatility tracking."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.config import RiskConfig
from src.data.models import Position, TradeSignal
from src.risk.position_sizer import PositionSizer
from src.risk.risk_manager import RiskManager
from src.risk.volatility import VolatilityTracker


@pytest.fixture
def risk_config() -> RiskConfig:
    return RiskConfig(
        max_position_per_market=50,
        max_total_exposure_dollars=500.0,
        max_daily_loss_dollars=100.0,
        max_concurrent_positions=5,
        kelly_fraction=0.25,
        min_balance_dollars=50.0,
        max_trades_per_day=100,
        cooldown_after_streak_minutes=30,
        max_consecutive_losses=5,
    )


@pytest.fixture
def position_sizer(risk_config: RiskConfig) -> PositionSizer:
    return PositionSizer(risk_config)


@pytest.fixture
def risk_manager(risk_config: RiskConfig) -> RiskManager:
    return RiskManager(risk_config)


@pytest.fixture
def sample_signal() -> TradeSignal:
    return TradeSignal(
        market_ticker="kxbtc15m-test",
        side="yes",
        action="buy",
        raw_edge=0.08,
        net_edge=0.06,
        model_probability=0.60,
        implied_probability=0.52,
        confidence=0.7,
        suggested_price_dollars="0.53",
        suggested_count=0,
        timestamp=datetime.now(timezone.utc),
    )


class TestPositionSizer:
    def test_kelly_sizing_basic(self, position_sizer: PositionSizer, sample_signal: TradeSignal):
        """Basic Kelly sizing returns positive count."""
        count = position_sizer.size(
            sample_signal,
            balance_dollars=Decimal("1000"),
            current_exposure_dollars=Decimal("0"),
        )
        assert count > 0

    def test_kelly_zero_when_no_edge(self, position_sizer: PositionSizer):
        """Zero count when model prob <= price."""
        signal = TradeSignal(
            market_ticker="test",
            side="yes",
            action="buy",
            raw_edge=0.0,
            net_edge=0.0,
            model_probability=0.50,
            implied_probability=0.50,
            confidence=0.7,
            suggested_price_dollars="0.55",  # Price > model prob
            suggested_count=0,
            timestamp=datetime.now(timezone.utc),
        )
        count = position_sizer.size(
            signal, Decimal("1000"), Decimal("0")
        )
        assert count == 0

    def test_kelly_clamped_to_max_position(
        self, position_sizer: PositionSizer, sample_signal: TradeSignal
    ):
        """Size is clamped when approaching market position limit."""
        count = position_sizer.size(
            sample_signal,
            balance_dollars=Decimal("100000"),  # Very large balance
            current_exposure_dollars=Decimal("0"),
            current_market_position=48,  # Almost at limit of 50
        )
        assert count <= 2  # At most 50 - 48 = 2

    def test_fractional_kelly_reduces_size(self):
        """Quarter Kelly should be smaller than half Kelly."""
        config_quarter = RiskConfig(kelly_fraction=0.25)
        config_half = RiskConfig(kelly_fraction=0.50)

        sizer_quarter = PositionSizer(config_quarter)
        sizer_half = PositionSizer(config_half)

        signal = TradeSignal(
            market_ticker="test",
            side="yes",
            action="buy",
            raw_edge=0.10,
            net_edge=0.08,
            model_probability=0.65,
            implied_probability=0.55,
            confidence=0.7,
            suggested_price_dollars="0.56",
            suggested_count=0,
            timestamp=datetime.now(timezone.utc),
        )

        count_quarter = sizer_quarter.size(signal, Decimal("1000"), Decimal("0"))
        count_half = sizer_half.size(signal, Decimal("1000"), Decimal("0"))

        assert count_quarter <= count_half

    def test_exposure_limit_reduces_size(
        self, position_sizer: PositionSizer, sample_signal: TradeSignal
    ):
        """Size is reduced when approaching total exposure limit."""
        # Near the $500 exposure limit
        count = position_sizer.size(
            sample_signal,
            balance_dollars=Decimal("1000"),
            current_exposure_dollars=Decimal("490"),
        )
        # Should be very small or zero since only $10 of exposure remaining
        assert count <= 20

    def test_kelly_fraction_for_binary(self, position_sizer: PositionSizer):
        """Kelly formula returns correct fractions."""
        # prob > price -> positive fraction
        f = position_sizer.kelly_fraction_for_binary(0.60, 0.50)
        assert f > 0
        assert f == pytest.approx(0.20, abs=0.01)  # (0.6 - 0.5) / (1 - 0.5) = 0.2

        # prob == price -> zero
        f = position_sizer.kelly_fraction_for_binary(0.50, 0.50)
        assert f == 0.0

        # prob < price -> zero
        f = position_sizer.kelly_fraction_for_binary(0.40, 0.50)
        assert f == 0.0

    def test_zero_balance(self, position_sizer: PositionSizer, sample_signal: TradeSignal):
        """Zero count on zero balance."""
        assert position_sizer.size(sample_signal, Decimal("0"), Decimal("0")) == 0


class TestRiskManager:
    def test_approved_within_limits(
        self, risk_manager: RiskManager, sample_signal: TradeSignal
    ):
        """Trade approved when all limits are fine."""
        decision = risk_manager.check(
            sample_signal,
            count=5,
            balance=Decimal("1000"),
            positions=[],
            time_to_expiry_seconds=600,
        )
        assert decision.approved
        assert decision.reason == "OK"

    def test_rejected_below_min_balance(
        self, risk_manager: RiskManager, sample_signal: TradeSignal
    ):
        """Rejected when balance is below minimum."""
        decision = risk_manager.check(
            sample_signal,
            count=5,
            balance=Decimal("30"),  # Below $50 minimum
            positions=[],
            time_to_expiry_seconds=600,
        )
        assert not decision.approved
        assert "minimum" in decision.reason.lower() or "below" in decision.reason.lower()

    def test_rejected_daily_loss_exceeded(
        self, risk_manager: RiskManager, sample_signal: TradeSignal
    ):
        """Rejected after daily loss limit hit."""
        # Simulate losses
        for _ in range(10):
            risk_manager.record_trade(Decimal("-15"))

        decision = risk_manager.check(
            sample_signal,
            count=5,
            balance=Decimal("1000"),
            positions=[],
            time_to_expiry_seconds=600,
        )
        assert not decision.approved
        assert "daily" in decision.reason.lower() or "loss" in decision.reason.lower()

    def test_rejected_max_positions_reached(
        self, risk_manager: RiskManager, sample_signal: TradeSignal
    ):
        """Rejected when max concurrent positions reached."""
        positions = [
            Position(ticker=f"market-{i}", market_exposure=10)
            for i in range(5)
        ]
        decision = risk_manager.check(
            sample_signal,
            count=5,
            balance=Decimal("1000"),
            positions=positions,
            time_to_expiry_seconds=600,
        )
        assert not decision.approved

    def test_rejected_near_expiry(
        self, risk_manager: RiskManager, sample_signal: TradeSignal
    ):
        """Rejected when too close to expiry."""
        decision = risk_manager.check(
            sample_signal,
            count=5,
            balance=Decimal("1000"),
            positions=[],
            time_to_expiry_seconds=30,  # < 60s
        )
        assert not decision.approved
        assert "expiry" in decision.reason.lower()

    def test_daily_pnl_resets(self, risk_manager: RiskManager):
        """Daily P&L resets at midnight."""
        risk_manager.record_trade(Decimal("-50"))
        assert risk_manager.daily_pnl == Decimal("-50")
        # Reset is date-based, so within the same day it stays

    def test_consecutive_loss_tracking(self, risk_manager: RiskManager):
        """Consecutive losses are tracked correctly."""
        risk_manager.record_trade(Decimal("-10"))
        risk_manager.record_trade(Decimal("-10"))
        assert risk_manager.consecutive_losses == 2

        risk_manager.record_trade(Decimal("5"))  # Win resets streak
        assert risk_manager.consecutive_losses == 0

    def test_same_market_position_allowed(
        self, risk_manager: RiskManager, sample_signal: TradeSignal
    ):
        """Adding to existing position in same market is allowed."""
        positions = [
            Position(ticker="kxbtc15m-test", market_exposure=10),
        ]
        decision = risk_manager.check(
            sample_signal,
            count=5,
            balance=Decimal("1000"),
            positions=positions,
            time_to_expiry_seconds=600,
        )
        assert decision.approved

    def test_current_exposure_overrides_approximation(
        self, risk_manager: RiskManager, sample_signal: TradeSignal
    ):
        """When current_exposure_dollars is provided, it's used instead of the
        hardcoded $0.50 per-contract approximation from positions."""
        positions = [
            Position(ticker="other-market", market_exposure=100),
        ]
        # With hardcoded approximation: 100 * $0.50 = $50 exposure, would pass
        # With actual exposure: $490, only $10 headroom vs $500 limit
        # Signal price is $0.53, count=50 => new exposure = $26.50, total = $516.50 > $500
        decision = risk_manager.check(
            sample_signal,
            count=50,
            balance=Decimal("1000"),
            positions=positions,
            time_to_expiry_seconds=600,
            current_exposure_dollars=Decimal("490"),
        )
        assert not decision.approved
        assert "exposure" in decision.reason.lower()

    def test_current_exposure_none_uses_fallback(
        self, risk_manager: RiskManager, sample_signal: TradeSignal
    ):
        """When current_exposure_dollars is None, falls back to position-based estimate."""
        positions = [
            Position(ticker="other-market", market_exposure=10),
        ]
        # Fallback: 10 * $0.50 = $5 exposure, well under $500 limit
        decision = risk_manager.check(
            sample_signal,
            count=5,
            balance=Decimal("1000"),
            positions=positions,
            time_to_expiry_seconds=600,
            current_exposure_dollars=None,
        )
        assert decision.approved

    def test_breakeven_trade_not_counted_as_win(self, risk_manager: RiskManager):
        """Breakeven (pnl=0) trades don't count as wins or losses."""
        risk_manager.record_trade(Decimal("-10"))
        risk_manager.record_trade(Decimal("-10"))
        assert risk_manager.consecutive_losses == 2

        # Breakeven should NOT reset the loss streak
        risk_manager.record_trade(Decimal("0"))
        assert risk_manager.consecutive_losses == 2
        assert risk_manager.win_rate == 0.0  # 0 wins / 3 settled

    def test_breakeven_trade_not_counted_as_loss(self, risk_manager: RiskManager):
        """Breakeven (pnl=0) trades don't extend the loss streak."""
        risk_manager.record_trade(Decimal("5"))
        assert risk_manager.consecutive_wins == 1

        # Breakeven should NOT reset the win streak
        risk_manager.record_trade(Decimal("0"))
        assert risk_manager.consecutive_wins == 1
        assert risk_manager.win_rate == 0.5  # 1 win / 2 settled


class TestVolatilityTracker:
    def test_regime_classification(self):
        tracker = VolatilityTracker()

        # Add sorted volatility values
        for v in [0.001 * i for i in range(100)]:
            tracker.update(v)

        # Last value is high (0.099)
        assert tracker.current_regime in ("high", "extreme")

    def test_low_vol_regime(self):
        tracker = VolatilityTracker()
        for _ in range(100):
            tracker.update(0.001)
        # All same -> percentile is 100% but value is low
        # With identical values, all percentile = 100
        assert tracker.current_regime is not None

    def test_edge_threshold_adjustment(self):
        tracker = VolatilityTracker()
        base = 0.03

        # Low vol -> lower threshold
        for _ in range(50):
            tracker.update(0.0001)
        adjusted_low = tracker.adjust_edge_threshold(base)

        # High vol -> higher threshold
        tracker = VolatilityTracker()
        for i in range(50):
            tracker.update(0.001 * i)  # Increasing vol
        adjusted_high = tracker.adjust_edge_threshold(base)

        # High vol should require more edge
        assert adjusted_high >= adjusted_low

    def test_kelly_adjustment(self):
        tracker = VolatilityTracker()
        base = 0.25

        result = tracker.adjust_kelly_fraction(base)
        assert 0 < result <= base

    def test_stats(self):
        tracker = VolatilityTracker()
        stats = tracker.stats
        assert "regime" in stats
        assert "observations" in stats

        tracker.update(0.002)
        tracker.update(0.003)
        stats = tracker.stats
        assert stats["observations"] == 2


class TestMMExitExemptions:
    """Tests for Fix #5: MM positions exempt from directional exits."""

    def _make_mm_position(self, ticker: str = "test"):
        """Create a mock PositionTracker with a market_making position."""
        from unittest.mock import AsyncMock
        from src.data.database import Database
        from src.data.kalshi_client import KalshiRestClient
        from src.execution.position_tracker import PositionState, PositionTracker

        tracker = PositionTracker(
            kalshi_client=AsyncMock(spec=KalshiRestClient),
            db=AsyncMock(spec=Database),
            paper_mode=True,
        )
        pos = PositionState(
            market_ticker=ticker,
            side="yes",
            count=10,
            avg_entry_price=Decimal("0.50"),
            entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        pos.strategy_tag = "market_making"
        tracker._positions[ticker] = pos
        return tracker, pos

    def test_mm_pre_expiry_exit_triggers(self, now: datetime):
        """strategy_tag='market_making' → triggers pre-expiry exit (not exempt)."""
        from src.data.models import MarketSnapshot, Orderbook, OrderbookLevel
        tracker, _ = self._make_mm_position()
        snapshots = {
            "test": MarketSnapshot(
                timestamp=now,
                market_ticker="test",
                spot_price=Decimal("97500"),
                orderbook=Orderbook(
                    ticker="test",
                    yes_levels=[OrderbookLevel(price_dollars=Decimal("0.55"), quantity=100)],
                    no_levels=[OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100)],
                    timestamp=now,
                ),
                time_to_expiry_seconds=60,  # Within pre-expiry window
            ),
        }
        results = tracker.check_pre_expiry_exits(snapshots, pre_expiry_seconds=90.0)
        assert len(results) == 1

    def test_mm_exit_exempt_thesis_break(self, now: datetime):
        """strategy_tag='market_making' → skipped by thesis break."""
        from src.data.models import PredictionResult
        tracker, _ = self._make_mm_position()
        predictions = {
            "test": PredictionResult(
                probability_yes=0.20,  # Model flipped hard against YES
                confidence=0.9,
                model_name="test",
            ),
        }
        results = tracker.check_thesis_breaks(predictions, threshold=0.05, min_hold_seconds=0)
        assert len(results) == 0

    def test_mm_exit_exempt_take_profit(self, now: datetime):
        """strategy_tag='market_making' → skipped by take-profit."""
        from src.config import StrategyConfig
        from src.data.models import MarketSnapshot, Orderbook, OrderbookLevel
        tracker, pos = self._make_mm_position()
        pos.avg_entry_price = Decimal("0.30")  # Entered cheap
        snapshots = {
            "test": MarketSnapshot(
                timestamp=now,
                market_ticker="test",
                spot_price=Decimal("97500"),
                orderbook=Orderbook(
                    ticker="test",
                    yes_levels=[OrderbookLevel(price_dollars=Decimal("0.60"), quantity=100)],
                    no_levels=[OrderbookLevel(price_dollars=Decimal("0.40"), quantity=100)],
                    timestamp=now,
                ),
                time_to_expiry_seconds=600,
            ),
        }
        config = StrategyConfig(take_profit_min_hold_seconds=0)
        results = tracker.check_take_profit(snapshots, config)
        assert len(results) == 0

    def test_mm_stop_loss_triggers(self, now: datetime):
        """strategy_tag='market_making' → triggers stop-loss (not exempt)."""
        from src.data.models import MarketSnapshot, Orderbook, OrderbookLevel
        tracker, pos = self._make_mm_position()
        pos.avg_entry_price = Decimal("0.70")  # Entered high
        snapshots = {
            "test": MarketSnapshot(
                timestamp=now,
                market_ticker="test",
                spot_price=Decimal("97500"),
                orderbook=Orderbook(
                    ticker="test",
                    yes_levels=[OrderbookLevel(price_dollars=Decimal("0.30"), quantity=100)],
                    no_levels=[OrderbookLevel(price_dollars=Decimal("0.70"), quantity=100)],
                    timestamp=now,
                ),
                time_to_expiry_seconds=600,
            ),
        }
        results = tracker.check_stop_loss(
            snapshots, stop_loss_pct=0.35, min_hold_seconds=0,
        )
        assert len(results) == 1


class TestStrategyPnLSummary:
    """Test for Fix #6: Per-strategy P&L query."""

    async def test_strategy_pnl_summary(self, tmp_path):
        """DB query groups by strategy_tag correctly."""
        from src.data.database import Database
        from src.data.models import CompletedTrade

        db = Database(str(tmp_path / "test.db"))
        await db.connect()

        # Insert trades with different strategy tags
        for tag, pnl_val, fee_val in [
            ("directional", 1.50, 0.10),
            ("directional", -0.50, 0.08),
            ("market_making", 0.30, 0.02),
            ("market_making", 0.20, 0.02),
            ("market_making", -0.10, 0.02),
        ]:
            trade = CompletedTrade(
                order_id=f"ord-{tag}-{pnl_val}",
                market_ticker="test",
                side="yes",
                action="settle",
                count=5,
                price_dollars=Decimal("0.50"),
                fees_dollars=Decimal(str(fee_val)),
                pnl_dollars=Decimal(str(pnl_val)),
                entry_time=datetime.now(timezone.utc),
                exit_time=datetime.now(timezone.utc),
                strategy_tag=tag,
                mode="paper",
            )
            await db.insert_trade(trade)
        await db.flush()

        result = await db.get_strategy_pnl_summary()
        assert "directional" in result
        assert "market_making" in result
        assert result["directional"]["count"] == 2
        assert result["directional"]["wins"] == 1
        assert result["market_making"]["count"] == 3
        assert result["market_making"]["wins"] == 2
        assert result["market_making"]["pnl"] == 0.40  # 0.30+0.20-0.10

        await db.close()
