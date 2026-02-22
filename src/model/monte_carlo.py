"""Monte Carlo price path simulation for probability estimation.

Supports bootstrap resampling from observed returns (preferred) with
GBM parametric fallback when insufficient price history is available.
"""

from __future__ import annotations

import math
from decimal import Decimal

import numpy as np
import structlog

from src.data.models import FeatureVector, MarketSnapshot

logger = structlog.get_logger()


class MonteCarloSimulator:
    """Simulates BTC price paths to estimate settlement probability.

    Primary method: bootstrap resampling from observed log-returns in
    snapshot.btc_prices_5min. Falls back to parametric GBM when fewer
    than min_bootstrap_returns observations are available.
    """

    def __init__(
        self,
        n_samples: int = 10000,
        drift_mode: str = "momentum",
        vol_multiplier: float = 1.0,
        min_bootstrap_returns: int = 30,
    ):
        self._n_samples = n_samples
        self._drift_mode = drift_mode
        self._vol_multiplier = vol_multiplier
        self._min_bootstrap_returns = min_bootstrap_returns
        self._rng = np.random.default_rng()

    def estimate_probability(
        self,
        snapshot: MarketSnapshot,
        features: FeatureVector,
    ) -> tuple[float, float]:
        """Estimate P(YES) using Monte Carlo simulation.

        Uses bootstrap resampling when price history is available,
        otherwise falls back to parametric GBM.

        Args:
            snapshot: Current market data with btc_price, strike_price, time_to_expiry_seconds
            features: Feature vector with realized_vol_5min and momentum_180s

        Returns:
            (prob_yes, confidence) where prob_yes is in [0.05, 0.95]
            and confidence is in [0, 1].
        """
        spot = float(snapshot.btc_price)
        strike = float(snapshot.strike_price) if snapshot.strike_price is not None else spot
        ttx = snapshot.time_to_expiry_seconds

        # Try bootstrap resampling first
        log_returns = self._compute_log_returns(snapshot.btc_prices_5min)

        if log_returns is not None and len(log_returns) >= self._min_bootstrap_returns:
            prob_yes, confidence = self._bootstrap_estimate(
                spot, strike, ttx, log_returns, len(snapshot.btc_prices_5min),
            )
            method = "bootstrap"
        else:
            prob_yes, confidence = self._parametric_estimate(
                spot, strike, ttx, features,
            )
            method = "parametric_fallback"

        logger.debug(
            "mc_simulation_complete",
            method=method,
            spot=round(spot, 2),
            strike=round(strike, 2),
            ttx=round(ttx, 1),
            prob_yes=round(prob_yes, 4),
            confidence=round(confidence, 4),
            n_samples=self._n_samples,
            n_returns=len(log_returns) if log_returns is not None else 0,
        )

        return prob_yes, confidence

    def _compute_log_returns(self, prices: list[Decimal]) -> np.ndarray | None:
        """Compute log-returns from a price series.

        Returns None if fewer than 2 prices are available.
        """
        if len(prices) < 2:
            return None

        price_arr = np.array([float(p) for p in prices], dtype=np.float64)

        # Filter out zero/negative prices
        valid = price_arr > 0
        if np.sum(valid) < 2:
            return None

        price_arr = price_arr[valid]
        log_returns = np.log(price_arr[1:] / price_arr[:-1])

        # Filter out NaN/Inf
        finite_mask = np.isfinite(log_returns)
        log_returns = log_returns[finite_mask]

        if len(log_returns) == 0:
            return None

        return log_returns

    def _bootstrap_estimate(
        self,
        spot: float,
        strike: float,
        ttx: float,
        log_returns: np.ndarray,
        n_prices: int,
    ) -> tuple[float, float]:
        """Estimate probability via bootstrap resampling of observed returns.

        Samples returns with replacement, compounds from spot price,
        and counts paths ending above/below strike.
        """
        # Estimate average interval between price observations
        # btc_prices_5min covers ~300 seconds, so interval = 300 / (n_prices - 1)
        avg_interval = 300.0 / max(n_prices - 1, 1)
        n_steps = max(1, int(math.ceil(ttx / avg_interval)))

        # Apply vol_multiplier to sampled returns for risk adjustment
        scaled_returns = log_returns * self._vol_multiplier

        # Sample returns with replacement: (n_samples, n_steps)
        indices = self._rng.integers(0, len(scaled_returns), size=(self._n_samples, n_steps))
        sampled = scaled_returns[indices]

        # Compound: final_price = spot * exp(sum(sampled_returns))
        cumulative = np.sum(sampled, axis=1)
        final_prices = spot * np.exp(cumulative)

        # Count paths above strike
        n_above = np.sum(final_prices > strike)
        prob_yes = float(n_above) / self._n_samples

        # Clamp to [0.05, 0.95]
        prob_yes = max(0.05, min(0.95, prob_yes))

        # Confidence: bootstrap standard error of the proportion
        se = math.sqrt(prob_yes * (1.0 - prob_yes) / self._n_samples)
        confidence = max(0.0, 1.0 - 2.0 * se)

        return prob_yes, confidence

    def _parametric_estimate(
        self,
        spot: float,
        strike: float,
        ttx: float,
        features: FeatureVector,
    ) -> tuple[float, float]:
        """Estimate probability via parametric GBM (fallback).

        Used when insufficient price history is available for bootstrap.
        """
        # Annualized volatility from 5-min realized vol
        sigma = features.realized_vol_5min * self._vol_multiplier
        if sigma <= 0:
            sigma = 0.001  # Floor to avoid degenerate paths

        # Drift: momentum-based or zero
        if self._drift_mode == "momentum":
            mu = features.momentum_180s
        else:
            mu = 0.0

        # Time step: simulate in a single step (GBM terminal distribution)
        dt = max(ttx, 1.0)

        # Vectorized GBM: S(T) = S(0) * exp((mu - sigma^2/2)*dt + sigma*sqrt(dt)*Z)
        z = self._rng.standard_normal(self._n_samples)
        drift_term = (mu - 0.5 * sigma * sigma) * dt
        diffusion_term = sigma * math.sqrt(dt) * z
        final_prices = spot * np.exp(drift_term + diffusion_term)

        # Count paths above strike
        n_above = np.sum(final_prices > strike)
        prob_yes = float(n_above) / self._n_samples

        # Clamp to [0.05, 0.95]
        prob_yes = max(0.05, min(0.95, prob_yes))

        # Confidence
        se = math.sqrt(prob_yes * (1.0 - prob_yes) / self._n_samples)
        confidence = max(0.0, 1.0 - 2.0 * se)

        return prob_yes, confidence
