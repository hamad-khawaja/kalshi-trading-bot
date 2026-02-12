"""Edge detection: identifies mispriced Kalshi contracts."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from src.config import StrategyConfig
from src.data.models import MarketSnapshot, PredictionResult, TradeSignal

logger = structlog.get_logger()


class EdgeDetector:
    """Identifies trading opportunities where model disagrees with market.

    Compares model probability to Kalshi implied probability,
    accounts for trading fees, and generates trade signals when
    the net edge exceeds the configured threshold.
    """

    def __init__(self, config: StrategyConfig):
        self._config = config

    def detect(
        self,
        prediction: PredictionResult,
        snapshot: MarketSnapshot,
    ) -> TradeSignal | None:
        """Detect edge between model probability and market implied probability.

        Returns a TradeSignal if edge exceeds threshold, None otherwise.
        """
        implied_prob = snapshot.implied_yes_prob
        if implied_prob is None:
            return None

        implied = float(implied_prob)
        model_prob = prediction.probability_yes

        # Determine direction
        if model_prob > implied:
            # Model thinks YES is underpriced -> buy YES
            side = "yes"
            raw_edge = model_prob - implied
            trade_price = implied  # We'd pay roughly implied price
        else:
            # Model thinks NO is underpriced -> buy NO
            side = "no"
            raw_edge = implied - model_prob
            trade_price = 1.0 - implied  # NO price = 1 - YES implied

        # Compute fee drag per contract
        # Using taker fee as conservative estimate
        fee_per_contract = self.compute_fee_dollars(1, trade_price, is_maker=False)
        fee_drag = float(fee_per_contract)

        net_edge = raw_edge - fee_drag

        # Check thresholds
        if net_edge < self._config.min_edge_threshold:
            return None

        if net_edge > self._config.max_edge_threshold:
            logger.warning(
                "edge_too_large",
                net_edge=net_edge,
                model_prob=model_prob,
                implied=implied,
                ticker=snapshot.market_ticker,
            )
            return None

        # Confidence gate
        if prediction.confidence < self._config.confidence_weight * 0.5:
            return None

        # Determine price to submit
        if side == "yes":
            # Place bid slightly above current best YES bid
            best_bid = snapshot.orderbook.best_yes_bid
            if best_bid is not None:
                # Improve by 1 cent
                price = float(best_bid) + 0.01
            else:
                price = implied
            suggested_price = f"{min(0.99, max(0.01, price)):.2f}"
        else:
            # Place bid slightly above current best NO bid
            best_bid = snapshot.orderbook.best_no_bid
            if best_bid is not None:
                price = float(best_bid) + 0.01
            else:
                price = 1.0 - implied
            suggested_price = f"{min(0.99, max(0.01, price)):.2f}"

        logger.info(
            "edge_detected",
            ticker=snapshot.market_ticker,
            side=side,
            raw_edge=round(raw_edge, 4),
            net_edge=round(net_edge, 4),
            model_prob=round(model_prob, 4),
            implied=round(implied, 4),
            confidence=round(prediction.confidence, 4),
            price=suggested_price,
        )

        return TradeSignal(
            market_ticker=snapshot.market_ticker,
            side=side,
            action="buy",
            raw_edge=round(raw_edge, 4),
            net_edge=round(net_edge, 4),
            model_probability=model_prob,
            implied_probability=implied,
            confidence=prediction.confidence,
            suggested_price_dollars=suggested_price,
            suggested_count=0,  # Filled in by position sizer
            timestamp=datetime.now(timezone.utc),
            signal_type="directional",
        )

    @staticmethod
    def compute_fee_dollars(
        count: int, price_dollars: float, is_maker: bool = False
    ) -> Decimal:
        """Compute Kalshi trading fee in dollars.

        Taker fee: ceil(0.07 * C * P * (1 - P))
        Maker fee: ceil(0.0175 * C * P * (1 - P))

        Where P = price in dollars (e.g., 0.56), C = contract count.
        Fee is maximized at P = 0.50 and approaches 0 at extremes.
        """
        rate = Decimal("0.0175") if is_maker else Decimal("0.07")
        p = Decimal(str(price_dollars))
        c = Decimal(str(count))

        raw_fee = rate * c * p * (1 - p)
        # Ceiling to nearest cent
        fee_cents = math.ceil(float(raw_fee) * 100)
        return Decimal(str(fee_cents)) / 100
