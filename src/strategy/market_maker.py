"""Market-making strategy for wide-spread KXBTC15M markets."""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timedelta, timezone
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
        # Throttle skip logging: ticker → last_reason logged
        self._last_skip_reason: dict[str, str] = {}
        # Fill tracking for asymmetry detection: (ticker, side, timestamp)
        self._recent_fills: deque[tuple[str, str, datetime]] = deque(
            maxlen=config.mm_fill_asymmetry_window
        )

    def compute_fair_value(
        self, prediction: PredictionResult, snapshot: MarketSnapshot
    ) -> Decimal:
        """Compute blended fair value (public — used by requote check)."""
        return self._compute_fair_value(prediction, snapshot)

    def _compute_fair_value(
        self, prediction: PredictionResult, snapshot: MarketSnapshot
    ) -> Decimal:
        """Blend model probability with orderbook midpoint for fair value."""
        model_fv = Decimal(str(prediction.probability_yes))
        ob_mid = snapshot.orderbook.implied_yes_prob
        blend = self._config.mm_ob_mid_blend
        if ob_mid is not None and blend > 0:
            blended = Decimal(str(blend)) * ob_mid + Decimal(str(1 - blend)) * model_fv
            return max(Decimal("0.01"), min(Decimal("0.99"), blended))
        return model_fv

    def _fills_per_minute(self, ticker: str) -> float:
        """Count fills for a ticker in the last 60 seconds."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
        return sum(1 for t, _, ts in self._recent_fills if t == ticker and ts >= cutoff)

    def _dynamic_spread_offset(self, ticker: str) -> Decimal:
        """Compute spread offset based on fill rate and vol regime.

        Higher fill rate → wider spread (adverse selection protection).
        Vol regime applies a multiplier on top.
        """
        min_offset = self._config.mm_min_spread_offset
        max_offset = self._config.mm_max_spread_offset
        target_fpm = self._config.mm_target_fills_per_minute

        # Linear interpolation based on fill rate
        fpm = self._fills_per_minute(ticker)
        if target_fpm > 0:
            ratio = min(fpm / target_fpm, 1.0)
        else:
            ratio = 0.0
        base_offset = min_offset + ratio * (max_offset - min_offset)

        # Vol regime multiplier
        vol_mult = 1.0
        if self._vol_tracker is not None:
            regime = self._vol_tracker.current_regime
            vol_mults = {"low": 0.7, "normal": 1.0, "high": 1.5, "extreme": 2.0}
            vol_mult = vol_mults.get(regime, 1.0)

        return Decimal(str(round(base_offset * vol_mult, 6)))

    def _depth_imbalance_skew(self, snapshot: MarketSnapshot) -> Decimal:
        """Compute quote skew based on orderbook depth imbalance.

        Positive imbalance (more YES depth) → tighten YES bid, widen NO bid.
        """
        yes_depth = snapshot.orderbook.yes_bid_depth
        no_depth = snapshot.orderbook.no_bid_depth
        total = yes_depth + no_depth
        if total == 0:
            return Decimal("0")
        imbalance = (yes_depth - no_depth) / total  # [-1, 1]
        skew = imbalance * self._config.mm_depth_imbalance_max_skew
        return Decimal(str(round(skew, 6)))

    def should_requote(
        self, ticker: str, current_fair_value: Decimal, threshold: float | None = None
    ) -> bool:
        """Check if fair value has moved enough to warrant requoting.

        Returns True if the fair value has moved more than threshold since
        the last quotes were generated for this ticker.
        """
        if threshold is None:
            threshold = self._config.mm_requote_threshold
        prev = self._last_quotes.get(ticker)
        if prev is None:
            return False
        prev_fv, _ = prev
        return abs(float(current_fair_value - prev_fv)) > threshold

    def clear_quote_state(self, ticker: str) -> None:
        """Clear tracked quote state for a ticker (after cancellation)."""
        self._last_quotes.pop(ticker, None)

    def record_fill(self, ticker: str, side: str) -> None:
        """Record an MM fill for asymmetry tracking."""
        self._recent_fills.append((ticker, side, datetime.now(timezone.utc)))

    def is_quote_stale(self, ticker: str, max_age: float) -> bool:
        """Check if existing quotes for a ticker are older than max_age seconds."""
        prev = self._last_quotes.get(ticker)
        if prev is None:
            return False
        _, ts = prev
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age > max_age

    def _fill_ratio(self, ticker: str) -> tuple[int, int]:
        """Count YES and NO fills for a ticker in the last 60 seconds."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
        yes_count = sum(
            1 for t, s, ts in self._recent_fills
            if t == ticker and s == "yes" and ts >= cutoff
        )
        no_count = sum(
            1 for t, s, ts in self._recent_fills
            if t == ticker and s == "no" and ts >= cutoff
        )
        return yes_count, no_count

    def _log_skip(self, ticker: str, reason: str, **kwargs: object) -> None:
        """Log MM skip reason once per ticker per reason change."""
        if self._last_skip_reason.get(ticker) != reason:
            self._last_skip_reason[ticker] = reason
            logger.info("mm_skipped", ticker=ticker, reason=reason, **kwargs)

    def generate_quotes(
        self,
        prediction: PredictionResult,
        snapshot: MarketSnapshot,
        current_position: int,  # Positive = net YES, negative = net NO
        directional_side: str | None = None,  # When running alongside directional
        resting_qty_yes: int = 0,  # Unfilled YES bid contracts resting on book
        resting_qty_no: int = 0,  # Unfilled NO bid contracts resting on book
    ) -> list[TradeSignal]:
        """Generate bid/ask quote pair for market making.

        Returns 0-2 TradeSignals (YES bid, NO bid).

        Args:
            prediction: Model probability estimate
            snapshot: Current market data snapshot
            current_position: Net YES contracts held (positive = long YES)
            directional_side: If set, only generate MM quotes on the opposite side
            resting_qty_yes: Unfilled YES contracts in resting orders
            resting_qty_no: Unfilled NO contracts in resting orders
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
            self._log_skip(
                snapshot.market_ticker,
                "spread_too_narrow",
                spread=float(spread) if spread else None,
                min_spread=effective_min_spread,
            )
            return []

        # Don't market-make into dead/illiquid markets
        if float(spread) > self._max_spread:
            self._log_skip(
                snapshot.market_ticker,
                "spread_too_wide",
                spread=round(float(spread), 4),
                max_spread=self._max_spread,
            )
            return []

        # Don't market-make with low confidence
        if prediction.confidence < 0.3:
            self._log_skip(
                snapshot.market_ticker,
                "low_confidence",
                confidence=round(prediction.confidence, 3),
            )
            return []

        # Don't market-make too close to expiry
        if snapshot.time_to_expiry_seconds < 120:
            self._log_skip(
                snapshot.market_ticker,
                "near_expiry",
                time_to_expiry=round(snapshot.time_to_expiry_seconds),
            )
            return []

        # Effective position includes resting orders that haven't filled yet.
        # If they all fill simultaneously (e.g. price sweep), this is our
        # actual exposure — use it for inventory cap and skew.
        effective_position = current_position + resting_qty_yes - resting_qty_no

        # Don't market-make when inventory is already large
        if abs(effective_position) >= self._max_inventory:
            self._log_skip(
                snapshot.market_ticker,
                "inventory_full",
                position=effective_position,
                cap=self._max_inventory,
            )
            return []

        ob = snapshot.orderbook
        best_yes_bid = ob.best_yes_bid
        best_no_bid = ob.best_no_bid

        if best_yes_bid is None or best_no_bid is None:
            return []

        # Fair value: blend model probability with OB midpoint
        fair_value = self._compute_fair_value(prediction, snapshot)

        # Dynamic spread offset based on fill rate + vol regime
        spread_offset = self._dynamic_spread_offset(snapshot.market_ticker)

        # Depth imbalance skew: tighten on heavy side, widen on thin side
        depth_skew = self._depth_imbalance_skew(snapshot)

        signals: list[TradeSignal] = []
        now = datetime.now(timezone.utc)

        # Non-linear inventory skew: quadratic scaling
        # normalized ∈ [-1, 1], skew = sign(n) * n² * 0.08
        if self._max_inventory > 0:
            normalized = effective_position / self._max_inventory
        else:
            normalized = 0.0
        inventory_skew = Decimal(
            str(round(math.copysign(normalized ** 2, normalized) * 0.08, 6))
        )

        # YES bid: buy YES below fair value
        # +depth_skew: positive imbalance (more YES depth) → tighten YES bid
        yes_bid_price = fair_value - spread_offset - inventory_skew + depth_skew
        yes_bid_price = max(Decimal("0.01"), min(Decimal("0.99"), yes_bid_price))

        # Clamp YES bid below the effective YES ask to prevent post_only cross
        yes_ask = Decimal("1") - best_no_bid  # Effective YES ask
        if yes_bid_price >= yes_ask:
            yes_bid_price = yes_ask - Decimal("0.01")

        # Accurate maker fee using Kalshi formula: ceil(0.0175 * C * P * (1-P))
        yes_fee = EdgeDetector.compute_fee_dollars(1, float(yes_bid_price), is_maker=True)
        potential_profit_yes = yes_ask - yes_bid_price

        # Only generate YES bid if not filtered by directional side or fill asymmetry
        generate_yes = directional_side != "yes"
        if generate_yes:
            yes_fills, no_fills = self._fill_ratio(snapshot.market_ticker)
            threshold = self._config.mm_fill_asymmetry_threshold
            if yes_fills > 0 and yes_fills / max(1, no_fills) > threshold:
                generate_yes = False
                self._log_skip(
                    snapshot.market_ticker, "fill_asymmetry_yes",
                    yes_fills=yes_fills, no_fills=no_fills,
                )
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
                    suggested_price_dollars=f"{float(yes_bid_price):.2f}",
                    suggested_count=0,
                    timestamp=now,
                    signal_type="market_making",
                    post_only=True,
                )
            )

        # NO bid: buy NO below (1 - fair_value)
        # -depth_skew: positive imbalance (more YES depth) → widen NO bid
        no_fair = Decimal("1") - fair_value
        no_bid_price = no_fair - spread_offset + inventory_skew - depth_skew
        no_bid_price = max(Decimal("0.01"), min(Decimal("0.99"), no_bid_price))

        # Clamp NO bid below the effective NO ask to prevent post_only cross
        no_ask = Decimal("1") - best_yes_bid  # Effective NO ask
        if no_bid_price >= no_ask:
            no_bid_price = no_ask - Decimal("0.01")

        # Accurate maker fee for NO side
        no_fee = EdgeDetector.compute_fee_dollars(1, float(no_bid_price), is_maker=True)
        potential_profit_no = no_ask - no_bid_price

        # Only generate NO bid if not filtered by directional side or fill asymmetry
        generate_no = directional_side != "no"
        if generate_no:
            yes_fills, no_fills = self._fill_ratio(snapshot.market_ticker)
            threshold = self._config.mm_fill_asymmetry_threshold
            if no_fills > 0 and no_fills / max(1, yes_fills) > threshold:
                generate_no = False
                self._log_skip(
                    snapshot.market_ticker, "fill_asymmetry_no",
                    yes_fills=yes_fills, no_fills=no_fills,
                )
        if generate_no and potential_profit_no > no_fee and no_bid_price >= Decimal("0.01"):
            signals.append(
                TradeSignal(
                    market_ticker=snapshot.market_ticker,
                    side="no",
                    action="buy",
                    raw_edge=float(potential_profit_no),
                    net_edge=float(potential_profit_no - no_fee),
                    model_probability=prediction.probability_yes,
                    implied_probability=float(
                        Decimal("1") - (snapshot.implied_yes_prob or Decimal("0.5"))
                    ),
                    confidence=prediction.confidence,
                    suggested_price_dollars=f"{float(no_bid_price):.2f}",
                    suggested_count=0,
                    timestamp=now,
                    signal_type="market_making",
                    post_only=True,
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
                effective_inventory=effective_position,
                vol_regime=vol_regime,
                spread_offset=float(spread_offset),
                inventory_skew=float(inventory_skew),
                depth_skew=float(depth_skew),
            )
            # Track quote state for requote detection
            self._last_quotes[snapshot.market_ticker] = (fair_value, now)
            # Clear skip reason so next skip gets logged
            self._last_skip_reason.pop(snapshot.market_ticker, None)

        return signals
