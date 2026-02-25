"""Integration tests for recently added features.

Covers:
- TC extreme vol filter (blocks trend continuation in extreme vol)
- Mode switching (switch_mode safety gates)
- Trading pause position gate (409 when positions open)
- Database mode column (migration + insert + query)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.config import (
    BotSettings,
    DatabaseConfig,
    FeatureConfig,
    KalshiConfig,
    LoggingConfig,
    RiskConfig,
    StrategyConfig,
)
from src.data.database import Database
from src.data.models import (
    CompletedTrade,
    FeatureVector,
    MarketSnapshot,
    Orderbook,
    OrderbookLevel,
    PredictionResult,
)
from src.dashboard.server import DashboardServer, DashboardState
from src.execution.order_manager import OrderManager
from src.execution.position_tracker import PositionTracker
from src.risk.volatility import VolatilityTracker
from src.strategy.signal_combiner import SignalCombiner

# ---------------------------------------------------------------------------
# Shared helpers (same pattern as test_integration_e2e.py)
# ---------------------------------------------------------------------------

NOW = datetime(2026, 2, 25, 14, 0, 0, tzinfo=UTC)
TICKER = "KXBTC15M-25FEB26-1415"


def make_orderbook(
    yes_prices: list[float] | None = None,
    no_prices: list[float] | None = None,
    qty: int = 100,
) -> Orderbook:
    if yes_prices is None:
        yes_prices = [0.52, 0.50, 0.48]
    if no_prices is None:
        no_prices = [0.50, 0.48, 0.45]
    return Orderbook(
        ticker=TICKER,
        yes_levels=[
            OrderbookLevel(price_dollars=Decimal(str(p)), quantity=qty)
            for p in yes_prices
        ],
        no_levels=[
            OrderbookLevel(price_dollars=Decimal(str(p)), quantity=qty)
            for p in no_prices
        ],
        timestamp=NOW,
    )


def make_snapshot(
    implied_yes_prob: float = 0.50,
    ttx: float = 800.0,
    time_elapsed: float = 100.0,
    phase: int = 1,
    spot_price: float = 97500.0,
    strike_price: float = 97400.0,
    orderbook: Orderbook | None = None,
) -> MarketSnapshot:
    if orderbook is None:
        orderbook = make_orderbook()
    return MarketSnapshot(
        timestamp=NOW,
        market_ticker=TICKER,
        spot_price=Decimal(str(spot_price)),
        spot_prices_1min=[Decimal(str(spot_price + i * 0.1)) for i in range(60)],
        spot_prices_5min=[Decimal(str(spot_price - 10 + i * 0.01)) for i in range(300)],
        spot_volumes_1min=[Decimal("0.01") for _ in range(60)],
        orderbook=orderbook,
        implied_yes_prob=Decimal(str(implied_yes_prob)),
        spread=Decimal("0.02"),
        strike_price=Decimal(str(strike_price)),
        time_to_expiry_seconds=ttx,
        time_elapsed_seconds=time_elapsed,
        window_phase=phase,
        volume=250,
    )


def make_features(
    momentum_60s: float = 0.0005,
    **kwargs: float,
) -> FeatureVector:
    defaults = dict(
        timestamp=NOW,
        market_ticker=TICKER,
        momentum_15s=0.0002,
        momentum_60s=momentum_60s,
        momentum_180s=0.0008,
        momentum_600s=0.0012,
        realized_vol_5min=0.002,
        rsi_14=55.0,
        vwap_deviation=0.0001,
        order_flow_imbalance=0.15,
        spread=0.02,
        spread_ratio=0.04,
        time_to_expiry_normalized=0.67,
        kalshi_volume=250,
        implied_probability=0.50,
        bollinger_position=0.1,
        macd_histogram=0.0005,
        roc_acceleration=0.0001,
        volume_weighted_momentum=0.0003,
        orderbook_depth_imbalance=0.15,
        orderbook_support_resistance=0.1,
        orderbook_wall_distance=-0.05,
        orderbook_wall_strength=0.3,
        taker_buy_sell_ratio=0.1,
        path_efficiency_60s=0.70,
        path_efficiency_180s=0.65,
        path_efficiency_300s=0.60,
    )
    defaults.update(kwargs)
    return FeatureVector(**defaults)


def _base_strategy_config(**overrides) -> StrategyConfig:
    """Base config with gates disabled for deterministic tests."""
    defaults = dict(
        min_edge_threshold=0.03,
        max_edge_threshold=0.30,
        confidence_min=0.55,
        min_entry_price=0.10,
        min_quality_score=0.0,
        edge_confirmation_cycles=1,
        phase_filter_enabled=False,
        zone_filter_enabled=False,
        vol_regime_filter_enabled=False,
        quiet_hours_enabled=False,
        trend_guard_enabled=False,
        overreaction_enabled=False,
        edge_expiry_decay_enabled=False,
        yes_side_edge_multiplier=1.0,
        no_side_edge_multiplier=1.0,
        directional_enabled=False,
        use_market_maker=False,
        fomo_enabled=False,
        certainty_scalp_enabled=False,
        settlement_ride_enabled=False,
        trend_continuation_enabled=True,
        trend_continuation_min_streak=2,
        trend_continuation_max_phase=2,
        trend_continuation_min_edge=0.03,
        trend_continuation_streak_prob=0.65,
        trend_continuation_min_entry_price=0.10,
        trend_continuation_momentum_threshold=0.001,
    )
    defaults.update(overrides)
    return StrategyConfig(**defaults)


# ---------------------------------------------------------------------------
# 1. TC Extreme Vol Filter
# ---------------------------------------------------------------------------


class TestTCExtremeVolFilter:
    """Trend continuation blocked in extreme vol regime."""

    def _make_extreme_vol_tracker(self) -> VolatilityTracker:
        """Create a VolatilityTracker in the extreme regime."""
        vt = VolatilityTracker()
        # Feed extreme volatility readings to push into extreme regime
        for _ in range(200):
            vt.update(0.05)  # Very high vol reading
        assert vt.current_regime == "extreme", (
            f"Expected extreme regime, got {vt.current_regime}"
        )
        return vt

    def _make_normal_vol_tracker(self) -> VolatilityTracker:
        """Create a VolatilityTracker in a normal regime.

        Percentile < 70 => normal. Place latest reading at the median.
        """
        vt = VolatilityTracker()
        # Spread readings evenly; last reading in the middle → ~50th percentile
        for i in range(100):
            vt.update(0.001 * (i + 1))  # 0.001 .. 0.100
        vt.update(0.050)  # 50th percentile → "normal"
        assert vt.current_regime == "normal", (
            f"Expected normal regime, got {vt.current_regime}"
        )
        return vt

    async def test_tc_blocked_in_extreme_vol(self):
        """Trend continuation signal is blocked when vol regime is extreme."""
        cfg = _base_strategy_config(tc_extreme_vol_filter_enabled=True)
        vol_tracker = self._make_extreme_vol_tracker()
        history = {"BTC": [{"result": "yes"}, {"result": "yes"}]}
        combiner = SignalCombiner(cfg, vol_tracker=vol_tracker, settlement_history=history)

        snapshot = make_snapshot(implied_yes_prob=0.50, ttx=800.0, time_elapsed=100.0, phase=1)
        prediction = PredictionResult(
            probability_yes=0.55, confidence=0.65, model_name="test"
        )
        features = make_features()

        signals = combiner.evaluate(prediction, snapshot, current_position=0, features=features)
        trend = [s for s in signals if s.signal_type == "trend_continuation"]
        assert len(trend) == 0, "TC should be blocked in extreme vol"

    async def test_tc_allowed_in_normal_vol(self):
        """Trend continuation signal fires when vol regime is NOT extreme."""
        cfg = _base_strategy_config(tc_extreme_vol_filter_enabled=True)
        vol_tracker = self._make_normal_vol_tracker()
        history = {"BTC": [{"result": "yes"}, {"result": "yes"}]}
        combiner = SignalCombiner(cfg, vol_tracker=vol_tracker, settlement_history=history)

        snapshot = make_snapshot(implied_yes_prob=0.50, ttx=800.0, time_elapsed=100.0, phase=1)
        prediction = PredictionResult(
            probability_yes=0.55, confidence=0.65, model_name="test"
        )
        features = make_features()

        signals = combiner.evaluate(prediction, snapshot, current_position=0, features=features)
        trend = [s for s in signals if s.signal_type == "trend_continuation"]
        assert len(trend) == 1, "TC should fire in normal vol"
        assert trend[0].side == "yes"

    async def test_tc_allowed_when_filter_disabled(self):
        """Trend continuation fires even in extreme vol when filter is disabled."""
        cfg = _base_strategy_config(tc_extreme_vol_filter_enabled=False)
        vol_tracker = self._make_extreme_vol_tracker()
        history = {"BTC": [{"result": "yes"}, {"result": "yes"}]}
        combiner = SignalCombiner(cfg, vol_tracker=vol_tracker, settlement_history=history)

        snapshot = make_snapshot(implied_yes_prob=0.50, ttx=800.0, time_elapsed=100.0, phase=1)
        prediction = PredictionResult(
            probability_yes=0.55, confidence=0.65, model_name="test"
        )
        features = make_features()

        signals = combiner.evaluate(prediction, snapshot, current_position=0, features=features)
        trend = [s for s in signals if s.signal_type == "trend_continuation"]
        assert len(trend) == 1, "TC should fire when filter is disabled (even in extreme vol)"

    async def test_tc_allowed_when_no_vol_tracker(self):
        """TC fires when vol_tracker is None (filter can't evaluate)."""
        cfg = _base_strategy_config(tc_extreme_vol_filter_enabled=True)
        history = {"BTC": [{"result": "yes"}, {"result": "yes"}]}
        # No vol_tracker passed
        combiner = SignalCombiner(cfg, vol_tracker=None, settlement_history=history)

        snapshot = make_snapshot(implied_yes_prob=0.50, ttx=800.0, time_elapsed=100.0, phase=1)
        prediction = PredictionResult(
            probability_yes=0.55, confidence=0.65, model_name="test"
        )
        features = make_features()

        signals = combiner.evaluate(prediction, snapshot, current_position=0, features=features)
        trend = [s for s in signals if s.signal_type == "trend_continuation"]
        assert len(trend) == 1, "TC should fire when vol_tracker is None"


# ---------------------------------------------------------------------------
# 2. Trading Pause Position Gate
# ---------------------------------------------------------------------------


class TestTradingPausePositionGate:
    """Toggle-trading endpoint returns 409 when positions are open."""

    def _build_app(self, state: DashboardState) -> web.Application:
        server = DashboardServer(state, "127.0.0.1", 0)
        app = web.Application()
        app.router.add_post("/api/toggle-trading", server._handle_toggle_trading)
        return app

    @pytest.mark.asyncio
    async def test_pause_rejected_with_open_positions(self):
        """Cannot pause trading when positions are open."""
        state = DashboardState()
        state.positions = [{"ticker": TICKER, "side": "yes", "count": 5}]
        app = self._build_app(state)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/toggle-trading")
            assert resp.status == 409
            data = await resp.json()
            assert "error" in data
            assert "open positions" in data["error"].lower()
            # State should NOT have changed
            assert state.trading_paused is False

    @pytest.mark.asyncio
    async def test_pause_allowed_with_no_positions(self):
        """Can pause trading when no positions are open."""
        state = DashboardState()
        state.positions = []
        app = self._build_app(state)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/toggle-trading")
            assert resp.status == 200
            data = await resp.json()
            assert data["trading_paused"] is True
            assert state.trading_paused is True

    @pytest.mark.asyncio
    async def test_unpause_rejected_with_open_positions(self):
        """Cannot unpause trading when positions are open."""
        state = DashboardState()
        state.trading_paused = True
        state.positions = [{"ticker": TICKER, "side": "no", "count": 3}]
        app = self._build_app(state)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/toggle-trading")
            assert resp.status == 409
            # Should remain paused
            assert state.trading_paused is True


# ---------------------------------------------------------------------------
# 3. Mode Switching API
# ---------------------------------------------------------------------------


class TestModeSwitchEndpoint:
    """Dashboard /api/switch-mode endpoint with safety gates."""

    def _build_app(
        self, state: DashboardState, bot: AsyncMock | None = None,
    ) -> web.Application:
        server = DashboardServer(state, "127.0.0.1", 0, bot=bot)
        app = web.Application()
        app.router.add_post("/api/switch-mode", server._handle_switch_mode)
        return app

    @pytest.mark.asyncio
    async def test_switch_mode_no_bot_returns_503(self):
        """Returns 503 when bot reference is not available."""
        state = DashboardState()
        app = self._build_app(state, bot=None)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/switch-mode", json={"mode": "live"},
            )
            assert resp.status == 503
            data = await resp.json()
            assert "error" in data

    @pytest.mark.asyncio
    async def test_switch_mode_invalid_mode_returns_400(self):
        """Returns 400 for invalid mode string."""
        state = DashboardState()
        bot = AsyncMock()
        app = self._build_app(state, bot=bot)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/switch-mode", json={"mode": "invalid"},
            )
            assert resp.status == 400
            data = await resp.json()
            assert "Invalid mode" in data["error"]

    @pytest.mark.asyncio
    async def test_switch_mode_invalid_json_returns_400(self):
        """Returns 400 for malformed request body."""
        state = DashboardState()
        bot = AsyncMock()
        app = self._build_app(state, bot=bot)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/switch-mode",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_switch_mode_success(self):
        """Returns 200 when bot.switch_mode succeeds."""
        state = DashboardState()
        bot = AsyncMock()
        bot.switch_mode = AsyncMock(return_value={"mode": "live", "message": "Switched"})
        app = self._build_app(state, bot=bot)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/switch-mode", json={"mode": "live"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["mode"] == "live"
            bot.switch_mode.assert_called_once_with("live")

    @pytest.mark.asyncio
    async def test_switch_mode_rejected_with_positions(self):
        """Returns 409 when bot.switch_mode returns an error (positions open)."""
        state = DashboardState()
        bot = AsyncMock()
        bot.switch_mode = AsyncMock(return_value={
            "error": "Cannot switch mode with open positions",
            "mode": "paper",
        })
        app = self._build_app(state, bot=bot)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/switch-mode", json={"mode": "live"},
            )
            assert resp.status == 409
            data = await resp.json()
            assert "error" in data

    @pytest.mark.asyncio
    async def test_switch_mode_internal_error_returns_500(self):
        """Returns 500 when bot.switch_mode raises an unexpected exception."""
        state = DashboardState()
        bot = AsyncMock()
        bot.switch_mode = AsyncMock(side_effect=RuntimeError("boom"))
        app = self._build_app(state, bot=bot)

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/switch-mode", json={"mode": "live"},
            )
            assert resp.status == 500
            data = await resp.json()
            assert "error" in data
            assert "Internal error" in data["error"]


