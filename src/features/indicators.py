"""Pure functions for technical indicator computation."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd


def momentum(prices: np.ndarray, window: int) -> float:
    """Price return over a window.

    Returns (price[-1] - price[-window]) / price[-window].
    Returns 0.0 if insufficient data.
    """
    if len(prices) < window or window < 1:
        return 0.0
    start_price = prices[-window]
    if start_price == 0:
        return 0.0
    return float((prices[-1] - start_price) / start_price)


def volatility_realized(prices: np.ndarray, window: int | None = None) -> float:
    """Realized volatility from log returns over a window.

    Returns the standard deviation of log returns. Not annualized —
    the caller can annualize if needed.
    """
    if len(prices) < 2:
        return 0.0

    data = prices[-window:] if window and window < len(prices) else prices
    if len(data) < 2:
        return 0.0

    # Log returns
    log_returns = np.diff(np.log(data.astype(float)))
    if len(log_returns) == 0:
        return 0.0

    return float(np.std(log_returns))


def rsi(prices: np.ndarray, period: int = 14) -> float:
    """Relative Strength Index.

    Returns value between 0 and 100.
    50 = neutral, >70 = overbought, <30 = oversold.
    """
    if len(prices) < period + 1:
        return 50.0  # Neutral when insufficient data

    deltas = np.diff(prices[-period - 1:].astype(float))
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def vwap(prices: np.ndarray, volumes: np.ndarray) -> float:
    """Volume-weighted average price.

    Returns 0.0 if no volume data.
    """
    if len(prices) == 0 or len(volumes) == 0:
        return 0.0

    min_len = min(len(prices), len(volumes))
    p = prices[-min_len:].astype(float)
    v = volumes[-min_len:].astype(float)

    total_volume = np.sum(v)
    if total_volume == 0:
        return float(np.mean(p))

    return float(np.sum(p * v) / total_volume)


def vwap_deviation(current_price: float, vwap_value: float) -> float:
    """Deviation of current price from VWAP as a fraction.

    Positive = above VWAP (bullish), negative = below (bearish).
    """
    if vwap_value == 0:
        return 0.0
    return (current_price - vwap_value) / vwap_value


def order_flow_imbalance(
    yes_bid_volume: int | float,
    no_bid_volume: int | float,
) -> float:
    """Orderbook imbalance between YES and NO bid volumes.

    Returns value in [-1, 1]:
    +1 = all volume on YES side (bullish pressure)
    -1 = all volume on NO side (bearish pressure)
    0 = balanced
    """
    total = yes_bid_volume + no_bid_volume
    if total == 0:
        return 0.0
    return float((yes_bid_volume - no_bid_volume) / total)


def spread_ratio(spread: float, implied_prob: float) -> float:
    """Spread as a fraction of implied probability — liquidity signal.

    Higher = less liquid, less reliable prices.
    """
    if implied_prob <= 0 or implied_prob >= 1:
        return 0.0
    max_possible_spread = min(implied_prob, 1 - implied_prob) * 2
    if max_possible_spread == 0:
        return 0.0
    return min(spread / max_possible_spread, 1.0)


def time_decay_factor(
    seconds_to_expiry: float, total_window: float = 900.0
) -> float:
    """Normalized time remaining in [0, 1].

    1.0 = full window remaining
    0.0 = at expiry
    Values > 1 clamped (market opened longer than total_window ago).
    """
    if total_window <= 0:
        return 0.0
    return max(0.0, min(1.0, seconds_to_expiry / total_window))


def mean_reversion_z_score(prices: np.ndarray, window: int = 60) -> float:
    """Z-score of current price vs rolling mean — mean reversion signal.

    Positive = above mean (potential sell), negative = below (potential buy).
    """
    if len(prices) < window:
        return 0.0
    data = prices[-window:].astype(float)
    mean = np.mean(data)
    std = np.std(data)
    if std == 0:
        return 0.0
    return float((prices[-1] - mean) / std)


def momentum_divergence(
    short_momentum: float, long_momentum: float
) -> float:
    """Divergence between short-term and long-term momentum.

    Positive = short-term outperforming (momentum accelerating).
    Negative = short-term lagging (momentum decelerating / reversal).
    """
    return short_momentum - long_momentum


def bollinger_band_position(
    prices: np.ndarray, window: int = 20, num_std: float = 2.0
) -> float:
    """Position within Bollinger Bands as [-1, 1].

    -1 = at lower band, 0 = at middle (SMA), +1 = at upper band.
    Combines volatility context with mean-reversion zone info.
    """
    if len(prices) < window:
        return 0.0

    data = prices[-window:].astype(float)
    sma = float(np.mean(data))
    std = float(np.std(data))

    if std == 0:
        return 0.0

    band_width = num_std * std
    position = (float(prices[-1]) - sma) / band_width
    return float(np.clip(position, -1.0, 1.0))


def macd_signal(
    prices: np.ndarray,
    fast: int = 60,
    slow: int = 130,
    signal_period: int = 45,
) -> tuple[float, float, float]:
    """MACD indicator returning (macd_line, signal_line, histogram).

    Default periods are scaled for tick data (fast=60, slow=130, signal=45).
    Provides trend confirmation less noisy than raw momentum.
    """
    if len(prices) < slow + signal_period:
        return (0.0, 0.0, 0.0)

    data = prices.astype(float)

    # EMA helper — delegates to C-level pandas internals
    def _ema(arr: np.ndarray, span: int) -> np.ndarray:
        return pd.Series(arr).ewm(span=span, adjust=False).mean().values

    fast_ema = _ema(data, fast)
    slow_ema = _ema(data, slow)
    macd_line = fast_ema - slow_ema

    signal_line = _ema(macd_line, signal_period)
    histogram = macd_line - signal_line

    return (float(macd_line[-1]), float(signal_line[-1]), float(histogram[-1]))


def rate_of_change_acceleration(prices: np.ndarray, window: int = 30) -> float:
    """2nd derivative of price: is momentum increasing or decreasing?

    Computes (ROC_now - ROC_prev) / window where ROC = price change over window.
    Catches inflection points before raw momentum does.
    """
    if len(prices) < 2 * window + 1:
        return 0.0

    data = prices.astype(float)
    current = data[-1]
    mid = data[-window - 1]
    prev = data[-2 * window - 1]

    if mid == 0 or prev == 0:
        return 0.0

    roc_now = (current - mid) / mid
    roc_prev = (mid - prev) / prev

    return (roc_now - roc_prev) / window


def volume_weighted_momentum(
    prices: np.ndarray, volumes: np.ndarray, window: int = 60
) -> float:
    """Momentum weighted by trade volume.

    Big-volume moves produce a stronger signal; low-volume drift is weaker.
    Returns a volume-weighted sum of per-tick returns.
    """
    if len(prices) < 2 or len(volumes) < 2:
        return 0.0

    min_len = min(len(prices), len(volumes))
    p = prices[-min_len:].astype(float)
    v = volumes[-min_len:].astype(float)

    # Use the last `window` ticks
    if len(p) > window:
        p = p[-window:]
        v = v[-window:]

    if len(p) < 2:
        return 0.0

    returns = np.diff(p) / p[:-1]
    vol_weights = v[1:]  # align volumes with returns

    total_vol = np.sum(vol_weights)
    if total_vol == 0:
        return 0.0

    return float(np.sum(returns * vol_weights) / total_vol)


def orderbook_depth_imbalance(
    yes_levels: list, no_levels: list, max_depth: int = 5
) -> float:
    """Weight-decayed imbalance across multiple orderbook levels.

    Top of book weighted 3x, 2nd level 2x, deeper levels 1x.
    Returns value in [-1, 1].
    """
    # Weight schedule: level 0 -> 3x, level 1 -> 2x, level 2+ -> 1x
    def _weighted_depth(levels: list, depth: int) -> float:
        total = 0.0
        for i, level in enumerate(levels[:depth]):
            qty = level.quantity if hasattr(level, "quantity") else 0
            if i == 0:
                weight = 3.0
            elif i == 1:
                weight = 2.0
            else:
                weight = 1.0
            total += qty * weight
        return total

    yes_depth = _weighted_depth(yes_levels, max_depth)
    no_depth = _weighted_depth(no_levels, max_depth)

    total = yes_depth + no_depth
    if total == 0:
        return 0.0

    return float((yes_depth - no_depth) / total)
