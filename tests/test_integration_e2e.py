"""End-to-end integration tests: signal → sizing → risk → order → fill → exit.

Wires real components together in paper mode. Only external I/O (Kalshi API,
database) is mocked. Each test class exercises a specific strategy's full
pipeline through entry and (where applicable) exit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.config import (
    BotSettings,
    DatabaseConfig,
    FeatureConfig,
    KalshiConfig,
    LoggingConfig,
    RiskConfig,
    StrategyConfig,
)
from src.data.models import (
    FeatureVector,
    MarketSnapshot,
    Orderbook,
    OrderbookLevel,
    PredictionResult,
)
from src.execution.order_manager import OrderManager
from src.execution.position_tracker import PositionTracker
from src.risk.position_sizer import PositionSizer
from src.risk.risk_manager import RiskManager
from src.risk.volatility import VolatilityTracker
from src.strategy.signal_combiner import SignalCombiner

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

NOW = datetime(2026, 2, 24, 14, 0, 0, tzinfo=UTC)
TICKER = "KXBTC15M-26FEB24-1415"


@pytest.fixture
def integration_settings() -> BotSettings:
    """Paper-mode settings with relaxed thresholds so signals actually fire."""
    return BotSettings(
        mode="paper",
        kalshi=KalshiConfig(
            api_key_id="test",
            private_key_path="/tmp/test.pem",
        ),
        strategy=StrategyConfig(
            # Core thresholds — relaxed
            min_edge_threshold=0.03,
            max_edge_threshold=0.30,
            confidence_min=0.55,
            min_entry_price=0.10,
            min_quality_score=0.0,
            # Disable all gates that interfere with deterministic tests
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
            # Strategy toggles — all enabled
            directional_enabled=True,
            use_market_maker=True,
            certainty_scalp_enabled=True,
            settlement_ride_enabled=True,
            trend_continuation_enabled=True,
            trend_continuation_min_streak=2,
            trend_continuation_max_phase=2,
            # MM parameters
            mm_min_spread=0.05,
            mm_max_spread=0.30,
            mm_max_inventory=20,
            mm_vol_filter_enabled=False,
            # Settlement ride — relaxed
            settlement_ride_min_elapsed_seconds=600.0,
            settlement_ride_min_edge=0.03,
            settlement_ride_min_implied_distance=0.12,
            # Certainty scalp
            certainty_scalp_max_ttx=240.0,
            certainty_scalp_min_ttx=60.0,
            certainty_scalp_min_implied_prob=0.85,
            certainty_scalp_min_model_prob=0.80,
            certainty_scalp_min_edge=0.02,
            certainty_scalp_min_spot_distance_pct=0.001,
            # Trend continuation
            trend_continuation_min_edge=0.03,
            trend_continuation_streak_prob=0.65,
            trend_continuation_min_entry_price=0.10,
            trend_continuation_momentum_threshold=0.001,
            # Exits
            stop_loss_enabled=True,
            stop_loss_pct=0.35,
            stop_loss_max_dollar_loss=5.0,
            take_profit_enabled=True,
            take_profit_min_profit_cents=0.08,
            take_profit_min_hold_seconds=0.0,
            take_profit_time_decay_start_seconds=300.0,
            take_profit_time_decay_floor_cents=0.03,
            trailing_take_profit_enabled=False,
            pre_expiry_exit_enabled=True,
            pre_expiry_exit_seconds=90.0,
            pre_expiry_exit_min_pnl_cents=-0.10,
            hold_to_settle_seconds=0.0,
        ),
        risk=RiskConfig(
            max_position_per_market=50,
            max_total_exposure_dollars=500.0,
            max_daily_loss_dollars=100.0,
            max_concurrent_positions=10,
            kelly_fraction=0.25,
            min_balance_dollars=50.0,
            max_trades_per_day=100,
            max_consecutive_losses=10,
            min_position_size=1,
            time_scale_enabled=False,
        ),
        features=FeatureConfig(),
        logging=LoggingConfig(level="WARNING"),
        database=DatabaseConfig(path=":memory:"),
    )


@pytest.fixture
def order_manager(integration_settings: BotSettings) -> OrderManager:
    return OrderManager(None, integration_settings)  # type: ignore[arg-type]


@pytest.fixture
def position_tracker() -> PositionTracker:
    mock_db = AsyncMock()
    return PositionTracker(None, mock_db, paper_mode=True)  # type: ignore[arg-type]


@pytest.fixture
def risk_manager(integration_settings: BotSettings) -> RiskManager:
    return RiskManager(integration_settings.risk)


@pytest.fixture
def position_sizer(integration_settings: BotSettings) -> PositionSizer:
    return PositionSizer(integration_settings.risk, integration_settings.strategy)


@pytest.fixture
def vol_tracker() -> VolatilityTracker:
    vt = VolatilityTracker()
    for i in range(100):
        vt.update(0.002 + (i % 10) * 0.0001)
    return vt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_orderbook(
    yes_prices: list[float] | None = None,
    no_prices: list[float] | None = None,
    qty: int = 100,
    ticker: str = TICKER,
    ts: datetime = NOW,
) -> Orderbook:
    """Build an orderbook with customizable levels."""
    if yes_prices is None:
        yes_prices = [0.52, 0.50, 0.48]
    if no_prices is None:
        no_prices = [0.50, 0.48, 0.45]
    return Orderbook(
        ticker=ticker,
        yes_levels=[
            OrderbookLevel(price_dollars=Decimal(str(p)), quantity=qty)
            for p in yes_prices
        ],
        no_levels=[
            OrderbookLevel(price_dollars=Decimal(str(p)), quantity=qty)
            for p in no_prices
        ],
        timestamp=ts,
    )


def make_snapshot(
    ticker: str = TICKER,
    spot_price: float = 97500.0,
    strike_price: float | None = 97400.0,
    implied_yes_prob: float = 0.51,
    spread: float = 0.02,
    ttx: float = 600.0,
    time_elapsed: float = 300.0,
    phase: int = 3,
    orderbook: Orderbook | None = None,
    volume: int = 250,
) -> MarketSnapshot:
    """Build a MarketSnapshot with customizable fields."""
    if orderbook is None:
        orderbook = make_orderbook(ticker=ticker)
    return MarketSnapshot(
        timestamp=NOW,
        market_ticker=ticker,
        spot_price=Decimal(str(spot_price)),
        spot_prices_1min=[Decimal(str(spot_price + i * 0.1)) for i in range(60)],
        spot_prices_5min=[Decimal(str(spot_price - 10 + i * 0.01)) for i in range(300)],
        spot_volumes_1min=[Decimal("0.01") for _ in range(60)],
        orderbook=orderbook,
        implied_yes_prob=Decimal(str(implied_yes_prob)),
        spread=Decimal(str(spread)),
        strike_price=Decimal(str(strike_price)) if strike_price is not None else None,
        time_to_expiry_seconds=ttx,
        time_elapsed_seconds=time_elapsed,
        window_phase=phase,
        volume=volume,
    )


def make_features(
    ticker: str = TICKER,
    momentum_60s: float = 0.0005,
    momentum_180s: float = 0.0008,
    momentum_600s: float = 0.0012,
    rsi_14: float = 55.0,
    implied_probability: float = 0.51,
    orderbook_depth_imbalance: float = 0.15,
    volume_weighted_momentum: float = 0.0003,
    taker_buy_sell_ratio: float = 0.1,
    **kwargs: float,
) -> FeatureVector:
    """Build a FeatureVector with customizable fields."""
    defaults = dict(
        timestamp=NOW,
        market_ticker=ticker,
        momentum_15s=0.0002,
        momentum_60s=momentum_60s,
        momentum_180s=momentum_180s,
        momentum_600s=momentum_600s,
        realized_vol_5min=0.002,
        rsi_14=rsi_14,
        vwap_deviation=0.0001,
        order_flow_imbalance=0.15,
        spread=0.02,
        spread_ratio=0.04,
        time_to_expiry_normalized=0.67,
        kalshi_volume=250,
        implied_probability=implied_probability,
        bollinger_position=0.1,
        macd_histogram=0.0005,
        roc_acceleration=0.0001,
        volume_weighted_momentum=volume_weighted_momentum,
        orderbook_depth_imbalance=orderbook_depth_imbalance,
        orderbook_support_resistance=0.1,
        orderbook_wall_distance=-0.05,
        orderbook_wall_strength=0.3,
        taker_buy_sell_ratio=taker_buy_sell_ratio,
        path_efficiency_60s=0.70,
        path_efficiency_180s=0.65,
        path_efficiency_300s=0.60,
    )
    defaults.update(kwargs)
    return FeatureVector(**defaults)


async def run_pipeline(
    signal,
    sizer: PositionSizer,
    risk_mgr: RiskManager,
    order_mgr: OrderManager,
    tracker: PositionTracker,
    balance: Decimal = Decimal("500.00"),
    vol_tracker: VolatilityTracker | None = None,
    ttx: float = 600.0,
):
    """Run signal through sizing → risk → order → fill → position tracking.

    Returns (order_id, position_state) or (None, None) if blocked.
    """
    count = sizer.size(
        signal,
        balance_dollars=balance,
        current_exposure_dollars=tracker.total_exposure_dollars,
        current_market_position=tracker.get_market_position_count(signal.market_ticker),
        vol_tracker=vol_tracker,
        time_to_expiry=ttx,
    )
    if count <= 0:
        return None, None

    decision = risk_mgr.check(
        signal,
        count,
        balance,
        positions=[],
        time_to_expiry_seconds=ttx,
        current_exposure_dollars=tracker.total_exposure_dollars,
    )
    if not decision.approved:
        return None, None

    final_count = decision.adjusted_count or count
    order_id = await order_mgr.submit(signal, final_count)
    if order_id is None:
        return None, None

    order_state = order_mgr.get_order(order_id)
    assert order_state is not None
    assert order_state.status == "filled"

    tracker.update_on_fill(order_state)
    position = tracker.get_position(signal.market_ticker)
    return order_id, position


# ---------------------------------------------------------------------------
# 1. Directional flow
# ---------------------------------------------------------------------------


class TestDirectionalFlow:
    """Test directional signal → full pipeline."""

    async def test_directional_entry_yes_side(
        self,
        integration_settings,
        order_manager,
        position_tracker,
        risk_manager,
        position_sizer,
        vol_tracker,
    ):
        """Model bullish (0.62) vs market (0.51) → buy YES, fill, position tracked."""
        combiner = SignalCombiner(
            integration_settings.strategy, vol_tracker=vol_tracker
        )
        snapshot = make_snapshot(implied_yes_prob=0.51, ttx=600.0, phase=3)
        prediction = PredictionResult(
            probability_yes=0.62, confidence=0.70, model_name="test"
        )
        features = make_features()

        signals = combiner.evaluate(prediction, snapshot, current_position=0, features=features)
        directional = [s for s in signals if s.signal_type == "directional"]
        assert len(directional) >= 1
        signal = directional[0]
        assert signal.side == "yes"
        assert signal.net_edge > 0

        order_id, position = await run_pipeline(
            signal, position_sizer, risk_manager, order_manager,
            position_tracker, vol_tracker=vol_tracker,
        )
        assert order_id is not None
        assert position is not None
        assert position.side == "yes"
        assert position.count > 0
        assert position.market_ticker == TICKER

    async def test_directional_entry_no_side(
        self,
        integration_settings,
        order_manager,
        position_tracker,
        risk_manager,
        position_sizer,
        vol_tracker,
    ):
        """Model bearish (0.38) vs market (0.51) → buy NO."""
        combiner = SignalCombiner(
            integration_settings.strategy, vol_tracker=vol_tracker
        )
        snapshot = make_snapshot(implied_yes_prob=0.51, ttx=600.0, phase=3)
        prediction = PredictionResult(
            probability_yes=0.38, confidence=0.70, model_name="test"
        )
        features = make_features(
            momentum_60s=-0.0005, momentum_180s=-0.0008, momentum_600s=-0.0012,
        )

        signals = combiner.evaluate(prediction, snapshot, current_position=0, features=features)
        directional = [s for s in signals if s.signal_type == "directional"]
        assert len(directional) >= 1
        signal = directional[0]
        assert signal.side == "no"

        order_id, position = await run_pipeline(
            signal, position_sizer, risk_manager, order_manager,
            position_tracker, vol_tracker=vol_tracker,
        )
        assert order_id is not None
        assert position is not None
        assert position.side == "no"
        assert position.count > 0

    async def test_directional_blocked_low_confidence(
        self,
        integration_settings,
        vol_tracker,
    ):
        """Low confidence (0.40) blocks the directional signal."""
        combiner = SignalCombiner(
            integration_settings.strategy, vol_tracker=vol_tracker
        )
        snapshot = make_snapshot(implied_yes_prob=0.51, ttx=600.0, phase=3)
        prediction = PredictionResult(
            probability_yes=0.62, confidence=0.40, model_name="test"
        )
        features = make_features()

        signals = combiner.evaluate(prediction, snapshot, current_position=0, features=features)
        directional = [s for s in signals if s.signal_type == "directional"]
        assert len(directional) == 0


# ---------------------------------------------------------------------------
# 2. Trend continuation flow
# ---------------------------------------------------------------------------


class TestTrendContinuationFlow:
    """Test trend continuation from settlement history → full pipeline."""

    def _make_combiner(self, settings, history: dict) -> SignalCombiner:
        cfg = settings.strategy.model_copy(update={"directional_enabled": False})
        return SignalCombiner(cfg, settlement_history=history)

    async def test_trend_streak_2_fires(
        self,
        integration_settings,
        order_manager,
        position_tracker,
        risk_manager,
        position_sizer,
    ):
        """2-window YES streak → trend continuation signal fires in phase 1."""
        history = {"BTC": [{"result": "yes"}, {"result": "yes"}]}
        combiner = self._make_combiner(integration_settings, history)

        snapshot = make_snapshot(
            implied_yes_prob=0.50, ttx=800.0, time_elapsed=100.0, phase=1,
        )
        prediction = PredictionResult(
            probability_yes=0.55, confidence=0.65, model_name="test"
        )
        features = make_features(
            momentum_60s=0.0005, implied_probability=0.50,
        )

        signals = combiner.evaluate(prediction, snapshot, current_position=0, features=features)
        trend = [s for s in signals if s.signal_type == "trend_continuation"]
        assert len(trend) == 1
        assert trend[0].side == "yes"

        order_id, position = await run_pipeline(
            trend[0], position_sizer, risk_manager, order_manager, position_tracker,
            ttx=800.0,
        )
        assert order_id is not None
        assert position is not None
        assert position.side == "yes"

    async def test_trend_streak_3_technical_confirmation(
        self,
        integration_settings,
    ):
        """3-window streak passes technical confirmation when signals agree."""
        history = {"BTC": [{"result": "yes"}, {"result": "yes"}, {"result": "yes"}]}
        combiner = self._make_combiner(integration_settings, history)

        snapshot = make_snapshot(
            implied_yes_prob=0.50, ttx=800.0, time_elapsed=100.0, phase=1,
        )
        prediction = PredictionResult(
            probability_yes=0.55, confidence=0.65, model_name="test"
        )
        # All 4 technical signals confirm YES direction
        features = make_features(
            momentum_60s=0.0005,
            rsi_14=55.0,  # < 70 → confirms YES
            orderbook_depth_imbalance=0.2,  # > 0 → confirms YES
            volume_weighted_momentum=0.001,  # > 0 → confirms YES
            taker_buy_sell_ratio=0.3,  # > 0 → confirms YES
        )

        signals = combiner.evaluate(prediction, snapshot, current_position=0, features=features)
        trend = [s for s in signals if s.signal_type == "trend_continuation"]
        assert len(trend) == 1

    async def test_trend_streak_3_blocked_no_confirmation(
        self,
        integration_settings,
    ):
        """3-window streak blocked when technical signals disagree."""
        history = {"BTC": [{"result": "yes"}, {"result": "yes"}, {"result": "yes"}]}
        combiner = self._make_combiner(integration_settings, history)

        snapshot = make_snapshot(
            implied_yes_prob=0.50, ttx=800.0, time_elapsed=100.0, phase=1,
        )
        prediction = PredictionResult(
            probability_yes=0.55, confidence=0.65, model_name="test"
        )
        # Technical signals all against YES direction
        features = make_features(
            momentum_60s=0.0005,
            rsi_14=80.0,  # > 70 → overbought, fails YES confirmation
            orderbook_depth_imbalance=-0.3,  # negative → fails YES
            volume_weighted_momentum=-0.001,  # negative → fails YES
            taker_buy_sell_ratio=-0.3,  # negative → fails YES
        )

        signals = combiner.evaluate(prediction, snapshot, current_position=0, features=features)
        trend = [s for s in signals if s.signal_type == "trend_continuation"]
        assert len(trend) == 0


# ---------------------------------------------------------------------------
# 4. Certainty scalp flow
# ---------------------------------------------------------------------------


class TestCertaintyScalpFlow:
    """Test certainty scalp in last minutes → full pipeline."""

    async def test_certainty_scalp_near_expiry(
        self,
        integration_settings,
        order_manager,
        position_tracker,
        risk_manager,
        position_sizer,
    ):
        """High implied (0.90) + model confirms + near expiry → certainty scalp."""
        # Disable directional so certainty scalp gets evaluated
        cfg = integration_settings.strategy.model_copy(
            update={"directional_enabled": False}
        )
        combiner = SignalCombiner(cfg)

        # Spot well above strike → YES is near-certain
        ob = make_orderbook(
            yes_prices=[0.90, 0.88, 0.85],
            no_prices=[0.12, 0.10, 0.08],
        )
        snapshot = make_snapshot(
            spot_price=97800.0,
            strike_price=97400.0,
            implied_yes_prob=0.90,
            spread=0.02,
            ttx=120.0,
            time_elapsed=780.0,
            phase=5,
            orderbook=ob,
        )
        prediction = PredictionResult(
            probability_yes=0.92, confidence=0.85, model_name="test"
        )

        signals = combiner.evaluate(prediction, snapshot, current_position=0)
        certainty = [s for s in signals if s.signal_type == "certainty_scalp"]
        assert len(certainty) == 1
        assert certainty[0].side == "yes"

        order_id, position = await run_pipeline(
            certainty[0], position_sizer, risk_manager, order_manager,
            position_tracker, ttx=120.0,
        )
        assert order_id is not None
        assert position is not None
        assert position.side == "yes"
        assert position.strategy_tag == "certainty_scalp"


# ---------------------------------------------------------------------------
# 5. Settlement ride flow
# ---------------------------------------------------------------------------


class TestSettlementRideFlow:
    """Test settlement ride → full pipeline."""

    async def test_settlement_ride_late_window(
        self,
        integration_settings,
        order_manager,
        position_tracker,
        risk_manager,
        position_sizer,
        vol_tracker,
    ):
        """Late window (650s elapsed), implied far from 0.50 → settlement ride."""
        # Disable directional to let settlement ride fire
        cfg = integration_settings.strategy.model_copy(
            update={"directional_enabled": False}
        )
        combiner = SignalCombiner(cfg, vol_tracker=vol_tracker)

        # Implied 0.65 → distance from 0.50 = 0.15 > 0.12 threshold
        # Model agrees strongly (0.80 > 0.50) → same direction as market
        # Need large edge to survive vol-tracker threshold adjustment
        ob = make_orderbook(
            yes_prices=[0.65, 0.63, 0.60],
            no_prices=[0.37, 0.35, 0.32],
        )
        snapshot = make_snapshot(
            implied_yes_prob=0.65,
            spread=0.02,
            ttx=250.0,
            time_elapsed=650.0,
            phase=4,
            orderbook=ob,
        )
        prediction = PredictionResult(
            probability_yes=0.80, confidence=0.70, model_name="test"
        )

        signals = combiner.evaluate(prediction, snapshot, current_position=0)
        ride = [s for s in signals if s.signal_type == "settlement_ride"]
        assert len(ride) == 1
        assert ride[0].side == "yes"

        order_id, position = await run_pipeline(
            ride[0], position_sizer, risk_manager, order_manager,
            position_tracker, vol_tracker=vol_tracker, ttx=250.0,
        )
        assert order_id is not None
        assert position is not None
        assert position.side == "yes"
        assert position.strategy_tag == "settlement_ride"


# ---------------------------------------------------------------------------
# 6. Market making flow
# ---------------------------------------------------------------------------


class TestMarketMakingFlow:
    """Test market making quotes → full pipeline."""

    async def test_mm_both_sides(
        self,
        integration_settings,
        order_manager,
        position_tracker,
        risk_manager,
        position_sizer,
    ):
        """Wide spread → MM generates quotes on both sides."""
        # Use tight model probability near 0.50, wide spread → MM opportunity
        cfg = integration_settings.strategy.model_copy(
            update={"directional_enabled": False}
        )
        combiner = SignalCombiner(cfg)

        # Wide spread: YES bid at 0.45, NO bid at 0.45 → YES ask = 0.55
        # This gives a 10-cent spread with room for MM to capture
        ob = make_orderbook(
            yes_prices=[0.45, 0.43, 0.40],
            no_prices=[0.45, 0.43, 0.40],
        )
        snapshot = make_snapshot(
            implied_yes_prob=0.50,
            spread=0.10,
            ttx=600.0,
            orderbook=ob,
        )
        prediction = PredictionResult(
            probability_yes=0.50, confidence=0.65, model_name="test"
        )

        signals = combiner.evaluate(prediction, snapshot, current_position=0)
        mm = [s for s in signals if s.signal_type == "market_making"]
        assert len(mm) == 2
        sides = {s.side for s in mm}
        assert sides == {"yes", "no"}

        # Run just the first signal through the full pipeline
        order_id, position = await run_pipeline(
            mm[0], position_sizer, risk_manager, order_manager, position_tracker,
        )
        assert order_id is not None
        assert position is not None

    async def test_mm_filters_when_directional(
        self,
        integration_settings,
        vol_tracker,
    ):
        """Directional YES present → MM only generates NO side."""
        combiner = SignalCombiner(
            integration_settings.strategy, vol_tracker=vol_tracker
        )

        # Wide spread: YES bid at 0.45, NO bid at 0.45
        ob = make_orderbook(
            yes_prices=[0.45, 0.43, 0.40],
            no_prices=[0.45, 0.43, 0.40],
        )
        snapshot = make_snapshot(
            implied_yes_prob=0.50,
            spread=0.10,
            ttx=600.0,
            orderbook=ob,
        )
        # Strong edge → directional YES fires
        prediction = PredictionResult(
            probability_yes=0.62, confidence=0.70, model_name="test"
        )
        features = make_features()

        signals = combiner.evaluate(prediction, snapshot, current_position=0, features=features)
        directional = [s for s in signals if s.signal_type == "directional"]
        mm = [s for s in signals if s.signal_type == "market_making"]

        assert len(directional) >= 1
        assert directional[0].side == "yes"
        # MM should be filtered to only NO side (opposite of directional)
        for sig in mm:
            assert sig.side == "no"


# ---------------------------------------------------------------------------
# 7. Exit flows
# ---------------------------------------------------------------------------


class TestExitFlows:
    """Test exit signals: settlement, take-profit, stop-loss, pre-expiry."""

    async def _enter_position(
        self,
        order_manager: OrderManager,
        position_tracker: PositionTracker,
        risk_manager: RiskManager,
        position_sizer: PositionSizer,
        settings: BotSettings,
        vol_tracker: VolatilityTracker | None = None,
    ) -> str:
        """Helper: enter a directional YES position and return the ticker."""
        combiner = SignalCombiner(settings.strategy, vol_tracker=vol_tracker)

        snapshot = make_snapshot(implied_yes_prob=0.51, ttx=600.0, phase=3)
        prediction = PredictionResult(
            probability_yes=0.62, confidence=0.70, model_name="test"
        )
        features = make_features()

        signals = combiner.evaluate(prediction, snapshot, current_position=0, features=features)
        directional = [s for s in signals if s.signal_type == "directional"]
        assert len(directional) >= 1

        order_id, position = await run_pipeline(
            directional[0], position_sizer, risk_manager, order_manager,
            position_tracker, vol_tracker=vol_tracker,
        )
        assert order_id is not None
        assert position is not None
        return position.market_ticker

    async def test_settlement_exit(
        self,
        integration_settings,
        order_manager,
        position_tracker,
        risk_manager,
        position_sizer,
        vol_tracker,
    ):
        """Position with ttx=0 → check_exits returns ticker."""
        ticker = await self._enter_position(
            order_manager, position_tracker, risk_manager,
            position_sizer, integration_settings, vol_tracker,
        )

        expired_snapshot = make_snapshot(ttx=0.0, time_elapsed=900.0)
        exits = position_tracker.check_exits({ticker: expired_snapshot})
        assert ticker in exits

    async def test_take_profit_exit(
        self,
        integration_settings,
        order_manager,
        position_tracker,
        risk_manager,
        position_sizer,
        vol_tracker,
    ):
        """Position with bid above entry + threshold → take profit fires."""
        ticker = await self._enter_position(
            order_manager, position_tracker, risk_manager,
            position_sizer, integration_settings, vol_tracker,
        )
        position = position_tracker.get_position(ticker)
        assert position is not None
        entry_price = float(position.avg_entry_price)

        # Force entry_time into the past so hold-time check passes
        position.entry_time = NOW - timedelta(minutes=5)

        # Create snapshot where best bid is significantly above entry
        high_bid = entry_price + 0.15
        ob = make_orderbook(
            yes_prices=[high_bid, high_bid - 0.02, high_bid - 0.04],
            no_prices=[0.50, 0.48, 0.45],
        )
        tp_snapshot = make_snapshot(ttx=400.0, orderbook=ob)
        results = position_tracker.check_take_profit(
            {ticker: tp_snapshot}, integration_settings.strategy,
        )
        assert len(results) >= 1
        assert results[0][0] == ticker

    async def test_stop_loss_exit(
        self,
        integration_settings,
        order_manager,
        position_tracker,
        risk_manager,
        position_sizer,
        vol_tracker,
    ):
        """Position with bid far below entry → stop loss fires."""
        ticker = await self._enter_position(
            order_manager, position_tracker, risk_manager,
            position_sizer, integration_settings, vol_tracker,
        )
        position = position_tracker.get_position(ticker)
        assert position is not None
        entry_price = float(position.avg_entry_price)

        # Force entry_time into the past so hold-time check passes
        position.entry_time = NOW - timedelta(minutes=5)

        # Create snapshot where bid is well below entry (>35% drop)
        low_bid = max(0.05, entry_price * 0.55)
        ob = make_orderbook(
            yes_prices=[low_bid, low_bid - 0.02, low_bid - 0.04],
            no_prices=[0.50, 0.48, 0.45],
        )
        sl_snapshot = make_snapshot(ttx=400.0, orderbook=ob)
        results = position_tracker.check_stop_loss(
            {ticker: sl_snapshot},
            stop_loss_pct=integration_settings.strategy.stop_loss_pct,
            max_dollar_loss=integration_settings.strategy.stop_loss_max_dollar_loss,
        )
        assert len(results) >= 1
        assert results[0][0] == ticker

    async def test_pre_expiry_exit(
        self,
        integration_settings,
        order_manager,
        position_tracker,
        risk_manager,
        position_sizer,
        vol_tracker,
    ):
        """Profitable position near expiry → pre-expiry exit fires."""
        ticker = await self._enter_position(
            order_manager, position_tracker, risk_manager,
            position_sizer, integration_settings, vol_tracker,
        )
        position = position_tracker.get_position(ticker)
        assert position is not None
        entry_price = float(position.avg_entry_price)

        # Force entry_time into the past
        position.entry_time = NOW - timedelta(minutes=10)

        # Slightly profitable position near expiry (80s → within 90s window)
        profit_bid = entry_price + 0.03
        ob = make_orderbook(
            yes_prices=[profit_bid, profit_bid - 0.02, profit_bid - 0.04],
            no_prices=[0.50, 0.48, 0.45],
        )
        pre_snapshot = make_snapshot(ttx=80.0, time_elapsed=820.0, orderbook=ob)
        results = position_tracker.check_pre_expiry_exits(
            {ticker: pre_snapshot},
            pre_expiry_seconds=integration_settings.strategy.pre_expiry_exit_seconds,
            min_pnl_per_contract=integration_settings.strategy.pre_expiry_exit_min_pnl_cents,
        )
        assert len(results) >= 1
        assert results[0][0] == ticker
