"""Market-making strategy for wide-spread KXBTC15M markets."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from src.config import StrategyConfig
from src.data.models import MarketSnapshot, PredictionResult, TradeSignal
from src.risk.volatility import VolatilityTracker
from src.strategy.edge_detector import EdgeDetector

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

    def __init__(
        self,
        config: StrategyConfig,
        vol_tracker: VolatilityTracker | None = None,
    ):
        self._config = config
        self._min_spread = config.mm_min_spread
        self._max_spread = config.mm_max_spread
        self._max_inventory = config.mm_max_inventory
        self._vol_tracker = vol_tracker

        # Quote refresh state: ticker → (fair_value, timestamp)
        self._last_quotes: dict[str, tuple[Decimal, datetime]] = {}

    def _vol_spread_offset(self) -> Decimal:
        """Return spread offset based on current volatility regime.

        Low vol = tighter quotes (more fills), high vol = wider quotes (less adverse selection).
        Returns early skip signal via extreme regime check in generate_quotes.
        """
        if self._vol_tracker is None:
            return Decimal("0.02")

        regime = self._vol_tracker.current_regime
        offsets = {
            "low": Decimal("0.01"),
            "normal": Decimal("0.02"),
            "high": Decimal("0.04"),
            "extreme": Decimal("0.04"),  # Won't reach here — blocked in generate_quotes
        }
        return offsets.get(regime, Decimal("0.02"))

    def should_requote(
        self, ticker: str, current_fair_value: Decimal, threshold: float = 0.03
    ) -> bool:
        """Check if fair value has moved enough to warrant requoting.

        Returns True if the fair value has moved more than threshold since
        the last quotes were generated for this ticker.
        """
        prev = self._last_quotes.get(ticker)
        if prev is None:
            return False
        prev_fv, _ = prev
        return abs(float(current_fair_value - prev_fv)) > threshold

    def clear_quote_state(self, ticker: str) -> None:
        """Clear tracked quote state for a ticker (after cancellation)."""
        self._last_quotes.pop(ticker, None)

    def generate_quotes(
        self,
        prediction: PredictionResult,
        snapshot: MarketSnapshot,
        current_position: int,  # Positive = net YES, negative = net NO
        directional_side: str | None = None,  # When running alongside directional
    ) -> list[TradeSignal]:
        """Generate bid/ask quote pair for market making.

        Returns 0-2 TradeSignals (YES bid, NO bid).

        Args:
            prediction: Model probability estimate
            snapshot: Current market data snapshot
            current_position: Net YES contracts held (positive = long YES)
            directional_side: If set, only generate MM quotes on the opposite side
        """
        # Skip market-making in high/extreme volatility (togglable from dashboard)
        if (
            self._config.mm_vol_filter_enabled
            and self._vol_tracker is not None
            and self._vol_tracker.current_regime in ("high", "extreme")
        ):
            logger.info(
                "mm_skipped_high_vol",
                ticker=snapshot.market_ticker,
                vol_regime=self._vol_tracker.current_regime,
            )
            return []

        spread = snapshot.spread
        effective_min_spread = self._min_spread
        if self._config.asset_mm_min_spread:
            ticker_upper = snapshot.market_ticker.upper()
            for asset, asset_spread in self._config.asset_mm_min_spread.items():
                if asset.upper() in ticker_upper:
                    effective_min_spread = asset_spread
                    break
        if spread is None or float(spread) < effective_min_spread:
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

        # Vol-aware spread offset
        spread_offset = self._vol_spread_offset()

        signals: list[TradeSignal] = []
        now = datetime.now(timezone.utc)

        # Non-linear inventory skew: quadratic scaling
        # normalized ∈ [-1, 1], skew = sign(n) * n² * 0.08
        if self._max_inventory > 0:
            normalized = current_position / self._max_inventory
        else:
            normalized = 0.0
        inventory_skew = Decimal(
            str(round(math.copysign(normalized ** 2, normalized) * 0.08, 6))
        )

        # YES bid: buy YES below fair value
        yes_bid_price = fair_value - spread_offset - max(Decimal("0"), inventory_skew)
        yes_bid_price = max(best_yes_bid + Decimal("0.01"), yes_bid_price)
        yes_bid_price = max(Decimal("0.01"), min(Decimal("0.99"), yes_bid_price))

        # Clamp YES bid below the effective YES ask to prevent post_only cross
        yes_ask = Decimal("1") - best_no_bid  # Effective YES ask
        if yes_bid_price >= yes_ask:
            yes_bid_price = yes_ask - Decimal("0.01")

        # Accurate maker fee using Kalshi formula: ceil(0.0175 * C * P * (1-P))
        yes_fee = EdgeDetector.compute_fee_dollars(1, float(yes_bid_price), is_maker=True)
        potential_profit_yes = yes_ask - yes_bid_price

        # Only generate YES bid if not filtered by directional side
        generate_yes = directional_side != "yes"
        if generate_yes and potential_profit_yes > yes_fee and yes_bid_price >= Decimal("0.01"):
            signals.append(
                TradeSignal(
                    market_ticker=snapshot.market_ticker,
                    side="yes",
                    action="buy",
                    raw_edge=float(potential_profit_yes),
                    net_edge=float(potential_profit_yes - yes_fee),
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
        no_bid_price = no_fair - spread_offset + min(Decimal("0"), inventory_skew)
        no_bid_price = max(best_no_bid + Decimal("0.01"), no_bid_price)
        no_bid_price = max(Decimal("0.01"), min(Decimal("0.99"), no_bid_price))

        # Clamp NO bid below the effective NO ask to prevent post_only cross
        no_ask = Decimal("1") - best_yes_bid  # Effective NO ask
        if no_bid_price >= no_ask:
            no_bid_price = no_ask - Decimal("0.01")

        # Accurate maker fee for NO side
        no_fee = EdgeDetector.compute_fee_dollars(1, float(no_bid_price), is_maker=True)
        potential_profit_no = no_ask - no_bid_price

        # Only generate NO bid if not filtered by directional side
        generate_no = directional_side != "no"
        if generate_no and potential_profit_no > no_fee and no_bid_price >= Decimal("0.01"):
            signals.append(
                TradeSignal(
                    market_ticker=snapshot.market_ticker,
                    side="no",
                    action="buy",
                    raw_edge=float(potential_profit_no),
                    net_edge=float(potential_profit_no - no_fee),
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
            vol_regime = self._vol_tracker.current_regime if self._vol_tracker else "unknown"
            logger.info(
                "mm_quotes_generated",
                ticker=snapshot.market_ticker,
                spread=float(spread),
                fair_value=float(fair_value),
                num_quotes=len(signals),
                inventory=current_position,
                vol_regime=vol_regime,
                spread_offset=float(spread_offset),
            )
            # Track quote state for requote detection
            self._last_quotes[snapshot.market_ticker] = (fair_value, now)

        return signals
