"""Monte Carlo GBM price path simulation for probability estimation."""

from __future__ import annotations

import math

import numpy as np
import structlog

from src.data.models import FeatureVector, MarketSnapshot

logger = structlog.get_logger()


class MonteCarloSimulator:
    """Simulates BTC price paths using Geometric Brownian Motion.

    Generates thousands of price paths from current price to settlement,
    counts how many end above/below the strike, and uses that fraction
    as its probability estimate.
    """

    def __init__(
        self,
        n_samples: int = 10000,
        drift_mode: str = "momentum",
        vol_multiplier: float = 1.0,
    ):
        self._n_samples = n_samples
        self._drift_mode = drift_mode
        self._vol_multiplier = vol_multiplier
        self._rng = np.random.default_rng()

    def estimate_probability(
        self,
        snapshot: MarketSnapshot,
        features: FeatureVector,
    ) -> tuple[float, float]:
        """Estimate P(YES) using Monte Carlo simulation.

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

        # Annualized volatility from 5-min realized vol
        # realized_vol_5min is already a fractional value (e.g. 0.001 = 0.1%)
        sigma = features.realized_vol_5min * self._vol_multiplier
        if sigma <= 0:
            sigma = 0.001  # Floor to avoid degenerate paths

        # Drift: momentum-based or zero
        if self._drift_mode == "momentum":
            mu = features.momentum_180s  # Per-second fractional drift
        else:
            mu = 0.0

        # Time step: simulate in a single step (GBM terminal distribution)
        # dt is in seconds — sigma and mu are per-second rates
        dt = max(ttx, 1.0)

        # Vectorized GBM: S(T) = S(0) * exp((mu - sigma^2/2)*dt + sigma*sqrt(dt)*Z)
        z = self._rng.standard_normal(self._n_samples)
        drift_term = (mu - 0.5 * sigma * sigma) * dt
        diffusion_term = sigma * math.sqrt(dt) * z
        final_prices = spot * np.exp(drift_term + diffusion_term)

        # Count paths ending above strike
        n_above = np.sum(final_prices > strike)
        prob_yes = float(n_above) / self._n_samples

        # Clamp to [0.05, 0.95]
        prob_yes = max(0.05, min(0.95, prob_yes))

        # Confidence: tighter when probability is extreme
        # confidence = max(0, 1 - 2*sqrt(p*(1-p)/n))
        se = math.sqrt(prob_yes * (1.0 - prob_yes) / self._n_samples)
        confidence = max(0.0, 1.0 - 2.0 * se)

        logger.debug(
            "mc_simulation_complete",
            spot=round(spot, 2),
            strike=round(strike, 2),
            ttx=round(ttx, 1),
            sigma=round(sigma, 6),
            mu=round(mu, 6),
            prob_yes=round(prob_yes, 4),
            confidence=round(confidence, 4),
            n_samples=self._n_samples,
        )

        return prob_yes, confidence
