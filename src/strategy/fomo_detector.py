"""FOMO exploitation: detects retail panic and generates contrarian signals.

Core thesis (empirical, n=29):
- BTC direction predicts 15-min binary resolution 96.6% of the time
- When retail panics and pushes prices to extremes, the OPPOSITE side
  becomes underpriced
- Fee advantage at extremes: ~0.2% at 20c vs 1.56% at 50c
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import structlog

from src.config import StrategyConfig
from src.data.models import FeatureVector, MarketSnapshot, PredictionResult, TradeSignal
from src.strategy.edge_detector import EdgeDetector

logger = structlog.get_logger()


class FomoDetector:
    """Detects FOMO-driven mispricing and generates contrarian trade signals.

    When BTC moves sharply and retail piles into the trending side,
    the opposite side becomes underpriced. This detector identifies
    the divergence between implied probability and model probability
    in the direction of BTC momentum, then signals to buy the cheap side.
    """

    def __init__(self, config: StrategyConfig):
        self._config = config
        self.last_analysis: dict = {}

    def detect(
        self,
        prediction: PredictionResult,
        features: FeatureVector,
        snapshot: MarketSnapshot,
    ) -> TradeSignal | None:
        """Detect FOMO overreaction and generate contrarian signal."""
        if not self._config.fomo_enabled:
            self.last_analysis = {"enabled": False}
            return None

        ob = snapshot.orderbook
        implied_prob = ob.implied_yes_prob
        if implied_prob is None:
            self.last_analysis = {"decision": "NO FOMO: no implied probability"}
            return None

        implied = float(implied_prob)
        model_prob = prediction.probability_yes

        # Step 1: Analyze BTC momentum
        momentum_direction, momentum_magnitude, momentum_consistent = (
            self._analyze_momentum(features)
        )

        if momentum_direction == 0:
            self.last_analysis = {"decision": "NO FOMO: no clear momentum direction"}
            return None

        if self._config.fomo_momentum_consistency_required and not momentum_consistent:
            self.last_analysis = {
                "decision": "NO FOMO: momentum not consistent across timeframes",
            }
            return None

        if momentum_magnitude < self._config.fomo_momentum_min_magnitude:
            self.last_analysis = {
                "decision": (
                    f"NO FOMO: momentum magnitude {momentum_magnitude:.5f} "
                    f"< threshold {self._config.fomo_momentum_min_magnitude:.5f}"
                ),
            }
            return None

        # Step 2: Check for FOMO divergence
        # BTC UP -> retail buys YES aggressively -> implied YES > model -> buy NO
        # BTC DOWN -> retail buys NO aggressively -> implied YES < model -> buy YES
        if momentum_direction > 0:
            divergence = implied - model_prob
            underpriced_side = "no"
            trade_price = 1.0 - implied
        else:
            divergence = model_prob - implied
            underpriced_side = "yes"
            trade_price = implied

        # FOMO-specific min entry price (lower than global since FOMO targets cheap contracts)
        min_price = self._config.fomo_min_entry_price
        if trade_price < min_price:
            logger.info(
                "min_price_blocked_fomo",
                ticker=snapshot.market_ticker,
                trade_price=round(trade_price, 4),
                min_entry_price=min_price,
            )
            self.last_analysis = {
                "decision": (
                    f"NO FOMO: trade_price {trade_price:.4f} "
                    f"< min_entry_price {min_price:.4f}"
                ),
            }
            return None

        if divergence < self._config.fomo_min_divergence:
            self.last_analysis = {
                "decision": (
                    f"NO FOMO: divergence {divergence:.4f} "
                    f"< threshold {self._config.fomo_min_divergence:.4f}"
                ),
                "momentum_direction": momentum_direction,
                "divergence": round(divergence, 4),
                "implied": round(implied, 4),
                "model_prob": round(model_prob, 4),
            }
            return None

        # Step 3: Price range check
        if implied > self._config.fomo_max_implied_prob or implied < self._config.fomo_min_implied_prob:
            self.last_analysis = {
                "decision": (
                    f"NO FOMO: implied {implied:.4f} outside range "
                    f"[{self._config.fomo_min_implied_prob}, {self._config.fomo_max_implied_prob}]"
                ),
            }
            return None

        # Step 4: Confidence gate — only high-conviction FOMO
        if prediction.confidence < self._config.fomo_min_confidence:
            self.last_analysis = {
                "decision": (
                    f"NO FOMO: confidence {prediction.confidence:.2f} "
                    f"< threshold {self._config.fomo_min_confidence:.2f}"
                ),
                "divergence": round(divergence, 4),
            }
            return None

        # Step 5: Compute FOMO score and check minimum
        fomo_score = self._compute_fomo_score(
            divergence=divergence,
            momentum_magnitude=momentum_magnitude,
            trade_price=trade_price,
            time_to_expiry=snapshot.time_to_expiry_seconds,
        )

        if fomo_score < self._config.fomo_min_score:
            self.last_analysis = {
                "decision": (
                    f"NO FOMO: score {fomo_score:.4f} "
                    f"< threshold {self._config.fomo_min_score:.4f}"
                ),
                "fomo_score": round(fomo_score, 4),
                "divergence": round(divergence, 4),
            }
            return None

        # Step 7: Compute edge and check threshold
        raw_edge = divergence
        fee_drag = float(
            EdgeDetector.compute_fee_dollars(1, trade_price, is_maker=True)
        )
        net_edge = raw_edge - fee_drag

        if net_edge < self._config.fomo_edge_threshold:
            self.last_analysis = {
                "decision": (
                    f"NO FOMO: net edge {net_edge:.4f} "
                    f"< threshold {self._config.fomo_edge_threshold:.4f}"
                ),
                "fomo_score": round(fomo_score, 4),
                "divergence": round(divergence, 4),
                "raw_edge": round(raw_edge, 4),
                "fee_drag": round(fee_drag, 4),
            }
            return None

        # Step 8: Determine entry price (cap below ask to avoid post_only cross)
        if underpriced_side == "yes":
            best_bid = ob.best_yes_bid
            best_ask = ob.best_yes_ask
            target_price = implied + raw_edge * 0.5
            if best_bid is not None:
                price = max(float(best_bid) + 0.01, target_price)
            else:
                price = target_price
            if best_ask is not None:
                price = min(price, float(best_ask) - 0.01)
            else:
                # No ask visible — cannot safely cap price for maker order
                self.last_analysis = {
                    "decision": "NO FOMO: no YES ask to cap against",
                }
                return None
        else:
            best_bid = ob.best_no_bid
            best_no_ask = (
                1.0 - float(ob.best_yes_bid) if ob.best_yes_bid is not None else None
            )
            target_price = (1.0 - implied) + raw_edge * 0.5
            if best_bid is not None:
                price = max(float(best_bid) + 0.01, target_price)
            else:
                price = target_price
            if best_no_ask is not None:
                price = min(price, best_no_ask - 0.01)
            else:
                # No ask visible — cannot safely cap price for maker order
                self.last_analysis = {
                    "decision": "NO FOMO: no NO ask to cap against",
                }
                return None

        suggested_price = f"{min(0.99, max(0.01, price)):.2f}"

        self.last_analysis = {
            "decision": (
                f"FOMO: buy {underpriced_side.upper()} "
                f"edge={net_edge:.4f} score={fomo_score:.4f}"
            ),
            "fomo_score": round(fomo_score, 4),
            "divergence": round(divergence, 4),
            "raw_edge": round(raw_edge, 4),
            "net_edge": round(net_edge, 4),
            "fee_drag": round(fee_drag, 4),
            "momentum_direction": momentum_direction,
            "momentum_magnitude": round(momentum_magnitude, 6),
            "momentum_consistent": momentum_consistent,
            "implied": round(implied, 4),
            "model_prob": round(model_prob, 4),
            "underpriced_side": underpriced_side,
            "trade_price": round(trade_price, 4),
            "suggested_price": suggested_price,
        }

        logger.info(
            "fomo_detected",
            ticker=snapshot.market_ticker,
            side=underpriced_side,
            fomo_score=round(fomo_score, 4),
            divergence=round(divergence, 4),
            net_edge=round(net_edge, 4),
            momentum_direction=momentum_direction,
            implied=round(implied, 4),
            model_prob=round(model_prob, 4),
        )

        return TradeSignal(
            market_ticker=snapshot.market_ticker,
            side=underpriced_side,
            action="buy",
            raw_edge=round(raw_edge, 4),
            net_edge=round(net_edge, 4),
            model_probability=prediction.probability_yes,
            implied_probability=implied,
            confidence=prediction.confidence,
            suggested_price_dollars=suggested_price,
            suggested_count=0,
            timestamp=datetime.now(timezone.utc),
            signal_type="fomo",
            entry_zone=EdgeDetector.classify_zone(trade_price),
        )

    def _analyze_momentum(
        self, features: FeatureVector
    ) -> tuple[int, float, bool]:
        """Analyze BTC momentum direction, magnitude, and consistency.

        Returns:
            (direction, magnitude, consistent)
            direction: +1 (up), -1 (down), 0 (mixed/weak)
            magnitude: absolute value of the 600s momentum
            consistent: True if all nonzero timeframes agree
        """
        momentums = [
            features.momentum_15s,
            features.momentum_60s,
            features.momentum_180s,
            features.momentum_600s,
        ]
        nonzero = [m for m in momentums if m != 0]

        if not nonzero:
            return (0, 0.0, False)

        all_positive = all(m > 0 for m in nonzero)
        all_negative = all(m < 0 for m in nonzero)
        consistent = all_positive or all_negative

        magnitude = abs(features.momentum_600s)

        # Weighted consensus (matches HeuristicModel weights)
        weighted = (
            0.1 * features.momentum_15s
            + 0.2 * features.momentum_60s
            + 0.3 * features.momentum_180s
            + 0.4 * features.momentum_600s
        )

        if weighted > 0:
            direction = 1
        elif weighted < 0:
            direction = -1
        else:
            direction = 0

        return (direction, magnitude, consistent)

    def _compute_fomo_score(
        self,
        divergence: float,
        momentum_magnitude: float,
        trade_price: float,
        time_to_expiry: float,
    ) -> float:
        """Compute composite FOMO score in [0, 1].

        Higher score = stronger FOMO overreaction = better opportunity.
        """
        # Divergence component (larger gap = stronger FOMO)
        divergence_score = math.tanh(divergence / 0.20)

        # Momentum strength component
        momentum_score = math.tanh(momentum_magnitude / 0.008)

        # Fee advantage component (extreme prices = tiny fees)
        fee_fraction = trade_price * (1.0 - trade_price)
        fee_score = 1.0 - (fee_fraction / 0.25)

        # Time remaining component (more time = more chance of reversion)
        time_score = min(1.0, time_to_expiry / 600.0)

        score = (
            0.40 * divergence_score
            + 0.30 * momentum_score
            + 0.15 * fee_score
            + 0.15 * time_score
        )

        return max(0.0, min(1.0, score))
