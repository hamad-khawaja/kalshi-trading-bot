"""Market-making strategy for wide-spread KXBTC15M markets."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import structlog

from src.config import StrategyConfig
from src.data.models import MarketSnapshot, PredictionResult, TradeSignal

logger = structlog.get_logger()


class MarketMaker:
    """Market-making strategy for capturing spread in wide Kalshi markets.

    When the orderbook spread exceeds a threshold, places resting limit
    orders on both sides to capture the bid-ask spread. Best in low-vol
    regimes with wide spreads and sufficient volume.

    Key principles:
    - Only market-make when spread is wide enough to be profitable after fees
    - Center quotes around model's fair value
    - Use post_only orders (maker fees = 1.75% vs taker 7%)
    - Manage inventory: widen quotes on the side where exposure is building
    """

    def __init__(self, config: StrategyConfig):
        self._config = config
        self._min_spread = config.mm_min_spread
        self._max_spread = config.mm_max_spread
        self._max_inventory = config.mm_max_inventory

    def generate_quotes(
        self,
        prediction: PredictionResult,
        snapshot: MarketSnapshot,
        current_position: int,  # Positive = net YES, negative = net NO
    ) -> list[TradeSignal]:
        """Generate bid/ask quote pair for market making.

        Returns 0-2 TradeSignals (YES bid, NO bid).
        """
        spread = snapshot.spread
        if spread is None or float(spread) < self._min_spread:
            return []

        # Don't market-make into dead/illiquid markets
        if float(spread) > self._max_spread:
            logger.debug(
                "mm_skipped_spread_too_wide",
                ticker=snapshot.market_ticker,
                spread=float(spread),
                max_spread=self._max_spread,
            )
            return []

        # Don't market-make with low confidence
        if prediction.confidence < 0.3:
            return []

        # Don't market-make too close to expiry
        if snapshot.time_to_expiry_seconds < 120:
            return []

        # Don't market-make when inventory is already large
        if abs(current_position) >= self._max_inventory:
            logger.debug(
                "mm_skipped_inventory_full",
                ticker=snapshot.market_ticker,
                position=current_position,
                cap=self._max_inventory,
            )
            return []

        ob = snapshot.orderbook
        best_yes_bid = ob.best_yes_bid
        best_no_bid = ob.best_no_bid

        if best_yes_bid is None or best_no_bid is None:
            return []

        # Fair value from model
        fair_value = Decimal(str(round(prediction.probability_yes, 2)))

        signals: list[TradeSignal] = []
        now = datetime.now(timezone.utc)

        # Inventory adjustment: widen quote on side with excess exposure
        inventory_skew = Decimal(str(current_position)) * Decimal("0.01")

        # YES bid: buy YES below fair value
        yes_bid_price = fair_value - Decimal("0.02") - max(Decimal("0"), inventory_skew)
        yes_bid_price = max(best_yes_bid + Decimal("0.01"), yes_bid_price)
        yes_bid_price = max(Decimal("0.01"), min(Decimal("0.99"), yes_bid_price))

        # Clamp YES bid below the effective YES ask to prevent post_only cross
        yes_ask = Decimal("1") - best_no_bid  # Effective YES ask
        if yes_bid_price >= yes_ask:
            yes_bid_price = yes_ask - Decimal("0.01")

        fee_estimate = Decimal("0.02")  # Conservative maker fee estimate
        potential_profit_yes = yes_ask - yes_bid_price

        if potential_profit_yes > fee_estimate and yes_bid_price >= Decimal("0.01"):
            signals.append(
                TradeSignal(
                    market_ticker=snapshot.market_ticker,
                    side="yes",
                    action="buy",
                    raw_edge=float(potential_profit_yes),
                    net_edge=float(potential_profit_yes - fee_estimate),
                    model_probability=prediction.probability_yes,
                    implied_probability=float(snapshot.implied_yes_prob or Decimal("0.5")),
                    confidence=prediction.confidence,
                    suggested_price_dollars=f"{yes_bid_price:.2f}",
                    suggested_count=0,
                    timestamp=now,
                    signal_type="market_making",
                )
            )

        # NO bid: buy NO below (1 - fair_value)
        no_fair = Decimal("1") - fair_value
        no_bid_price = no_fair - Decimal("0.02") + min(Decimal("0"), inventory_skew)
        no_bid_price = max(best_no_bid + Decimal("0.01"), no_bid_price)
        no_bid_price = max(Decimal("0.01"), min(Decimal("0.99"), no_bid_price))

        # Clamp NO bid below the effective NO ask to prevent post_only cross
        no_ask = Decimal("1") - best_yes_bid  # Effective NO ask
        if no_bid_price >= no_ask:
            no_bid_price = no_ask - Decimal("0.01")

        potential_profit_no = no_ask - no_bid_price

        if potential_profit_no > fee_estimate and no_bid_price >= Decimal("0.01"):
            signals.append(
                TradeSignal(
                    market_ticker=snapshot.market_ticker,
                    side="no",
                    action="buy",
                    raw_edge=float(potential_profit_no),
                    net_edge=float(potential_profit_no - fee_estimate),
                    model_probability=1.0 - prediction.probability_yes,
                    implied_probability=float(
                        Decimal("1") - (snapshot.implied_yes_prob or Decimal("0.5"))
                    ),
                    confidence=prediction.confidence,
                    suggested_price_dollars=f"{no_bid_price:.2f}",
                    suggested_count=0,
                    timestamp=now,
                    signal_type="market_making",
                )
            )

        if signals:
            logger.info(
                "mm_quotes_generated",
                ticker=snapshot.market_ticker,
                spread=float(spread),
                fair_value=float(fair_value),
                num_quotes=len(signals),
                inventory=current_position,
            )

        return signals
