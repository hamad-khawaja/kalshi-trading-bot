"""Probability calibration for model outputs."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class ProbabilityCalibrator:
    """Calibrates raw model probabilities to true probabilities.

    Uses isotonic regression or Platt scaling (sigmoid) to map
    model outputs to well-calibrated probability estimates.

    A well-calibrated model means: when it predicts 60%, the event
    should happen ~60% of the time.
    """

    def __init__(self, method: str = "isotonic"):
        self._method = method
        self._calibrator = None
        self._fitted = False

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def fit(self, predictions: np.ndarray, outcomes: np.ndarray) -> None:
        """Train calibrator on historical predictions vs actual outcomes.

        Args:
            predictions: Model probability outputs (0 to 1)
            outcomes: Actual outcomes (0 or 1)
        """
        try:
            from sklearn.isotonic import IsotonicRegression
            from sklearn.linear_model import LogisticRegression
        except ImportError:
            raise ImportError(
                "scikit-learn is required for calibration. "
                "Install with: pip install kalshi-btc-bot[ml]"
            )

        if len(predictions) < 20:
            raise ValueError(
                f"Need at least 20 samples for calibration, got {len(predictions)}"
            )

        if self._method == "isotonic":
            self._calibrator = IsotonicRegression(
                y_min=0.01, y_max=0.99, out_of_bounds="clip"
            )
            self._calibrator.fit(predictions, outcomes)
        elif self._method == "platt":
            # Platt scaling: fit logistic regression on model outputs
            self._calibrator = LogisticRegression(C=1.0)
            self._calibrator.fit(predictions.reshape(-1, 1), outcomes)
        else:
            raise ValueError(f"Unknown calibration method: {self._method}")

        self._fitted = True

    def calibrate(self, raw_probability: float) -> float:
        """Map raw model output to calibrated probability."""
        if not self._fitted or self._calibrator is None:
            return raw_probability

        if self._method == "isotonic":
            result = self._calibrator.predict([raw_probability])[0]
        elif self._method == "platt":
            result = self._calibrator.predict_proba([[raw_probability]])[0][1]
        else:
            result = raw_probability

        return float(np.clip(result, 0.01, 0.99))

    def save(self, path: str) -> None:
        """Save calibrator state to disk."""
        import pickle
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(
                {"method": self._method, "calibrator": self._calibrator},
                f,
            )

    def load(self, path: str) -> None:
        """Load calibrator state from disk."""
        import pickle
        if not Path(path).exists():
            return
        with open(path, "rb") as f:
            state = pickle.load(f)
        self._method = state["method"]
        self._calibrator = state["calibrator"]
        self._fitted = self._calibrator is not None
