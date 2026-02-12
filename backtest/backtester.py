"""Event-driven backtester for strategy evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import structlog

from src.config import BotSettings, load_settings
from src.data.models import (
    FeatureVector,
    MarketSnapshot,
    Orderbook,
    OrderbookLevel,
    PredictionResult,
)
from src.features.feature_engine import FeatureEngine
from src.model.predict import HeuristicModel, ProbabilityModel
from src.risk.position_sizer import PositionSizer
from src.risk.risk_manager import RiskManager
from src.strategy.edge_detector import EdgeDetector

logger = structlog.get_logger()


@dataclass
class BacktestTrade:
    """A single backtest trade with outcome."""

    timestamp: datetime
    market_ticker: str
    side: str
    count: int
    price: float
    model_prob: float
    implied_prob: float
    edge: float
    actual_up: bool
    pnl: float
    fees: float
    bankroll_after: float


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
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)


class Backtester:
    """Event-driven backtester that replays historical data through the strategy.

    Simulates the full pipeline: features -> model -> edge detection ->
    risk management -> position sizing, with realistic fees and slippage.
    """

    def __init__(self, settings: BotSettings):
        self._settings = settings
        self._feature_engine = FeatureEngine(settings.features)
        self._model: ProbabilityModel = HeuristicModel()
        self._edge_detector = EdgeDetector(settings.strategy)
        self._position_sizer = PositionSizer(settings.risk)
        self._risk_manager = RiskManager(settings.risk)

    def run(
        self,
        data: pd.DataFrame,
        initial_bankroll: float = 1000.0,
    ) -> BacktestResult:
        """Run backtest on historical data.

        Args:
            data: DataFrame with columns:
                - timestamp: datetime
                - btc_price: float
                - kalshi_yes_bid: float (optional)
                - kalshi_yes_ask: float (optional)
                - actual_up: bool (did BTC go up in this 15-min window?)
            initial_bankroll: starting capital in dollars

        Returns:
            BacktestResult with full metrics and trade list
        """
        bankroll = initial_bankroll
        peak_bankroll = initial_bankroll
        max_drawdown = 0.0
        trades: list[BacktestTrade] = []
        equity_curve: list[tuple[datetime, float]] = []

        # Group data into 15-minute windows
        for _, row in data.iterrows():
            timestamp = row["timestamp"]
            btc_price = row["btc_price"]
            yes_bid = row.get("kalshi_yes_bid", 0.50)
            yes_ask = row.get("kalshi_yes_ask", 0.52)
            actual_up = row.get("actual_up", None)

            if actual_up is None or pd.isna(yes_bid) or pd.isna(yes_ask):
                continue

            if yes_bid <= 0 or yes_ask <= 0:
                continue

            # Build synthetic snapshot
            snapshot = self._build_snapshot(
                timestamp, btc_price, yes_bid, yes_ask, row
            )

            # Compute features
            features = self._feature_engine.compute(snapshot)

            # Get model prediction
            prediction = self._model.predict(features)

            # Detect edge
            signal = self._edge_detector.detect(prediction, snapshot)

            if signal is None:
                equity_curve.append((timestamp, bankroll))
                continue

            # Size position
            count = self._position_sizer.size(
                signal,
                Decimal(str(bankroll)),
                Decimal("0"),
            )

            if count <= 0:
                equity_curve.append((timestamp, bankroll))
                continue

            # Simulate trade
            price = float(signal.suggested_price_dollars)
            fee = float(
                EdgeDetector.compute_fee_dollars(count, price, is_maker=False)
            )

            # Determine P&L
            if signal.side == "yes":
                if actual_up:
                    pnl = count * (1.0 - price) - fee  # Win
                else:
                    pnl = -count * price - fee  # Lose
            else:
                if not actual_up:
                    pnl = count * (1.0 - price) - fee  # Win
                else:
                    pnl = -count * price - fee  # Lose

            bankroll += pnl
            peak_bankroll = max(peak_bankroll, bankroll)
            drawdown = peak_bankroll - bankroll
            max_drawdown = max(max_drawdown, drawdown)

            trade = BacktestTrade(
                timestamp=timestamp,
                market_ticker=signal.market_ticker,
                side=signal.side,
                count=count,
                price=price,
                model_prob=prediction.probability_yes,
                implied_prob=signal.implied_probability,
                edge=signal.net_edge,
                actual_up=bool(actual_up),
                pnl=pnl,
                fees=fee,
                bankroll_after=bankroll,
            )
            trades.append(trade)
            equity_curve.append((timestamp, bankroll))

        # Compute metrics
        result = self._compute_metrics(
            trades, equity_curve, initial_bankroll, max_drawdown
        )
        return result

    def _build_snapshot(
        self,
        timestamp: datetime,
        btc_price: float,
        yes_bid: float,
        yes_ask: float,
        row: pd.Series,
    ) -> MarketSnapshot:
        """Build a synthetic MarketSnapshot from backtest data."""
        no_bid = 1.0 - yes_ask  # NO bid = 1 - YES ask
        no_bid = max(0.01, no_bid)

        ob = Orderbook(
            ticker="backtest",
            yes_levels=[
                OrderbookLevel(
                    price_dollars=Decimal(str(round(yes_bid, 2))),
                    quantity=100,
                ),
            ],
            no_levels=[
                OrderbookLevel(
                    price_dollars=Decimal(str(round(no_bid, 2))),
                    quantity=100,
                ),
            ],
            timestamp=timestamp if isinstance(timestamp, datetime)
            else datetime.now(timezone.utc),
        )

        return MarketSnapshot(
            timestamp=timestamp if isinstance(timestamp, datetime)
            else datetime.now(timezone.utc),
            market_ticker="backtest",
            btc_price=Decimal(str(btc_price)),
            btc_prices_1min=[Decimal(str(btc_price))] * 60,
            btc_prices_5min=[Decimal(str(btc_price))] * 300,
            orderbook=ob,
            implied_yes_prob=ob.implied_yes_prob,
            spread=ob.spread,
            funding_rate=row.get("funding_rate"),
            time_to_expiry_seconds=450.0,  # Assume mid-window
            volume=int(row.get("volume", 100)),
        )

    @staticmethod
    def _compute_metrics(
        trades: list[BacktestTrade],
        equity_curve: list[tuple[datetime, float]],
        initial_bankroll: float,
        max_drawdown: float,
    ) -> BacktestResult:
        """Compute backtest performance metrics."""
        if not trades:
            return BacktestResult()

        pnls = [t.pnl for t in trades]
        winning = [t for t in trades if t.pnl > 0]
        losing = [t for t in trades if t.pnl <= 0]

        total_pnl = sum(pnls)
        win_rate = len(winning) / len(trades) if trades else 0

        # Sharpe ratio (annualized, assuming ~96 trades per day)
        pnl_array = np.array(pnls)
        if len(pnl_array) > 1 and np.std(pnl_array) > 0:
            sharpe = (
                np.mean(pnl_array) / np.std(pnl_array) * np.sqrt(252 * 96)
            )
        else:
            sharpe = 0.0

        # Profit factor
        total_wins = sum(t.pnl for t in winning) if winning else 0
        total_losses = abs(sum(t.pnl for t in losing)) if losing else 1
        profit_factor = total_wins / total_losses if total_losses > 0 else 0

        avg_edge = np.mean([t.edge for t in trades]) if trades else 0

        return BacktestResult(
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=round(win_rate, 4),
            total_pnl=round(total_pnl, 2),
            max_drawdown=round(max_drawdown, 2),
            sharpe_ratio=round(float(sharpe), 4),
            profit_factor=round(profit_factor, 4),
            avg_edge=round(float(avg_edge), 4),
            trades=trades,
            equity_curve=equity_curve,
        )
