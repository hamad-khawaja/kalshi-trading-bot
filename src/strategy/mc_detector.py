"""Monte Carlo signal detector — parallel, independent strategy."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import structlog

from src.config import StrategyConfig
from src.data.models import FeatureVector, MarketSnapshot, TradeSignal
from src.model.monte_carlo import MonteCarloSimulator
from src.risk.volatility import VolatilityTracker
from src.strategy.edge_detector import EdgeDetector

logger = structlog.get_logger()


class MCSignalDetector:
    """Detects trading opportunities using Monte Carlo simulation.

    Runs bootstrap (or GBM fallback) simulation to estimate P(YES),
    compares to market implied probability, and fires a signal when
    the divergence exceeds thresholds. Includes edge cap, regime-aware
    thresholds, and settlement confidence discount.
    """

    def __init__(
        self,
        config: StrategyConfig,
        vol_tracker: VolatilityTracker | None = None,
    ):
        self._config = config
        self._vol_tracker = vol_tracker
        self._simulator = MonteCarloSimulator(
            n_samples=config.mc_samples,
            drift_mode=config.mc_drift_mode,
            vol_multiplier=config.mc_vol_multiplier,
            min_bootstrap_returns=config.mc_bootstrap_min_returns,
        )

    def detect(
        self,
        snapshot: MarketSnapshot,
        features: FeatureVector,
    ) -> TradeSignal | None:
        """Run MC simulation and return a signal if edge is found.

        Args:
            snapshot: Current market data snapshot
            features: Computed feature vector

        Returns:
            TradeSignal with signal_type="monte_carlo", or None
        """
        cfg = self._config
        ttx = snapshot.time_to_expiry_seconds

        # Guard: TTX bounds
        if ttx < cfg.mc_min_ttx or ttx > cfg.mc_max_ttx:
            return None

        # Guard: need a strike price
        if snapshot.strike_price is None:
            return None

        # Guard: skip in extreme volatility regime
        if self._vol_tracker is not None and self._vol_tracker.current_regime == "extreme":
            logger.info(
                "mc_skipped_extreme_vol",
                ticker=snapshot.market_ticker,
                regime="extreme",
            )
            return None

        # Run MC simulation
        mc_prob, mc_confidence = self._simulator.estimate_probability(snapshot, features)

        # Get implied probability from orderbook
        implied = (
            float(snapshot.implied_yes_prob)
            if snapshot.implied_yes_prob is not None
            else None
        )
        if implied is None:
            return None

        # Guard: skip coin-flip markets
        if abs(implied - 0.50) < cfg.mc_min_implied_distance:
            return None

        # Determine direction and edge
        if mc_prob > implied:
            side = "yes"
            raw_edge = mc_prob - implied
            trade_price = implied
        else:
            side = "no"
            raw_edge = implied - mc_prob
            trade_price = 1.0 - implied

        # Cap edge to kill phantom edges from distributional mismatch
        raw_edge = min(raw_edge, cfg.mc_max_edge)

        # Guard: minimum entry price (block cheap lottery-ticket contracts)
        if trade_price < cfg.min_entry_price:
            return None

        # Compute fee drag (reuse EdgeDetector static method)
        fee_per_contract = EdgeDetector.compute_fee_dollars(1, trade_price, is_maker=True)
        fee_drag = float(fee_per_contract)
        net_edge = raw_edge - fee_drag

        # Regime-aware edge threshold
        min_edge = cfg.mc_min_edge
        if self._vol_tracker is not None and self._vol_tracker.current_regime == "high":
            min_edge *= 1.5

        # Guard: minimum edge
        if net_edge < min_edge:
            return None

        # Settlement confidence discount: reduce confidence when MC side
        # disagrees with settlement trend
        if side == "yes" and features.settlement_bias < -0.3:
            mc_confidence *= cfg.mc_settlement_discount
        elif side == "no" and features.settlement_bias > 0.3:
            mc_confidence *= cfg.mc_settlement_discount

        # Guard: minimum confidence
        if mc_confidence < cfg.mc_min_confidence:
            return None

        # Determine price from orderbook (same pattern as EdgeDetector)
        ob = snapshot.orderbook
        if side == "yes":
            best_bid = ob.best_yes_bid
            best_ask = ob.best_yes_ask
            if best_bid is not None and best_ask is not None:
                price = float(best_bid) + 0.01
                price = min(price, float(best_ask) - 0.01)
            elif best_bid is not None:
                price = float(best_bid) + 0.01
            else:
                price = trade_price
            suggested_price = f"{min(0.99, max(0.01, price)):.2f}"
        else:
            best_bid = ob.best_no_bid
            best_no_ask = (
                Decimal("1") - ob.best_yes_bid
                if ob.best_yes_bid is not None
                else None
            )
            if best_bid is not None and best_no_ask is not None:
                price = float(best_bid) + 0.01
                price = min(price, float(best_no_ask) - 0.01)
            elif best_bid is not None:
                price = float(best_bid) + 0.01
            else:
                price = trade_price
            suggested_price = f"{min(0.99, max(0.01, price)):.2f}"

        zone = EdgeDetector.classify_zone(trade_price)

        logger.info(
            "mc_signal_detected",
            ticker=snapshot.market_ticker,
            side=side,
            mc_prob=round(mc_prob, 4),
            implied=round(implied, 4),
            raw_edge=round(raw_edge, 4),
            net_edge=round(net_edge, 4),
            mc_confidence=round(mc_confidence, 4),
            ttx=round(ttx, 1),
            price=suggested_price,
        )

        return TradeSignal(
            market_ticker=snapshot.market_ticker,
            side=side,
            action="buy",
            raw_edge=round(raw_edge, 4),
            net_edge=round(net_edge, 4),
            model_probability=mc_prob,
            implied_probability=implied,
            confidence=mc_confidence,
            suggested_price_dollars=suggested_price,
            suggested_count=0,  # Filled in by position sizer
            timestamp=datetime.now(UTC),
            signal_type="monte_carlo",
            entry_zone=zone,
        )
