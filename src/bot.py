"""Main bot orchestrator: wires all components and runs the async event loop."""

from __future__ import annotations

import asyncio
import signal
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import structlog

from src.config import BotSettings, load_settings
from src.data.binance_feed import BinanceFeed
from src.data.coinglass_client import CoinglassClient
from src.data.data_hub import DataHub
from src.data.database import Database
from src.data.kalshi_auth import KalshiAuth
from src.data.kalshi_client import KalshiRestClient
from src.data.kalshi_ws import KalshiWebSocket
from src.data.market_scanner import MarketScanner
from src.execution.order_manager import OrderManager
from src.execution.position_tracker import PositionTracker
from src.features.feature_engine import FeatureEngine
from src.model.predict import HeuristicModel, ProbabilityModel
from src.risk.position_sizer import PositionSizer
from src.risk.risk_manager import RiskManager
from src.risk.volatility import VolatilityTracker
from src.strategy.signal_combiner import SignalCombiner

logger = structlog.get_logger()


class TradingBot:
    """Main bot orchestrator.

    Initializes all components and runs concurrent asyncio tasks:
    1. strategy_loop — core trading logic every N seconds
    2. market_scan_loop — discover new markets every 60s
    3. position_monitor_loop — check exits every 10s
    4. coinglass_poll_loop — refresh funding/OI data every 30s
    5. health_check_loop — log bot status every 60s
    """

    def __init__(self, settings: BotSettings):
        self._settings = settings
        self._running = False
        self._cycle_count = 0
        self._start_time: datetime | None = None

        # Data layer
        self._auth = KalshiAuth(
            settings.kalshi.api_key_id,
            settings.kalshi.private_key_path,
        )
        self._kalshi_rest = KalshiRestClient(settings.kalshi, self._auth)
        self._kalshi_ws = KalshiWebSocket(settings.kalshi, self._auth)
        self._binance = BinanceFeed(settings.binance)
        self._coinglass = CoinglassClient(settings.coinglass)
        self._scanner = MarketScanner(self._kalshi_rest, settings.kalshi)
        self._data_hub = DataHub(
            self._kalshi_rest,
            self._kalshi_ws,
            self._binance,
            self._coinglass,
            self._scanner,
        )
        self._db = Database(settings.database.path)

        # Feature engine
        self._feature_engine = FeatureEngine(settings.features)

        # Model
        self._model: ProbabilityModel = HeuristicModel()

        # Strategy
        self._signal_combiner = SignalCombiner(settings.strategy)

        # Risk
        self._position_sizer = PositionSizer(settings.risk)
        self._risk_manager = RiskManager(settings.risk)
        self._vol_tracker = VolatilityTracker()

        # Execution
        self._order_manager = OrderManager(self._kalshi_rest, settings)
        self._position_tracker = PositionTracker(
            self._kalshi_rest, self._db, paper_mode=(settings.mode == "paper")
        )

    async def start(self) -> None:
        """Main entry point: connect, subscribe, and run concurrent loops."""
        self._running = True
        self._start_time = datetime.now(timezone.utc)

        logger.info(
            "bot_starting",
            mode=self._settings.mode,
            environment=self._settings.kalshi.environment,
            series=self._settings.kalshi.series_ticker,
        )

        # Setup signal handlers
        self._setup_signal_handlers()

        # Initialize database
        await self._db.connect()

        # Connect data sources
        await self._data_hub.start()

        # Initial market scan
        await self._scanner.scan()

        # Subscribe to active markets
        for ticker in self._scanner.active_markets:
            await self._data_hub.subscribe_market(ticker)

        logger.info(
            "bot_started",
            active_markets=len(self._scanner.active_markets),
        )

        # Run concurrent tasks
        tasks = [
            asyncio.create_task(self._strategy_loop(), name="strategy"),
            asyncio.create_task(self._market_scan_loop(), name="market_scan"),
            asyncio.create_task(self._position_monitor_loop(), name="position_monitor"),
            asyncio.create_task(self._coinglass_poll_loop(), name="coinglass_poll"),
            asyncio.create_task(self._health_check_loop(), name="health_check"),
        ]

        try:
            # Wait until shutdown signal
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def _strategy_loop(self) -> None:
        """Core trading loop — runs every poll_interval_seconds."""
        interval = self._settings.strategy.poll_interval_seconds

        # Wait for initial data
        await asyncio.sleep(2)

        while self._running:
            try:
                await self._run_one_cycle()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("strategy_cycle_error")
                await asyncio.sleep(10)  # Back off on unexpected errors
            else:
                await asyncio.sleep(interval)

    async def _run_one_cycle(self) -> None:
        """Execute one strategy cycle."""
        self._cycle_count += 1

        # Get current market
        market = self._scanner.get_current_market()
        if market is None:
            return

        ticker = market.ticker

        # Build snapshot
        snapshot = await self._data_hub.get_snapshot(ticker)
        if snapshot is None:
            return

        # Compute features
        features = self._feature_engine.compute(snapshot)

        # Update volatility tracker
        self._vol_tracker.update(features.realized_vol_5min)

        # Get model prediction
        prediction = self._model.predict(features)

        # Log prediction for data collection
        try:
            implied = float(snapshot.implied_yes_prob) if snapshot.implied_yes_prob else 0.5
            await self._db.insert_prediction(
                market_ticker=ticker,
                prediction=prediction,
                implied_prob=implied,
                edge=abs(prediction.probability_yes - implied),
            )
        except Exception:
            pass  # Non-critical

        # Get current position for this market
        current_position = self._position_tracker.get_market_position_count(ticker)

        # Generate signals
        signals = self._signal_combiner.evaluate(
            prediction, snapshot, current_position
        )

        if not signals:
            return

        # Get balance
        balance = await self._get_balance()

        # Process each signal
        for signal_item in signals:
            # Size position
            count = self._position_sizer.size(
                signal_item,
                balance,
                self._position_tracker.total_exposure_dollars,
                abs(current_position),
            )

            if count <= 0:
                continue

            # Risk check
            positions = self._position_tracker.get_all_positions()
            # Convert PositionState to Position for risk manager
            from src.data.models import Position

            position_models = [
                Position(
                    ticker=p.market_ticker,
                    market_exposure=p.count if p.side == "yes" else -p.count,
                )
                for p in positions
            ]

            decision = self._risk_manager.check(
                signal_item,
                count,
                balance,
                position_models,
                snapshot.time_to_expiry_seconds,
            )

            if not decision.approved:
                logger.debug(
                    "trade_rejected",
                    ticker=ticker,
                    reason=decision.reason,
                )
                continue

            final_count = decision.adjusted_count or count

            # Submit order
            order_id = await self._order_manager.submit(signal_item, final_count)

            if order_id:
                # Update position tracker
                order_state = self._order_manager.get_order(order_id)
                if order_state and order_state.filled_count > 0:
                    self._position_tracker.update_on_fill(order_state)

                logger.info(
                    "trade_executed",
                    ticker=ticker,
                    side=signal_item.side,
                    count=final_count,
                    price=signal_item.suggested_price_dollars,
                    edge=signal_item.net_edge,
                    model_prob=round(prediction.probability_yes, 4),
                    cycle=self._cycle_count,
                )

    async def _market_scan_loop(self) -> None:
        """Scan for new markets every 60 seconds."""
        while self._running:
            try:
                await asyncio.sleep(60)
                if not self._running:
                    break

                prev_tickers = set(self._scanner.active_markets.keys())
                await self._scanner.scan()
                new_tickers = set(self._scanner.active_markets.keys())

                # Subscribe to new markets
                for ticker in new_tickers - prev_tickers:
                    await self._data_hub.subscribe_market(ticker)
                    logger.info("new_market_found", ticker=ticker)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("market_scan_error")
                await asyncio.sleep(30)

    async def _position_monitor_loop(self) -> None:
        """Monitor positions and handle exits every 10 seconds."""
        while self._running:
            try:
                await asyncio.sleep(10)
                if not self._running:
                    break

                # Sync with exchange
                await self._position_tracker.sync_from_exchange()

                # Check for expired positions
                snapshots = {}
                for ticker in list(self._position_tracker._positions.keys()):
                    snap = await self._data_hub.get_snapshot(ticker)
                    if snap:
                        snapshots[ticker] = snap

                exits = self._position_tracker.check_exits(snapshots)
                if exits:
                    self._position_tracker.remove_expired_positions(exits)

                # Cleanup old terminal orders
                self._order_manager.cleanup_terminal_orders()

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("position_monitor_error")

    async def _coinglass_poll_loop(self) -> None:
        """Refresh Coinglass data every 30 seconds."""
        while self._running:
            try:
                await asyncio.sleep(30)
                if not self._running:
                    break
                await self._coinglass.refresh_all()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("coinglass_poll_error")

    async def _health_check_loop(self) -> None:
        """Log bot health status every 60 seconds."""
        while self._running:
            try:
                await asyncio.sleep(60)
                if not self._running:
                    break

                balance = await self._get_balance()
                positions = self._position_tracker.get_all_positions()

                logger.info(
                    "health_check",
                    uptime_seconds=(
                        (datetime.now(timezone.utc) - self._start_time).total_seconds()
                        if self._start_time
                        else 0
                    ),
                    mode=self._settings.mode,
                    balance=float(balance),
                    positions=len(positions),
                    daily_pnl=float(self._risk_manager.daily_pnl),
                    trades_today=self._risk_manager.trades_today,
                    active_markets=len(self._scanner.active_markets),
                    cycles=self._cycle_count,
                    btc_price=float(self._binance.latest_price or 0),
                    vol_regime=self._vol_tracker.current_regime,
                    consecutive_losses=self._risk_manager.consecutive_losses,
                )

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("health_check_error")

    async def _get_balance(self) -> Decimal:
        """Get account balance, with fallback for paper mode."""
        if self._settings.mode == "paper":
            # Paper mode: start with configured max exposure as balance
            return Decimal(str(self._settings.risk.max_total_exposure_dollars * 2))
        try:
            return await self._kalshi_rest.get_balance()
        except Exception:
            logger.warning("balance_fetch_error")
            return Decimal("0")

    async def shutdown(self) -> None:
        """Graceful shutdown: cancel orders, close connections, persist state."""
        if not self._running:
            return
        self._running = False

        logger.info("bot_shutting_down")

        # Cancel all open orders
        try:
            canceled = await self._order_manager.cancel_all()
            if canceled:
                logger.info("orders_canceled_on_shutdown", count=canceled)
        except Exception:
            logger.exception("shutdown_cancel_error")

        # Close data sources
        try:
            await self._data_hub.stop()
        except Exception:
            logger.exception("shutdown_data_hub_error")

        # Close database
        try:
            await self._db.close()
        except Exception:
            logger.exception("shutdown_db_error")

        logger.info(
            "bot_stopped",
            total_cycles=self._cycle_count,
            uptime_seconds=(
                (datetime.now(timezone.utc) - self._start_time).total_seconds()
                if self._start_time
                else 0
            ),
        )

    def _setup_signal_handlers(self) -> None:
        """Register SIGINT/SIGTERM for graceful shutdown."""
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig,
                lambda: asyncio.create_task(self._handle_signal()),
            )

    async def _handle_signal(self) -> None:
        """Handle shutdown signal."""
        logger.info("shutdown_signal_received")
        self._running = False
        # Cancel all running tasks
        for task in asyncio.all_tasks():
            if task != asyncio.current_task():
                task.cancel()


def configure_logging(settings: BotSettings) -> None:
    """Configure structured logging."""
    log_level = settings.logging.level.upper()

    # Ensure log directory exists
    log_file = settings.logging.file
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    if settings.logging.format == "json":
        renderer = structlog.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            structlog.get_level_from_name(log_level)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def main() -> None:
    """CLI entry point."""
    # Load config
    config_path = "config/settings.yaml"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    settings = load_settings(config_path)

    # Configure logging
    configure_logging(settings)

    logger.info(
        "kalshi_btc_bot_starting",
        mode=settings.mode,
        environment=settings.kalshi.environment,
        series=settings.kalshi.series_ticker,
        min_edge=settings.strategy.min_edge_threshold,
        kelly_fraction=settings.risk.kelly_fraction,
    )

    # Create and run bot
    bot = TradingBot(settings)

    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
    except Exception:
        logger.exception("fatal_error")
        sys.exit(1)


if __name__ == "__main__":
    main()
