"""Combines and prioritizes signals from multiple strategies."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import structlog

from src.config import StrategyConfig
from src.data.models import FeatureVector, MarketSnapshot, PredictionResult, TradeSignal
from src.data.time_profile import TimeProfiler
from src.risk.volatility import VolatilityTracker
from src.strategy.edge_detector import EdgeDetector
from src.strategy.fair_value import compute_fair_value_from_prices
from src.strategy.fomo_detector import FomoDetector
from src.strategy.market_maker import MarketMaker
from src.strategy.trend_continuation_detector import TrendContinuationDetector

logger = structlog.get_logger()


class SignalCombiner:
    """Combines directional and market-making signals with priority logic.

    Priority rules:
    1. Strong directional signal -> trade directionally (MM can run alongside)
    2. Trend continuation -> early-window entry during persistent settlement streaks
    3. FOMO signal -> buy underpriced side during retail panic
    4. Certainty scalp -> near-certain outcome in last 3 min
    5. Settlement ride -> late-window hold-to-settlement
    6. Market making -> spread capture (standalone or alongside directional)
    7. Time-to-expiry filter: no new trades within 60s of expiry
    """

    MIN_TIME_TO_TRADE_SECONDS = 60.0  # No new positions < 60s to expiry
    MM_CANCEL_BEFORE_EXPIRY_SECONDS = 30.0  # Cancel MM orders 30s before expiry

    def __init__(
        self,
        config: StrategyConfig,
        vol_tracker: VolatilityTracker | None = None,
        time_profiler: TimeProfiler | None = None,
        settlement_history: dict | None = None,
    ):
        self._config = config
        self._vol_tracker = vol_tracker
        self._time_profiler = time_profiler
        self._edge_detector = EdgeDetector(
            config, vol_tracker=vol_tracker, time_profiler=time_profiler
        )
        self._fomo_detector = FomoDetector(config)
        self._market_maker = MarketMaker(config, vol_tracker=vol_tracker)
        self._trend_detector = TrendContinuationDetector(
            config, settlement_history if settlement_history is not None else {}
        )
        # Edge persistence: track consecutive cycles with same-side edge
        self._edge_streak: dict[str, tuple[str, int]] = {}  # ticker → (side, count)
        # Phase 1 overreaction state: ticker → {direction, extreme}
        self._phase1_state: dict[str, dict] = {}
        # Block reasons from last evaluate() call (for logging)
        self.last_block_reasons: list[str] = []
        # Simulated time for backtest quiet hours
        self._simulated_time: datetime | None = None
        self.quiet_hours_override: bool = False
        # Throttle certainty scalp skip logging: ticker → last_reason
        self._last_cert_skip: dict[str, str] = {}

    def set_simulated_time(self, dt: datetime | None) -> None:
        """Set simulated time for backtest. Pass None to use real time."""
        self._simulated_time = dt

    def _get_current_est_hour(self) -> int:
        """Get current EST hour, using simulated time if set."""
        if self._simulated_time is not None:
            return self._simulated_time.astimezone(ZoneInfo("America/New_York")).hour
        return datetime.now(ZoneInfo("America/New_York")).hour

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
        # Reset block reasons for this cycle
        self.last_block_reasons = []

        # Time-to-expiry gate
        if snapshot.time_to_expiry_seconds < self.MIN_TIME_TO_TRADE_SECONDS:
            self.last_block_reasons.append("ttx_too_low")
            logger.debug(
                "ttx_gate_blocked",
                ticker=snapshot.market_ticker,
                ttx=round(snapshot.time_to_expiry_seconds, 1),
                min_ttx=self.MIN_TIME_TO_TRADE_SECONDS,
            )
            return []

        signals: list[TradeSignal] = []

        # Phase timing logic
        phase = snapshot.window_phase
        phase_enabled = self._config.phase_filter_enabled

        # Track Phase 1 overreaction state for bounce-back detection
        if phase_enabled and self._config.overreaction_enabled and features is not None:
            ticker = snapshot.market_ticker
            if phase == 1:
                # Record direction and extremity during observation phase
                direction = 1 if features.momentum_180s > 0 else (-1 if features.momentum_180s < 0 else 0)
                implied = float(snapshot.implied_yes_prob) if snapshot.implied_yes_prob is not None else 0.5
                extreme_thresh = self._config.overreaction_extreme_threshold
                extreme = implied < extreme_thresh or implied > (1.0 - extreme_thresh)
                self._phase1_state[ticker] = {
                    "direction": direction,
                    "extreme": extreme,
                }
            elif phase > 2 and ticker in self._phase1_state:
                # Clean up state past Phase 2
                del self._phase1_state[ticker]

        # Quiet hours: skip directional trading during low-volume EST hours
        quiet_hours_active = False
        if self._config.quiet_hours_enabled and self._config.quiet_hours_est and not self.quiet_hours_override:
            current_hour = self._get_current_est_hour()
            if current_hour in self._config.quiet_hours_est:
                quiet_hours_active = True
                self.last_block_reasons.append(f"quiet_hours(hr={current_hour})")
                logger.info(
                    "quiet_hours_blocked",
                    ticker=snapshot.market_ticker,
                    hour_est=current_hour,
                )

        # 1. Check for directional edge (highest priority)
        # Skip directional if globally disabled via config toggle
        directional_disabled = not self._config.directional_enabled
        # Skip directional for disabled assets (MM-only mode)
        if self._config.asset_directional_disabled:
            ticker_upper = snapshot.market_ticker.upper()
            for asset in self._config.asset_directional_disabled:
                if asset.upper() in ticker_upper:
                    directional_disabled = True
                    break

        # Beta-led override: allow directional for disabled assets when BTC
        # gives a strong lead signal (btc_beta_signal on the features)
        btc_beta_override = False
        if directional_disabled and features is not None:
            btc_beta_threshold = self._config.btc_beta_min_signal
            if abs(features.btc_beta_signal) >= btc_beta_threshold:
                btc_beta_override = True
                logger.info(
                    "btc_beta_override_directional",
                    ticker=snapshot.market_ticker,
                    btc_beta_signal=round(features.btc_beta_signal, 4),
                )

        directional = None if ((directional_disabled and not btc_beta_override) or quiet_hours_active) else self._edge_detector.detect(prediction, snapshot)

        if directional is not None:
            # Trend guard: if majority of momentum timeframes agree on a
            # direction, don't trade against the trend. Uses 60s/180s/600s
            # (skip 15s — too noisy) with a magnitude threshold to ignore
            # near-zero values that are effectively noise.
            if features is not None and self._config.trend_guard_enabled:
                trend_min_magnitude = 0.0005  # Hybrid: ~0.05% move (was 0.0001, filters noise)
                momentums = [
                    features.momentum_60s,
                    features.momentum_180s,
                    features.momentum_600s,
                ]
                positive = sum(1 for m in momentums if m > trend_min_magnitude)
                negative = sum(1 for m in momentums if m < -trend_min_magnitude)
                # Settlement override: if recent settlements contradict the
                # momentum-based block, the cross-window trend trumps
                # intra-window noise.
                settlement_override_threshold = 0.3
                if negative >= 2 and directional.side == "yes":
                    if features.settlement_bias > settlement_override_threshold:
                        logger.info(
                            "trend_guard_settlement_override",
                            ticker=snapshot.market_ticker,
                            side="yes",
                            settlement_bias=round(features.settlement_bias, 4),
                            negative_count=negative,
                        )
                    else:
                        self.last_block_reasons.append(
                            f"trend_guard(yes vs {negative}neg)"
                        )
                        logger.info(
                            "trend_guard_blocked",
                            ticker=snapshot.market_ticker,
                            side="yes",
                            reason="majority_momentum_negative",
                            negative_count=negative,
                            net_edge=directional.net_edge,
                        )
                        directional = None
                elif positive >= 2 and directional.side == "no":
                    if features.settlement_bias < -settlement_override_threshold:
                        logger.info(
                            "trend_guard_settlement_override",
                            ticker=snapshot.market_ticker,
                            side="no",
                            settlement_bias=round(features.settlement_bias, 4),
                            positive_count=positive,
                        )
                    else:
                        self.last_block_reasons.append(
                            f"trend_guard(no vs {positive}pos)"
                        )
                        logger.info(
                            "trend_guard_blocked",
                            ticker=snapshot.market_ticker,
                            side="no",
                            reason="majority_momentum_positive",
                            positive_count=positive,
                            net_edge=directional.net_edge,
                        )
                        directional = None

        # Path efficiency filter: block fakeout entries with choppy price paths
        if (
            directional is not None
            and features is not None
            and self._config.ppe_filter_enabled
        ):
            ppe = features.path_efficiency_300s
            if ppe < self._config.ppe_min_threshold:
                self.last_block_reasons.append(
                    f"ppe_filter({ppe:.2f}<{self._config.ppe_min_threshold})"
                )
                logger.info(
                    "ppe_filter_blocked",
                    ticker=snapshot.market_ticker,
                    side=directional.side,
                    ppe_300s=round(ppe, 4),
                    ppe_180s=round(features.path_efficiency_180s, 4),
                    ppe_60s=round(features.path_efficiency_60s, 4),
                    threshold=self._config.ppe_min_threshold,
                    net_edge=directional.net_edge,
                )
                directional = None

        # Phase gating for directional signals
        if directional is not None and phase_enabled:
            ticker = snapshot.market_ticker
            if phase == 1:
                # Observation phase: no directional trades
                self.last_block_reasons.append("phase1_observation")
                logger.info(
                    "phase_blocked_directional",
                    ticker=ticker,
                    phase=1,
                    side=directional.side,
                    net_edge=directional.net_edge,
                )
                directional = None
            elif phase == 2:
                # Confirmation phase: only allow with bounce-back
                p1 = self._phase1_state.get(ticker)
                bounce_back = False
                if p1 and features is not None and p1["direction"] != 0:
                    reversal_thresh = self._config.overreaction_momentum_reversal_threshold
                    # Check if 60s momentum reversed from Phase 1 direction
                    if p1["direction"] > 0 and features.momentum_60s < -reversal_thresh:
                        bounce_back = True
                    elif p1["direction"] < 0 and features.momentum_60s > reversal_thresh:
                        bounce_back = True
                if bounce_back:
                    logger.info(
                        "phase2_bounce_back_confirmed",
                        ticker=ticker,
                        side=directional.side,
                        net_edge=directional.net_edge,
                        p1_direction=p1["direction"] if p1 else 0,
                        mom_60s=features.momentum_60s if features else 0,
                    )
                else:
                    self.last_block_reasons.append("phase2_no_bounce")
                    logger.info(
                        "phase2_no_bounce_back",
                        ticker=ticker,
                        side=directional.side,
                        net_edge=directional.net_edge,
                    )
                    directional = None
            elif phase == 4:
                # Late phase: tighten thresholds
                min_edge_late = self._config.min_edge_threshold * self._config.phase_late_edge_multiplier
                min_conf_late = self._config.confidence_min + self._config.phase_late_confidence_boost
                if directional.net_edge < min_edge_late or directional.confidence < min_conf_late:
                    self.last_block_reasons.append("phase4_tightened")
                    logger.info(
                        "phase_late_tightened",
                        ticker=ticker,
                        phase=4,
                        side=directional.side,
                        net_edge=directional.net_edge,
                        min_edge_late=round(min_edge_late, 4),
                        confidence=directional.confidence,
                        min_conf_late=round(min_conf_late, 4),
                    )
                    directional = None
            elif phase == 5:
                # Final phase (last 60s): no new entries — contracts are lottery
                # tickets with unpredictable resolution
                self.last_block_reasons.append("phase5_final")
                logger.info(
                    "phase5_blocked",
                    ticker=ticker,
                    phase=5,
                    side=directional.side,
                    net_edge=directional.net_edge,
                )
                directional = None

        # Track whether a confirmed directional signal exists (for MM filtering)
        confirmed_directional_side: str | None = None

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
                self.last_block_reasons.append(
                    f"edge_streak({streak}/{required})"
                )
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
                if features is not None:
                    directional.path_efficiency = max(
                        features.path_efficiency_180s,
                        features.path_efficiency_300s,
                    )
                signals.append(directional)
                confirmed_directional_side = directional.side
                logger.debug(
                    "signal_directional",
                    ticker=ticker,
                    side=directional.side,
                    net_edge=directional.net_edge,
                    streak=streak,
                )
                # Fall through — MM can still run alongside directional
        else:
            # No directional edge this cycle — reset streak for this market
            ticker = snapshot.market_ticker
            if ticker in self._edge_streak:
                del self._edge_streak[ticker]

        # 2. Trend continuation / FOMO / certainty / settlement — only when no directional signal
        if not signals:
            # 2a. Check for trend continuation (enters early in window during streaks)
            if (
                self._config.trend_continuation_enabled
                and features is not None
                and not quiet_hours_active
            ):
                # Block trend continuation in extreme vol regime
                if (
                    self._config.tc_extreme_vol_filter_enabled
                    and self._vol_tracker is not None
                    and self._vol_tracker.current_regime == "extreme"
                ):
                    logger.info(
                        "tc_extreme_vol_blocked",
                        ticker=snapshot.market_ticker,
                        vol_regime="extreme",
                    )
                else:
                    trend_signal = self._trend_detector.detect(
                        prediction, features, snapshot,
                        current_position=current_position,
                    )
                    if trend_signal is not None:
                        signals.append(trend_signal)
                        logger.debug(
                            "signal_trend_continuation",
                            ticker=snapshot.market_ticker,
                            side=trend_signal.side,
                            net_edge=trend_signal.net_edge,
                        )
                        return signals

            # 2b. Check for FOMO signal
            if self._config.fomo_enabled and features is not None:
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

            # 2b. Certainty scalp: near-certain outcome in last 3 min, bet large
            if self._config.certainty_scalp_enabled and not quiet_hours_active:
                certainty = self._evaluate_certainty_scalp(prediction, snapshot)
                if certainty is not None:
                    signals.append(certainty)
                    return signals

            # 2c. Settlement ride: fallback for late-window entry (hold to settlement)
            if self._config.settlement_ride_enabled and not quiet_hours_active:
                settlement_ride = self._evaluate_settlement_ride(prediction, snapshot)
                if settlement_ride is not None:
                    signals.append(settlement_ride)
                    return signals

        # 3. Market making — always runs (alongside directional or standalone)
        mm_allowed = True
        if self._time_profiler is not None:
            session = self._time_profiler.get_current_session()
            mm_allowed = self._time_profiler.should_market_make(session)

        # Per-asset MM disable
        if mm_allowed and self._config.asset_market_maker_disabled:
            ticker_upper = snapshot.market_ticker.upper()
            for asset in self._config.asset_market_maker_disabled:
                if asset.upper() in ticker_upper:
                    mm_allowed = False
                    break

        if self._config.use_market_maker and mm_allowed:
            # When directional signal exists, only MM on the opposite side
            mm_signals = self._market_maker.generate_quotes(
                prediction, snapshot, current_position,
                directional_side=confirmed_directional_side,
            )
            if mm_signals:
                signals.extend(mm_signals)
                logger.debug(
                    "signal_market_making",
                    ticker=snapshot.market_ticker,
                    num_quotes=len(mm_signals),
                    alongside_directional=confirmed_directional_side is not None,
                )
            elif not signals:
                # MM returned nothing — check why
                mm_vol = self._market_maker._vol_tracker
                if (
                    self._config.mm_vol_filter_enabled
                    and mm_vol is not None
                    and mm_vol.current_regime == "extreme"
                ):
                    self.last_block_reasons.append("mm_extreme_vol")

        return signals

    def _evaluate_settlement_ride(
        self,
        prediction: PredictionResult,
        snapshot: MarketSnapshot,
    ) -> TradeSignal | None:
        """Evaluate settlement-ride entry: late in window, hold to settlement.

        Only activates after min_elapsed_seconds when implied prob is far enough
        from 0.50 (not a coin flip) and the edge detector finds a directional edge.
        """
        cfg = self._config
        if not cfg.settlement_ride_enabled:
            return None

        # Per-asset settlement ride disable
        if cfg.asset_settlement_ride_disabled:
            ticker_upper = snapshot.market_ticker.upper()
            for asset in cfg.asset_settlement_ride_disabled:
                if asset.upper() in ticker_upper:
                    return None

        # Must be late enough in the window
        if snapshot.time_elapsed_seconds < cfg.settlement_ride_min_elapsed_seconds:
            return None

        # Need at least 60s to expiry (same as MIN_TIME_TO_TRADE_SECONDS)
        if snapshot.time_to_expiry_seconds <= self.MIN_TIME_TO_TRADE_SECONDS:
            return None

        # Resolve per-asset overrides for settlement ride thresholds
        ticker_upper = snapshot.market_ticker.upper()
        min_implied_distance = cfg.settlement_ride_min_implied_distance
        min_edge = cfg.settlement_ride_min_edge
        for asset, val in cfg.asset_settlement_ride_min_implied_distance.items():
            if asset.upper() in ticker_upper:
                min_implied_distance = val
                break
        for asset, val in cfg.asset_settlement_ride_min_edge.items():
            if asset.upper() in ticker_upper:
                min_edge = val
                break

        # Skip coin-flip markets: implied prob too close to 0.50
        implied = float(snapshot.implied_yes_prob) if snapshot.implied_yes_prob is not None else 0.5
        distance_from_half = abs(implied - 0.50)
        if distance_from_half < min_implied_distance:
            return None

        # Model-market direction agreement: block when model ACTIVELY disagrees
        # with market lean. When model is neutral (within 2% of 0.50), trust the
        # market direction — settlement ride only fires when implied is far from
        # 0.50, so market consensus is strong. Without this tolerance, settlement
        # ride can never fire because directional consumes all non-neutral model
        # predictions first (catch-22).
        model_neutral_tolerance = 0.02
        model_neutral = abs(prediction.probability_yes - 0.50) <= model_neutral_tolerance
        model_bullish = prediction.probability_yes > 0.50
        market_bullish = implied > 0.50
        if not model_neutral and model_bullish != market_bullish:
            logger.info(
                "settlement_ride_direction_mismatch",
                ticker=snapshot.market_ticker,
                model_prob=round(prediction.probability_yes, 4),
                implied=round(implied, 4),
                model_side="yes" if model_bullish else "no",
                market_side="yes" if market_bullish else "no",
            )
            return None

        # Re-use edge detector for directional signal (ignores phase gating / streak)
        directional = self._edge_detector.detect(prediction, snapshot)
        if directional is None:
            return None

        if directional.net_edge < min_edge:
            return None

        # Convert to settlement_ride signal
        logger.info(
            "settlement_ride_signal",
            ticker=snapshot.market_ticker,
            side=directional.side,
            net_edge=directional.net_edge,
            implied_distance=round(distance_from_half, 4),
            time_elapsed=round(snapshot.time_elapsed_seconds, 1),
            time_to_expiry=round(snapshot.time_to_expiry_seconds, 1),
        )
        return TradeSignal(
            market_ticker=directional.market_ticker,
            side=directional.side,
            action=directional.action,
            raw_edge=directional.raw_edge,
            net_edge=directional.net_edge,
            model_probability=directional.model_probability,
            implied_probability=directional.implied_probability,
            confidence=directional.confidence,
            suggested_price_dollars=directional.suggested_price_dollars,
            suggested_count=directional.suggested_count,
            timestamp=directional.timestamp,
            signal_type="settlement_ride",
            entry_zone=directional.entry_zone,
            post_only=directional.post_only,
        )

    def _evaluate_certainty_scalp(
        self,
        prediction: PredictionResult,
        snapshot: MarketSnapshot,
    ) -> TradeSignal | None:
        """Evaluate certainty-scalp entry: vol-mispricing in last 4 min.

        Uses Black-Scholes fair value (from realized vol) as primary signal.
        When the mathematical probability is much higher than what the market
        implies, buys the near-certain side. Holds to settlement (free exit).
        Fees are minimal at extreme prices since fee proportional to p*(1-p).

        Two paths to trigger:
        1. Vol-based: fair_value >= 0.95 AND implied >= 0.85 AND low/normal vol
        2. Legacy:    model >= 0.80 AND implied >= 0.85 (original certainty scalp)
        """
        cfg = self._config
        if not cfg.certainty_scalp_enabled:
            return None

        ticker = snapshot.market_ticker
        ttx = snapshot.time_to_expiry_seconds
        if ttx > cfg.certainty_scalp_max_ttx or ttx <= cfg.certainty_scalp_min_ttx:
            return None

        implied = float(snapshot.implied_yes_prob) if snapshot.implied_yes_prob is not None else 0.5
        model_prob = prediction.probability_yes
        min_prob = cfg.certainty_scalp_min_implied_prob
        side: str | None = None
        trigger = "legacy"

        # Path 1: Vol-based fair value confirmation (preferred)
        # Requires: strike, spot price, price history, and low/normal vol regime
        vol_regime_ok = (
            self._vol_tracker is None
            or self._vol_tracker.current_regime in ("low", "normal")
        )
        fair_value_prob: float | None = None

        if (
            vol_regime_ok
            and snapshot.strike_price is not None
            and snapshot.spot_price is not None
            and snapshot.spot_prices_5min
        ):
            price_arr = np.array(
                [float(p) for p in snapshot.spot_prices_5min], dtype=np.float64
            )
            fair_value_prob = compute_fair_value_from_prices(
                spot_price=float(snapshot.spot_price),
                strike_price=float(snapshot.strike_price),
                price_history=price_arr,
                time_to_expiry_seconds=ttx,
            )

            if fair_value_prob is not None:
                # Vol-based: fair value says near-certain AND market is extreme
                min_fv = cfg.certainty_scalp_min_fair_value_prob
                if fair_value_prob >= min_fv and implied >= min_prob:
                    side = "yes"
                    trigger = "vol_based"
                elif fair_value_prob <= (1.0 - min_fv) and implied <= (1.0 - min_prob):
                    side = "no"
                    trigger = "vol_based"

        # Path 2: Legacy model-based confirmation (fallback)
        if side is None:
            if implied >= min_prob and model_prob >= cfg.certainty_scalp_min_model_prob:
                side = "yes"
                trigger = "legacy"
            elif implied <= (1.0 - min_prob) and model_prob <= (1.0 - cfg.certainty_scalp_min_model_prob):
                side = "no"
                trigger = "legacy"

        if side is None:
            reason = "no_trigger"
            if self._last_cert_skip.get(ticker) != reason:
                self._last_cert_skip[ticker] = reason
                logger.info(
                    "cert_scalp_skipped", ticker=ticker, reason=reason,
                    implied=round(implied, 4), model_prob=round(model_prob, 4),
                    fair_value_prob=round(fair_value_prob, 4) if fair_value_prob else None,
                    ttx=round(ttx, 1),
                )
            return None

        # Spot price confirmation: verify spot is well past strike
        if snapshot.strike_price is not None and snapshot.spot_price is not None:
            strike = float(snapshot.strike_price)
            spot = float(snapshot.spot_price)
            if strike > 0:
                distance_pct = (spot - strike) / strike
                min_dist = cfg.certainty_scalp_min_spot_distance_pct
                if side == "yes" and distance_pct < min_dist:
                    reason = "spot_too_close_yes"
                    if self._last_cert_skip.get(ticker) != reason:
                        self._last_cert_skip[ticker] = reason
                        logger.info(
                            "cert_scalp_skipped", ticker=ticker, reason=reason,
                            side=side, distance_pct=round(distance_pct, 5),
                            min_dist=min_dist,
                        )
                    return None
                if side == "no" and distance_pct > -min_dist:
                    reason = "spot_too_close_no"
                    if self._last_cert_skip.get(ticker) != reason:
                        self._last_cert_skip[ticker] = reason
                        logger.info(
                            "cert_scalp_skipped", ticker=ticker, reason=reason,
                            side=side, distance_pct=round(distance_pct, 5),
                            min_dist=min_dist,
                        )
                    return None

        # Compute edge from vol-based fair value or fall back to edge detector
        if trigger == "vol_based" and fair_value_prob is not None:
            # Edge = fair_value_prob - implied (how much market underprices certainty)
            if side == "yes":
                trade_price = implied
                raw_edge = fair_value_prob - implied
            else:
                trade_price = 1.0 - implied
                raw_edge = (1.0 - fair_value_prob) - (1.0 - implied)

            fee_drag = float(
                EdgeDetector.compute_fee_dollars(
                    count=1, price_dollars=trade_price, is_maker=False
                )
            )
            net_edge = raw_edge - fee_drag

            if net_edge < cfg.certainty_scalp_min_edge:
                reason = "edge_too_low_vol"
                if self._last_cert_skip.get(ticker) != reason:
                    self._last_cert_skip[ticker] = reason
                    logger.info(
                        "cert_scalp_skipped", ticker=ticker, reason=reason,
                        side=side, net_edge=round(net_edge, 4),
                        min_edge=cfg.certainty_scalp_min_edge,
                    )
                return None

            # Price: use best available level
            if side == "yes":
                best_ask = snapshot.orderbook.best_yes_ask
                if best_ask is None:
                    return None
                price = str(min(float(best_ask), max(0.01, trade_price)))
            else:
                best_no_ask = snapshot.orderbook.best_no_ask
                if best_no_ask is None:
                    return None
                price = str(min(float(best_no_ask), max(0.01, trade_price)))

            self._last_cert_skip.pop(ticker, None)
            logger.info(
                "certainty_scalp_signal",
                ticker=ticker,
                side=side,
                trigger=trigger,
                net_edge=round(net_edge, 4),
                fair_value_prob=round(fair_value_prob, 4),
                implied_prob=round(implied, 4),
                vol_regime=self._vol_tracker.current_regime if self._vol_tracker else "unknown",
                time_to_expiry=round(ttx, 1),
            )
            return TradeSignal(
                market_ticker=snapshot.market_ticker,
                side=side,
                action="buy",
                raw_edge=round(raw_edge, 4),
                net_edge=round(net_edge, 4),
                model_probability=fair_value_prob,
                implied_probability=implied,
                confidence=prediction.confidence,
                suggested_price_dollars=price,
                suggested_count=1,  # Sized by position sizer
                timestamp=snapshot.timestamp,
                signal_type="certainty_scalp",
                entry_zone=EdgeDetector.classify_zone(trade_price),
                post_only=False,  # Taker — need guaranteed fill in final minutes
            )

        # Legacy path: use edge detector for price/fee calculation
        directional = self._edge_detector.detect(prediction, snapshot)
        if directional is None:
            return None

        if directional.side != side:
            return None

        if directional.net_edge < cfg.certainty_scalp_min_edge:
            return None

        logger.info(
            "certainty_scalp_signal",
            ticker=snapshot.market_ticker,
            side=side,
            trigger=trigger,
            net_edge=directional.net_edge,
            implied_prob=round(implied, 4),
            model_prob=round(model_prob, 4),
            time_to_expiry=round(ttx, 1),
        )
        return TradeSignal(
            market_ticker=directional.market_ticker,
            side=side,
            action=directional.action,
            raw_edge=directional.raw_edge,
            net_edge=directional.net_edge,
            model_probability=directional.model_probability,
            implied_probability=directional.implied_probability,
            confidence=directional.confidence,
            suggested_price_dollars=directional.suggested_price_dollars,
            suggested_count=directional.suggested_count,
            timestamp=directional.timestamp,
            signal_type="certainty_scalp",
            entry_zone=directional.entry_zone,
            post_only=False,  # Taker — need guaranteed fill in final minutes
        )
