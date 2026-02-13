"""Typed configuration for the Kalshi BTC trading bot."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator


class KalshiConfig(BaseModel):
    environment: Literal["demo", "prod"] = "demo"
    api_key_id: str = ""
    private_key_path: str = ""
    series_ticker: str = "KXBTC15M"
    rate_limit_ms: int = 100

    @property
    def base_url(self) -> str:
        if self.environment == "demo":
            return "https://demo-api.kalshi.co/trade-api/v2"
        return "https://api.elections.kalshi.com/trade-api/v2"

    @property
    def ws_url(self) -> str:
        if self.environment == "demo":
            return "wss://demo-api.kalshi.co/trade-api/ws/v2"
        return "wss://api.elections.kalshi.com/trade-api/ws/v2"


class BinanceConfig(BaseModel):
    ws_url: str = "wss://stream.binance.com:9443/ws/btcusdt@trade"
    symbol: str = "BTCUSDT"


class SecondaryFeedConfig(BaseModel):
    enabled: bool = True
    ws_url: str = "wss://ws.kraken.com/v2"
    symbol: str = "BTC/USD"


class CoinglassConfig(BaseModel):
    api_key: str = ""
    base_url: str = "https://open-api-v3.coinglass.com/api"


class StrategyConfig(BaseModel):
    poll_interval_seconds: float = 4.0
    min_edge_threshold: float = 0.03
    max_edge_threshold: float = 0.25
    confidence_weight: float = 0.7
    confidence_min: float = 0.45  # Minimum model confidence to trade
    directional_max_spread: float = 0.25  # Skip directional trades when spread > this
    directional_min_depth: int = 5  # Require at least this many contracts in orderbook
    use_statistical_fair_value: bool = True  # Use fair value when orderbook is thin
    thin_book_edge_multiplier: float = 1.5  # Require 1.5x edge on thin orderbooks
    use_market_maker: bool = True
    mm_min_spread: float = 0.05
    mm_max_spread: float = 0.30
    mm_max_inventory: int = 37  # Stop MM when holding this many contracts
    use_time_profiles: bool = True
    time_profile_lookback_days: int = 30


class RiskConfig(BaseModel):
    max_position_per_market: int = 10
    max_total_exposure_dollars: float = 200.0
    max_daily_loss_dollars: float = 30.0
    max_concurrent_positions: int = 3
    kelly_fraction: float = 0.25
    min_balance_dollars: float = 50.0
    max_trades_per_day: int = 50
    cooldown_after_streak_minutes: int = 30
    max_consecutive_losses: int = 4


class FeatureConfig(BaseModel):
    lookback_seconds: int = 900
    momentum_windows: list[int] = [15, 60, 180, 600]
    volatility_window: int = 300
    orderbook_depth: int = 10


class DashboardConfig(BaseModel):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "logs/bot.log"
    format: str = "json"


class DatabaseConfig(BaseModel):
    path: str = "data/bot.db"


class BotSettings(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    kalshi: KalshiConfig = KalshiConfig()
    binance: BinanceConfig = BinanceConfig()
    secondary_feed: SecondaryFeedConfig = SecondaryFeedConfig()
    coinglass: CoinglassConfig = CoinglassConfig()
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    features: FeatureConfig = FeatureConfig()
    logging: LoggingConfig = LoggingConfig()
    database: DatabaseConfig = DatabaseConfig()
    dashboard: DashboardConfig = DashboardConfig()

    @model_validator(mode="before")
    @classmethod
    def apply_env_overrides(cls, values: dict) -> dict:
        """Apply environment variable overrides for secrets."""
        if isinstance(values, dict):
            kalshi = values.get("kalshi", {})
            if isinstance(kalshi, dict):
                if not kalshi.get("api_key_id"):
                    kalshi["api_key_id"] = os.environ.get("KALSHI_API_KEY_ID", "")
                if not kalshi.get("private_key_path"):
                    kalshi["private_key_path"] = os.environ.get(
                        "KALSHI_PRIVATE_KEY_PATH", ""
                    )
                values["kalshi"] = kalshi

            coinglass = values.get("coinglass", {})
            if isinstance(coinglass, dict):
                if not coinglass.get("api_key"):
                    coinglass["api_key"] = os.environ.get("COINGLASS_API_KEY", "")
                values["coinglass"] = coinglass
        return values


def load_settings(config_path: str = "config/settings.yaml") -> BotSettings:
    """Load settings from YAML file with environment variable overrides."""
    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    return BotSettings(**data)
