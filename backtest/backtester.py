"""Full-pipeline backtester for BTC 15-minute binary options.

Simulates: BacktestFeatureEngine → HeuristicModel → SignalCombiner →
PositionSizer → RiskManager over 15-minute windows built from real
1-minute Binance candles.
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
    signal_type: str  # "directional" / "fomo"
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
    trend_guard_blocks: int = 0
    risk_blocks: int = 0
    initial_bankroll: float = 0.0
    final_bankroll: float = 0.0
    total_fees: float = 0.0
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
            "trend_guard_blocks": self.trend_guard_blocks,
            "risk_blocks": self.risk_blocks,
            "initial_bankroll": self.initial_bankroll,
            "final_bankroll": self.final_bankroll,
            "total_fees": self.total_fees,
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
            trend_guard_blocks=data.get("trend_guard_blocks", 0),
            risk_blocks=data.get("risk_blocks", 0),
            initial_bankroll=data.get("initial_bankroll", 0.0),
            final_bankroll=data.get("final_bankroll", 0.0),
            total_fees=data.get("total_fees", 0.0),
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
    eval_time: datetime  # minute 7 of window
    end: datetime
    strike: float  # BTC close at window start
    btc_at_eval: float  # BTC close at eval time
    btc_at_settlement: float  # BTC close at window end
    settled_yes: bool  # btc_at_settlement > strike
    # Candle indices for history lookup
    start_idx: int
    eval_idx: int
    end_idx: int


# ---------------------------------------------------------------------------
# Main backtester
# ---------------------------------------------------------------------------


class Backtester:
    """Full-pipeline backtester using real 1-minute candle data.

    Simulates the complete trading pipeline for each 15-minute window:
    1. Compute strike from window start candle
    2. Evaluate at minute 7 (realistic entry delay)
    3. Compute features from candle history
    4. Run through SignalCombiner (edge detection + trend guard + FOMO)
    5. Size position via Kelly
    6. Check risk limits
    7. Settle at window end
    """

    # How many history candles we need before eval point
    HISTORY_CANDLES = 30

    def __init__(self, settings: BotSettings) -> None:
        self._settings = settings

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
        timestamps = candles["timestamp"].values

        # Build 15-minute windows aligned to :00/:15/:30/:45
        windows = self._build_windows(candles)

        bankroll = initial_bankroll
        peak_bankroll = initial_bankroll
        max_drawdown = 0.0
        trades: list[BacktestTrade] = []
        equity_curve: list[tuple[str, float]] = []
        trend_guard_blocks = 0
        risk_blocks = 0

        # Reset model EMA state
        self._model._prev_probability = None
        # Reset risk manager for clean backtest
        self._risk_manager = RiskManager(self._risk_config)

        for window in windows:
            # Check minimum bankroll
            if bankroll < self._risk_config.min_balance_dollars:
                break

            # --- Step 1: Get candle history up to eval point ---
            hist_start = max(0, window.eval_idx - self.HISTORY_CANDLES)
            hist_closes = closes[hist_start : window.eval_idx + 1]
            hist_volumes = volumes[hist_start : window.eval_idx + 1]
            hist_taker_buys = taker_buys[hist_start : window.eval_idx + 1]

            if len(hist_closes) < 5:
                continue

            # --- Step 2: Compute fair value for synthetic orderbook ---
            time_remaining = (
                window.end - window.eval_time
            ).total_seconds()

            fair_value = compute_fair_value_from_prices(
                btc_price=window.btc_at_eval,
                strike_price=window.strike,
                price_history=np.array(hist_closes),
                time_to_expiry_seconds=time_remaining,
                price_window_seconds=len(hist_closes) * 60.0,
            )

            if fair_value is None:
                # Fallback: simple distance-based estimate
                if window.btc_at_eval > window.strike:
                    fair_value = 0.55 + min(0.40, (window.btc_at_eval - window.strike) / window.strike * 50)
                else:
                    fair_value = 0.45 - min(0.40, (window.strike - window.btc_at_eval) / window.strike * 50)
                fair_value = max(0.05, min(0.95, fair_value))

            # --- Step 3: Build synthetic orderbook ---
            orderbook = build_synthetic_orderbook(
                fair_value=fair_value,
                spread=0.04,
                depth=100,
                ticker=f"KXBTC-{window.start.strftime('%H%M')}",
                timestamp=window.eval_time,
            )

            # --- Step 4: Compute features ---
            features = self._feature_engine.compute(
                closes=np.array(hist_closes),
                volumes=np.array(hist_volumes),
                taker_buy_volumes=np.array(hist_taker_buys),
                orderbook=orderbook,
                time_to_expiry_seconds=time_remaining,
                market_ticker=orderbook.ticker,
                timestamp=window.eval_time,
            )

            # --- Step 5: Build snapshot for signal combiner ---
            snapshot = self._build_snapshot(
                window=window,
                orderbook=orderbook,
                fair_value=fair_value,
                time_remaining=time_remaining,
                closes=hist_closes,
                volumes=hist_volumes,
            )

            # --- Step 6: Model prediction ---
            prediction = self._model.predict(features)

            # --- Step 7: Signal evaluation (edge + trend guard + FOMO) ---
            signals = self._signal_combiner.evaluate(
                prediction, snapshot, current_position=0, features=features
            )

            if not signals:
                equity_curve.append(
                    (window.eval_time.isoformat(), bankroll)
                )
                # Check if trend guard blocked
                edge_analysis = self._signal_combiner._edge_detector.last_analysis
                if edge_analysis.get("passed") is False and edge_analysis.get("edge_passed"):
                    # Had edge but confidence failed — not trend guard
                    pass
                continue

            signal = signals[0]

            # Skip market-making signals
            if signal.signal_type == "market_making":
                equity_curve.append(
                    (window.eval_time.isoformat(), bankroll)
                )
                continue

            # --- Step 8: Position sizing ---
            count = self._position_sizer.size(
                signal,
                Decimal(str(bankroll)),
                Decimal("0"),
            )

            if count <= 0:
                equity_curve.append(
                    (window.eval_time.isoformat(), bankroll)
                )
                continue

            # --- Step 9: Risk check ---
            risk_decision = self._risk_manager.check(
                signal,
                count,
                Decimal(str(bankroll)),
                positions=[],
                time_to_expiry_seconds=time_remaining,
            )

            if not risk_decision.approved:
                risk_blocks += 1
                equity_curve.append(
                    (window.eval_time.isoformat(), bankroll)
                )
                continue

            if risk_decision.adjusted_count is not None:
                count = risk_decision.adjusted_count

            # --- Step 10: Simulate trade settlement ---
            price = float(signal.suggested_price_dollars)
            fee = float(
                EdgeDetector.compute_fee_dollars(count, price, is_maker=False)
            )

            # Binary contract P&L
            if signal.side == "yes":
                won = window.settled_yes
            else:
                won = not window.settled_yes

            if won:
                pnl = count * (1.0 - price) - fee
            else:
                pnl = -count * price - fee

            bankroll += pnl
            peak_bankroll = max(peak_bankroll, bankroll)
            drawdown = peak_bankroll - bankroll
            max_drawdown = max(max_drawdown, drawdown)

            # Record trade with risk manager
            self._risk_manager.record_trade(Decimal(str(round(pnl, 4))))
            self._risk_manager._trades_today += 1

            trade = BacktestTrade(
                window_start=window.start,
                timestamp=window.eval_time,
                market_ticker=signal.market_ticker,
                side=signal.side,
                signal_type=signal.signal_type,
                count=count,
                price=price,
                model_prob=prediction.probability_yes,
                implied_prob=signal.implied_probability,
                edge=signal.net_edge,
                strike=window.strike,
                btc_at_entry=window.btc_at_eval,
                btc_at_settlement=window.btc_at_settlement,
                settled_yes=window.settled_yes,
                pnl=round(pnl, 4),
                fees=round(fee, 4),
                bankroll_after=round(bankroll, 4),
            )
            trades.append(trade)
            equity_curve.append(
                (window.eval_time.isoformat(), round(bankroll, 4))
            )

        return self._compute_metrics(
            trades=trades,
            equity_curve=equity_curve,
            initial_bankroll=initial_bankroll,
            max_drawdown=max_drawdown,
            total_windows=len(windows),
            trend_guard_blocks=trend_guard_blocks,
            risk_blocks=risk_blocks,
        )

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
            eval_time = current + pd.Timedelta(minutes=7)

            # Find candle indices
            start_mask = timestamps >= current
            if not start_mask.any():
                current = window_end
                continue

            start_idx = start_mask.idxmax()

            eval_mask = timestamps >= eval_time
            if not eval_mask.any():
                current = window_end
                continue
            eval_idx = eval_mask.idxmax()

            end_mask = timestamps >= window_end
            if not end_mask.any():
                current = window_end
                continue
            end_idx = end_mask.idxmax()

            # Ensure we have enough candle range
            if eval_idx <= start_idx or end_idx <= eval_idx:
                current = window_end
                continue

            strike = float(closes[start_idx])
            btc_at_eval = float(closes[eval_idx])
            btc_at_settlement = float(closes[end_idx])
            settled_yes = bool(btc_at_settlement > strike)

            windows.append(
                Window(
                    start=pd.Timestamp(current, tz="UTC") if current.tzinfo is None else current,
                    eval_time=pd.Timestamp(eval_time, tz="UTC") if eval_time.tzinfo is None else eval_time,
                    end=pd.Timestamp(window_end, tz="UTC") if window_end.tzinfo is None else window_end,
                    strike=strike,
                    btc_at_eval=btc_at_eval,
                    btc_at_settlement=btc_at_settlement,
                    settled_yes=settled_yes,
                    start_idx=start_idx,
                    eval_idx=eval_idx,
                    end_idx=end_idx,
                )
            )

            current = window_end

        return windows

    def _build_snapshot(
        self,
        window: Window,
        orderbook: Orderbook,
        fair_value: float,
        time_remaining: float,
        closes: np.ndarray,
        volumes: np.ndarray,
    ) -> MarketSnapshot:
        """Build a MarketSnapshot from window data."""
        btc_price = Decimal(str(window.btc_at_eval))

        # Build price lists from candle history
        prices_1min = [Decimal(str(c)) for c in closes[-60:]]
        prices_5min = [Decimal(str(c)) for c in closes[-300:]] if len(closes) > 5 else prices_1min
        volumes_1min = [Decimal(str(v)) for v in volumes[-60:]]

        return MarketSnapshot(
            timestamp=window.eval_time,
            market_ticker=orderbook.ticker,
            btc_price=btc_price,
            btc_prices_1min=prices_1min,
            btc_prices_5min=prices_5min,
            btc_volumes_1min=volumes_1min,
            orderbook=orderbook,
            implied_yes_prob=orderbook.implied_yes_prob,
            spread=orderbook.spread,
            strike_price=Decimal(str(window.strike)),
            statistical_fair_value=fair_value,
            binance_btc_price=btc_price,
            time_to_expiry_seconds=time_remaining,
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

        final_bankroll = trades[-1].bankroll_after if trades else initial_bankroll

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
            trend_guard_blocks=trend_guard_blocks,
            risk_blocks=risk_blocks,
            initial_bankroll=initial_bankroll,
            final_bankroll=round(final_bankroll, 2),
            total_fees=round(total_fees, 4),
            trades=trades,
            equity_curve=equity_curve,
        )
