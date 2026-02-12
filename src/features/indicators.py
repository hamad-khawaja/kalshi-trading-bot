"""Pure functions for technical indicator computation."""

from __future__ import annotations

from decimal import Decimal

import numpy as np


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


def funding_rate_z_score(
    current_rate: float, historical_rates: np.ndarray
) -> float:
    """How extreme the current funding rate is vs recent history.

    Returns z-score. Positive = funding is higher than usual.
    """
    if len(historical_rates) < 2:
        return 0.0
    mean = float(np.mean(historical_rates))
    std = float(np.std(historical_rates))
    if std == 0:
        return 0.0
    return (current_rate - mean) / std


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
