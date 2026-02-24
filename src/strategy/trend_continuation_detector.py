"""Trend continuation: enter early in window when recent settlements show a persistent trend.

Core thesis:
- When BTC grinds persistently in one direction across multiple 15-min windows,
  early prices (phase 1-2) are still near 50/50 — a perfect entry point.
- Settlement history (last N windows all settled same direction) provides the signal.
- Enters on the continuation side before the market moves to extremes.

Safety guards:
- Momentum confirmation: skip if current-window momentum fights the streak.
- One entry per market: no accumulation into a losing position.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from src.config import StrategyConfig
from src.data.models import FeatureVector, MarketSnapshot, PredictionResult, TradeSignal
from src.strategy.edge_detector import EdgeDetector

logger = structlog.get_logger()


class TrendContinuationDetector:
    """Detects persistent settlement trends and generates early-window signals.

    When the last N settlements for an asset all resolved the same way
    (all YES or all NO), enters on the continuation side during phase 1-2
    when prices are still near 50/50.
    """

    def __init__(
        self,
        config: StrategyConfig,
        settlement_history: dict[str, list[dict]],
    ):
        self._config = config
        self._settlement_history = settlement_history  # shared ref from DashboardState
        self.last_analysis: dict = {}
        # Track which markets we've already entered this window to prevent accumulation
        self._entered_markets: set[str] = set()

    def detect(
        self,
        prediction: PredictionResult,
        features: FeatureVector,
        snapshot: MarketSnapshot,
        current_position: int = 0,
    ) -> TradeSignal | None:
        """Detect trend continuation opportunity and generate signal."""
        if not self._config.trend_continuation_enabled:
            self.last_analysis = {"enabled": False}
            return None

        cfg = self._config
        ticker = snapshot.market_ticker

        # Phase gate: only fire during early phases
        if snapshot.window_phase > cfg.trend_continuation_max_phase:
            # Clean up entered_markets when we move past entry phases
            self._entered_markets.discard(ticker)
            self.last_analysis = {
                "decision": (
                    f"NO TREND: phase {snapshot.window_phase} "
                    f"> max {cfg.trend_continuation_max_phase}"
                ),
            }
            return None

        # One entry per market: don't accumulate into same window
        if current_position != 0 or ticker in self._entered_markets:
            self.last_analysis = {
                "decision": (
                    f"NO TREND: already entered {ticker} "
                    f"(pos={current_position}, tracked={ticker in self._entered_markets})"
                ),
            }
            return None

        # Extract asset symbol from ticker
        asset_symbol = self._extract_asset_symbol(ticker)

        # Check settlement history for streak
        history = self._settlement_history.get(asset_symbol, [])
        min_streak = cfg.trend_continuation_min_streak
        if len(history) < min_streak:
            self.last_analysis = {
                "decision": (
                    f"NO TREND: only {len(history)} settlements "
                    f"< min_streak {min_streak}"
                ),
            }
            return None

        # Compute full streak length: walk from most recent, count consecutive same results
        # History is ordered most-recent-first from the API
        first_result = history[0].get("result")
        streak_length = 0
        for entry in history:
            if entry.get("result") == first_result:
                streak_length += 1
            else:
                break

        if streak_length < min_streak:
            self.last_analysis = {
                "decision": (
                    f"NO TREND: streak {streak_length} "
                    f"< min_streak {min_streak}"
                ),
            }
            return None

        streak_direction = first_result  # "yes" or "no"
        if streak_direction not in ("yes", "no"):
            self.last_analysis = {
                "decision": f"NO TREND: unknown result type '{streak_direction}'",
            }
            return None

        # Momentum confirmation: current-window momentum must agree with streak
        # If streak says NO (price dropping) but 60s momentum is positive (price rising),
        # skip — current window is reversing.
        mom_60 = features.momentum_60s
        mom_threshold = cfg.trend_continuation_momentum_threshold
        if streak_direction == "no" and mom_60 > mom_threshold:
            self.last_analysis = {
                "decision": (
                    f"NO TREND: momentum fighting streak "
                    f"(streak=no, mom_60s={mom_60:+.5f} > +{mom_threshold})"
                ),
            }
            return None
        if streak_direction == "yes" and mom_60 < -mom_threshold:
            self.last_analysis = {
                "decision": (
                    f"NO TREND: momentum fighting streak "
                    f"(streak=yes, mom_60s={mom_60:+.5f} < -{mom_threshold})"
                ),
            }
            return None

        # Technical confirmation gate for extended streaks (3+)
        ext_thresh = cfg.trend_continuation_extended_streak_threshold
        if streak_length >= ext_thresh:
            passes, details = self._check_technical_confirmation(
                features, streak_direction, cfg
            )
            if not passes:
                self.last_analysis = {
                    "decision": (
                        f"NO TREND: technical confirmation failed "
                        f"(streak={streak_length}, {details})"
                    ),
                    "streak_length": streak_length,
                    "streak_direction": streak_direction,
                }
                logger.info(
                    "trend_technical_confirmation_failed",
                    ticker=ticker,
                    streak_direction=streak_direction,
                    streak_length=streak_length,
                    **details,
                )
                return None
            logger.info(
                "trend_technical_confirmation_passed",
                ticker=ticker,
                streak_direction=streak_direction,
                streak_length=streak_length,
                **details,
            )

        # Implied probability gate: must be in the valid range (not already extreme)
        ob = snapshot.orderbook
        implied_prob = ob.implied_yes_prob
        if implied_prob is None:
            self.last_analysis = {"decision": "NO TREND: no implied probability"}
            return None

        implied = float(implied_prob)
        if implied < cfg.trend_continuation_min_implied_prob:
            self.last_analysis = {
                "decision": (
                    f"NO TREND: implied {implied:.4f} "
                    f"< min {cfg.trend_continuation_min_implied_prob}"
                ),
            }
            return None
        if implied > cfg.trend_continuation_max_implied_prob:
            self.last_analysis = {
                "decision": (
                    f"NO TREND: implied {implied:.4f} "
                    f"> max {cfg.trend_continuation_max_implied_prob}"
                ),
            }
            return None

        # Determine side and compute edge
        side = streak_direction  # continuation: same as recent settlements
        streak_prob = cfg.trend_continuation_streak_prob

        if side == "yes":
            # We think YES is likely; edge = our probability - market price
            raw_edge = streak_prob - implied
            trade_price = implied
        else:
            # We think NO is likely; edge = our probability - market NO price
            no_implied = 1.0 - implied
            raw_edge = streak_prob - no_implied
            trade_price = no_implied

        # Fee calculation
        fee_drag = float(
            EdgeDetector.compute_fee_dollars(1, trade_price, is_maker=True)
        )
        net_edge = raw_edge - fee_drag

        if net_edge < cfg.trend_continuation_min_edge:
            self.last_analysis = {
                "decision": (
                    f"NO TREND: net_edge {net_edge:.4f} "
                    f"< min {cfg.trend_continuation_min_edge}"
                ),
                "raw_edge": round(raw_edge, 4),
                "fee_drag": round(fee_drag, 4),
                "streak_direction": streak_direction,
            }
            return None

        # Min entry price gate
        min_price = cfg.trend_continuation_min_entry_price
        if trade_price < min_price - 0.01:
            self.last_analysis = {
                "decision": (
                    f"NO TREND: entry price {trade_price:.4f} "
                    f"< min {min_price}"
                ),
            }
            logger.info(
                "trend_min_price_blocked",
                ticker=ticker,
                side=side,
                entry_price=round(trade_price, 4),
                min_price=min_price,
            )
            return None

        # Determine entry price from orderbook
        if side == "yes":
            best_bid = ob.best_yes_bid
            best_ask = ob.best_yes_ask
            target_price = implied + raw_edge * 0.3  # Slightly above mid
            if best_bid is not None:
                price = max(float(best_bid) + 0.01, target_price)
            else:
                price = target_price
            if best_ask is not None:
                price = min(price, float(best_ask) - 0.01)
            else:
                self.last_analysis = {
                    "decision": "NO TREND: no YES ask to cap against",
                }
                return None
        else:
            best_bid = ob.best_no_bid
            best_no_ask = (
                1.0 - float(ob.best_yes_bid) if ob.best_yes_bid is not None else None
            )
            target_price = (1.0 - implied) + raw_edge * 0.3
            if best_bid is not None:
                price = max(float(best_bid) + 0.01, target_price)
            else:
                price = target_price
            if best_no_ask is not None:
                price = min(price, best_no_ask - 0.01)
            else:
                self.last_analysis = {
                    "decision": "NO TREND: no NO ask to cap against",
                }
                return None

        suggested_price = f"{min(0.99, max(0.01, price)):.2f}"

        # NOTE: do NOT mark _entered_markets here — the signal may fail
        # sizing/risk/execution. bot.py calls mark_entered() after fill.

        self.last_analysis = {
            "decision": (
                f"TREND: buy {side.upper()} "
                f"edge={net_edge:.4f} streak={streak_length}"
            ),
            "streak_direction": streak_direction,
            "streak_length": streak_length,
            "raw_edge": round(raw_edge, 4),
            "net_edge": round(net_edge, 4),
            "fee_drag": round(fee_drag, 4),
            "implied": round(implied, 4),
            "streak_prob": streak_prob,
            "trade_price": round(trade_price, 4),
            "suggested_price": suggested_price,
            "mom_60s": round(mom_60, 5),
        }

        logger.info(
            "trend_continuation_detected",
            ticker=ticker,
            side=side,
            streak_direction=streak_direction,
            streak_length=streak_length,
            net_edge=round(net_edge, 4),
            implied=round(implied, 4),
            phase=snapshot.window_phase,
            mom_60s=round(mom_60, 5),
        )

        return TradeSignal(
            market_ticker=ticker,
            side=side,
            action="buy",
            raw_edge=round(raw_edge, 4),
            net_edge=round(net_edge, 4),
            model_probability=prediction.probability_yes,
            implied_probability=implied,
            confidence=prediction.confidence,
            suggested_price_dollars=suggested_price,
            suggested_count=0,
            timestamp=datetime.now(timezone.utc),
            signal_type="trend_continuation",
            entry_zone=EdgeDetector.classify_zone(trade_price),
        )

    @staticmethod
    def _check_technical_confirmation(
        features: FeatureVector,
        streak_direction: str,
        cfg: StrategyConfig,
    ) -> tuple[bool, dict]:
        """Check 4 technical signals to confirm an extended streak (3+).

        Returns (passes, details) where passes is True if enough signals confirm.
        """
        rsi_thresh = cfg.trend_continuation_rsi_extreme_threshold
        confirmations = 0
        signals: dict[str, bool] = {}

        # 1. RSI: not overbought for YES streak, not oversold for NO streak
        rsi = features.rsi_14
        if streak_direction == "yes":
            rsi_ok = rsi < rsi_thresh
        else:
            rsi_ok = rsi > (100.0 - rsi_thresh)
        signals["rsi_ok"] = rsi_ok
        if rsi_ok:
            confirmations += 1

        # 2. Orderbook depth imbalance: positive favours YES, negative favours NO
        ob_imb = features.orderbook_depth_imbalance
        if streak_direction == "yes":
            ob_ok = ob_imb > 0.0
        else:
            ob_ok = ob_imb < 0.0
        signals["ob_imbalance_ok"] = ob_ok
        if ob_ok:
            confirmations += 1

        # 3. Volume-weighted momentum: positive favours YES, negative favours NO
        vwm = features.volume_weighted_momentum
        if streak_direction == "yes":
            vwm_ok = vwm > 0.0
        else:
            vwm_ok = vwm < 0.0
        signals["vw_momentum_ok"] = vwm_ok
        if vwm_ok:
            confirmations += 1

        # 4. Taker buy/sell ratio: positive = net buying (YES), negative = net selling (NO)
        taker = features.taker_buy_sell_ratio
        if streak_direction == "yes":
            taker_ok = taker > 0.0
        else:
            taker_ok = taker < 0.0
        signals["taker_ok"] = taker_ok
        if taker_ok:
            confirmations += 1

        details = {
            "confirmations": confirmations,
            "min_required": cfg.trend_continuation_min_confirming_signals,
            "rsi": round(rsi, 2),
            "ob_imbalance": round(ob_imb, 4),
            "vw_momentum": round(vwm, 6),
            "taker_ratio": round(taker, 4),
            **signals,
        }
        passes = confirmations >= cfg.trend_continuation_min_confirming_signals
        return passes, details

    def mark_entered(self, ticker: str) -> None:
        """Mark a market as entered after a confirmed fill."""
        self._entered_markets.add(ticker)

    @staticmethod
    def _extract_asset_symbol(market_ticker: str) -> str:
        """Extract asset symbol from market ticker (e.g. 'KXBTC15M-...' -> 'BTC')."""
        ticker = market_ticker
        if ticker.startswith("KX"):
            ticker = ticker[2:]
        symbol = ""
        for ch in ticker:
            if ch.isalpha():
                symbol += ch
            else:
                break
        return symbol or "BTC"
