"""Full-pipeline backtester for BTC/ETH 15-minute binary options.

Simulates: BacktestFeatureEngine -> HeuristicModel -> SignalCombiner ->
PositionSizer -> RiskManager over 15-minute windows built from real
1-minute Binance candles.

Evaluates each window at multiple time points (minutes 3-14) so that
late-window strategies like settlement_ride and certainty_scalp can fire.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import structlog

from backtest.candle_features import BacktestFeatureEngine, build_synthetic_orderbook
from src.config import BotSettings, StrategyConfig
from src.data.models import (
    FeatureVector,
    MarketSnapshot,
    Orderbook,
    Position,
    TradeSignal,
)
from src.model.predict import HeuristicModel
from src.risk.position_sizer import PositionSizer
from src.risk.risk_manager import RiskManager
from src.strategy.edge_detector import EdgeDetector
from src.strategy.fair_value import compute_fair_value_from_prices
from src.strategy.signal_combiner import SignalCombiner

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BacktestTrade:
    """A single backtest trade with full context."""

    window_start: datetime
    timestamp: datetime
    market_ticker: str
    side: str  # "yes" / "no"
    signal_type: str  # "directional" / "fomo" / "settlement_ride" / "certainty_scalp"
    count: int
    price: float
    model_prob: float
    implied_prob: float
    edge: float
    strike: float
    btc_at_entry: float
    btc_at_settlement: float
    settled_yes: bool
    pnl: float
    fees: float
    bankroll_after: float
    asset: str = ""
    exit_type: str = "settlement"  # "settlement" or "stop_loss"
    time_elapsed_at_entry: float = 0.0
    strategy_tag: str = ""

    def to_dict(self) -> dict:
        return {
            "window_start": self.window_start.isoformat(),
            "timestamp": self.timestamp.isoformat(),
            "market_ticker": self.market_ticker,
            "side": self.side,
            "signal_type": self.signal_type,
            "count": self.count,
            "price": self.price,
            "model_prob": self.model_prob,
            "implied_prob": self.implied_prob,
            "edge": self.edge,
            "strike": self.strike,
            "btc_at_entry": self.btc_at_entry,
            "btc_at_settlement": self.btc_at_settlement,
            "settled_yes": self.settled_yes,
            "pnl": self.pnl,
            "fees": self.fees,
            "bankroll_after": self.bankroll_after,
            "asset": self.asset,
            "exit_type": self.exit_type,
            "time_elapsed_at_entry": self.time_elapsed_at_entry,
            "strategy_tag": self.strategy_tag,
        }

    @classmethod
    def from_dict(cls, d: dict) -> BacktestTrade:
        return cls(
            window_start=datetime.fromisoformat(d["window_start"]),
            timestamp=datetime.fromisoformat(d["timestamp"]),
            market_ticker=d["market_ticker"],
            side=d["side"],
            signal_type=d["signal_type"],
            count=d["count"],
            price=d["price"],
            model_prob=d["model_prob"],
            implied_prob=d["implied_prob"],
            edge=d["edge"],
            strike=d["strike"],
            btc_at_entry=d["btc_at_entry"],
            btc_at_settlement=d["btc_at_settlement"],
            settled_yes=d["settled_yes"],
            pnl=d["pnl"],
            fees=d["fees"],
            bankroll_after=d["bankroll_after"],
            asset=d.get("asset", ""),
            exit_type=d.get("exit_type", "settlement"),
            time_elapsed_at_entry=d.get("time_elapsed_at_entry", 0.0),
            strategy_tag=d.get("strategy_tag", ""),
        )


@dataclass
class BacktestResult:
    """Complete results from a backtest run."""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    avg_edge: float = 0.0
    total_windows: int = 0
    trade_rate: float = 0.0  # trades / windows
    directional_trades: int = 0
    fomo_trades: int = 0
    settlement_ride_trades: int = 0
    certainty_scalp_trades: int = 0
    stop_loss_exits: int = 0
    trend_guard_blocks: int = 0
    risk_blocks: int = 0
    drawdown_blocks: int = 0
    initial_bankroll: float = 0.0
    final_bankroll: float = 0.0
    total_fees: float = 0.0
    asset: str = ""
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    label: str = ""

    def to_json(self, path: str) -> None:
        """Serialize result to JSON file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
            "total_pnl": self.total_pnl,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "profit_factor": self.profit_factor,
            "avg_edge": self.avg_edge,
            "total_windows": self.total_windows,
            "trade_rate": self.trade_rate,
            "directional_trades": self.directional_trades,
            "fomo_trades": self.fomo_trades,
            "settlement_ride_trades": self.settlement_ride_trades,
            "certainty_scalp_trades": self.certainty_scalp_trades,
            "stop_loss_exits": self.stop_loss_exits,
            "trend_guard_blocks": self.trend_guard_blocks,
            "risk_blocks": self.risk_blocks,
            "drawdown_blocks": self.drawdown_blocks,
            "initial_bankroll": self.initial_bankroll,
            "final_bankroll": self.final_bankroll,
            "total_fees": self.total_fees,
            "asset": self.asset,
            "label": self.label,
            "trades": [t.to_dict() for t in self.trades],
            "equity_curve": self.equity_curve,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def from_json(cls, path: str) -> BacktestResult:
        """Deserialize result from JSON file."""
        with open(path) as f:
            data = json.load(f)
        trades = [BacktestTrade.from_dict(t) for t in data.get("trades", [])]
        equity = data.get("equity_curve", [])
        return cls(
            total_trades=data["total_trades"],
            winning_trades=data["winning_trades"],
            losing_trades=data["losing_trades"],
            win_rate=data["win_rate"],
            total_pnl=data["total_pnl"],
            max_drawdown=data["max_drawdown"],
            sharpe_ratio=data["sharpe_ratio"],
            profit_factor=data["profit_factor"],
            avg_edge=data["avg_edge"],
            total_windows=data.get("total_windows", 0),
            trade_rate=data.get("trade_rate", 0.0),
            directional_trades=data.get("directional_trades", 0),
            fomo_trades=data.get("fomo_trades", 0),
            settlement_ride_trades=data.get("settlement_ride_trades", 0),
            certainty_scalp_trades=data.get("certainty_scalp_trades", 0),
            stop_loss_exits=data.get("stop_loss_exits", 0),
            trend_guard_blocks=data.get("trend_guard_blocks", 0),
            risk_blocks=data.get("risk_blocks", 0),
            drawdown_blocks=data.get("drawdown_blocks", 0),
            initial_bankroll=data.get("initial_bankroll", 0.0),
            final_bankroll=data.get("final_bankroll", 0.0),
            total_fees=data.get("total_fees", 0.0),
            asset=data.get("asset", ""),
            label=data.get("label", ""),
            trades=trades,
            equity_curve=equity,
        )


