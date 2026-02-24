"""Statistical fair value for binary options on BTC price."""

from __future__ import annotations

import math
import re
from decimal import Decimal

import numpy as np

STRIKE_PATTERN = re.compile(r"\$([0-9,]+\.?\d*)")


def parse_strike_price(yes_sub_title: str) -> Decimal | None:
    """Parse strike from market title like '$66,357.71 or above'.

    Returns the strike price as Decimal, or None if unparseable.
    """
    match = STRIKE_PATTERN.search(yes_sub_title)
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except Exception:
        return None


def _normal_cdf(x: float) -> float:
    """Standard normal CDF using math.erf (no scipy needed)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def compute_fair_value(
    spot_price: float,
    strike_price: float,
    realized_vol: float,
    time_to_expiry_seconds: float,
    n_price_ticks: int,
    price_window_seconds: float = 300.0,
) -> float | None:
    """Compute P(BTC > strike at expiry) using a log-normal model.

    This is essentially a digital option price under geometric Brownian motion.

    Args:
        spot_price: Current spot price.
        strike_price: Market strike price (the threshold).
        realized_vol: Std dev of per-tick log returns (from volatility_realized).
        time_to_expiry_seconds: Seconds until market settles.
        n_price_ticks: Number of price ticks in the vol estimation window.
        price_window_seconds: Duration of the vol estimation window (default 300s).

    Returns:
        Fair value probability P(BTC > strike), or None if inputs are invalid.
    """
    if spot_price <= 0 or strike_price <= 0 or time_to_expiry_seconds <= 0:
        return None
    if realized_vol <= 0 or n_price_ticks < 10:
        return None

    # Per-tick vol → per-second vol → scale to remaining time
    # tick_rate = ticks / seconds in the estimation window
    tick_rate = n_price_ticks / price_window_seconds
    if tick_rate <= 0:
        return None

    # Total standard deviation of log-price over the remaining time
    # σ_T = σ_per_tick * √(tick_rate * T)
    sigma_t = realized_vol * math.sqrt(tick_rate * time_to_expiry_seconds)

    if sigma_t <= 0:
        return None

    # d = ln(S/K) / σ_T
    # P(S_T > K) = Φ(d)  under log-normal assumption (zero drift for short horizons)
    d = math.log(spot_price / strike_price) / sigma_t

    fair_value = _normal_cdf(d)

    # Clamp to avoid extreme values
    return max(0.02, min(0.98, fair_value))


def compute_fair_value_from_prices(
    spot_price: float,
    strike_price: float,
    price_history: np.ndarray,
    time_to_expiry_seconds: float,
    price_window_seconds: float = 300.0,
) -> float | None:
    """Convenience wrapper that computes vol from raw price history.

    Args:
        spot_price: Current spot price.
        strike_price: Market strike price.
        price_history: Array of recent BTC prices (e.g. 5-min history).
        time_to_expiry_seconds: Seconds until settlement.
        price_window_seconds: Duration covered by price_history.

    Returns:
        Fair value probability, or None if insufficient data.
    """
    if len(price_history) < 20:
        return None

    log_returns = np.diff(np.log(price_history.astype(float)))
    if len(log_returns) == 0:
        return 0.5

    realized_vol = float(np.std(log_returns))

    return compute_fair_value(
        spot_price=spot_price,
        strike_price=strike_price,
        realized_vol=realized_vol,
        time_to_expiry_seconds=time_to_expiry_seconds,
        n_price_ticks=len(price_history),
        price_window_seconds=price_window_seconds,
    )
