"""Compute FeatureVector from 1-minute candle data.

Honest about data resolution: features that require sub-minute data
(momentum_15s) are set to 0.0. All other features are computed from
candle close/volume arrays using the same indicator functions as production.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import numpy as np

from src.data.models import FeatureVector, Orderbook, OrderbookLevel
from src.features import indicators


class BacktestFeatureEngine:
    """Compute features from 1-minute candle arrays for backtesting."""

    def compute(
        self,
        closes: np.ndarray,
        volumes: np.ndarray,
        taker_buy_volumes: np.ndarray,
        orderbook: Orderbook,
        time_to_expiry_seconds: float,
        market_ticker: str,
        timestamp: datetime,
    ) -> FeatureVector:
        """Compute a FeatureVector from candle data.

        Args:
            closes: Array of close prices (1-minute candles, most recent last)
            volumes: Array of total volumes per candle
            taker_buy_volumes: Array of taker buy volumes per candle
            orderbook: Synthetic orderbook for this window
            time_to_expiry_seconds: Seconds until settlement
            market_ticker: Market identifier
            timestamp: Current evaluation time
        """
        n = len(closes)

        # --- Momentum features ---
        # momentum_15s: can't compute from 1m candles (2.5% total weight)
        momentum_15s = 0.0
        # momentum_60s: 1 candle = 60s
        momentum_60s = _safe_return(closes, 1) if n >= 2 else 0.0
        # momentum_180s: 3 candles = 180s
        momentum_180s = _safe_return(closes, 3) if n >= 4 else 0.0
        # momentum_600s: 10 candles = 600s
        momentum_600s = _safe_return(closes, 10) if n >= 11 else 0.0

        # --- Volatility ---
        if n >= 6:
            log_rets = np.diff(np.log(closes[-6:]))
            realized_vol = float(np.std(log_rets)) if len(log_rets) > 0 else 0.0
        else:
            realized_vol = 0.0

        # --- RSI ---
        rsi_val = indicators.rsi(closes, 14) if n >= 16 else 50.0

        # --- Bollinger Band Position ---
        bb_pos = indicators.bollinger_band_position(closes, 20) if n >= 20 else 0.0

        # --- MACD ---
        # Use standard 1m params (12, 26, 9), not tick-scaled (60, 130, 45)
        if n >= 26 + 9:
            _, _, macd_hist = indicators.macd_signal(closes, fast=12, slow=26, signal_period=9)
        else:
            macd_hist = 0.0

        # --- Rate of Change Acceleration ---
        roc_accel = indicators.rate_of_change_acceleration(closes, 5) if n >= 11 else 0.0

        # --- Volume-Weighted Momentum ---
        if n >= 2 and len(volumes) >= 2:
            vwm = indicators.volume_weighted_momentum(closes, volumes, 10)
        else:
            vwm = 0.0

        # --- Taker Buy/Sell Ratio ---
        if len(volumes) > 0 and len(taker_buy_volumes) > 0:
            recent_vol = volumes[-1]
            recent_taker_buy = taker_buy_volumes[-1]
            if recent_vol > 0:
                taker_sell = recent_vol - recent_taker_buy
                taker_ratio = float((recent_taker_buy - taker_sell) / recent_vol)
            else:
                taker_ratio = 0.0
        else:
            taker_ratio = 0.0

        # --- Orderbook features ---
        ob = orderbook
        flow_imbalance = indicators.order_flow_imbalance(
            ob.yes_bid_depth, ob.no_bid_depth
        )
        depth_imbalance = indicators.orderbook_depth_imbalance(
            ob.yes_levels, ob.no_levels, max_depth=5
        )

        implied_prob = float(ob.implied_yes_prob) if ob.implied_yes_prob is not None else 0.5
        spread_val = float(ob.spread) if ob.spread is not None else 0.0
        spread_r = indicators.spread_ratio(spread_val, implied_prob)

        # --- VWAP deviation ---
        if n >= 2 and len(volumes) >= 2:
            vwap_val = indicators.vwap(closes, volumes)
            vwap_dev = indicators.vwap_deviation(float(closes[-1]), vwap_val)
        else:
            vwap_dev = 0.0

        # --- Time features ---
        time_norm = indicators.time_decay_factor(time_to_expiry_seconds, 900.0)

        return FeatureVector(
            timestamp=timestamp,
            market_ticker=market_ticker,
            momentum_15s=momentum_15s,
            momentum_60s=momentum_60s,
            momentum_180s=momentum_180s,
            momentum_600s=momentum_600s,
            realized_vol_5min=realized_vol,
            rsi_14=rsi_val,
            vwap_deviation=vwap_dev,
            order_flow_imbalance=flow_imbalance,
            spread=spread_val,
            spread_ratio=spread_r,
            time_to_expiry_normalized=time_norm,
            funding_rate=None,
            funding_rate_z_score=None,
            open_interest_change=None,
            long_short_ratio=None,
            kalshi_volume=100,  # Synthetic
            implied_probability=implied_prob,
            bollinger_position=bb_pos,
            macd_histogram=macd_hist,
            roc_acceleration=roc_accel,
            volume_weighted_momentum=vwm,
            orderbook_depth_imbalance=depth_imbalance,
            cross_exchange_spread=0.0,
            cross_exchange_lead=0.0,
            liquidation_intensity=0.0,
            liquidation_imbalance=0.0,
            taker_buy_sell_ratio=taker_ratio,
        )


def build_synthetic_orderbook(
    fair_value: float,
    spread: float = 0.04,
    depth: int = 100,
    ticker: str = "backtest",
    timestamp: datetime | None = None,
) -> Orderbook:
    """Build a synthetic orderbook centered on fair_value.

    YES bid = fair_value - spread/2
    NO bid = (1 - fair_value) - spread/2  (i.e. YES ask = fair_value + spread/2)
    """
    from datetime import timezone

    ts = timestamp or datetime.now(timezone.utc)

    yes_bid = max(0.01, min(0.99, fair_value - spread / 2))
    no_bid = max(0.01, min(0.99, (1.0 - fair_value) - spread / 2))

    return Orderbook(
        ticker=ticker,
        yes_levels=[
            OrderbookLevel(
                price_dollars=Decimal(f"{yes_bid:.2f}"),
                quantity=depth,
            ),
        ],
        no_levels=[
            OrderbookLevel(
                price_dollars=Decimal(f"{no_bid:.2f}"),
                quantity=depth,
            ),
        ],
        timestamp=ts,
    )


def _safe_return(prices: np.ndarray, lookback: int) -> float:
    """Compute price return over lookback candles, avoiding division by zero."""
    if len(prices) < lookback + 1:
        return 0.0
    start = prices[-(lookback + 1)]
    if start == 0:
        return 0.0
    return float((prices[-1] - start) / start)
