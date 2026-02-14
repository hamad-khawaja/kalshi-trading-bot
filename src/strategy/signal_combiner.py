"""Combines and prioritizes signals from multiple strategies."""

from __future__ import annotations

import structlog

from src.config import StrategyConfig
from src.data.models import FeatureVector, MarketSnapshot, PredictionResult, TradeSignal
from src.data.time_profile import TimeProfiler
from src.risk.volatility import VolatilityTracker
from src.strategy.edge_detector import EdgeDetector
from src.strategy.fomo_detector import FomoDetector
from src.strategy.market_maker import MarketMaker

logger = structlog.get_logger()


class SignalCombiner:
    """Combines directional and market-making signals with priority logic.

    Priority rules:
    1. Strong directional signal -> trade directionally only
    2. FOMO signal -> buy underpriced side during retail panic
    3. No directional/FOMO edge + wide spread -> market-making quotes
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
        self._fomo_detector = FomoDetector(config) if config.fomo_enabled else None
        self._market_maker = (
            MarketMaker(config) if config.use_market_maker else None
        )
        # Edge persistence: track consecutive cycles with same-side edge
        self._edge_streak: dict[str, tuple[str, int]] = {}  # ticker → (side, count)

    def evaluate(
        self,
        prediction: PredictionResult,
        snapshot: MarketSnapshot,
        current_position: int = 0,
        features: FeatureVector | None = None,
    ) -> list[TradeSignal]:
        """Evaluate all strategies and return prioritized signals.

        Args:
            prediction: Model probability estimate
            snapshot: Current market data snapshot
            current_position: Net YES contracts held (positive = long YES)
            features: Feature vector (needed for FOMO detection)

        Returns:
            List of trade signals to execute (may be empty)
        """
        # Time-to-expiry gate
        if snapshot.time_to_expiry_seconds < self.MIN_TIME_TO_TRADE_SECONDS:
            return []

        signals: list[TradeSignal] = []

        # 1. Check for directional edge (highest priority)
        directional = self._edge_detector.detect(prediction, snapshot)

        if directional is not None:
            # Trend guard: if all momentum timeframes agree on a direction,
            # don't trade against the trend. The model hovers near 0.50 and
            # generates fake edges against clear trends.
            if features is not None:
                momentums = [
                    features.momentum_15s,
                    features.momentum_60s,
                    features.momentum_180s,
                    features.momentum_600s,
                ]
                nonzero = [m for m in momentums if m != 0]
                if len(nonzero) >= 3:
                    all_negative = all(m < 0 for m in nonzero)
                    all_positive = all(m > 0 for m in nonzero)
                    if all_negative and directional.side == "yes":
                        logger.info(
                            "trend_guard_blocked",
                            ticker=snapshot.market_ticker,
                            side="yes",
                            reason="all_momentum_negative",
                            net_edge=directional.net_edge,
                        )
                        directional = None
                    elif all_positive and directional.side == "no":
                        logger.info(
                            "trend_guard_blocked",
                            ticker=snapshot.market_ticker,
                            side="no",
                            reason="all_momentum_positive",
                            net_edge=directional.net_edge,
                        )
                        directional = None

        if directional is not None:
            # Edge persistence: require N consecutive cycles with same-side edge
            ticker = snapshot.market_ticker
            required = self._config.edge_confirmation_cycles
            prev = self._edge_streak.get(ticker)
            if prev and prev[0] == directional.side:
                streak = prev[1] + 1
            else:
                streak = 1
            self._edge_streak[ticker] = (directional.side, streak)

            if streak < required:
                logger.info(
                    "edge_streak_building",
                    ticker=ticker,
                    side=directional.side,
                    streak=streak,
                    required=required,
                    net_edge=directional.net_edge,
                )
                # Don't emit directional signal yet — fall through to FOMO/MM
                directional = None
            else:
                signals.append(directional)
                logger.debug(
                    "signal_directional",
                    ticker=ticker,
                    side=directional.side,
                    net_edge=directional.net_edge,
                    streak=streak,
                )
                return signals
        else:
            # No directional edge this cycle — reset streak for this market
            ticker = snapshot.market_ticker
            if ticker in self._edge_streak:
                del self._edge_streak[ticker]

        # 2. Check for FOMO signal (second priority)
        if self._fomo_detector is not None and features is not None:
            fomo_signal = self._fomo_detector.detect(prediction, features, snapshot)
            if fomo_signal is not None:
                signals.append(fomo_signal)
                logger.debug(
                    "signal_fomo",
                    ticker=snapshot.market_ticker,
                    side=fomo_signal.side,
                    net_edge=fomo_signal.net_edge,
                )
                return signals

        # 3. No directional or FOMO edge — try market making (if session allows)
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
