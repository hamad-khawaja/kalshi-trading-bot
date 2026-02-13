"""Combines and prioritizes signals from multiple strategies."""

from __future__ import annotations

import structlog

from src.config import StrategyConfig
from src.data.models import MarketSnapshot, PredictionResult, TradeSignal
from src.data.time_profile import TimeProfiler
from src.risk.volatility import VolatilityTracker
from src.strategy.edge_detector import EdgeDetector
from src.strategy.market_maker import MarketMaker

logger = structlog.get_logger()


class SignalCombiner:
    """Combines directional and market-making signals with priority logic.

    Priority rules:
    1. Strong directional signal -> trade directionally only
    2. No directional edge + wide spread -> market-making quotes
    3. Both -> directional only (avoid conflicting positions)
    4. Neither -> no signals
    5. Time-to-expiry filter: no new trades within 60s of expiry
    """

    MIN_TIME_TO_TRADE_SECONDS = 60.0  # No new positions < 60s to expiry
    MM_CANCEL_BEFORE_EXPIRY_SECONDS = 30.0  # Cancel MM orders 30s before expiry

    def __init__(
        self,
        config: StrategyConfig,
        vol_tracker: VolatilityTracker | None = None,
        time_profiler: TimeProfiler | None = None,
    ):
        self._config = config
        self._time_profiler = time_profiler
        self._edge_detector = EdgeDetector(
            config, vol_tracker=vol_tracker, time_profiler=time_profiler
        )
        self._market_maker = (
            MarketMaker(config) if config.use_market_maker else None
        )

    def evaluate(
        self,
        prediction: PredictionResult,
        snapshot: MarketSnapshot,
        current_position: int = 0,
    ) -> list[TradeSignal]:
        """Evaluate all strategies and return prioritized signals.

        Args:
            prediction: Model probability estimate
            snapshot: Current market data snapshot
            current_position: Net YES contracts held (positive = long YES)

        Returns:
            List of trade signals to execute (may be empty)
        """
        # Time-to-expiry gate
        if snapshot.time_to_expiry_seconds < self.MIN_TIME_TO_TRADE_SECONDS:
            return []

        signals: list[TradeSignal] = []

        # 1. Check for directional edge
        directional = self._edge_detector.detect(prediction, snapshot)

        if directional is not None:
            # Strong directional signal — skip market making
            signals.append(directional)
            logger.debug(
                "signal_directional",
                ticker=snapshot.market_ticker,
                side=directional.side,
                net_edge=directional.net_edge,
            )
            return signals

        # 2. No directional edge — try market making (if session allows)
        mm_allowed = True
        if self._time_profiler is not None:
            session = self._time_profiler.get_current_session()
            mm_allowed = self._time_profiler.should_market_make(session)

        if self._market_maker is not None and mm_allowed:
            mm_signals = self._market_maker.generate_quotes(
                prediction, snapshot, current_position
            )
            if mm_signals:
                signals.extend(mm_signals)
                logger.debug(
                    "signal_market_making",
                    ticker=snapshot.market_ticker,
                    num_quotes=len(mm_signals),
                )

        return signals
