"""Typed configuration for the Kalshi BTC trading bot."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator


class AssetConfig(BaseModel):
    series_ticker: str        # "KXBTC15M" or "KXETH15M"
    symbol: str               # "BTC" or "ETH"
    primary_ws_url: str       # Coinbase WS for this asset
    primary_symbol: str       # "BTC-USD" or "ETH-USD"
    secondary_ws_url: str = ""
    secondary_symbol: str = ""
    chainlink_contract: str = ""
    chainlink_rpc_url: str = ""


class KalshiConfig(BaseModel):
    api_key_id: str = ""
    private_key_path: str = ""
    series_ticker: str = "KXBTC15M"
    rate_limit_ms: int = 100
    assets: list[AssetConfig] = []

    @property
    def base_url(self) -> str:
        return "https://api.elections.kalshi.com/trade-api/v2"

    @property
    def ws_url(self) -> str:
        return "wss://api.elections.kalshi.com/trade-api/ws/v2"


class BinanceConfig(BaseModel):
    ws_url: str = "wss://stream.binance.com:9443/ws/btcusdt@trade"
    symbol: str = "BTCUSDT"


class SecondaryFeedConfig(BaseModel):
    enabled: bool = True
    ws_url: str = "wss://ws.kraken.com/v2"
    symbol: str = "BTC/USD"


class AveragingConfig(BaseModel):
    enabled: bool = True
    discount_tiers: list[float] = [0.10, 0.20, 0.35]  # % below avg entry
    size_multipliers: list[float] = [1.0, 1.5, 2.0]    # multiplier per tier
    max_adds_per_position: int = 3
    min_time_to_expiry_seconds: float = 120.0
    momentum_threshold: float = 0.003  # magnitude to consider "strong" momentum
    dead_zone: float = 0.02  # buffer around 0.50 for thesis check


class StrategyConfig(BaseModel):
    poll_interval_seconds: float = 4.0
    min_edge_threshold: float = 0.03
    max_edge_threshold: float = 0.25
    confidence_weight: float = 0.7
    confidence_min: float = 0.55  # Minimum model confidence to trade
    directional_max_spread: float = 0.15  # Skip directional trades when spread > this
    directional_min_depth: int = 5  # Require at least this many contracts in orderbook
    use_statistical_fair_value: bool = True  # Use fair value when orderbook is thin
    thin_book_edge_multiplier: float = 1.5  # Require 1.5x edge on thin orderbooks
    directional_enabled: bool = True
    use_market_maker: bool = True
    mm_min_spread: float = 0.05
    mm_max_spread: float = 0.30
    mm_max_inventory: int = 20  # Stop MM when holding this many contracts
    use_time_profiles: bool = True
    time_profile_lookback_days: int = 30
    # FOMO exploitation parameters
    fomo_enabled: bool = True
    fomo_min_divergence: float = 0.18
    fomo_edge_threshold: float = 0.06
    fomo_momentum_min_magnitude: float = 0.003
    fomo_momentum_consistency_required: bool = True
    fomo_max_implied_prob: float = 0.85
    fomo_min_implied_prob: float = 0.15
    fomo_min_confidence: float = 0.70
    fomo_min_score: float = 0.50
    fomo_max_bet_dollars: float = 2.00  # Max dollar exposure per FOMO trade
    fomo_min_entry_price: float = 0.10  # FOMO-specific floor (lower than global)
    # Stop-loss parameters
    stop_loss_enabled: bool = True
    stop_loss_pct: float = 0.35  # Exit when loss > 35% of entry price
    stop_loss_min_bid: float = 0.05  # Don't sell if bid is below $0.05
    stop_loss_min_hold_seconds: float = 60.0  # Minimum hold before SL can trigger
    stop_loss_max_dollar_loss: float = 2.0  # Cap absolute loss per position
    # Take-profit parameters
    take_profit_enabled: bool = True
    take_profit_min_profit_cents: float = 0.10
    take_profit_min_hold_seconds: float = 20.0
    take_profit_time_decay_start_seconds: float = 300.0
    take_profit_time_decay_floor_cents: float = 0.05
    take_profit_cooldown_seconds: float = 900.0  # Block re-entry after TP for rest of window
    # Trailing take-profit: ratchet exit price up as position gains
    trailing_take_profit_enabled: bool = True
    trailing_take_profit_activation_cents: float = 0.08  # Profit needed to activate trailing
    trailing_take_profit_drop_cents: float = 0.05  # Exit when price drops this much from peak
    # Pre-expiry exit: sell before settlement instead of gambling
    pre_expiry_exit_enabled: bool = True
    pre_expiry_exit_seconds: float = 90.0  # Sell with this many seconds left
    pre_expiry_exit_min_pnl_cents: float = -0.03  # Only pre-expiry exit if PnL >= this per contract
    # Edge persistence: require N consecutive cycles with same-side edge before entry
    edge_confirmation_cycles: int = 2
    # Thesis-break exit: sell position when model flips against us
    thesis_break_enabled: bool = True
    thesis_break_threshold: float = 0.05  # model must cross 0.50 +/- this to trigger exit
    thesis_break_min_hold_seconds: float = 60.0  # minimum hold before thesis break can fire
    # Entry price filter: block cheap contracts with poor hit rates
    min_entry_price: float = 0.40
    # Per-asset min entry price override (e.g. ETH needs higher floor)
    asset_min_entry_price: dict[str, float] = {}
    # YES-side edge penalty: require more edge for YES (NO side is more profitable empirically)
    yes_side_edge_multiplier: float = 1.4
    # NO-side edge penalty: require more edge for NO (16.7% WR in backtest, model fights market)
    no_side_edge_multiplier: float = 1.5
    # Per-asset edge multipliers: require more edge for noisier assets
    # Keys are asset symbols (e.g. "ETH"), values are multipliers applied to edge thresholds
    asset_edge_multipliers: dict[str, float] = {}
    # Per-asset stop-loss override: noisier assets may need wider stops
    asset_stop_loss_pct: dict[str, float] = {}
    # Disable directional trading for specific assets (keep MM only)
    asset_directional_disabled: list[str] = []
    # Zone filter: block expensive directional trades
    zone_filter_enabled: bool = True
    max_directional_price: float = 0.60
    zone_edge_multipliers: list[float] = [0.6, 0.8, 1.0]  # Zones 1, 2, 3
    # Expiry decay: require more edge as expiry approaches (model accuracy degrades)
    edge_expiry_decay_enabled: bool = True
    edge_expiry_decay_max: float = 1.8  # At 1 min left, require 1.8x normal edge
    # Phase timing: gate trades by window phase
    phase_filter_enabled: bool = True
    phase_observation_end: float = 420.0  # 7 min observation (early entries lose money)
    phase_confirmation_end: float = 540.0  # 9 min confirmation end
    phase_active_end: float = 720.0
    phase_late_end: float = 840.0
    phase_late_edge_multiplier: float = 1.3
    phase_late_confidence_boost: float = 0.05
    # Overreaction / bounce-back detection
    overreaction_enabled: bool = True
    overreaction_extreme_threshold: float = 0.20
    overreaction_momentum_reversal_threshold: float = 0.002
    # Settlement bias: use recent settlement outcomes as a directional signal
    settlement_bias_enabled: bool = True
    settlement_bias_weight: float = 0.08
    # Cross-asset divergence: use other asset's implied prob as a lead signal
    cross_asset_divergence_enabled: bool = True
    cross_asset_divergence_weight: float = 0.06
    # Composite quality score: require combined edge + confidence quality
    min_quality_score: float = 0.80
    # BTC beta leader: use BTC momentum to enable ETH directional
    btc_beta_enabled: bool = True
    btc_beta_min_signal: float = 0.40  # Min |btc_beta_signal| to override ETH directional disable
    # Quiet hours: skip directional trading during low-volume hours (MM still allowed)
    quiet_hours_enabled: bool = True
    quiet_hours_est: list[int] = [17, 18]  # Worst P&L hours (-$123): hard-block directional (EST)
    # Settlement-ride: enter late in window, hold to settlement (no TP/SL/pre-expiry)
    settlement_ride_enabled: bool = True
    settlement_ride_min_elapsed_seconds: float = 600.0  # Only enter after 10 min elapsed
    settlement_ride_min_edge: float = 0.03               # Lower edge ok (no exit fees)
    settlement_ride_min_implied_distance: float = 0.12   # Min |implied - 0.50| to enter
    settlement_ride_kelly_fraction: float = 0.10         # Conservative sizing (can't cut losses)
    # Per-asset settlement ride overrides: noisier assets need higher thresholds
    asset_settlement_ride_min_edge: dict[str, float] = {}
    asset_settlement_ride_min_implied_distance: dict[str, float] = {}
    # Disable market-making for specific assets
    asset_market_maker_disabled: list[str] = []
    # Per-asset minimum spread for market making
    asset_mm_min_spread: dict[str, float] = {}
    # Disable settlement ride for specific assets
    asset_settlement_ride_disabled: list[str] = []
    # Trend continuation: enter early when recent settlements show persistent trend
    trend_continuation_enabled: bool = True
    trend_continuation_min_streak: int = 3
    trend_continuation_max_phase: int = 2
    trend_continuation_min_implied_prob: float = 0.35
    trend_continuation_max_implied_prob: float = 0.65
    trend_continuation_streak_prob: float = 0.65
    trend_continuation_min_edge: float = 0.04
    trend_continuation_kelly_fraction: float = 0.15
    trend_continuation_momentum_threshold: float = 0.001  # Skip if momentum fights streak
    trend_continuation_min_entry_price: float = 0.30  # Relaxed vs global 0.40 (enters near 50/50)
    trend_continuation_extended_streak_threshold: int = 3  # Streak length requiring extra checks
    trend_continuation_min_confirming_signals: int = 3  # 3/4 technicals must confirm for 3+ streak
    trend_continuation_rsi_extreme_threshold: float = 70.0  # RSI overbought/oversold boundary
    # Certainty scalp: bet large on near-certain outcomes in last 3 min
    certainty_scalp_enabled: bool = True
    certainty_scalp_max_ttx: float = 240.0          # 4 min window (widened for vol-based path)
    certainty_scalp_min_ttx: float = 60.0            # At least 60s to get filled
    certainty_scalp_min_implied_prob: float = 0.85   # Market must show 85%+ one direction
    certainty_scalp_min_model_prob: float = 0.70     # Model must agree at 70%+ (legacy path)
    certainty_scalp_min_edge: float = 0.02           # Low bar (fees tiny at extremes)
    certainty_scalp_kelly_fraction: float = 0.30     # Aggressive sizing
    certainty_scalp_min_spot_distance_pct: float = 0.002  # 0.2% spot past strike
    certainty_scalp_min_fair_value_prob: float = 0.90  # Vol-based: require 90%+ mathematical prob
    # Trend guard: block trades against majority momentum direction
    trend_guard_enabled: bool = False
    # MM vol filter: skip market-making in extreme volatility regime
    mm_vol_filter_enabled: bool = False
    # Volatility regime filter: block entries when realized vol is too high (coin-flip territory)
    vol_regime_filter_enabled: bool = True
    vol_regime_max_realized_vol: float = 0.008
    # Hold-to-settlement: skip TP/pre-expiry exit for profitable positions near expiry
    # Settling naturally avoids exit fees entirely ($0 vs maker/taker sell fee)
    hold_to_settle_seconds: float = 180.0  # Within this many seconds of expiry
    hold_to_settle_min_profit_cents: float = 0.15  # Min profit/contract to qualify


class RiskConfig(BaseModel):
    max_position_per_market: int = 15
    max_total_exposure_dollars: float = 50.0
    max_daily_loss_dollars: float = 5.0
    max_concurrent_positions: int = 3
    kelly_fraction: float = 0.15
    min_balance_dollars: float = 50.0
    max_trades_per_day: int = 40
    cooldown_after_streak_minutes: int = 30
    max_consecutive_losses: int = 3
    # Drawdown circuit breaker: stop trading if daily P&L drops X below its peak
    drawdown_limit_enabled: bool = True
    drawdown_limit_dollars: float = 20.0  # Stop if daily PnL drops $20 from peak
    # Entry cooldown: minimum seconds between fills on the same market
    entry_cooldown_seconds: float = 30.0
    # Per-cycle contract cap: max contracts placed in a single strategy cycle
    max_contracts_per_cycle: int = 10
    # Fee-aware position sizing at extreme prices
    fee_extreme_price_threshold: float = 0.25
    fee_extreme_kelly_multiplier: float = 1.0
    # Zone-based Kelly scaling
    zone_kelly_multipliers: list[float] = [1.3, 1.15, 1.0, 0.7, 0.5]
    # Time-based position scaling: reduce size when less time for take-profit
    time_scale_enabled: bool = True
    time_scale_full_seconds: float = 480.0  # Full size above this time remaining
    time_scale_min_multiplier: float = 0.4  # Minimum scaling at expiry
    min_position_size: int = 5  # Don't enter with fewer than this many contracts
    # Directional high-price boost: size up directional at $0.50+ (92-99% WR zone)
    directional_high_price_boost: float = 1.5
    directional_high_price_threshold: float = 0.50
    # Per-asset position limits: noisier assets get smaller positions
    asset_max_position: dict[str, int] = {}
    asset_max_per_cycle: dict[str, int] = {}


class FeatureConfig(BaseModel):
    lookback_seconds: int = 900
    momentum_windows: list[int] = [15, 60, 180, 600, 1800]
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


class BinanceFuturesConfig(BaseModel):
    enabled: bool = True
    symbols: list[str] = ["BTCUSDT", "ETHUSDT"]
    funding_poll_interval: float = 45.0
    liquidation_ws_url: str = "wss://stream.bybit.com/v5/public/linear"
    funding_api_base: str = "https://api.bybit.com"


class BotSettings(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    kalshi: KalshiConfig = KalshiConfig()
    binance: BinanceConfig = BinanceConfig()
    secondary_feed: SecondaryFeedConfig = SecondaryFeedConfig()
    binance_futures: BinanceFuturesConfig = BinanceFuturesConfig()

    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    features: FeatureConfig = FeatureConfig()
    logging: LoggingConfig = LoggingConfig()
    database: DatabaseConfig = DatabaseConfig()
    averaging: AveragingConfig = AveragingConfig()
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

            # Backward compat: auto-populate assets from legacy binance/secondary_feed
            kalshi = values.get("kalshi", {})
            if isinstance(kalshi, dict) and not kalshi.get("assets"):
                binance = values.get("binance", {})
                secondary = values.get("secondary_feed", {})
                series = kalshi.get("series_ticker", "KXBTC15M")
                kalshi["assets"] = [
                    {
                        "series_ticker": series,
                        "symbol": "BTC",
                        "primary_ws_url": binance.get("ws_url", "wss://ws-feed.exchange.coinbase.com") if isinstance(binance, dict) else "wss://ws-feed.exchange.coinbase.com",
                        "primary_symbol": binance.get("symbol", "BTC-USD") if isinstance(binance, dict) else "BTC-USD",
                        "secondary_ws_url": secondary.get("ws_url", "") if isinstance(secondary, dict) else "",
                        "secondary_symbol": secondary.get("symbol", "") if isinstance(secondary, dict) else "",
                    }
                ]
                values["kalshi"] = kalshi
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
