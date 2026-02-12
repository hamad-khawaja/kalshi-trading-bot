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
    market_exposure: int = 0
    resting_orders_count: int = 0
    fees_paid: Decimal = Decimal("0")
    total_traded: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")


class FundingRate(BaseModel):
    """Aggregate BTC funding rate data."""

    rate: float = 0.0
    timestamp: datetime | None = None


class OpenInterest(BaseModel):
    """Aggregate BTC open interest data."""

    value: float = 0.0
    change_24h: float = 0.0
    timestamp: datetime | None = None


class LongShortRatio(BaseModel):
    """Aggregate BTC long/short ratio."""

    ratio: float = 1.0
    long_pct: float = 50.0
    short_pct: float = 50.0
    timestamp: datetime | None = None


class MarketSnapshot(BaseModel):
    """Immutable snapshot of all market data at a point in time."""

    timestamp: datetime
    market_ticker: str
    btc_price: Decimal
    btc_prices_1min: list[Decimal] = Field(default_factory=list)
    btc_prices_5min: list[Decimal] = Field(default_factory=list)
    btc_volumes_1min: list[Decimal] = Field(default_factory=list)
    orderbook: Orderbook
    implied_yes_prob: Decimal | None = None
    spread: Decimal | None = None
    funding_rate: float | None = None
    open_interest: float | None = None
    open_interest_change: float | None = None
    long_short_ratio: float | None = None
    time_to_expiry_seconds: float = 0.0
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
    signal_type: Literal["directional", "market_making"] = "directional"


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


class FeatureVector(BaseModel):
    """Computed features for model input."""

    timestamp: datetime
    market_ticker: str
    momentum_15s: float = 0.0
    momentum_60s: float = 0.0
    momentum_180s: float = 0.0
    momentum_600s: float = 0.0
    realized_vol_5min: float = 0.0
    rsi_14: float = 50.0
    vwap_deviation: float = 0.0
    order_flow_imbalance: float = 0.0
    spread: float = 0.0
    spread_ratio: float = 0.0
    time_to_expiry_normalized: float = 1.0
    funding_rate: float | None = None
    funding_rate_z_score: float | None = None
    open_interest_change: float | None = None
    long_short_ratio: float | None = None
    kalshi_volume: int = 0
    implied_probability: float = 0.5

    def to_array(self) -> list[float]:
        """Convert to flat list for model input, replacing None with 0."""
        return [
            self.momentum_15s,
            self.momentum_60s,
            self.momentum_180s,
            self.momentum_600s,
            self.realized_vol_5min,
            self.rsi_14,
            self.vwap_deviation,
            self.order_flow_imbalance,
            self.spread,
            self.spread_ratio,
            self.time_to_expiry_normalized,
            self.funding_rate or 0.0,
            self.funding_rate_z_score or 0.0,
            self.open_interest_change or 0.0,
            self.long_short_ratio or 0.0,
            float(self.kalshi_volume),
            self.implied_probability,
        ]

    @staticmethod
    def feature_names() -> list[str]:
        """Return ordered feature names matching to_array() output."""
        return [
            "momentum_15s",
            "momentum_60s",
            "momentum_180s",
            "momentum_600s",
            "realized_vol_5min",
            "rsi_14",
            "vwap_deviation",
            "order_flow_imbalance",
            "spread",
            "spread_ratio",
            "time_to_expiry_normalized",
            "funding_rate",
            "funding_rate_z_score",
            "open_interest_change",
            "long_short_ratio",
            "kalshi_volume",
            "implied_probability",
        ]


class PredictionResult(BaseModel):
    """Output of the probability model."""

    probability_yes: float
    confidence: float
    model_name: str
    features_used: dict[str, float] = Field(default_factory=dict)
