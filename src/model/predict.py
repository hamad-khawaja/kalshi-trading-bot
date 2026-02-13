"""Probability models for predicting BTC 15-minute price movement."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from pathlib import Path

from src.data.models import FeatureVector, PredictionResult


class ProbabilityModel(ABC):
    """Abstract base for probability estimation models.

    All models must return calibrated probability estimates
    of P(BTC price goes UP in the 15-minute window).
    """

    @abstractmethod
    def predict(self, features: FeatureVector) -> PredictionResult:
        """Return estimated probability and confidence."""
        ...

    @abstractmethod
    def name(self) -> str:
        """Model identifier."""
        ...


class HeuristicModel(ProbabilityModel):
    """Rule-based probability estimator combining momentum, volatility, and order flow.

    This is the Phase 1 model — simple but interpretable. It combines
    multiple signal categories with fixed weights to estimate P(BTC up).

    The model adjusts a base probability of 0.50 based on:
    1. Multi-timeframe momentum consensus
    2. Orderbook flow imbalance
    3. Funding rate signal
    4. Volatility regime (affects confidence, not probability)
    5. Time to expiry (affects confidence)
    """

    # Signal weights — favor longer-term signals over short-term noise
    MOMENTUM_WEIGHT = 0.10
    ORDERFLOW_WEIGHT = 0.20
    FUNDING_WEIGHT = 0.0  # Coinglass returning 500s
    MEAN_REVERSION_WEIGHT = 0.30
    TIME_DECAY_WEIGHT = 0.40

    # Maximum adjustment from 0.50 base
    MAX_ADJUSTMENT = 0.20

    # Dead zone: suppress marginal signals near 0.50
    DEAD_ZONE = 0.04

    # EMA smoothing alpha (0 = fully smooth, 1 = no smoothing)
    EMA_ALPHA = 0.3

    def __init__(self) -> None:
        self._prev_probability: float | None = None

    def name(self) -> str:
        return "heuristic_v2"

    def predict(self, features: FeatureVector) -> PredictionResult:
        """Estimate P(BTC up) using rule-based signals."""
        # --- 1. Momentum signal ---
        # Favor longer timeframes to reduce noise from short-term jitter
        mom_signal = (
            0.1 * self._normalize_momentum(features.momentum_15s)
            + 0.2 * self._normalize_momentum(features.momentum_60s)
            + 0.3 * self._normalize_momentum(features.momentum_180s)
            + 0.4 * self._normalize_momentum(features.momentum_600s)
        )

        # Check momentum consistency (all timeframes agree = stronger signal)
        momentums = [
            features.momentum_15s,
            features.momentum_60s,
            features.momentum_180s,
            features.momentum_600s,
        ]
        nonzero = [m for m in momentums if m != 0]
        if nonzero:
            all_positive = all(m > 0 for m in nonzero)
            all_negative = all(m < 0 for m in nonzero)
            consistency = 1.0 if (all_positive or all_negative) else 0.5
        else:
            consistency = 0.5
        mom_signal *= consistency

        # --- 2. Order flow signal ---
        # Positive imbalance = more YES bids = bullish pressure
        flow_signal = features.order_flow_imbalance * 0.5  # Scale to [-0.5, 0.5]

        # --- 3. Funding rate signal ---
        funding_signal = 0.0
        if features.funding_rate is not None:
            # Positive funding = longs paying shorts = bullish sentiment
            # But extreme funding can signal reversal
            fr = features.funding_rate
            if abs(fr) < 0.001:
                funding_signal = fr * 100  # Scale small funding rates
            else:
                # Extreme funding: contrarian signal
                funding_signal = -math.copysign(0.3, fr)

        # --- 4. Mean reversion component ---
        # RSI-based: overbought/oversold
        rsi_norm = (features.rsi_14 - 50) / 50  # [-1, 1]
        # When RSI is extreme, bet on reversion
        if abs(rsi_norm) > 0.4:
            mr_signal = -rsi_norm * 0.5  # Contrarian
        else:
            mr_signal = 0.0  # Neutral zone, no signal

        # --- 5. Time decay signal ---
        # As market approaches expiry, pull toward implied market price
        # (reduces unnecessary trades near expiry)
        time_decay_signal = 0.0
        if features.time_to_expiry_normalized < 0.3:
            # Pull toward 0.0 adjustment (i.e., toward 0.50 base)
            decay_strength = 1.0 - (features.time_to_expiry_normalized / 0.3)
            # Counteract other signals proportionally
            other_adjustment = (
                self.MOMENTUM_WEIGHT * mom_signal
                + self.ORDERFLOW_WEIGHT * flow_signal
                + self.MEAN_REVERSION_WEIGHT * mr_signal
            )
            time_decay_signal = -other_adjustment * decay_strength

        # --- Combine signals ---
        raw_adjustment = (
            self.MOMENTUM_WEIGHT * mom_signal
            + self.ORDERFLOW_WEIGHT * flow_signal
            + self.FUNDING_WEIGHT * funding_signal
            + self.MEAN_REVERSION_WEIGHT * mr_signal
            + self.TIME_DECAY_WEIGHT * time_decay_signal
        )

        # Clamp adjustment
        adjustment = max(-self.MAX_ADJUSTMENT, min(self.MAX_ADJUSTMENT, raw_adjustment))
        probability = 0.50 + adjustment

        # Dead zone: suppress marginal signals near 0.50
        if abs(probability - 0.50) < self.DEAD_ZONE:
            probability = 0.50

        # EMA smoothing to reduce oscillation
        if self._prev_probability is not None:
            probability = (
                self.EMA_ALPHA * probability
                + (1 - self.EMA_ALPHA) * self._prev_probability
            )
        self._prev_probability = probability

        # Clamp to valid probability range
        probability = max(0.05, min(0.95, probability))

        # --- Confidence estimation ---
        confidence = self._compute_confidence(features, consistency)

        # Features used for explainability
        features_used = {
            "mom_signal": round(mom_signal, 4),
            "flow_signal": round(flow_signal, 4),
            "funding_signal": round(funding_signal, 4),
            "mr_signal": round(mr_signal, 4),
            "time_decay_signal": round(time_decay_signal, 4),
            "consistency": round(consistency, 4),
            "raw_adjustment": round(raw_adjustment, 4),
        }

        return PredictionResult(
            probability_yes=round(probability, 4),
            confidence=round(confidence, 4),
            model_name=self.name(),
            features_used=features_used,
        )

    @staticmethod
    def _normalize_momentum(mom: float) -> float:
        """Normalize momentum to roughly [-1, 1] using tanh-like scaling.

        BTC 15-second momentum is typically in [-0.005, 0.005] range.
        """
        # Scale factor: 0.005 movement -> ~0.5 signal (wider to reduce noise)
        return math.tanh(mom / 0.005)

    @staticmethod
    def _compute_confidence(features: FeatureVector, consistency: float) -> float:
        """Estimate confidence in the prediction.

        Higher confidence when:
        - Multiple signals agree (consistency)
        - Spread is tight (liquid market)
        - Volatility is moderate (not extreme)
        - Sufficient time to expiry
        """
        conf = 0.5  # Base confidence

        # Signal consistency bonus
        conf += 0.15 * consistency

        # Spread penalty: wide spreads = uncertain implied prob
        if features.spread > 0.10:
            conf -= 0.15
        elif features.spread < 0.03:
            conf += 0.10

        # Volatility: moderate is best, extreme reduces confidence
        vol = features.realized_vol_5min
        if vol > 0.005:
            conf -= 0.15  # High vol regime
        elif vol > 0.002:
            conf += 0.05  # Normal vol
        elif vol < 0.0005:
            conf -= 0.05  # Too quiet, no signal

        # Time to expiry: more time = more uncertainty but also more opportunity
        if features.time_to_expiry_normalized < 0.1:
            conf -= 0.10  # Too close to expiry
        elif features.time_to_expiry_normalized > 0.5:
            conf += 0.05

        # Volume bonus: higher volume = more reliable orderbook
        if features.kalshi_volume > 100:
            conf += 0.05
        elif features.kalshi_volume < 10:
            conf -= 0.10

        return max(0.0, min(1.0, conf))


class LightGBMModel(ProbabilityModel):
    """LightGBM-based probability model.

    Placeholder for future ML model. Will be trained on collected
    historical data from the bot's operation.
    """

    def __init__(self, model_path: str):
        self._model_path = model_path
        self._model = None
        self._load_model()

    def _load_model(self) -> None:
        """Load trained LightGBM model from disk."""
        if not Path(self._model_path).exists():
            return
        try:
            import lightgbm as lgb
            self._model = lgb.Booster(model_file=self._model_path)
        except ImportError:
            raise ImportError(
                "lightgbm is required for LightGBMModel. "
                "Install with: pip install kalshi-btc-bot[ml]"
            )

    def name(self) -> str:
        return "lightgbm_v1"

    def predict(self, features: FeatureVector) -> PredictionResult:
        """Predict using trained LightGBM model."""
        if self._model is None:
            raise RuntimeError(
                f"Model not loaded from {self._model_path}. "
                "Train a model first using src.model.train"
            )

        import numpy as np

        feature_array = np.array([features.to_array()])
        prob = float(self._model.predict(feature_array)[0])
        prob = max(0.01, min(0.99, prob))

        return PredictionResult(
            probability_yes=round(prob, 4),
            confidence=0.7,  # Could be estimated from prediction variance
            model_name=self.name(),
            features_used=dict(
                zip(features.feature_names(), features.to_array())
            ),
        )
