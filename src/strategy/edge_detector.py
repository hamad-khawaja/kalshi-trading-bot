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

        When the orderbook is liquid (tight spread), uses orderbook-implied probability.
        When the orderbook is thin, falls back to statistical fair value computed
        from BTC price distance to strike, realized volatility, and time to expiry.
        Thin-book trades require a higher edge threshold (configurable multiplier).

        Returns a TradeSignal if edge exceeds threshold, None otherwise.
        """
        ob = snapshot.orderbook
        spread = ob.spread
        max_spread = self._config.directional_max_spread
        min_depth = self._config.directional_min_depth
        total_depth = ob.yes_bid_depth + ob.no_bid_depth

        orderbook_is_thin = (
            (spread is not None and float(spread) > max_spread)
            or total_depth < min_depth
        )

        # Determine which implied probability source to use
        using_fair_value = False
        if orderbook_is_thin:
            # Try statistical fair value as fallback
            if (
                self._config.use_statistical_fair_value
                and snapshot.statistical_fair_value is not None
            ):
                implied = snapshot.statistical_fair_value
                using_fair_value = True
                logger.debug(
                    "edge_using_fair_value",
                    ticker=snapshot.market_ticker,
                    fair_value=round(implied, 4),
                    strike=str(snapshot.strike_price),
                    btc_price=float(snapshot.btc_price),
                    spread=float(spread) if spread else None,
                )
            else:
                # No fair value available — cannot compute edge
                spread_str = f"{float(spread):.2f}" if spread else "N/A"
                self.last_analysis = {
                    "side": "none",
                    "raw_edge": 0,
                    "fee_drag": 0,
                    "net_edge": 0,
                    "min_threshold": self._config.min_edge_threshold,
                    "max_threshold": self._config.max_edge_threshold,
                    "model_prob": round(prediction.probability_yes, 4),
                    "implied_prob": float(snapshot.implied_yes_prob) if snapshot.implied_yes_prob else 0,
                    "confidence": round(prediction.confidence, 4),
                    "edge_passed": False,
                    "confidence_ok": False,
                    "passed": False,
                    "using_fair_value": False,
                    "decision": f"NO TRADE: thin orderbook (spread={spread_str}, depth={total_depth}) and no fair value available",
                }
                return None
        else:
            # Use orderbook-derived implied probability
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

        # Invert: trade the opposite side (model is consistently wrong).
        # If the model underestimates YES (model < implied), normally we'd buy NO.
        # Inverting means: model is wrong → market is right → buy YES instead.
        # For Kelly sizing, our "true prob" = implied + fraction of the divergence
        # (we believe the market is right and the model error IS the edge).
        if self._config.invert_signals:
            side = "no" if side == "yes" else "yes"
            trade_price = (1.0 - trade_price) if trade_price > 0 else trade_price
            # Inverted model prob: market price + 30% of the model's error as real edge
            if side == "yes":
                model_prob = min(0.95, implied + raw_edge * 0.3)
            else:
                model_prob = max(0.05, (1.0 - implied) + raw_edge * 0.3)

        # Compute fee drag per contract
        # Using maker fee since all orders use post_only=True
        fee_per_contract = self.compute_fee_dollars(1, trade_price, is_maker=True)
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
            # Hard cap: never accept edges above 0.20 regardless of vol adjustment
            max_threshold = min(max_threshold, 0.20)
        else:
            min_threshold = self._config.min_edge_threshold
            max_threshold = self._config.max_edge_threshold

        # Apply session-based threshold multiplier on top of vol adjustment
        if self._time_profiler is not None:
            session = self._time_profiler.get_current_session()
            session_mult = self._time_profiler.get_edge_threshold_multiplier(session)
            min_threshold *= session_mult
            max_threshold *= session_mult

        # Apply thin-book multiplier: require higher edge when using fair value
        if using_fair_value:
            thin_mult = self._config.thin_book_edge_multiplier
            min_threshold *= thin_mult
            max_threshold *= thin_mult

        # Capture analysis state for dashboard
        fv_label = " [fair value]" if using_fair_value else ""
        edge_passed = min_threshold <= net_edge <= max_threshold
        confidence_ok = prediction.confidence >= self._config.confidence_min
        if not edge_passed:
            if net_edge < min_threshold:
                decision = f"NO TRADE: net edge {net_edge:.4f} < threshold {min_threshold:.4f}{fv_label}"
            else:
                decision = f"NO TRADE: net edge {net_edge:.4f} > max threshold {max_threshold:.4f}{fv_label}"
        elif not confidence_ok:
            decision = f"NO TRADE: confidence {prediction.confidence:.3f} < min {self._config.confidence_min:.3f}{fv_label}"
        else:
            decision = f"TRADE: {side.upper()} edge={net_edge:.4f} (threshold {min_threshold:.4f}){fv_label}"
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
            "using_fair_value": using_fair_value,
            "strike_price": float(snapshot.strike_price) if snapshot.strike_price else None,
            "statistical_fair_value": snapshot.statistical_fair_value,
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
        if prediction.confidence < self._config.confidence_min:
            return None

        # Determine price to submit
        # Use model probability as basis, discounted by a fraction of the edge
        # to ensure we still get filled while capturing most of the edge.
        # CRITICAL: cap below the best ask to avoid crossing (post_only rejection).
        if side == "yes":
            best_bid = snapshot.orderbook.best_yes_bid
            best_ask = snapshot.orderbook.best_yes_ask

            if using_fair_value:
                # Thin book: anchor to actual orderbook levels, not fair value.
                # Fair value is used for edge detection only — pricing must
                # be grounded in real levels to avoid post_only cross.
                if best_bid is None or best_ask is None:
                    self.last_analysis["decision"] = "NO TRADE: thin book, insufficient YES levels"
                    self.last_analysis["passed"] = False
                    return None
                bid_f, ask_f = float(best_bid), float(best_ask)
                if ask_f - bid_f < 0.03:
                    self.last_analysis["decision"] = "NO TRADE: thin book, spread too tight"
                    self.last_analysis["passed"] = False
                    return None
                # Place conservatively: one tick above bid, capped well below ask
                price = bid_f + 0.01
                price = min(price, ask_f - 0.02)
            else:
                target_price = implied + raw_edge * 0.6
                if best_bid is not None:
                    price = max(float(best_bid) + 0.01, target_price)
                else:
                    price = target_price
                if best_ask is not None:
                    price = min(price, float(best_ask) - 0.01)

            suggested_price = f"{min(0.99, max(0.01, price)):.2f}"
        else:
            best_bid = snapshot.orderbook.best_no_bid
            best_no_ask = (
                Decimal("1") - snapshot.orderbook.best_yes_bid
                if snapshot.orderbook.best_yes_bid is not None
                else None
            )

            if using_fair_value:
                # Thin book: anchor to actual orderbook levels, not fair value.
                if best_bid is None or best_no_ask is None:
                    self.last_analysis["decision"] = "NO TRADE: thin book, insufficient NO levels"
                    self.last_analysis["passed"] = False
                    return None
                bid_f, ask_f = float(best_bid), float(best_no_ask)
                if ask_f - bid_f < 0.03:
                    self.last_analysis["decision"] = "NO TRADE: thin book, spread too tight"
                    self.last_analysis["passed"] = False
                    return None
                price = bid_f + 0.01
                price = min(price, ask_f - 0.02)
            else:
                target_price = (1.0 - implied) + raw_edge * 0.6
                if best_bid is not None:
                    price = max(float(best_bid) + 0.01, target_price)
                else:
                    price = target_price
                if best_no_ask is not None:
                    price = min(price, float(best_no_ask) - 0.01)

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
            fair_value=using_fair_value,
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
