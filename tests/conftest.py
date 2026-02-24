"""Shared test fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.config import (
    BinanceConfig,
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
    Market,
    MarketSnapshot,
    Orderbook,
    OrderbookLevel,
    PredictionResult,
)


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 2, 12, 14, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_orderbook(now: datetime) -> Orderbook:
    """Realistic orderbook with yes/no levels."""
    return Orderbook(
        ticker="kxbtc15m-26feb121415",
        yes_levels=[
            OrderbookLevel(price_dollars=Decimal("0.52"), quantity=100),
            OrderbookLevel(price_dollars=Decimal("0.50"), quantity=200),
            OrderbookLevel(price_dollars=Decimal("0.48"), quantity=150),
        ],
        no_levels=[
            OrderbookLevel(price_dollars=Decimal("0.50"), quantity=120),
            OrderbookLevel(price_dollars=Decimal("0.48"), quantity=180),
            OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100),
        ],
        timestamp=now,
    )


@pytest.fixture
def sample_snapshot(now: datetime, sample_orderbook: Orderbook) -> MarketSnapshot:
    """A realistic MarketSnapshot for testing."""
    return MarketSnapshot(
        timestamp=now,
        market_ticker="kxbtc15m-26feb121415",
        spot_price=Decimal("97500.00"),
        spot_prices_1min=[
            Decimal(f"{97500 + i * 0.5}") for i in range(60)
        ],
        spot_prices_5min=[
            Decimal(f"{97480 + i * 0.02}") for i in range(1800)
        ],
        spot_volumes_1min=[
            Decimal("0.01") for _ in range(60)
        ],
        orderbook=sample_orderbook,
        implied_yes_prob=Decimal("0.51"),
        spread=Decimal("0.02"),
        chainlink_oracle_price=Decimal("97480.00"),
        chainlink_divergence=0.0002,
        chainlink_round_updated=False,
        btc_momentum_lead=0.0,
        time_to_expiry_seconds=600.0,
        volume=250,
    )


@pytest.fixture
def sample_feature_vector(now: datetime) -> FeatureVector:
    """Pre-computed feature vector."""
    return FeatureVector(
        timestamp=now,
        market_ticker="kxbtc15m-26feb121415",
        momentum_15s=0.0002,
        momentum_60s=0.0005,
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
        implied_probability=0.51,
        bollinger_position=0.1,
        macd_histogram=0.0005,
        roc_acceleration=0.0001,
        volume_weighted_momentum=0.0003,
        orderbook_depth_imbalance=0.15,
        orderbook_support_resistance=0.1,
        orderbook_wall_distance=-0.05,
        orderbook_wall_strength=0.3,
    )


@pytest.fixture
def sample_prediction() -> PredictionResult:
    """Sample model prediction."""
    return PredictionResult(
        probability_yes=0.58,
        confidence=0.65,
        model_name="heuristic_v1",
        features_used={"mom_signal": 0.3, "flow_signal": 0.1},
    )


@pytest.fixture
def bot_settings() -> BotSettings:
    """Test configuration with paper mode, conservative limits."""
    return BotSettings(
        mode="paper",
        kalshi=KalshiConfig(
            api_key_id="test-key",
            private_key_path="/tmp/test-key.pem",
            series_ticker="KXBTC15M",
            rate_limit_ms=10,
        ),
        binance=BinanceConfig(),
        strategy=StrategyConfig(
            poll_interval_seconds=4,
            min_edge_threshold=0.03,
            max_edge_threshold=0.25,
            confidence_weight=0.7,
            use_market_maker=True,
            mm_min_spread=0.05,
        ),
        risk=RiskConfig(
            max_position_per_market=50,
            max_total_exposure_dollars=500.0,
            max_daily_loss_dollars=100.0,
            max_concurrent_positions=5,
            kelly_fraction=0.25,
            min_balance_dollars=50.0,
            max_trades_per_day=100,
            cooldown_after_streak_minutes=30,
            max_consecutive_losses=5,
        ),
        features=FeatureConfig(),
        logging=LoggingConfig(level="DEBUG"),
        database=DatabaseConfig(path=":memory:"),
    )
