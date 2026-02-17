"""Feature engine: transforms market snapshots into feature vectors."""

from __future__ import annotations

from decimal import Decimal

import math
from typing import Any

import numpy as np

from src.config import FeatureConfig
from src.data.models import FeatureVector, MarketSnapshot
from src.features.indicators import (
    bollinger_band_position,
    macd_signal,
    momentum,
    order_flow_imbalance,
    orderbook_depth_imbalance,
    rate_of_change_acceleration,
    rsi,
    spread_ratio,
    time_decay_factor,
    volatility_realized,
    volume_weighted_momentum,
    vwap,
    vwap_deviation,
)


class FeatureEngine:
    """Computes feature vectors from market data snapshots.

    Transforms raw data (prices, orderbook, funding rates) into
    normalized features suitable for model input.
    """

    def __init__(
        self,
        config: FeatureConfig,
        settlement_history: dict[str, list[dict[str, Any]]] | None = None,
    ):
        self._config = config
        self._momentum_windows = config.momentum_windows  # [15, 60, 180, 600]
        self._vol_window = config.volatility_window  # 300 seconds
        self._settlement_history: dict[str, list[dict[str, Any]]] = (
            settlement_history if settlement_history is not None else {}
        )

    def compute(self, snapshot: MarketSnapshot) -> FeatureVector:
        """Compute all features from a market snapshot."""
        # Convert price lists to numpy arrays
        prices_5min = self._to_price_array(snapshot.btc_prices_5min)
        prices_1min = self._to_price_array(snapshot.btc_prices_1min)
        volumes_1min = self._to_volume_array(snapshot.btc_volumes_1min)

        # Use the longer history for most calculations
        prices = prices_5min if len(prices_5min) > len(prices_1min) else prices_1min

        # Momentum at multiple timeframes
        # Each window is in seconds; we approximate by using tick count
        # since ticks arrive roughly every ~100-500ms from Binance
        mom_15s = self._compute_momentum(prices, self._momentum_windows[0])
        mom_60s = self._compute_momentum(prices, self._momentum_windows[1])
        mom_180s = self._compute_momentum(prices, self._momentum_windows[2])
        mom_600s = self._compute_momentum(prices, self._momentum_windows[3])

        # Realized volatility
        vol_5min = volatility_realized(prices, self._vol_window)

        # RSI
        rsi_val = rsi(prices, period=min(14, max(2, len(prices) - 1)))

        # VWAP and deviation
        vwap_val = vwap(prices_1min, volumes_1min) if len(volumes_1min) > 0 else 0.0
        vwap_dev = (
            vwap_deviation(float(snapshot.btc_price), vwap_val)
            if vwap_val > 0
            else 0.0
        )

        # Orderbook features
        ob = snapshot.orderbook
        ofi = order_flow_imbalance(ob.yes_bid_depth, ob.no_bid_depth)

        spread_val = float(ob.spread) if ob.spread is not None else 0.0
        implied_prob = (
            float(ob.implied_yes_prob) if ob.implied_yes_prob is not None else 0.5
        )
        sr = spread_ratio(spread_val, implied_prob)

        # Time to expiry
        time_norm = time_decay_factor(snapshot.time_to_expiry_seconds)

        # New technical indicators
        bb_pos = bollinger_band_position(prices, window=20)

        _, _, macd_hist = macd_signal(prices, fast=60, slow=130, signal_period=45)

        roc_accel = rate_of_change_acceleration(prices, window=30)

        vol_mom = volume_weighted_momentum(prices_1min, volumes_1min, window=60)

        ob_depth = orderbook_depth_imbalance(
            ob.yes_levels, ob.no_levels, max_depth=5
        )

        # Settlement bias from recent Kalshi outcomes
        asset_symbol = self._extract_asset_symbol(snapshot.market_ticker)
        settle_bias = self._compute_settlement_bias(asset_symbol)

        return FeatureVector(
            timestamp=snapshot.timestamp,
            market_ticker=snapshot.market_ticker,
            momentum_15s=mom_15s,
            momentum_60s=mom_60s,
            momentum_180s=mom_180s,
            momentum_600s=mom_600s,
            realized_vol_5min=vol_5min,
            rsi_14=rsi_val,
            vwap_deviation=vwap_dev,
            order_flow_imbalance=ofi,
            spread=spread_val,
            spread_ratio=sr,
            time_to_expiry_normalized=time_norm,
            kalshi_volume=snapshot.volume,
            implied_probability=implied_prob,
            bollinger_position=bb_pos,
            macd_histogram=macd_hist,
            roc_acceleration=roc_accel,
            volume_weighted_momentum=vol_mom,
            orderbook_depth_imbalance=ob_depth,
            cross_exchange_spread=snapshot.cross_exchange_spread or 0.0,
            cross_exchange_lead=snapshot.cross_exchange_lead or 0.0,
            taker_buy_sell_ratio=self._compute_taker_ratio(snapshot),
            settlement_bias=settle_bias,
            chainlink_divergence=snapshot.chainlink_divergence or 0.0,
            chainlink_confirmation=1.0 if snapshot.chainlink_round_updated else 0.0,
            time_elapsed_seconds=snapshot.time_elapsed_seconds,
            window_phase=snapshot.window_phase,
        )

    @staticmethod
    def _compute_taker_ratio(snapshot: MarketSnapshot) -> float:
        """Compute net taker buy/sell ratio.

        Positive = more taker buying = bullish aggression.
        Negative = more taker selling = bearish aggression.
        Returns [-1, 1].
        """
        buy = snapshot.taker_buy_volume or 0.0
        sell = snapshot.taker_sell_volume or 0.0
        total = buy + sell
        if total <= 0:
            return 0.0
        return (buy - sell) / total

    def _compute_momentum(self, prices: np.ndarray, window_seconds: int) -> float:
        """Compute momentum using approximate tick count for window.

        Coinbase sends ~1-3 trades per second for BTC-USD,
        so we estimate tick count from seconds.
        """
        if len(prices) < 2:
            return 0.0
        # Use approximately 2 ticks per second as estimate (realistic for Coinbase)
        estimated_ticks = max(1, window_seconds * 2)
        # Fallback: when array is shorter than estimated ticks, use full array
        window = min(estimated_ticks, len(prices))
        return momentum(prices, window)

    def _compute_settlement_bias(self, asset_symbol: str) -> float:
        """Compute directional bias from recent settlement outcomes.

        Reads the shared settlement_history dict (populated by the health check loop).
        Uses exponential decay weighting so most recent settlements matter more.
        Returns float in [-1, 1]: positive = recent YES bias.
        """
        settlements = self._settlement_history.get(asset_symbol, [])
        if not settlements:
            return 0.0

        # Exponential decay: most recent settlement gets weight 1.0,
        # each older one decays by factor 0.7
        decay = 0.7
        weighted_yes = 0.0
        total_weight = 0.0
        for i, market in enumerate(settlements):
            result = market.get("result", "").lower()
            if result not in ("yes", "no"):
                continue
            weight = decay ** i
            weighted_yes += weight * (1.0 if result == "yes" else 0.0)
            total_weight += weight

        if total_weight == 0:
            return 0.0

        # Map [0, 1] → [-1, 1]
        return (weighted_yes / total_weight) * 2.0 - 1.0

    @staticmethod
    def _extract_asset_symbol(market_ticker: str) -> str:
        """Extract asset symbol from market ticker (e.g. 'KXBTC15M-...' → 'BTC')."""
        # Strip 'KX' prefix, then take letters before digits
        ticker = market_ticker
        if ticker.startswith("KX"):
            ticker = ticker[2:]
        symbol = ""
        for ch in ticker:
            if ch.isalpha():
                symbol += ch
            else:
                break
        return symbol or "BTC"

    @staticmethod
    def _to_price_array(prices: list[Decimal]) -> np.ndarray:
        """Convert list of Decimal prices to numpy float array."""
        if not prices:
            return np.array([], dtype=np.float64)
        return np.array([float(p) for p in prices], dtype=np.float64)

    @staticmethod
    def _to_volume_array(volumes: list[Decimal]) -> np.ndarray:
        """Convert list of Decimal volumes to numpy float array."""
        if not volumes:
            return np.array([], dtype=np.float64)
        return np.array([float(v) for v in volumes], dtype=np.float64)