# ---------------------------------------------------------------------------
# 4. Database Mode Column
# ---------------------------------------------------------------------------


class TestDatabaseModeColumn:
    """Mode column migration, insert, and query."""

    async def test_mode_column_migration(self):
        """Mode column exists after database initialization."""
        db = Database(":memory:")
        await db.connect()
        cursor = await db._db.execute("PRAGMA table_info(trades)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert "mode" in columns
        await db.close()

    async def test_mode_column_migration_idempotent(self):
        """Running migration twice does not error."""
        db = Database(":memory:")
        await db.connect()
        # Run migration again — should be a no-op
        await db._migrate_mode_column()
        cursor = await db._db.execute("PRAGMA table_info(trades)")
        columns = [row[1] for row in await cursor.fetchall()]
        # Mode should appear exactly once
        assert columns.count("mode") == 1
        await db.close()

    async def test_insert_trade_with_paper_mode(self):
        """Trade inserted with mode='paper' is queryable."""
        db = Database(":memory:")
        await db.connect()

        trade = CompletedTrade(
            order_id="test-001",
            market_ticker=TICKER,
            side="yes",
            action="buy",
            count=5,
            price_dollars=Decimal("0.52"),
            fees_dollars=Decimal("0.07"),
            pnl_dollars=Decimal("1.50"),
            entry_time=NOW,
            exit_time=NOW + timedelta(minutes=5),
            strategy_tag="directional",
            mode="paper",
        )
        await db.insert_trade(trade)
        await db.flush()

        trades = await db.get_recent_trades(limit=10)
        assert len(trades) == 1
        assert trades[0]["mode"] == "paper"
        assert trades[0]["order_id"] == "test-001"
        await db.close()

    async def test_insert_trade_with_live_mode(self):
        """Trade inserted with mode='live' is queryable and distinct from paper."""
        db = Database(":memory:")
        await db.connect()

        paper_trade = CompletedTrade(
            order_id="paper-001",
            market_ticker=TICKER,
            side="yes",
            action="buy",
            count=5,
            price_dollars=Decimal("0.52"),
            fees_dollars=Decimal("0.07"),
            entry_time=NOW,
            mode="paper",
        )
        live_trade = CompletedTrade(
            order_id="live-001",
            market_ticker=TICKER,
            side="no",
            action="buy",
            count=3,
            price_dollars=Decimal("0.48"),
            fees_dollars=Decimal("0.05"),
            entry_time=NOW + timedelta(seconds=10),
            mode="live",
        )
        await db.insert_trade(paper_trade)
        await db.insert_trade(live_trade)
        await db.flush()

        trades = await db.get_recent_trades(limit=10)
        assert len(trades) == 2
        modes = {t["mode"] for t in trades}
        assert modes == {"paper", "live"}
        await db.close()

    async def test_mode_defaults_to_paper(self):
        """CompletedTrade defaults to mode='paper' when not specified."""
        trade = CompletedTrade(
            order_id="default-001",
            market_ticker=TICKER,
            side="yes",
            action="buy",
            count=1,
            price_dollars=Decimal("0.50"),
            fees_dollars=Decimal("0.01"),
            entry_time=NOW,
        )
        assert trade.mode == "paper"
