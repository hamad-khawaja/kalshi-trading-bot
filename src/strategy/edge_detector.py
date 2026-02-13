"""Edge detection: identifies mispriced Kalshi contracts."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from src.config import StrategyConfig
from src.data.models import MarketSnapshot, PredictionResult, TradeSignal
from src.data.time_profile import TimeProfiler
from src.risk.volatility import VolatilityTracker

logger = structlog.get_logger()


class EdgeDetector:
    """Identifies trading opportunities where model disagrees with market.

    Compares model probability to Kalshi implied probability,
    accounts for trading fees, and generates trade signals when
    the net edge exceeds the configured threshold.
    """

    def __init__(
        self,
        config: StrategyConfig,
        vol_tracker: VolatilityTracker | None = None,
        time_profiler: TimeProfiler | None = None,
    ):
        self._config = config
        self._vol_tracker = vol_tracker
        self._time_profiler = time_profiler
        self.last_analysis: dict = {}

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

        # Guard: skip when orderbook spread is too wide (implied prob unreliable)
        ob = snapshot.orderbook
        spread = ob.spread
        max_spread = self._config.directional_max_spread
        if spread is not None and float(spread) > max_spread:
            self.last_analysis = {
                "side": "none",
                "raw_edge": 0,
                "fee_drag": 0,
                "net_edge": 0,
                "min_threshold": self._config.min_edge_threshold,
                "max_threshold": self._config.max_edge_threshold,
                "model_prob": round(prediction.probability_yes, 4),
                "implied_prob": round(float(implied_prob), 4),
                "confidence": round(prediction.confidence, 4),
                "edge_passed": False,
                "confidence_ok": False,
                "passed": False,
                "decision": f"NO TRADE: spread {float(spread):.2f} > max {max_spread:.2f} (orderbook too thin)",
            }
            logger.debug(
                "edge_skipped_spread_wide",
                ticker=snapshot.market_ticker,
                spread=float(spread),
                max_spread=max_spread,
            )
            return None

        # Guard: skip when orderbook depth is too shallow
        min_depth = self._config.directional_min_depth
        total_depth = ob.yes_bid_depth + ob.no_bid_depth
        if total_depth < min_depth:
            self.last_analysis = {
                "side": "none",
                "raw_edge": 0,
                "fee_drag": 0,
                "net_edge": 0,
                "min_threshold": self._config.min_edge_threshold,
                "max_threshold": self._config.max_edge_threshold,
                "model_prob": round(prediction.probability_yes, 4),
                "implied_prob": round(float(implied_prob), 4),
                "confidence": round(prediction.confidence, 4),
                "edge_passed": False,
                "confidence_ok": False,
                "passed": False,
                "decision": f"NO TRADE: orderbook depth {total_depth} < min {min_depth} (no liquidity)",
            }
            logger.debug(
                "edge_skipped_low_depth",
                ticker=snapshot.market_ticker,
                total_depth=total_depth,
                min_depth=min_depth,
            )
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

        # Apply volatility-adjusted thresholds when tracker is available
        if self._vol_tracker is not None:
            min_threshold = self._vol_tracker.adjust_edge_threshold(
                self._config.min_edge_threshold
            )
            max_threshold = (
                self._config.max_edge_threshold
                * min_threshold
                / self._config.min_edge_threshold
            )
        else:
            min_threshold = self._config.min_edge_threshold
            max_threshold = self._config.max_edge_threshold

        # Apply session-based threshold multiplier on top of vol adjustment
        if self._time_profiler is not None:
            session = self._time_profiler.get_current_session()
            session_mult = self._time_profiler.get_edge_threshold_multiplier(session)
            min_threshold *= session_mult
            max_threshold *= session_mult

        # Capture analysis state for dashboard
        edge_passed = min_threshold <= net_edge <= max_threshold
        confidence_ok = prediction.confidence >= self._config.confidence_weight * 0.5
        if not edge_passed:
            if net_edge < min_threshold:
                decision = f"NO TRADE: net edge {net_edge:.4f} < threshold {min_threshold:.4f}"
            else:
                decision = f"NO TRADE: net edge {net_edge:.4f} > max threshold {max_threshold:.4f}"
        elif not confidence_ok:
            decision = f"NO TRADE: confidence {prediction.confidence:.3f} < min {self._config.confidence_weight * 0.5:.3f}"
        else:
            decision = f"TRADE: {side.upper()} edge={net_edge:.4f} (threshold {min_threshold:.4f})"
        self.last_analysis = {
            "side": side,
            "raw_edge": round(raw_edge, 4),
            "fee_drag": round(fee_drag, 4),
            "net_edge": round(net_edge, 4),
            "min_threshold": round(min_threshold, 4),
            "max_threshold": round(max_threshold, 4),
            "model_prob": round(model_prob, 4),
            "implied_prob": round(implied, 4),
            "confidence": round(prediction.confidence, 4),
            "edge_passed": edge_passed,
            "confidence_ok": confidence_ok,
            "passed": edge_passed and confidence_ok,
            "decision": decision,
        }

        # Check thresholds
        if net_edge < min_threshold:
            return None

        if net_edge > max_threshold:
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
        # Use model probability as basis, discounted by a fraction of the edge
        # to ensure we still get filled while capturing most of the edge.
        if side == "yes":
            # We think YES is worth model_prob, market says implied.
            # Bid between implied and model_prob (keep ~40% of edge as profit).
            target_price = implied + raw_edge * 0.6
            best_bid = snapshot.orderbook.best_yes_bid
            if best_bid is not None:
                # Don't bid below best bid (we want to be at top of book)
                price = max(float(best_bid) + 0.01, target_price)
            else:
                price = target_price
            suggested_price = f"{min(0.99, max(0.01, price)):.2f}"
        else:
            # NO price: we think NO is worth (1 - model_prob), market says (1 - implied).
            target_price = (1.0 - implied) + raw_edge * 0.6
            best_bid = snapshot.orderbook.best_no_bid
            if best_bid is not None:
                price = max(float(best_bid) + 0.01, target_price)
            else:
                price = target_price
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
