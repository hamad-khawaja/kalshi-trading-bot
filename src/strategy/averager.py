"""Asymmetric averaging (pyramid down): buy more at deeper discounts.

When we hold a position and the price drops below our average entry,
this module generates signals to add at the discount — increasing size
as the discount deepens.  Safety rails ensure the model still agrees on
direction, momentum isn't running hard against us, and we cap adds.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from src.config import AveragingConfig
from src.data.models import (
    FeatureVector,
    MarketSnapshot,
    PredictionResult,
    TradeSignal,
)
from src.execution.position_tracker import PositionState

logger = structlog.get_logger()


class Averager:
    """Generates averaging-down signals for existing positions."""

    def __init__(self, config: AveragingConfig):
        self._config = config

    def evaluate(
        self,
        position: PositionState,
        snapshot: MarketSnapshot,
        prediction: PredictionResult,
        features: FeatureVector,
    ) -> TradeSignal | None:
        """Check whether we should average into *position* at a discount.

        Returns a TradeSignal with ``signal_type="averaging"`` if all
        safety checks pass and the current price is at least tier-1
        discounted from the average entry.  The tier's size multiplier
        is stashed in ``suggested_count`` so the caller can apply it
        after normal Kelly sizing.
        """
        if not self._config.enabled:
            return None

        ticker = position.market_ticker

        # --- Safety 1: Max adds ---
        if position.add_count >= self._config.max_adds_per_position:
            logger.debug(
                "averaging_max_adds_reached",
                ticker=ticker,
                add_count=position.add_count,
            )
            return None

        # --- Safety 2: Time floor ---
        if snapshot.time_to_expiry_seconds < self._config.min_time_to_expiry_seconds:
            return None

        # --- Determine current best price on our side ---
        ob = snapshot.orderbook
        if position.side == "yes":
            best_ask = ob.best_yes_ask
            if best_ask is None:
                return None
            current_price = float(best_ask)
        else:
            # NO ask = 1 - best YES bid
            if ob.best_yes_bid is None:
                return None
            current_price = 1.0 - float(ob.best_yes_bid)

        avg_entry = float(position.avg_entry_price)
        if avg_entry <= 0:
            return None

        # --- Safety 3: Min discount (tier 1) ---
        discount = (avg_entry - current_price) / avg_entry
        if discount < self._config.discount_tiers[0]:
            return None

        # --- Safety 4: Thesis intact ---
        dead_zone = self._config.dead_zone
        if position.side == "yes":
            if prediction.probability_yes <= 0.50 + dead_zone:
                logger.debug(
                    "averaging_thesis_broken",
                    ticker=ticker,
                    side="yes",
                    model_prob=round(prediction.probability_yes, 4),
                )
                return None
        else:
            if prediction.probability_yes >= 0.50 - dead_zone:
                logger.debug(
                    "averaging_thesis_broken",
                    ticker=ticker,
                    side="no",
                    model_prob=round(prediction.probability_yes, 4),
                )
                return None

        # --- Safety 5: Momentum guard ---
        if not self._momentum_ok(position.side, features):
            logger.debug(
                "averaging_momentum_blocked",
                ticker=ticker,
                side=position.side,
            )
            return None

        # --- Determine tier & multiplier ---
        tier_index = 0
        for i, threshold in enumerate(self._config.discount_tiers):
            if discount >= threshold:
                tier_index = i
        size_multiplier = self._config.size_multipliers[tier_index]

        # Use the ask price as suggested price
        suggested_price = f"{min(0.99, max(0.01, current_price)):.2f}"

        # Edge: discount itself is a rough proxy
        raw_edge = discount
        net_edge = discount  # fees handled downstream by risk manager

        logger.info(
            "averaging_signal",
            ticker=ticker,
            side=position.side,
            tier=tier_index + 1,
            discount=round(discount, 4),
            avg_entry=round(avg_entry, 4),
            current_price=round(current_price, 4),
            size_multiplier=size_multiplier,
            add_count=position.add_count,
            model_prob=round(prediction.probability_yes, 4),
        )

        return TradeSignal(
            market_ticker=ticker,
            side=position.side,
            action="buy",
            raw_edge=round(raw_edge, 4),
            net_edge=round(net_edge, 4),
            model_probability=prediction.probability_yes,
            implied_probability=float(snapshot.implied_yes_prob)
            if snapshot.implied_yes_prob is not None
            else 0.5,
            confidence=prediction.confidence,
            suggested_price_dollars=suggested_price,
            suggested_count=int(size_multiplier * 100),  # encode multiplier as int (100 = 1.0x)
            timestamp=datetime.now(timezone.utc),
            signal_type="averaging",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _momentum_ok(self, side: str, features: FeatureVector) -> bool:
        """At least 2 of 3 momentum timeframes must NOT be strongly against us.

        "Against us" means:
        - For YES positions: strong negative momentum (price dropping)
        - For NO positions: strong positive momentum (price rising)
        """
        threshold = self._config.momentum_threshold
        timeframes = [
            features.momentum_60s,
            features.momentum_180s,
            features.momentum_600s,
        ]

        against_count = 0
        for m in timeframes:
            if side == "yes" and m < -threshold:
                against_count += 1
            elif side == "no" and m > threshold:
                against_count += 1

        # Fail if 2+ timeframes are strongly against us
        return against_count < 2