# ---------------------------------------------------------------------------
# Window abstraction
# ---------------------------------------------------------------------------


@dataclass
class Window:
    """A 15-minute simulation window."""

    start: datetime
    end: datetime
    strike: float  # BTC/ETH close at window start
    btc_at_settlement: float  # close at window end
    settled_yes: bool  # btc_at_settlement > strike
    start_idx: int
    end_idx: int


# ---------------------------------------------------------------------------
# Main backtester
# ---------------------------------------------------------------------------


class Backtester:
    """Full-pipeline backtester using real 1-minute candle data.

    Simulates the complete trading pipeline for each 15-minute window:
    1. Compute strike from window start candle
    2. Evaluate at minutes 3-14 (multi-point, first signal wins)
    3. Compute features from candle history
    4. Run through SignalCombiner (edge + trend guard + FOMO + settlement ride + certainty scalp)
    5. Size position via Kelly
    6. Check risk limits
    7. Simulate stop-loss or hold to settlement
    """

    # How many history candles we need before eval point
    HISTORY_CANDLES = 30
    # Evaluate minutes 3-14 within each 15-min window (12 points)
    EVAL_MINUTES = range(3, 15)

    def __init__(self, settings: BotSettings, asset: str = "BTC") -> None:
        self._settings = settings
        self._asset = asset
        self._ticker_prefix = f"KX{asset}-"

        # Adjust strategy config for backtest environment:
        # - Disable market making (synthetic orderbook makes MM meaningless)
        # - Widen max_edge_threshold: synthetic orderbooks produce larger edges
        #   than production orderbooks, so we relax the "suspiciously large" cap
        # - Relax directional_max_spread and directional_min_depth since our
        #   synthetic book has fixed 0.04 spread and 100 depth
        strategy_config = settings.strategy.model_copy()
        strategy_config.use_market_maker = False
        strategy_config.max_edge_threshold = 0.40
        strategy_config.directional_max_spread = 0.10
        strategy_config.directional_min_depth = 1
        self._strategy_config = strategy_config

        # Relax risk config for backtesting: we want to run through all windows
        # to get statistically meaningful results, not stop after a few losses
        risk_config = settings.risk.model_copy()
        risk_config.max_consecutive_losses = 100  # Don't stop on streaks
        risk_config.max_daily_loss_dollars = 1000.0  # Effectively no daily limit
        risk_config.max_trades_per_day = 500  # ~96 windows/day max
        risk_config.min_balance_dollars = 1.0  # Keep trading until nearly broke
        risk_config.max_total_exposure_dollars = 500.0
        risk_config.max_position_per_market = 100
        # Scale drawdown breaker proportionally to bankroll
        # Production uses $20 on ~$200 bankroll (10%); keep same ratio
        risk_config.drawdown_limit_dollars = 1000.0  # Effectively no daily drawdown limit

        self._model = HeuristicModel()
        self._signal_combiner = SignalCombiner(strategy_config)
        self._position_sizer = PositionSizer(risk_config)
        self._risk_manager = RiskManager(risk_config)
        self._feature_engine = BacktestFeatureEngine()
        self._risk_config = risk_config

    def run(
        self,
        candles: pd.DataFrame,
        initial_bankroll: float = 100.0,
    ) -> BacktestResult:
        """Run backtest over candle data.

        Args:
            candles: DataFrame from fetch_candles with columns:
                timestamp, open, high, low, close, volume, taker_buy_volume
            initial_bankroll: Starting capital in dollars

        Returns:
            BacktestResult with full metrics, trades, and equity curve
        """
        closes = candles["close"].values.astype(float)
        volumes = candles["volume"].values.astype(float)
        taker_buys = candles["taker_buy_volume"].values.astype(float)

        # Build 15-minute windows aligned to :00/:15/:30/:45
        windows = self._build_windows(candles)

        bankroll = initial_bankroll
        peak_bankroll = initial_bankroll
        max_drawdown = 0.0
        trades: list[BacktestTrade] = []
        equity_curve: list[tuple[str, float]] = []
        trend_guard_blocks = 0
        risk_blocks = 0

        # Drawdown circuit breaker state
        daily_pnl = 0.0
        daily_pnl_peak = 0.0
        current_date = None
        drawdown_blocks = 0

        # Reset model EMA state
        self._model._prev_probability = None
        # Reset risk manager for clean backtest
        self._risk_manager = RiskManager(self._risk_config)

        for window in windows:
            # Check minimum bankroll
            if bankroll < self._risk_config.min_balance_dollars:
                break

            # Daily reset at midnight UTC
            window_date = pd.Timestamp(window.start).date()
            if current_date is None or window_date != current_date:
                current_date = window_date
                daily_pnl = 0.0
                daily_pnl_peak = 0.0
                self._risk_manager = RiskManager(self._risk_config)

            # Drawdown circuit breaker
            if self._risk_config.drawdown_limit_enabled:
                if daily_pnl_peak - daily_pnl >= self._risk_config.drawdown_limit_dollars:
                    drawdown_blocks += 1
                    equity_curve.append(
                        (window.start.isoformat(), bankroll)
                    )
                    continue

            # --- Multi-evaluation-point loop ---
            # Evaluate at minutes 3-14; take first signal that passes all checks
            trade_taken = False
            for eval_minute in self.EVAL_MINUTES:
                eval_idx = window.start_idx + eval_minute

                # Safety: don't go past window end or array bounds
                if eval_idx >= window.end_idx or eval_idx >= len(closes):
                    break

                eval_time = window.start + pd.Timedelta(minutes=eval_minute)
                time_elapsed = eval_minute * 60.0
                time_remaining = (15 - eval_minute) * 60.0
                window_phase = self._compute_phase(time_elapsed)

                btc_at_eval = float(closes[eval_idx])

                # --- Candle history up to eval point ---
                hist_start = max(0, eval_idx - self.HISTORY_CANDLES)
                hist_closes = closes[hist_start : eval_idx + 1]
                hist_volumes = volumes[hist_start : eval_idx + 1]
                hist_taker_buys = taker_buys[hist_start : eval_idx + 1]

                if len(hist_closes) < 5:
                    continue

                # --- Compute fair value for synthetic orderbook ---
                fair_value = compute_fair_value_from_prices(
                    btc_price=btc_at_eval,
                    strike_price=window.strike,
                    price_history=np.array(hist_closes),
                    time_to_expiry_seconds=time_remaining,
                    price_window_seconds=len(hist_closes) * 60.0,
                )

                if fair_value is None:
                    # Fallback: simple distance-based estimate
                    if btc_at_eval > window.strike:
                        fair_value = 0.55 + min(
                            0.40,
                            (btc_at_eval - window.strike) / window.strike * 50,
                        )
                    else:
                        fair_value = 0.45 - min(
                            0.40,
                            (window.strike - btc_at_eval) / window.strike * 50,
                        )
                    fair_value = max(0.05, min(0.95, fair_value))

                # --- Build synthetic orderbook ---
                ticker = f"{self._ticker_prefix}{window.start.strftime('%H%M')}"
                orderbook = build_synthetic_orderbook(
                    fair_value=fair_value,
                    spread=0.04,
                    depth=100,
                    ticker=ticker,
                    timestamp=eval_time,
                )

                # --- Compute features ---
                features = self._feature_engine.compute(
                    closes=np.array(hist_closes),
                    volumes=np.array(hist_volumes),
                    taker_buy_volumes=np.array(hist_taker_buys),
                    orderbook=orderbook,
                    time_to_expiry_seconds=time_remaining,
                    market_ticker=ticker,
                    timestamp=eval_time,
                )

                # --- Build snapshot with timing fields ---
                snapshot = self._build_snapshot(
                    strike=window.strike,
                    eval_time=eval_time,
                    btc_at_eval=btc_at_eval,
                    orderbook=orderbook,
                    fair_value=fair_value,
                    time_remaining=time_remaining,
                    time_elapsed=time_elapsed,
                    window_phase=window_phase,
                    closes=hist_closes,
                    volumes=hist_volumes,
                )

                # --- Set simulated time for quiet hours ---
                self._signal_combiner.set_simulated_time(eval_time)

                # --- Model prediction ---
                prediction = self._model.predict(features)

                # --- Signal evaluation ---
                signals = self._signal_combiner.evaluate(
                    prediction, snapshot, current_position=0, features=features
                )

                if not signals:
                    continue

                signal = signals[0]

                # Skip market-making signals
                if signal.signal_type == "market_making":
                    continue

                # --- Position sizing ---
                count = self._position_sizer.size(
                    signal,
                    Decimal(str(bankroll)),
                    Decimal("0"),
                )

                if count <= 0:
                    continue

                # --- Risk check ---
                risk_decision = self._risk_manager.check(
                    signal,
                    count,
                    Decimal(str(bankroll)),
                    positions=[],
                    time_to_expiry_seconds=time_remaining,
                )

                if not risk_decision.approved:
                    risk_blocks += 1
                    continue

                if risk_decision.adjusted_count is not None:
                    count = risk_decision.adjusted_count

                # --- Trade taken! ---
                trade_taken = True
                entry_price = float(signal.suggested_price_dollars)
                entry_fee = float(
                    EdgeDetector.compute_fee_dollars(
                        count, entry_price, is_maker=False
                    )
                )

                # --- Stop-loss simulation ---
                # Skip SL for settlement_ride/certainty_scalp (hold to settlement)
                exit_type = "settlement"
                actual_pnl = None
                actual_fees = entry_fee

                if (
                    signal.signal_type not in ("settlement_ride", "certainty_scalp")
                    and self._strategy_config.stop_loss_enabled
                ):
                    sl_result = self._simulate_stop_loss(
                        window=window,
                        eval_idx=eval_idx,
                        signal=signal,
                        count=count,
                        entry_price=entry_price,
                        entry_fee=entry_fee,
                        closes=closes,
                    )
                    if sl_result is not None:
                        exit_type = "stop_loss"
                        actual_pnl = sl_result["pnl"]
                        actual_fees = sl_result["fees"]

                # --- Settlement PnL (if no stop-loss triggered) ---
                if actual_pnl is None:
                    if signal.side == "yes":
                        won = window.settled_yes
                    else:
                        won = not window.settled_yes

                    if won:
                        actual_pnl = count * (1.0 - entry_price) - entry_fee
                    else:
                        actual_pnl = -count * entry_price - entry_fee

                bankroll += actual_pnl
                peak_bankroll = max(peak_bankroll, bankroll)
                drawdown = peak_bankroll - bankroll
                max_drawdown = max(max_drawdown, drawdown)

                # Update daily PnL tracking
                daily_pnl += actual_pnl
                daily_pnl_peak = max(daily_pnl_peak, daily_pnl)

                # Record trade with risk manager
                self._risk_manager.record_trade(
                    Decimal(str(round(actual_pnl, 4)))
                )
                self._risk_manager._trades_today += 1

                trade = BacktestTrade(
                    window_start=window.start,
                    timestamp=eval_time,
                    market_ticker=signal.market_ticker,
                    side=signal.side,
                    signal_type=signal.signal_type,
                    count=count,
                    price=entry_price,
                    model_prob=prediction.probability_yes,
                    implied_prob=signal.implied_probability,
                    edge=signal.net_edge,
                    strike=window.strike,
                    btc_at_entry=btc_at_eval,
                    btc_at_settlement=window.btc_at_settlement,
                    settled_yes=window.settled_yes,
                    pnl=round(actual_pnl, 4),
                    fees=round(actual_fees, 4),
                    bankroll_after=round(bankroll, 4),
                    asset=self._asset,
                    exit_type=exit_type,
                    time_elapsed_at_entry=time_elapsed,
                    strategy_tag=signal.signal_type,
                )
                trades.append(trade)
                equity_curve.append(
                    (eval_time.isoformat(), round(bankroll, 4))
                )
                break  # Only one trade per window

            if not trade_taken:
                equity_curve.append(
                    (window.start.isoformat(), bankroll)
                )

        # Clear simulated time
        self._signal_combiner.set_simulated_time(None)

        return self._compute_metrics(
            trades=trades,
            equity_curve=equity_curve,
            initial_bankroll=initial_bankroll,
            max_drawdown=max_drawdown,
            total_windows=len(windows),
            trend_guard_blocks=trend_guard_blocks,
            risk_blocks=risk_blocks,
            drawdown_blocks=drawdown_blocks,
        )

    def _simulate_stop_loss(
        self,
        window: Window,
        eval_idx: int,
        signal: TradeSignal,
        count: int,
        entry_price: float,
        entry_fee: float,
        closes: np.ndarray,
    ) -> dict | None:
        """Check if stop-loss would trigger between entry and settlement.

        Walks the price path from entry to settlement candle-by-candle,
        computing synthetic fair value and bid at each point.

        Returns dict with pnl/fees if SL triggers, None otherwise.
        """
        stop_loss_pct = self._strategy_config.stop_loss_pct
        for asset, val in self._strategy_config.asset_stop_loss_pct.items():
            if asset.upper() == self._asset.upper():
                stop_loss_pct = val
                break

        min_hold = self._strategy_config.stop_loss_min_hold_seconds
        min_bid = self._strategy_config.stop_loss_min_bid

        for candle_offset in range(1, window.end_idx - eval_idx):
            check_idx = eval_idx + candle_offset
            if check_idx >= len(closes):
                break

            time_held = candle_offset * 60.0
            if time_held < min_hold:
                continue

            check_btc = float(closes[check_idx])
            check_time_remaining = (window.end_idx - check_idx) * 60.0
            if check_time_remaining <= 0:
                break

            # Compute synthetic fair value at this point
            hist_start = max(0, check_idx - 30)
            hist_prices = closes[hist_start : check_idx + 1]

            check_fv = compute_fair_value_from_prices(
                btc_price=check_btc,
                strike_price=window.strike,
                price_history=np.array(hist_prices),
                time_to_expiry_seconds=check_time_remaining,
                price_window_seconds=len(hist_prices) * 60.0,
            )

            if check_fv is None:
                if check_btc > window.strike:
                    check_fv = 0.55 + min(
                        0.40,
                        (check_btc - window.strike) / window.strike * 50,
                    )
                else:
                    check_fv = 0.45 - min(
                        0.40,
                        (window.strike - check_btc) / window.strike * 50,
                    )
                check_fv = max(0.05, min(0.95, check_fv))

            # Compute synthetic bid for our side
            spread = 0.04
            if signal.side == "yes":
                current_bid = max(0.01, check_fv - spread / 2)
            else:
                current_bid = max(0.01, (1.0 - check_fv) - spread / 2)

            if current_bid < min_bid:
                continue

            # Check if loss exceeds threshold
            loss_pct = (
                (entry_price - current_bid) / entry_price
                if entry_price > 0
                else 0
            )

            if loss_pct >= stop_loss_pct:
                exit_fee = float(
                    EdgeDetector.compute_fee_dollars(
                        count, current_bid, is_maker=False
                    )
                )
                pnl = count * (current_bid - entry_price) - entry_fee - exit_fee
                return {"pnl": pnl, "fees": entry_fee + exit_fee}

        return None

    def _compute_phase(self, time_elapsed: float) -> int:
        """Compute window phase (1-5) from elapsed seconds."""
        cfg = self._strategy_config
        if time_elapsed < cfg.phase_observation_end:
            return 1
        elif time_elapsed < cfg.phase_confirmation_end:
            return 2
        elif time_elapsed < cfg.phase_active_end:
            return 3
        elif time_elapsed < cfg.phase_late_end:
            return 4
        else:
            return 5

    def _build_windows(self, candles: pd.DataFrame) -> list[Window]:
        """Build 15-minute windows aligned to :00/:15/:30/:45."""
        windows: list[Window] = []
        timestamps = pd.to_datetime(candles["timestamp"])
        closes = candles["close"].values.astype(float)

        # Find all 15-minute boundaries
        first_ts = timestamps.iloc[0]
        last_ts = timestamps.iloc[-1]

        # Align to first :00/:15/:30/:45
        start_minute = (first_ts.minute // 15) * 15
        current = first_ts.replace(
            minute=start_minute, second=0, microsecond=0
        )
        if current < first_ts:
            current = current + pd.Timedelta(minutes=15)

        while current + pd.Timedelta(minutes=15) <= last_ts:
            window_end = current + pd.Timedelta(minutes=15)

            # Find candle indices
            start_mask = timestamps >= current
            if not start_mask.any():
                current = window_end
                continue

            start_idx = start_mask.idxmax()

            end_mask = timestamps >= window_end
            if not end_mask.any():
                current = window_end
                continue
            end_idx = end_mask.idxmax()

            # Need enough candles for at least the first eval point (minute 3)
            if end_idx <= start_idx + 3:
                current = window_end
                continue

            strike = float(closes[start_idx])
            btc_at_settlement = float(closes[end_idx])
            settled_yes = bool(btc_at_settlement > strike)

            windows.append(
                Window(
                    start=pd.Timestamp(current, tz="UTC") if current.tzinfo is None else current,
                    end=pd.Timestamp(window_end, tz="UTC") if window_end.tzinfo is None else window_end,
                    strike=strike,
                    btc_at_settlement=btc_at_settlement,
                    settled_yes=settled_yes,
                    start_idx=start_idx,
                    end_idx=end_idx,
                )
            )

            current = window_end

        return windows

    def _build_snapshot(
        self,
        *,
        strike: float,
        eval_time: datetime,
        btc_at_eval: float,
        orderbook: Orderbook,
        fair_value: float,
        time_remaining: float,
        time_elapsed: float,
        window_phase: int,
        closes: np.ndarray,
        volumes: np.ndarray,
    ) -> MarketSnapshot:
        """Build a MarketSnapshot from window + eval point data."""
        btc_price = Decimal(str(btc_at_eval))

        # Build price lists from candle history
        prices_1min = [Decimal(str(c)) for c in closes[-60:]]
        prices_5min = [Decimal(str(c)) for c in closes[-300:]] if len(closes) > 5 else prices_1min
        volumes_1min = [Decimal(str(v)) for v in volumes[-60:]]

        return MarketSnapshot(
            timestamp=eval_time,
            market_ticker=orderbook.ticker,
            btc_price=btc_price,
            btc_prices_1min=prices_1min,
            btc_prices_5min=prices_5min,
            btc_volumes_1min=volumes_1min,
            orderbook=orderbook,
            implied_yes_prob=orderbook.implied_yes_prob,
            spread=orderbook.spread,
            strike_price=Decimal(str(strike)),
            statistical_fair_value=fair_value,
            binance_btc_price=btc_price,
            time_to_expiry_seconds=time_remaining,
            time_elapsed_seconds=time_elapsed,
            window_phase=window_phase,
            volume=100,
        )

    @staticmethod
    def _compute_metrics(
        trades: list[BacktestTrade],
        equity_curve: list[tuple[str, float]],
        initial_bankroll: float,
        max_drawdown: float,
        total_windows: int,
        trend_guard_blocks: int,
        risk_blocks: int,
        drawdown_blocks: int = 0,
    ) -> BacktestResult:
        """Compute backtest performance metrics."""
        if not trades:
            return BacktestResult(
                total_windows=total_windows,
                initial_bankroll=initial_bankroll,
                final_bankroll=initial_bankroll,
                equity_curve=equity_curve,
            )

        pnls = [t.pnl for t in trades]
        winning = [t for t in trades if t.pnl > 0]
        losing = [t for t in trades if t.pnl <= 0]

        total_pnl = sum(pnls)
        win_rate = len(winning) / len(trades)
        total_fees = sum(t.fees for t in trades)

        # Sharpe ratio (annualized, ~96 windows per day)
        pnl_array = np.array(pnls)
        if len(pnl_array) > 1 and np.std(pnl_array) > 0:
            sharpe = float(
                np.mean(pnl_array) / np.std(pnl_array) * np.sqrt(252 * 96)
            )
        else:
            sharpe = 0.0

        # Profit factor
        total_wins = sum(t.pnl for t in winning) if winning else 0.0
        total_losses = abs(sum(t.pnl for t in losing)) if losing else 1.0
        profit_factor = total_wins / total_losses if total_losses > 0 else 0.0

        avg_edge = float(np.mean([t.edge for t in trades]))

        # Signal type breakdown
        directional = [t for t in trades if t.signal_type == "directional"]
        fomo = [t for t in trades if t.signal_type == "fomo"]
        settlement_ride = [t for t in trades if t.signal_type == "settlement_ride"]
        certainty_scalp = [t for t in trades if t.signal_type == "certainty_scalp"]
        stop_loss_exits = [t for t in trades if t.exit_type == "stop_loss"]

        final_bankroll = trades[-1].bankroll_after if trades else initial_bankroll
        asset = trades[0].asset if trades else ""

        return BacktestResult(
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=round(win_rate, 4),
            total_pnl=round(total_pnl, 2),
            max_drawdown=round(max_drawdown, 2),
            sharpe_ratio=round(sharpe, 4),
            profit_factor=round(profit_factor, 4),
            avg_edge=round(avg_edge, 4),
            total_windows=total_windows,
            trade_rate=round(len(trades) / total_windows, 4) if total_windows > 0 else 0.0,
            directional_trades=len(directional),
            fomo_trades=len(fomo),
            settlement_ride_trades=len(settlement_ride),
            certainty_scalp_trades=len(certainty_scalp),
            stop_loss_exits=len(stop_loss_exits),
            trend_guard_blocks=trend_guard_blocks,
            risk_blocks=risk_blocks,
            drawdown_blocks=drawdown_blocks,
            initial_bankroll=initial_bankroll,
            final_bankroll=round(final_bankroll, 2),
            total_fees=round(total_fees, 4),
            asset=asset,
            trades=trades,
            equity_curve=equity_curve,
        )
