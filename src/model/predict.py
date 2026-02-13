"""Probability models for predicting BTC 15-minute price movement."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

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

    # Signal weights — rebalanced for trend awareness + technical depth
    MOMENTUM_WEIGHT = 0.12
    TECHNICAL_WEIGHT = 0.10  # BB, MACD, ROC, vol-mom composite
    ORDERFLOW_WEIGHT = 0.15
    MEAN_REVERSION_WEIGHT = 0.13
    FUNDING_WEIGHT = 0.06
    TIME_DECAY_WEIGHT = 0.14
    CROSS_EXCHANGE_WEIGHT = 0.10  # Binance lead-lag signal
    LIQUIDATION_WEIGHT = 0.10  # Liquidation cascade + taker flow
    TAKER_FLOW_WEIGHT = 0.10  # Net aggressive buying/selling

    # Maximum adjustment from 0.50 base
    MAX_ADJUSTMENT = 0.20

    # Dead zone: suppress marginal signals near 0.50
    DEAD_ZONE = 0.04

    # EMA smoothing alpha (0 = fully smooth, 1 = no smoothing)
    EMA_ALPHA = 0.3

    def __init__(self, weight_multipliers: dict[str, float] | None = None) -> None:
        self._prev_probability: float | None = None
        self._weight_multipliers: dict[str, float] = weight_multipliers or {}

    def set_weight_multipliers(self, multipliers: dict[str, float]) -> None:
        """Update session-based weight multipliers at runtime."""
        self._weight_multipliers = multipliers

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

        # --- 2. Technical composite signal ---
        # Combine Bollinger, MACD histogram, ROC acceleration, volume-weighted
        # momentum into a single normalized signal
        bb_signal = features.bollinger_position  # already [-1, 1]
        macd_sig = float(np.clip(features.macd_histogram * 100, -1.0, 1.0))
        roc_sig = float(np.clip(features.roc_acceleration * 1000, -1.0, 1.0))
        vwm_sig = float(np.clip(features.volume_weighted_momentum * 100, -1.0, 1.0))
        tech_signal = (bb_signal + macd_sig + roc_sig + vwm_sig) / 4.0

        # --- 3. Order flow signal ---
        # Positive imbalance = more YES bids = bullish pressure
        flow_signal = features.order_flow_imbalance * 0.5  # Scale to [-0.5, 0.5]

        # --- 4. Mean reversion component ---
        # RSI-based: overbought/oversold with graduated response
        rsi_norm = (features.rsi_14 - 50) / 50  # [-1, 1]
        mr_signal = 0.0
        if abs(rsi_norm) > 0.25:
            mr_signal = -rsi_norm * 0.3  # Moderate contrarian
        if abs(rsi_norm) > 0.5:
            mr_signal += -rsi_norm * 0.2  # Additional push at extremes

        # --- 5. Funding rate signal ---
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

        # --- 6. Cross-exchange lead-lag signal ---
        # Binance typically leads Coinbase by 100-500ms.
        # If Binance is moving up faster than Coinbase, price will follow.
        cross_exchange_signal = 0.0
        if features.cross_exchange_lead != 0:
            # Scale: typical lead is 0.0001 to 0.001 (1-10 bps over 15s)
            cross_exchange_signal = math.tanh(features.cross_exchange_lead / 0.0003)
        # Also factor in persistent spread (premium/discount)
        if features.cross_exchange_spread != 0:
            # If Binance trades at a premium, that's bullish for BTC
            spread_signal = math.tanh(features.cross_exchange_spread / 0.0005)
            cross_exchange_signal = 0.7 * cross_exchange_signal + 0.3 * spread_signal

        # --- 7. Liquidation cascade signal ---
        # Large short liquidations = shorts getting squeezed = bullish
        # Large long liquidations = longs getting liquidated = bearish
        liquidation_signal = 0.0
        if features.liquidation_intensity > 0.01:
            # Imbalance drives direction, intensity scales magnitude
            liquidation_signal = (
                features.liquidation_imbalance * features.liquidation_intensity
            )
            # Clamp to [-1, 1]
            liquidation_signal = max(-1.0, min(1.0, liquidation_signal))

        # --- 8. Taker flow signal ---
        # Net aggressive buying (taker buys > sells) is bullish
        taker_signal = features.taker_buy_sell_ratio  # Already [-1, 1]

        # --- 9. Time decay signal ---
        # Reduced weight: dampen signals near expiry but don't negate them
        time_decay_signal = 0.0
        if features.time_to_expiry_normalized < 0.3:
            decay_strength = 1.0 - (features.time_to_expiry_normalized / 0.3)
            # Mild dampening rather than full counteraction
            other_adjustment = (
                self.MOMENTUM_WEIGHT * mom_signal
                + self.ORDERFLOW_WEIGHT * flow_signal
                + self.MEAN_REVERSION_WEIGHT * mr_signal
            )
            time_decay_signal = -other_adjustment * decay_strength * 0.5

        # --- Apply session weight multipliers ---
        m = self._weight_multipliers
        mom_w = self.MOMENTUM_WEIGHT * m.get("momentum", 1.0)
        tech_w = self.TECHNICAL_WEIGHT * m.get("technical", 1.0)
        flow_w = self.ORDERFLOW_WEIGHT * m.get("orderflow", 1.0)
        mr_w = self.MEAN_REVERSION_WEIGHT * m.get("mean_reversion", 1.0)
        fund_w = self.FUNDING_WEIGHT * m.get("funding", 1.0)
        td_w = self.TIME_DECAY_WEIGHT * m.get("time_decay", 1.0)
        cx_w = self.CROSS_EXCHANGE_WEIGHT * m.get("cross_exchange", 1.0)
        liq_w = self.LIQUIDATION_WEIGHT * m.get("liquidation", 1.0)
        tk_w = self.TAKER_FLOW_WEIGHT * m.get("taker_flow", 1.0)

        # Re-normalize so weights sum to the original total
        original_total = (
            self.MOMENTUM_WEIGHT
            + self.TECHNICAL_WEIGHT
            + self.ORDERFLOW_WEIGHT
            + self.MEAN_REVERSION_WEIGHT
            + self.FUNDING_WEIGHT
            + self.TIME_DECAY_WEIGHT
            + self.CROSS_EXCHANGE_WEIGHT
            + self.LIQUIDATION_WEIGHT
            + self.TAKER_FLOW_WEIGHT
        )
        adjusted_total = mom_w + tech_w + flow_w + mr_w + fund_w + td_w + cx_w + liq_w + tk_w
        if adjusted_total > 0:
            scale = original_total / adjusted_total
            mom_w *= scale
            tech_w *= scale
            flow_w *= scale
            mr_w *= scale
            fund_w *= scale
            td_w *= scale
            cx_w *= scale
            liq_w *= scale
            tk_w *= scale

        # --- Combine signals ---
        raw_adjustment = (
            mom_w * mom_signal
            + tech_w * tech_signal
            + flow_w * flow_signal
            + mr_w * mr_signal
            + fund_w * funding_signal
            + cx_w * cross_exchange_signal
            + liq_w * liquidation_signal
            + tk_w * taker_signal
            + td_w * time_decay_signal
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
            "tech_signal": round(tech_signal, 4),
            "flow_signal": round(flow_signal, 4),
            "funding_signal": round(funding_signal, 4),
            "mr_signal": round(mr_signal, 4),
            "cross_exchange_signal": round(cross_exchange_signal, 4),
            "liquidation_signal": round(liquidation_signal, 4),
            "taker_signal": round(taker_signal, 4),
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
        - Orderbook depth agrees with signal direction
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

        # Time decay confidence penalty: reduce confidence near expiry
        # instead of negating probability in signal combination
        if features.time_to_expiry_normalized < 0.3:
            time_norm = features.time_to_expiry_normalized
            conf -= (1.0 - time_norm / 0.3) * 0.25

        # Volume bonus: higher volume = more reliable orderbook
        if features.kalshi_volume > 100:
            conf += 0.05
        elif features.kalshi_volume < 10:
            conf -= 0.10

        # Orderbook depth imbalance: strong depth agreeing with signal
        # direction boosts confidence, opposing depth reduces it
        depth_imb = features.orderbook_depth_imbalance
        if abs(depth_imb) > 0.3:
            # Determine if depth agrees with overall signal direction
            # (positive imbalance = YES-side depth dominance)
            # Use order flow as proxy for signal direction
            signal_dir = features.order_flow_imbalance
            if depth_imb * signal_dir > 0:
                conf += 0.05  # Depth agrees with signal
            else:
                conf -= 0.05  # Depth opposes signal

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
