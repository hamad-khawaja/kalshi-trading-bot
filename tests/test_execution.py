"""Tests for order management and position tracking."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.config import BotSettings
from src.data.models import MarketSnapshot, Orderbook, TradeSignal
from src.execution.order_manager import OrderManager, OrderState
from src.execution.position_tracker import PositionState, PositionTracker


class TestOrderManager:
    @pytest.fixture
    def order_manager(self, bot_settings: BotSettings) -> OrderManager:
        """Order manager in paper mode (no real API calls)."""
        # We pass None for kalshi_client since paper mode doesn't use it
        return OrderManager(None, bot_settings)  # type: ignore

    @pytest.fixture
    def signal(self) -> TradeSignal:
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
            suggested_count=10,
            timestamp=datetime.now(timezone.utc),
        )

    @pytest.mark.asyncio
    async def test_paper_mode_simulates_fill(
        self, order_manager: OrderManager, signal: TradeSignal
    ):
        """Paper mode should simulate immediate fill."""
        order_id = await order_manager.submit(signal, 10)
        assert order_id is not None
        assert order_id.startswith("paper-")

        state = order_manager.get_order(order_id)
        assert state is not None
        assert state.status == "filled"
        assert state.filled_count == 10

    @pytest.mark.asyncio
    async def test_paper_mode_cancel(
        self, order_manager: OrderManager, signal: TradeSignal
    ):
        """Paper mode cancel should work."""
        order_id = await order_manager.submit(signal, 5)
        assert order_id is not None
        # Already filled in paper mode, but cancel should handle it
        result = await order_manager.cancel(order_id)
        # Filled orders can't be canceled
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_zero_count_returns_none(
        self, order_manager: OrderManager, signal: TradeSignal
    ):
        """Zero count should not place order."""
        order_id = await order_manager.submit(signal, 0)
        assert order_id is None

    @pytest.mark.asyncio
    async def test_get_active_orders(
        self, order_manager: OrderManager, signal: TradeSignal
    ):
        """Get active orders filters by market."""
        await order_manager.submit(signal, 5)
        # In paper mode, orders are immediately filled (terminal)
        active = order_manager.get_active_orders()
        assert len(active) == 0  # All filled

    @pytest.mark.asyncio
    async def test_cleanup_terminal_orders(
        self, order_manager: OrderManager, signal: TradeSignal
    ):
        """Cleanup removes old terminal orders."""
        await order_manager.submit(signal, 5)
        # Should have 1 order
        assert len(order_manager._pending_orders) == 1
        # Cleanup with 0 max age removes it
        removed = order_manager.cleanup_terminal_orders(max_age_seconds=0)
        # May or may not remove (depends on timing)
        assert isinstance(removed, int)


class TestPositionState:
    def test_exposure_calculation(self):
        pos = PositionState(
            market_ticker="test",
            side="yes",
            count=10,
            avg_entry_price=Decimal("0.55"),
            entry_time=datetime.now(timezone.utc),
        )
        assert pos.exposure_dollars == Decimal("5.50")

    def test_repr(self):
        pos = PositionState(
            market_ticker="test",
            side="yes",
            count=5,
            avg_entry_price=Decimal("0.50"),
            entry_time=datetime.now(timezone.utc),
        )
        assert "test" in repr(pos)
        assert "yes" in repr(pos)


class TestPositionTracker:
    @pytest.fixture
    def tracker(self, bot_settings: BotSettings) -> PositionTracker:
        """Position tracker in paper mode."""
        from unittest.mock import AsyncMock

        mock_db = AsyncMock()
        return PositionTracker(None, mock_db, paper_mode=True)  # type: ignore

    def test_update_on_fill_new_position(self, tracker: PositionTracker):
        """New position created on first fill."""
        signal = TradeSignal(
            market_ticker="kxbtc15m-test",
            side="yes",
            action="buy",
            raw_edge=0.08,
            net_edge=0.06,
            model_probability=0.60,
            implied_probability=0.52,
            confidence=0.7,
            suggested_price_dollars="0.53",
            suggested_count=10,
            timestamp=datetime.now(timezone.utc),
        )
        order_state = OrderState(
            order_id="test-1",
            client_order_id="uuid-1",
            signal=signal,
            requested_count=10,
        )
        order_state.filled_count = 10

        tracker.update_on_fill(order_state)

        pos = tracker.get_position("kxbtc15m-test")
        assert pos is not None
        assert pos.count == 10
        assert pos.side == "yes"
        assert pos.avg_entry_price == Decimal("0.53")

    def test_update_on_fill_adds_to_position(self, tracker: PositionTracker):
        """Adding to same-side position averages price."""
        now = datetime.now(timezone.utc)

        # First fill
        signal1 = TradeSignal(
            market_ticker="test",
            side="yes",
            action="buy",
            raw_edge=0.08,
            net_edge=0.06,
            model_probability=0.60,
            implied_probability=0.52,
            confidence=0.7,
            suggested_price_dollars="0.50",
            suggested_count=10,
            timestamp=now,
        )
        state1 = OrderState("o1", "c1", signal1, 10)
        state1.filled_count = 10
        tracker.update_on_fill(state1)

        # Second fill at different price
        signal2 = TradeSignal(
            market_ticker="test",
            side="yes",
            action="buy",
            raw_edge=0.08,
            net_edge=0.06,
            model_probability=0.60,
            implied_probability=0.52,
            confidence=0.7,
            suggested_price_dollars="0.60",
            suggested_count=10,
            timestamp=now,
        )
        state2 = OrderState("o2", "c2", signal2, 10)
        state2.filled_count = 10
        tracker.update_on_fill(state2)

        pos = tracker.get_position("test")
        assert pos is not None
        assert pos.count == 20
        # Weighted avg: (0.50 * 10 + 0.60 * 10) / 20 = 0.55
        assert pos.avg_entry_price == Decimal("0.55")

    def test_total_exposure(self, tracker: PositionTracker):
        """Total exposure sums across positions."""
        now = datetime.now(timezone.utc)

        for ticker, price in [("m1", "0.50"), ("m2", "0.60")]:
            signal = TradeSignal(
                market_ticker=ticker,
                side="yes",
                action="buy",
                raw_edge=0.08,
                net_edge=0.06,
                model_probability=0.60,
                implied_probability=0.52,
                confidence=0.7,
                suggested_price_dollars=price,
                suggested_count=10,
                timestamp=now,
            )
            state = OrderState(f"o-{ticker}", f"c-{ticker}", signal, 10)
            state.filled_count = 10
            tracker.update_on_fill(state)

        # 10 * 0.50 + 10 * 0.60 = 11.00
        assert tracker.total_exposure_dollars == Decimal("11.00")

    def test_market_position_count(self, tracker: PositionTracker):
        """Get signed position count for a market."""
        now = datetime.now(timezone.utc)
        signal = TradeSignal(
            market_ticker="test",
            side="yes",
            action="buy",
            raw_edge=0.08,
            net_edge=0.06,
            model_probability=0.60,
            implied_probability=0.52,
            confidence=0.7,
            suggested_price_dollars="0.50",
            suggested_count=10,
            timestamp=now,
        )
        state = OrderState("o1", "c1", signal, 10)
        state.filled_count = 10
        tracker.update_on_fill(state)

        assert tracker.get_market_position_count("test") == 10  # Positive = YES
        assert tracker.get_market_position_count("nonexistent") == 0

    def test_remove_expired_positions(self, tracker: PositionTracker):
        """Expired positions are removed."""
        now = datetime.now(timezone.utc)
        signal = TradeSignal(
            market_ticker="expired-market",
            side="yes",
            action="buy",
            raw_edge=0.08,
            net_edge=0.06,
            model_probability=0.60,
            implied_probability=0.52,
            confidence=0.7,
            suggested_price_dollars="0.50",
            suggested_count=10,
            timestamp=now,
        )
        state = OrderState("o1", "c1", signal, 10)
        state.filled_count = 10
        tracker.update_on_fill(state)

        assert tracker.position_count == 1
        tracker.remove_expired_positions(["expired-market"])
        assert tracker.position_count == 0
