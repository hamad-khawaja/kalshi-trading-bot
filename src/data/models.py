"""Data models for the Kalshi BTC trading bot."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class PriceTick(BaseModel):
    """A single BTC price observation from an exchange."""

    price: Decimal
    volume: Decimal
    timestamp: datetime
    is_taker_buy: bool | None = None


class OrderbookLevel(BaseModel):
    """A single price level in a Kalshi orderbook."""

    price_dollars: Decimal
    quantity: int


class Orderbook(BaseModel):
    """Kalshi orderbook snapshot for a market."""

    ticker: str
    yes_levels: list[OrderbookLevel] = Field(default_factory=list)
    no_levels: list[OrderbookLevel] = Field(default_factory=list)
    timestamp: datetime

    @property
    def best_yes_bid(self) -> Decimal | None:
        """Best bid price for YES contracts."""
        return self.yes_levels[0].price_dollars if self.yes_levels else None

    @property
    def best_no_bid(self) -> Decimal | None:
        """Best bid price for NO contracts."""
        return self.no_levels[0].price_dollars if self.no_levels else None

    @property
    def best_yes_ask(self) -> Decimal | None:
        """Best ask for YES = 1 - best NO bid."""
        if self.best_no_bid is not None:
            return Decimal("1") - self.best_no_bid
        return None

    @property
    def implied_yes_prob(self) -> Decimal | None:
        """Midpoint of YES bid/ask as implied probability."""
        bid = self.best_yes_bid
        ask = self.best_yes_ask
        if bid is not None and ask is not None:
            return (bid + ask) / 2
        if bid is not None:
            return bid
        if ask is not None:
            return ask
        return None

    @property
    def spread(self) -> Decimal | None:
        """Spread between YES ask and YES bid in dollars."""
        bid = self.best_yes_bid
        ask = self.best_yes_ask
        if bid is not None and ask is not None:
            return ask - bid
        return None

    @property
    def yes_bid_depth(self) -> int:
        """Total quantity on YES bid side."""
        return sum(level.quantity for level in self.yes_levels)

    @property
    def no_bid_depth(self) -> int:
        """Total quantity on NO bid side."""
        return sum(level.quantity for level in self.no_levels)


class Market(BaseModel):
    """A Kalshi market (single contract)."""

    ticker: str
    event_ticker: str = ""
    title: str = ""
    subtitle: str = ""
    yes_sub_title: str = ""
    status: str = ""
    yes_bid: Decimal | None = None
    yes_ask: Decimal | None = None
    no_bid: Decimal | None = None
    no_ask: Decimal | None = None
    last_price: Decimal | None = None
    volume: int = 0
    volume_24h: int = 0
    open_interest: int = 0
    open_time: datetime | None = None
    close_time: datetime | None = None
    expiration_time: datetime | None = None
    expected_expiration_time: datetime | None = None


class OrderRequest(BaseModel):
    """Request to place an order on Kalshi."""

    ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"] = "buy"
    type: Literal["limit"] = "limit"
    count: int
    yes_price_dollars: str | None = None
    no_price_dollars: str | None = None
    client_order_id: str
    expiration_ts: int | None = None
    post_only: bool = False

    def to_api_dict(self) -> dict:
        """Convert to Kalshi API request body."""
        body: dict = {
            "ticker": self.ticker,
            "side": self.side,
            "action": self.action,
            "type": self.type,
            "count": self.count,
            "client_order_id": self.client_order_id,
        }
        if self.yes_price_dollars is not None:
            body["yes_price_dollars"] = self.yes_price_dollars
        if self.no_price_dollars is not None:
            body["no_price_dollars"] = self.no_price_dollars
        if self.expiration_ts is not None:
            body["expiration_ts"] = self.expiration_ts
        if self.post_only:
            body["post_only"] = True
        return body


class OrderResponse(BaseModel):
    """Response from Kalshi after placing an order."""

    order_id: str = ""
    client_order_id: str = ""
    ticker: str = ""
    status: str = ""
    side: str = ""
    action: str = ""
    yes_price_dollars: Decimal | None = None
    no_price_dollars: Decimal | None = None
    count: int = 0
    fill_count: int = 0
    remaining_count: int = 0
    taker_fees_dollars: Decimal | None = None
    maker_fees_dollars: Decimal | None = None
    created_time: datetime | None = None


class Position(BaseModel):
    """A position held in a Kalshi market."""

    ticker: str = ""
    position: int = 0  # Signed contract count: +N = YES, -N = NO
    market_exposure: int = 0  # Cost of position in cents
    resting_orders_count: int = 0
    fees_paid: Decimal = Decimal("0")
    total_traded: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")


class MarketSnapshot(BaseModel):
    """Immutable snapshot of all market data at a point in time."""

    timestamp: datetime
    market_ticker: str
    btc_price: Decimal
    btc_prices_1min: list[Decimal] = Field(default_factory=list)
    btc_prices_5min: list[Decimal] = Field(default_factory=list)
    btc_prices_30min: list[Decimal] = Field(default_factory=list)
    btc_volumes_1min: list[Decimal] = Field(default_factory=list)
    orderbook: Orderbook
    implied_yes_prob: Decimal | None = None
    spread: Decimal | None = None
    strike_price: Decimal | None = None
    statistical_fair_value: float | None = None
    binance_btc_price: Decimal | None = None
    cross_exchange_spread: float | None = None
    cross_exchange_lead: float | None = None
    taker_buy_volume: float | None = None
    taker_sell_volume: float | None = None
    chainlink_oracle_price: Decimal | None = None
    chainlink_divergence: float | None = None
    chainlink_round_updated: bool = False
    btc_momentum_lead: float | None = None  # BTC momentum for non-BTC assets
    funding_rate: float | None = None
    predicted_funding_rate: float | None = None
    liquidation_long_usd: float | None = None
    liquidation_short_usd: float | None = None
    other_asset_funding_rate: float | None = None
    other_asset_liquidation_long_usd: float | None = None
    other_asset_liquidation_short_usd: float | None = None
    time_to_expiry_seconds: float = 0.0
    time_elapsed_seconds: float = 0.0
    window_phase: int = 0  # 1-5
    volume: int = 0


class TradeSignal(BaseModel):
    """A signal to execute a trade."""

    market_ticker: str
    side: Literal["yes", "no"]
    action: Literal["buy", "sell"] = "buy"
    raw_edge: float
    net_edge: float
    model_probability: float
    implied_probability: float
    confidence: float
    suggested_price_dollars: str
    suggested_count: int = 0
    timestamp: datetime
    signal_type: Literal[
        "directional", "market_making", "fomo",
        "averaging", "settlement_ride", "certainty_scalp",
    ] = "directional"
    entry_zone: int = 0  # Risk zone 1-5 (0 = unknown/MM)
    post_only: bool | None = None  # Override: True=maker, False=taker, None=default


class CompletedTrade(BaseModel):
    """A trade that has been executed and resolved."""

    order_id: str
    market_ticker: str
    side: str
    action: str
    count: int
    price_dollars: Decimal
    fees_dollars: Decimal
    pnl_dollars: Decimal | None = None
    entry_time: datetime
    exit_time: datetime | None = None
    model_probability: float | None = None
    implied_probability: float | None = None
    strategy_tag: str = "directional"
    market_volume: int | None = None


class FeatureVector(BaseModel):
    """Computed features for model input."""

    timestamp: datetime
    market_ticker: str
    momentum_15s: float = 0.0
    momentum_60s: float = 0.0
    momentum_180s: float = 0.0
    momentum_600s: float = 0.0
    momentum_1800s: float = 0.0
    realized_vol_5min: float = 0.0
    rsi_14: float = 50.0
    vwap_deviation: float = 0.0
    order_flow_imbalance: float = 0.0
    spread: float = 0.0
    spread_ratio: float = 0.0
    time_to_expiry_normalized: float = 1.0
    kalshi_volume: int = 0
    implied_probability: float = 0.5
    bollinger_position: float = 0.0
    macd_histogram: float = 0.0
    roc_acceleration: float = 0.0
    volume_weighted_momentum: float = 0.0
    orderbook_depth_imbalance: float = 0.0
    cross_exchange_spread: float = 0.0
    cross_exchange_lead: float = 0.0
    taker_buy_sell_ratio: float = 0.0
    settlement_bias: float = 0.0  # [-1, 1]: positive = recent YES bias
    cross_asset_divergence: float = 0.0  # [-1, 1]: positive = other asset more bullish
    chainlink_divergence: float = 0.0
    chainlink_confirmation: float = 0.0
    btc_beta_signal: float = 0.0  # BTC-led directional signal for non-BTC assets
    funding_rate_signal: float = 0.0  # [-1, 1]: negative = high positive funding (crowded longs, bearish)
    liquidation_imbalance: float = 0.0  # [-1, 1]: positive = more longs liquidated (bearish pressure)
    funding_rate_divergence: float = 0.0  # [-1, 1]: cross-asset funding rate divergence
    liquidation_ratio_divergence: float = 0.0  # [-1, 1]: cross-asset liquidation ratio divergence
    time_elapsed_seconds: float = 0.0
    window_phase: int = 0  # 1-5
    hour_of_day_sin: float = 0.0
    hour_of_day_cos: float = 0.0
    mc_probability: float = 0.5   # MC simulation P(YES), default neutral
    mc_confidence: float = 0.0    # MC bootstrap confidence, default zero (disabled)

    def to_array(self) -> list[float]:
        """Convert to flat list for model input, replacing None with 0."""
        return [
            self.momentum_15s,
            self.momentum_60s,
            self.momentum_180s,
            self.momentum_600s,
            self.momentum_1800s,
            self.realized_vol_5min,
            self.rsi_14,
            self.vwap_deviation,
            self.order_flow_imbalance,
            self.spread,
            self.spread_ratio,
            self.time_to_expiry_normalized,
            float(self.kalshi_volume),
            self.implied_probability,
            self.bollinger_position,
            self.macd_histogram,
            self.roc_acceleration,
            self.volume_weighted_momentum,
            self.orderbook_depth_imbalance,
            self.cross_exchange_spread,
            self.cross_exchange_lead,
            self.taker_buy_sell_ratio,
            self.settlement_bias,
            self.cross_asset_divergence,
            self.chainlink_divergence,
            self.chainlink_confirmation,
            self.btc_beta_signal,
            self.funding_rate_signal,
            self.liquidation_imbalance,
            self.funding_rate_divergence,
            self.liquidation_ratio_divergence,
            self.hour_of_day_sin,
            self.hour_of_day_cos,
            self.mc_probability,
            self.mc_confidence,
        ]

    @staticmethod
    def feature_names() -> list[str]:
        """Return ordered feature names matching to_array() output."""
        return [
            "momentum_15s",
            "momentum_60s",
            "momentum_180s",
            "momentum_600s",
            "momentum_1800s",
            "realized_vol_5min",
            "rsi_14",
            "vwap_deviation",
            "order_flow_imbalance",
            "spread",
            "spread_ratio",
            "time_to_expiry_normalized",
            "kalshi_volume",
            "implied_probability",
            "bollinger_position",
            "macd_histogram",
            "roc_acceleration",
            "volume_weighted_momentum",
            "orderbook_depth_imbalance",
            "cross_exchange_spread",
            "cross_exchange_lead",
            "taker_buy_sell_ratio",
            "settlement_bias",
            "cross_asset_divergence",
            "chainlink_divergence",
            "chainlink_confirmation",
            "btc_beta_signal",
            "funding_rate_signal",
            "liquidation_imbalance",
            "funding_rate_divergence",
            "liquidation_ratio_divergence",
            "hour_of_day_sin",
            "hour_of_day_cos",
            "mc_probability",
            "mc_confidence",
        ]


class PredictionResult(BaseModel):
    """Output of the probability model."""

    probability_yes: float
    confidence: float
    model_name: str
    features_used: dict[str, float] = Field(default_factory=dict)
