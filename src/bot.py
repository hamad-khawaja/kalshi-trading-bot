"""Main bot orchestrator: wires all components and runs the async event loop."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import structlog

from src.config import BinanceConfig, BotSettings, load_settings
from src.data.binance_feed import BinanceFeed
from src.data.time_profile import TimeProfiler
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
from src.dashboard.server import DashboardServer, DashboardState
from src.strategy.averager import Averager
from src.strategy.edge_detector import EdgeDetector
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

        # Balance cache (avoid API call every 4s cycle)
        self._cached_balance: Decimal | None = None
        self._balance_fetched_at: float = 0.0  # monotonic time
        self._balance_cache_ttl: float = 30.0  # seconds

        # Prediction cache for thesis-break checks in monitor loop
        self._last_predictions: dict[str, object] = {}  # ticker → PredictionResult

        # Data layer
        self._auth = KalshiAuth(
            settings.kalshi.api_key_id,
            settings.kalshi.private_key_path,
        )
        self._kalshi_rest = KalshiRestClient(settings.kalshi, self._auth)
        self._kalshi_ws = KalshiWebSocket(settings.kalshi, self._auth)
        self._binance = BinanceFeed(settings.binance)
        self._secondary_feed: BinanceFeed | None = None
        if settings.secondary_feed.enabled:
            self._secondary_feed = BinanceFeed(
                BinanceConfig(
                    ws_url=settings.secondary_feed.ws_url,
                    symbol=settings.secondary_feed.symbol,
                )
            )
        self._coinglass = CoinglassClient(settings.coinglass)
        self._scanner = MarketScanner(self._kalshi_rest, settings.kalshi)
        self._data_hub = DataHub(
            self._kalshi_rest,
            self._kalshi_ws,
            self._binance,
            self._coinglass,
            self._scanner,
            secondary_feed=self._secondary_feed,
        )
        self._db = Database(settings.database.path)

        # Feature engine
        self._feature_engine = FeatureEngine(settings.features)

        # Model
        self._model: ProbabilityModel = HeuristicModel()

        # Risk
        self._position_sizer = PositionSizer(settings.risk)
        self._risk_manager = RiskManager(settings.risk)
        self._vol_tracker = VolatilityTracker()

        # Time profiler (optional, controlled by config)
        self._time_profiler: TimeProfiler | None = None
        if settings.strategy.use_time_profiles:
            self._time_profiler = TimeProfiler(
                lookback_days=settings.strategy.time_profile_lookback_days
            )

        # Strategy (needs vol_tracker, so must come after risk)
        self._signal_combiner = SignalCombiner(
            settings.strategy,
            vol_tracker=self._vol_tracker,
            time_profiler=self._time_profiler,
        )

        # Averaging
        self._averager: Averager | None = None
        if settings.averaging.enabled:
            self._averager = Averager(settings.averaging)

        # Execution
        self._order_manager = OrderManager(self._kalshi_rest, settings)
        self._position_tracker = PositionTracker(
            self._kalshi_rest, self._db, paper_mode=(settings.mode == "paper")
        )

        # Dashboard
        self._dashboard_state = DashboardState()
        self._dashboard_state.mode = settings.mode
        self._dashboard_server = DashboardServer(
            self._dashboard_state,
            settings.dashboard.host,
            settings.dashboard.port,
        ) if settings.dashboard.enabled else None

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

        # Start dashboard
        self._dashboard_state.start_time = self._start_time
        if self._dashboard_server:
            await self._dashboard_server.start()

        # Connect data sources
        await self._data_hub.start()

        # Fetch time profiles
        if self._time_profiler is not None:
            await self._time_profiler.fetch_hourly_klines()

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
        if self._time_profiler is not None:
            tasks.append(
                asyncio.create_task(
                    self._time_profile_refresh_loop(), name="time_profile_refresh"
                )
            )

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
        ds = self._dashboard_state
        ds.cycle = self._cycle_count

        # Get current market
        market = self._scanner.get_current_market()
        if market is None:
            ds.add_decision(self._cycle_count, "no_market", "No active market")
            return

        ticker = market.ticker
        close_time = market.close_time or market.expected_expiration_time or market.expiration_time
        ds.market = {
            "ticker": ticker,
            "title": market.title,
            "yes_sub_title": market.yes_sub_title,
            "expiry": str(market.expiration_time) if market.expiration_time else None,
            "close_time": close_time.isoformat() if close_time else None,
            "volume": market.volume,
        }

        # Build snapshot
        snapshot = await self._data_hub.get_snapshot(ticker)
        if snapshot is None:
            ds.add_decision(self._cycle_count, "no_market", f"No snapshot for {ticker}")
            return

        ob = snapshot.orderbook
        ds.snapshot = {
            "btc_price": float(snapshot.btc_price),
            "implied_prob": float(snapshot.implied_yes_prob) if snapshot.implied_yes_prob else None,
            "time_to_expiry": snapshot.time_to_expiry_seconds,
            "strike_price": float(snapshot.strike_price) if snapshot.strike_price else None,
            "statistical_fair_value": snapshot.statistical_fair_value,
            "binance_btc_price": float(snapshot.binance_btc_price) if snapshot.binance_btc_price else None,
            "cross_exchange_spread": snapshot.cross_exchange_spread,
            "cross_exchange_lead": snapshot.cross_exchange_lead,
            "liquidation_long_usd": snapshot.liquidation_long_usd,
            "liquidation_short_usd": snapshot.liquidation_short_usd,
            "taker_buy_volume": snapshot.taker_buy_volume,
            "taker_sell_volume": snapshot.taker_sell_volume,
            "orderbook": {
                "best_yes_bid": str(ob.best_yes_bid) if ob.best_yes_bid else None,
                "best_no_bid": str(ob.best_no_bid) if ob.best_no_bid else None,
                "spread": str(ob.spread) if ob.spread else None,
                "yes_depth": ob.yes_bid_depth,
                "no_depth": ob.no_bid_depth,
                "implied_prob": float(ob.implied_yes_prob) if ob.implied_yes_prob else None,
            },
        }

        # Compute features
        features = self._feature_engine.compute(snapshot)

        ds.features = {
            name: getattr(features, name, 0.0) or 0.0
            for name in features.feature_names()
        }

        # Update volatility tracker
        self._vol_tracker.update(features.realized_vol_5min)

        # Update model weights based on current session
        if self._time_profiler is not None and self._time_profiler.loaded:
            session = self._time_profiler.get_current_session()
            multipliers = self._time_profiler.get_weight_multipliers(session)
            self._model.set_weight_multipliers(multipliers)

        # Get model prediction
        prediction = self._model.predict(features)

        ds.prediction = {
            "probability": prediction.probability_yes,
            "confidence": prediction.confidence,
            "model": prediction.model_name,
            "signals": {
                "momentum": prediction.features_used.get("mom_signal", 0),
                "technical": prediction.features_used.get("tech_signal", 0),
                "flow": prediction.features_used.get("flow_signal", 0),
                "mean_reversion": prediction.features_used.get("mr_signal", 0),
                "funding": prediction.features_used.get("funding_signal", 0),
                "cross_exchange": prediction.features_used.get("cross_exchange_signal", 0),
                "liquidation": prediction.features_used.get("liquidation_signal", 0),
                "taker_flow": prediction.features_used.get("taker_signal", 0),
                "time_decay": prediction.features_used.get("time_decay_signal", 0),
            },
        }

        # Cache prediction for thesis-break checks
        self._last_predictions[ticker] = prediction

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
            prediction, snapshot, current_position, features=features
        )

        # Update edge analysis from the internal edge detector
        edge_analysis = self._signal_combiner._edge_detector.last_analysis
        if edge_analysis:
            ds.edge = edge_analysis

        # Update FOMO analysis from the internal FOMO detector
        if self._signal_combiner._fomo_detector is not None:
            fomo_analysis = self._signal_combiner._fomo_detector.last_analysis
            if fomo_analysis:
                ds.fomo = fomo_analysis

        if not signals:
            # --- Averaging: check existing position for discount add ---
            if self._averager:
                position = self._position_tracker.get_position(ticker)
                if position and position.count > 0:
                    avg_signal = self._averager.evaluate(
                        position, snapshot, prediction, features
                    )
                    if avg_signal:
                        signals = [avg_signal]

            if not signals:
                reason = edge_analysis.get("decision", "No signal generated") if edge_analysis else "No signal generated"
                ds.add_decision(self._cycle_count, "reject", reason)
                return

        ds.signals = [
            {"side": s.side, "type": s.signal_type, "edge": s.net_edge, "price": s.suggested_price_dollars}
            for s in signals
        ]

        # Get balance
        balance = await self._get_balance()

        # Skip market-making signals when balance is too low
        min_mm_balance = Decimal(str(self._settings.risk.min_balance_dollars * 2))
        if float(balance) < float(min_mm_balance):
            signals = [s for s in signals if s.signal_type != "market_making"]
            if not signals:
                ds.add_decision(self._cycle_count, "reject", "Balance too low for market making")
                return

        # Entry cooldown: skip buy signals if we recently filled on this market
        cooldown = self._settings.risk.entry_cooldown_seconds
        position = self._position_tracker.get_position(ticker)
        if position and position.count > 0 and cooldown > 0:
            elapsed = (datetime.now(timezone.utc) - position.last_fill_time).total_seconds()
            if elapsed < cooldown:
                buy_signals = [s for s in signals if s.action == "buy"]
                if buy_signals:
                    logger.info(
                        "entry_cooldown_active",
                        ticker=ticker,
                        elapsed=round(elapsed, 1),
                        cooldown=cooldown,
                        count=position.count,
                    )
                    # Only keep sell signals (take-profit etc), skip buys
                    signals = [s for s in signals if s.action != "buy"]
                    if not signals:
                        ds.add_decision(
                            self._cycle_count, "reject",
                            f"Entry cooldown: {round(elapsed)}s / {cooldown}s",
                        )
                        return

        # Per-cycle contract cap
        cycle_contracts_placed = 0
        max_per_cycle = self._settings.risk.max_contracts_per_cycle

        # Process each signal
        for signal_item in signals:
            # Per-cycle cap check
            if signal_item.action == "buy" and cycle_contracts_placed >= max_per_cycle:
                ds.add_decision(
                    self._cycle_count, "reject",
                    f"Cycle cap reached: {cycle_contracts_placed}/{max_per_cycle}",
                )
                break

            # Cancel conflicting orders (opposite side) before placing new ones
            opposite_side = "no" if signal_item.side == "yes" else "yes"
            await self._order_manager.cancel_market_orders(ticker, side=opposite_side)

            # Include resting orders in position count for sizing
            resting = self._order_manager.get_resting_order_count(
                ticker, signal_item.side
            )
            effective_position = abs(current_position) + resting

            # Size position
            count = self._position_sizer.size(
                signal_item,
                balance,
                self._position_tracker.total_exposure_dollars,
                effective_position,
                vol_tracker=self._vol_tracker,
            )

            # Apply averaging tier multiplier
            if signal_item.signal_type == "averaging" and signal_item.suggested_count > 0:
                multiplier = signal_item.suggested_count / 100.0
                count = max(1, int(count * multiplier))

            # Enforce per-cycle contract cap on buy signals
            if signal_item.action == "buy":
                remaining = max_per_cycle - cycle_contracts_placed
                if count > remaining:
                    count = remaining

            if count <= 0:
                max_pos = self._settings.risk.max_position_per_market
                if effective_position >= max_pos:
                    reason = f"MAX POSITION: {effective_position}/{max_pos} contracts in {signal_item.side.upper()}"
                else:
                    reason = f"Size=0 for {signal_item.side} (pos={effective_position}, bal=${float(balance):.0f})"
                ds.add_decision(self._cycle_count, "reject", reason)
                continue

            ds.sizing = {"count": count, "side": signal_item.side, "price": signal_item.suggested_price_dollars}

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
                logger.info(
                    "trade_rejected",
                    ticker=ticker,
                    reason=decision.reason,
                )
                ds.add_decision(
                    self._cycle_count, "reject",
                    f"Risk rejected: {decision.reason}",
                )
                continue

            final_count = decision.adjusted_count or count

            # Submit order
            order_id = await self._order_manager.submit(signal_item, final_count)

            if order_id:
                # Update position tracker if order filled immediately
                order_state = self._order_manager.get_order(order_id)
                if order_state and order_state.filled_count > 0:
                    self._position_tracker.update_on_fill(order_state)
                    self._risk_manager._trades_today += 1
                    if signal_item.action == "buy":
                        cycle_contracts_placed += order_state.filled_count
                    logger.info(
                        "trade_filled",
                        ticker=ticker,
                        side=signal_item.side,
                        count=order_state.filled_count,
                        price=signal_item.suggested_price_dollars,
                        edge=signal_item.net_edge,
                        model_prob=round(prediction.probability_yes, 4),
                        cycle=self._cycle_count,
                    )
                    ds.last_trade = {
                        "ticker": ticker,
                        "side": signal_item.side,
                        "count": order_state.filled_count,
                        "price": signal_item.suggested_price_dollars,
                        "edge": signal_item.net_edge,
                    }
                    ds.add_decision(
                        self._cycle_count, "trade",
                        f"TRADE: buy {order_state.filled_count}x {signal_item.side.upper()} @ {signal_item.suggested_price_dollars}, edge={signal_item.net_edge:.4f}",
                    )
                    # Update dashboard positions/risk immediately
                    self._update_dashboard_positions()
                else:
                    logger.info(
                        "order_resting",
                        ticker=ticker,
                        side=signal_item.side,
                        count=final_count,
                        price=signal_item.suggested_price_dollars,
                        edge=signal_item.net_edge,
                        cycle=self._cycle_count,
                    )
                    ds.add_decision(
                        self._cycle_count, "trade",
                        f"RESTING: {final_count}x {signal_item.side.upper()} @ {signal_item.suggested_price_dollars}, edge={signal_item.net_edge:.4f}",
                    )

    async def _market_scan_loop(self) -> None:
        """Scan for new markets, polling every 1s after a market expires.

        Two triggers for fast polling:
        1. Clock-based: first 30s after :00/:15/:30/:45 boundaries
        2. Event-based: when active market count drops (expiry detected)
        Fast poll runs every 1s until a new market is found.
        """
        from datetime import datetime, timedelta, timezone

        fast_poll_until: datetime | None = None

        while self._running:
            try:
                now = datetime.now(timezone.utc)

                # Determine if we should fast-poll
                # Fast-poll 15s before and 30s after each :00/:15/:30/:45
                seconds_in_window = (now.minute % 15) * 60 + now.second
                seconds_to_boundary = 15 * 60 - seconds_in_window
                near_boundary = seconds_in_window < 30 or seconds_to_boundary < 15
                in_fast_poll = fast_poll_until is not None and now < fast_poll_until

                if near_boundary or in_fast_poll:
                    sleep_time = 1
                else:
                    sleep_time = 10

                await asyncio.sleep(sleep_time)
                if not self._running:
                    break

                prev_tickers = set(self._scanner.active_markets.keys())
                prev_count = len(prev_tickers)
                await self._scanner.scan()
                new_tickers = set(self._scanner.active_markets.keys())

                # If a market disappeared (expired), start fast polling
                lost = prev_tickers - new_tickers
                if lost:
                    fast_poll_until = datetime.now(timezone.utc) + timedelta(seconds=30)
                    logger.debug(
                        "fast_poll_triggered",
                        expired=list(lost),
                        until=fast_poll_until.isoformat(),
                    )

                # Subscribe to new markets
                for ticker in new_tickers - prev_tickers:
                    await self._data_hub.subscribe_market(ticker)
                    logger.info("new_market_found", ticker=ticker)
                    # Found new market, stop fast polling
                    fast_poll_until = None

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
                orphaned_tickers = []
                for ticker in list(self._position_tracker._positions.keys()):
                    snap = await self._data_hub.get_snapshot(ticker)
                    if snap:
                        snapshots[ticker] = snap
                    else:
                        # No snapshot = market likely expired and removed from scanner
                        orphaned_tickers.append(ticker)

                exits = self._position_tracker.check_exits(snapshots)
                # Add orphaned tickers (no snapshot) — these are expired markets
                exits.extend(
                    t for t in orphaned_tickers if t not in exits
                )

                if exits:
                    actually_settled = []
                    for exit_ticker in exits:
                        pos = self._position_tracker.get_position(exit_ticker)
                        if not pos:
                            actually_settled.append(exit_ticker)
                            continue

                        cost = pos.avg_entry_price * pos.count

                        if self._settings.mode != "paper":
                            # Live mode: require actual settlement result
                            settlement_result = None
                            try:
                                settlement_result = await self._kalshi_rest.get_market_result(exit_ticker)
                            except Exception:
                                pass

                            if settlement_result is not None:
                                # Actual settlement: YES pays $1/contract, NO pays $0
                                won = (settlement_result == pos.side)
                                payout = Decimal(str(pos.count)) if won else Decimal("0")
                                pnl = payout - cost
                                logger.info(
                                    "position_settled_actual",
                                    ticker=exit_ticker,
                                    side=pos.side,
                                    result=settlement_result,
                                    won=won,
                                    count=pos.count,
                                    entry_price=float(pos.avg_entry_price),
                                    pnl=float(pnl),
                                )
                                self._risk_manager.record_trade(pnl)
                                actually_settled.append(exit_ticker)
                            else:
                                # Result not available yet — keep position, retry next cycle
                                logger.info(
                                    "settlement_pending",
                                    ticker=exit_ticker,
                                    side=pos.side,
                                    count=pos.count,
                                )
                        else:
                            # Paper mode: estimate from implied prob
                            snap = snapshots.get(exit_ticker)
                            if snap and snap.implied_yes_prob is not None:
                                settle = float(snap.implied_yes_prob) if pos.side == "yes" else (1.0 - float(snap.implied_yes_prob))
                                pnl = Decimal(str(settle)) * pos.count - cost
                            else:
                                pnl = -cost  # Paper mode fallback
                            logger.info(
                                "position_settled_estimated",
                                ticker=exit_ticker,
                                side=pos.side,
                                count=pos.count,
                                entry_price=float(pos.avg_entry_price),
                                pnl=float(pnl),
                            )
                            self._risk_manager.record_trade(pnl)
                            actually_settled.append(exit_ticker)

                    if actually_settled:
                        self._position_tracker.remove_expired_positions(actually_settled)
                        self._update_dashboard_positions()

                # Check for take-profit opportunities
                if self._settings.strategy.take_profit_enabled:
                    tp_signals = self._position_tracker.check_take_profit(
                        snapshots, self._settings.strategy
                    )
                    for tp_ticker, sell_price in tp_signals:
                        pos = self._position_tracker.get_position(tp_ticker)
                        if not pos:
                            continue
                        from src.data.models import TradeSignal

                        sell_signal = TradeSignal(
                            market_ticker=tp_ticker,
                            side=pos.side,
                            action="sell",
                            raw_edge=0.0,
                            net_edge=0.0,
                            model_probability=0.0,
                            implied_probability=0.0,
                            confidence=1.0,
                            suggested_price_dollars=sell_price,
                            suggested_count=pos.count,
                            timestamp=datetime.now(timezone.utc),
                            signal_type="directional",
                        )
                        order_id = await self._order_manager.submit(sell_signal, pos.count)
                        if order_id:
                            entry_cost = pos.avg_entry_price * pos.count
                            exit_revenue = Decimal(sell_price) * pos.count
                            sell_fee = EdgeDetector.compute_fee_dollars(
                                pos.count, float(sell_price), is_maker=False
                            )
                            pnl = exit_revenue - entry_cost - sell_fee
                            logger.info(
                                "take_profit_executed",
                                ticker=tp_ticker,
                                side=pos.side,
                                count=pos.count,
                                entry_price=float(pos.avg_entry_price),
                                exit_price=sell_price,
                                pnl=float(pnl),
                                fee=float(sell_fee),
                            )
                            self._risk_manager.record_trade(pnl)
                            self._position_tracker.remove_expired_positions([tp_ticker])
                            self._update_dashboard_positions()

                # Check for thesis breaks — sell positions where model flipped
                if self._settings.strategy.thesis_break_enabled and self._last_predictions:
                    thesis_breaks = self._position_tracker.check_thesis_breaks(
                        self._last_predictions,
                        threshold=self._settings.strategy.thesis_break_threshold,
                    )
                    for tb_ticker in thesis_breaks:
                        pos = self._position_tracker.get_position(tb_ticker)
                        if not pos:
                            continue
                        snap = snapshots.get(tb_ticker)
                        if not snap:
                            continue
                        # Get best bid on our side for exit price
                        ob = snap.orderbook
                        if pos.side == "yes":
                            exit_bid = ob.best_yes_bid
                        else:
                            exit_bid = ob.best_no_bid
                        if exit_bid is None:
                            continue
                        sell_price = str(exit_bid)
                        from src.data.models import TradeSignal

                        sell_signal = TradeSignal(
                            market_ticker=tb_ticker,
                            side=pos.side,
                            action="sell",
                            raw_edge=0.0,
                            net_edge=0.0,
                            model_probability=0.0,
                            implied_probability=0.0,
                            confidence=1.0,
                            suggested_price_dollars=sell_price,
                            suggested_count=pos.count,
                            timestamp=datetime.now(timezone.utc),
                            signal_type="directional",
                        )
                        order_id = await self._order_manager.submit(sell_signal, pos.count)
                        if order_id:
                            entry_cost = pos.avg_entry_price * pos.count
                            exit_revenue = Decimal(sell_price) * pos.count
                            sell_fee = EdgeDetector.compute_fee_dollars(
                                pos.count, float(sell_price), is_maker=False
                            )
                            pnl = exit_revenue - entry_cost - sell_fee
                            logger.info(
                                "thesis_break_exit",
                                ticker=tb_ticker,
                                side=pos.side,
                                count=pos.count,
                                entry_price=float(pos.avg_entry_price),
                                exit_price=sell_price,
                                pnl=float(pnl),
                            )
                            self._risk_manager.record_trade(pnl)
                            self._position_tracker.remove_expired_positions([tb_ticker])
                            self._update_dashboard_positions()

                # Check for fills on resting orders (live mode only)
                newly_filled = await self._order_manager.check_resting_fills()
                for filled_state in newly_filled:
                    self._position_tracker.update_on_fill(filled_state)
                    self._risk_manager._trades_today += 1
                    logger.info(
                        "resting_order_fill_detected",
                        ticker=filled_state.signal.market_ticker,
                        side=filled_state.signal.side,
                        filled=filled_state.filled_count,
                    )
                    self._update_dashboard_positions()

                # Compute and push unrealized PNL to dashboard
                unrealized_pnl = self._position_tracker.compute_unrealized_pnl(snapshots)
                self._dashboard_state.risk["unrealized_pnl"] = float(unrealized_pnl)
                self._dashboard_state.risk["total_pnl"] = float(
                    self._risk_manager.daily_pnl + unrealized_pnl
                )

                # Cancel stale resting orders (older than 90s)
                await self._order_manager.cancel_stale_orders(max_age_seconds=90)

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

    async def _time_profile_refresh_loop(self) -> None:
        """Re-fetch Binance klines every 6 hours to keep profiles fresh."""
        while self._running:
            try:
                await asyncio.sleep(6 * 3600)
                if not self._running:
                    break
                await self._time_profiler.fetch_hourly_klines()
                logger.info("time_profile_refreshed")
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("time_profile_refresh_error")

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

                # Update dashboard risk/position state
                effective_balance = float(balance + self._risk_manager.daily_pnl) if self._settings.mode == "paper" else float(balance)
                prev_unrealized = self._dashboard_state.risk.get("unrealized_pnl", 0.0)
                daily_pnl = float(self._risk_manager.daily_pnl)
                self._dashboard_state.risk = {
                    "balance": effective_balance,
                    "daily_pnl": daily_pnl,
                    "unrealized_pnl": prev_unrealized,
                    "total_pnl": daily_pnl + prev_unrealized,
                    "trades_today": self._risk_manager.trades_today,
                    "consecutive_losses": self._risk_manager.consecutive_losses,
                    "consecutive_wins": self._risk_manager.consecutive_wins,
                    "win_rate": self._risk_manager.win_rate,
                    "total_settled": self._risk_manager.total_settled,
                    "last_pnl": float(self._risk_manager.last_pnl) if self._risk_manager.last_pnl is not None else None,
                    "vol_regime": self._vol_tracker.current_regime,
                    "exposure": float(self._position_tracker.total_exposure_dollars),
                }
                self._dashboard_state.positions = [
                    {
                        "ticker": p.market_ticker,
                        "side": p.side,
                        "count": p.count,
                        "avg_price": str(p.avg_entry_price),
                    }
                    for p in positions
                ]

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("health_check_error")

    async def _get_balance(self, force: bool = False) -> Decimal:
        """Get account balance, with caching to avoid per-cycle API calls.

        Live mode: caches for 30s. Paper mode: returns fixed value instantly.
        Pass force=True to bypass cache (e.g. after a trade).
        """
        if self._settings.mode == "paper":
            return Decimal(str(self._settings.risk.max_total_exposure_dollars * 2))

        import time
        now = time.monotonic()
        if (
            not force
            and self._cached_balance is not None
            and (now - self._balance_fetched_at) < self._balance_cache_ttl
        ):
            return self._cached_balance

        try:
            balance = await self._kalshi_rest.get_balance()
            self._cached_balance = balance
            self._balance_fetched_at = now
            return balance
        except Exception:
            logger.warning("balance_fetch_error")
            # Return stale cache if available, otherwise 0
            return self._cached_balance if self._cached_balance is not None else Decimal("0")

    def _update_dashboard_positions(self) -> None:
        """Push current positions and risk stats to the dashboard immediately."""
        positions = self._position_tracker.get_all_positions()
        self._dashboard_state.positions = [
            {
                "ticker": p.market_ticker,
                "side": p.side,
                "count": p.count,
                "avg_price": str(p.avg_entry_price),
            }
            for p in positions
        ]
        paper_start = Decimal(str(self._settings.risk.max_total_exposure_dollars * 2))
        balance = float(paper_start + self._risk_manager.daily_pnl) if self._settings.mode == "paper" else 0.0
        # Preserve unrealized_pnl/total_pnl from position monitor if already set
        prev_unrealized = self._dashboard_state.risk.get("unrealized_pnl", 0.0)
        prev_total = self._dashboard_state.risk.get("total_pnl", float(self._risk_manager.daily_pnl))
        self._dashboard_state.risk = {
            "balance": balance,
            "daily_pnl": float(self._risk_manager.daily_pnl),
            "unrealized_pnl": prev_unrealized,
            "total_pnl": prev_total,
            "trades_today": self._risk_manager.trades_today,
            "consecutive_losses": self._risk_manager.consecutive_losses,
            "consecutive_wins": self._risk_manager.consecutive_wins,
            "win_rate": self._risk_manager.win_rate,
            "total_settled": self._risk_manager.total_settled,
            "last_pnl": float(self._risk_manager.last_pnl) if self._risk_manager.last_pnl is not None else None,
            "vol_regime": self._vol_tracker.current_regime,
            "exposure": float(self._position_tracker.total_exposure_dollars),
        }

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

        # Stop dashboard
        if self._dashboard_server:
            try:
                await self._dashboard_server.stop()
            except Exception:
                logger.exception("shutdown_dashboard_error")

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
        renderer = structlog.processors.JSONRenderer()
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
            structlog._log_levels.NAME_TO_LEVEL[log_level.lower()]
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="kalshi-bot",
        description="Kalshi Bitcoin 15-minute binary options trading bot",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version="kalshi-btc-bot 0.1.0",
    )
    parser.add_argument(
        "-c", "--config",
        default="config/settings.yaml",
        help="path to settings YAML file (default: config/settings.yaml)",
    )
    parser.add_argument(
        "-m", "--mode",
        choices=["paper", "live"],
        default=None,
        help="trading mode — overrides config file",
    )
    parser.add_argument(
        "-e", "--env",
        choices=["demo", "prod"],
        default=None,
        help="Kalshi environment — overrides config file",
    )
    parser.add_argument(
        "-l", "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="log level — overrides config file",
    )
    parser.add_argument(
        "--max-exposure",
        type=float,
        default=None,
        help="max total exposure in dollars — overrides config file",
    )
    parser.add_argument(
        "--max-daily-loss",
        type=float,
        default=None,
        help="max daily loss in dollars — overrides config file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="shortcut for --mode paper --env demo",
    )
    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    # Load config from YAML
    settings = load_settings(args.config)

    # Apply --dry-run defaults (overridden by explicit --mode/--env)
    if args.dry_run:
        if args.mode is None:
            settings.mode = "paper"
        if args.env is None:
            settings.kalshi.environment = "demo"

    # Apply explicit CLI overrides
    if args.mode is not None:
        settings.mode = args.mode
    if args.env is not None:
        settings.kalshi.environment = args.env
    if args.log_level is not None:
        settings.logging.level = args.log_level
    if args.max_exposure is not None:
        settings.risk.max_total_exposure_dollars = args.max_exposure
    if args.max_daily_loss is not None:
        settings.risk.max_daily_loss_dollars = args.max_daily_loss

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
