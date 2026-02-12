"""Volatility regime tracking and classification."""

from __future__ import annotations

from collections import deque
from typing import Literal

import numpy as np


class VolatilityTracker:
    """Tracks and classifies current BTC volatility regime.

    Maintains a rolling window of realized volatility observations
    and classifies the current regime for strategy adjustment.

    Regimes:
    - low: Favorable for mean reversion / market making
    - normal: Standard conditions, directional trades viable
    - high: Increase edge threshold, reduce position size
    - extreme: Consider sitting out entirely
    """

    HISTORY_SIZE = 2000  # ~500 minutes at 4s intervals

    def __init__(self):
        self._vol_history: deque[float] = deque(maxlen=self.HISTORY_SIZE)

    def update(self, realized_vol: float) -> None:
        """Add a new volatility observation."""
        self._vol_history.append(realized_vol)

    @property
    def current_vol(self) -> float | None:
        """Most recent volatility observation."""
        return self._vol_history[-1] if self._vol_history else None

    @property
    def current_regime(self) -> Literal["low", "normal", "high", "extreme"]:
        """Classify current volatility vs historical distribution."""
        pct = self.vol_percentile
        if pct is None:
            return "normal"

        if pct < 20:
            return "low"
        elif pct < 70:
            return "normal"
        elif pct < 90:
            return "high"
        else:
            return "extreme"

    @property
    def vol_percentile(self) -> float | None:
        """Current vol as percentile of recent history (0-100)."""
        if len(self._vol_history) < 10:
            return None

        current = self._vol_history[-1]
        arr = np.array(self._vol_history)
        return float(np.sum(arr <= current) / len(arr) * 100)

    def adjust_edge_threshold(self, base_threshold: float) -> float:
        """Adjust the minimum edge threshold based on volatility regime.

        Higher vol -> require more edge (more uncertainty in signals).
        Lower vol -> can trade smaller edges (signals more reliable).
        """
        regime = self.current_regime

        multipliers = {
            "low": 0.8,     # 20% less edge required
            "normal": 1.0,  # Standard threshold
            "high": 1.5,    # 50% more edge required
            "extreme": 2.5, # 150% more edge required
        }

        return base_threshold * multipliers[regime]

    def adjust_kelly_fraction(self, base_kelly: float) -> float:
        """Reduce Kelly fraction in high-volatility regimes."""
        regime = self.current_regime

        multipliers = {
            "low": 1.0,
            "normal": 1.0,
            "high": 0.5,
            "extreme": 0.25,
        }

        return base_kelly * multipliers[regime]

    @property
    def stats(self) -> dict:
        """Summary statistics for monitoring."""
        if len(self._vol_history) < 2:
            return {
                "regime": self.current_regime,
                "current_vol": self.current_vol,
                "percentile": None,
                "observations": len(self._vol_history),
            }

        arr = np.array(self._vol_history)
        return {
            "regime": self.current_regime,
            "current_vol": round(self.current_vol or 0, 6),
            "percentile": round(self.vol_percentile or 0, 1),
            "mean_vol": round(float(np.mean(arr)), 6),
            "median_vol": round(float(np.median(arr)), 6),
            "observations": len(self._vol_history),
        }
