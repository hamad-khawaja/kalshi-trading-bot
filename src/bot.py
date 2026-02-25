"""Main bot orchestrator: wires all components and runs the async event loop."""

from __future__ import annotations

import argparse
import asyncio
import math
import signal
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import structlog

from src.config import AssetConfig, BinanceConfig, BotSettings, KalshiConfig, load_settings
from src.data.binance_feed import BinanceFeed
from src.data.binance_futures_feed import BinanceFuturesFeed
from src.data.chainlink_feed import ChainlinkFeed
from src.data.time_profile import TimeProfiler
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
    4. health_check_loop — log bot status every 60s
    """

    def __init__(self, settings: BotSettings):
        self._settings = settings
        self._running = False
        self._cycle_count = 0
        self._start_time: datetime | None = None

        # Balance cache (avoid API call every 4s cycle)
        self._cached_balance: Decimal | None = None
        self._balance_fetched_at: float = 0.0  # monotonic time
        self._balance_cache_ttl: float = 10.0  # seconds

        # Prediction cache for thesis-break checks in monitor loop
        self._last_predictions: dict[str, object] = {}  # ticker → PredictionResult

        # Thesis-break cooldown: don't re-enter markets after getting stopped out
        # ticker → strategy_tag that caused the exit
        self._thesis_break_markets: dict[str, str] = {}

        # Take-profit cooldown: block re-entry after TP for rest of window
        # ticker → (exit_time, strategy_tag)
        self._take_profit_markets: dict[str, tuple[datetime, str]] = {}

        # Stop-loss cooldown: block directional re-entry after SL on same market
        # ticker → set (one shot per market — if thesis was wrong, don't retry)
        self._stop_loss_markets: set[str] = set()

        # Fee tracking for dashboard
        self._total_fees: Decimal = Decimal("0")

        # Background settlement polling tasks (non-blocking)
        self._pending_settlements: dict[str, asyncio.Task] = {}

        # Data layer
        self._auth = KalshiAuth(
            settings.kalshi.api_key_id,
            settings.kalshi.private_key_path,
        )
        self._kalshi_rest = KalshiRestClient(settings.kalshi, self._auth)
        self._kalshi_ws = KalshiWebSocket(settings.kalshi, self._auth)

        # Per-asset price feeds and scanners
        self._feeds: dict[str, BinanceFeed] = {}
        self._secondary_feeds: dict[str, BinanceFeed] = {}
        self._chainlink_feeds: dict[str, ChainlinkFeed] = {}
        self._scanners: dict[str, MarketScanner] = {}

        for asset in settings.kalshi.assets:
            # Primary price feed
            self._feeds[asset.symbol] = BinanceFeed(
                BinanceConfig(ws_url=asset.primary_ws_url, symbol=asset.primary_symbol)
            )
            # Secondary price feed (optional)
            if asset.secondary_ws_url:
                self._secondary_feeds[asset.symbol] = BinanceFeed(
                    BinanceConfig(ws_url=asset.secondary_ws_url, symbol=asset.secondary_symbol)
                )
            # Chainlink oracle feed (optional)
            if asset.chainlink_contract:
                self._chainlink_feeds[asset.symbol] = ChainlinkFeed(
                    symbol=asset.symbol,
                    contract_address=asset.chainlink_contract,
                    rpc_url=asset.chainlink_rpc_url or None,
                )
            # Market scanner with per-asset series_ticker
            scanner_config = settings.kalshi.model_copy(update={"series_ticker": asset.series_ticker})
            self._scanners[asset.symbol] = MarketScanner(self._kalshi_rest, scanner_config)

        # Binance Futures feed (funding rate + liquidations)
        self._futures_feed: BinanceFuturesFeed | None = None
        if settings.binance_futures.enabled:
            fc = settings.binance_futures
            self._futures_feed = BinanceFuturesFeed(
                symbols=fc.symbols,
                funding_poll_interval=fc.funding_poll_interval,
                liquidation_ws_url=fc.liquidation_ws_url,
                funding_api_base=fc.funding_api_base,
            )

        self._data_hub = DataHub(
            self._kalshi_rest,
            self._kalshi_ws,
            feeds=self._feeds,
            scanners=self._scanners,
            secondary_feeds=self._secondary_feeds,
            chainlink_feeds=self._chainlink_feeds,
            futures_feed=self._futures_feed,
            strategy_config=settings.strategy,
            asset_configs=settings.kalshi.assets,
        )
        self._db = Database(settings.database.path)

        # Dashboard (must be before feature engine which references settlement_history)
        self._dashboard_state = DashboardState()
        self._dashboard_state.mode = settings.mode
        if settings.strategy.quiet_hours_enabled:
            self._dashboard_state.quiet_hours_est = settings.strategy.quiet_hours_est
        self._dashboard_state.strategy_toggles = {
            "directional": settings.strategy.directional_enabled,
            "fomo": settings.strategy.fomo_enabled,
            "certainty_scalp": settings.strategy.certainty_scalp_enabled,
            "settlement_ride": settings.strategy.settlement_ride_enabled,
            "trend_continuation": settings.strategy.trend_continuation_enabled,
            "market_making": settings.strategy.use_market_maker,
            "phase_filter": settings.strategy.phase_filter_enabled,
            "trend_guard": settings.strategy.trend_guard_enabled,
            "mm_vol_filter": settings.strategy.mm_vol_filter_enabled,
        }
        # Snapshot startup config for dashboard Settings tab
        self._dashboard_state.startup_config = {
            "mode": settings.mode,
            "strategy": settings.strategy.model_dump(),
            "risk": settings.risk.model_dump(),
            "features": settings.features.model_dump(),
            "averaging": settings.averaging.model_dump(),
        }
        self._dashboard_server = DashboardServer(
            self._dashboard_state,
            settings.dashboard.host,
            settings.dashboard.port,
            db=self._db,
        ) if settings.dashboard.enabled else None

        # Feature engine (pass shared settlement_history dict reference)
        self._feature_engine = FeatureEngine(
            settings.features,
            settlement_history=self._dashboard_state.settlement_history,
        )

        # Model
        self._model: ProbabilityModel = HeuristicModel()

        # Risk
        self._position_sizer = PositionSizer(settings.risk, strategy_config=settings.strategy)
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
            settlement_history=self._dashboard_state.settlement_history,
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

    async def start(self) -> None:
        """Main entry point: connect, subscribe, and run concurrent loops."""
        self._running = True
        self._start_time = datetime.now(timezone.utc)

        asset_symbols = [a.symbol for a in self._settings.kalshi.assets]
        logger.info(
            "bot_starting",
            mode=self._settings.mode,
            assets=asset_symbols,
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

        # Fetch time profiles (once per asset)
        if self._time_profiler is not None:
            for asset in self._settings.kalshi.assets:
                binance_symbol = asset.symbol + "USDT"  # e.g. "BTCUSDT", "ETHUSDT"
                await self._time_profiler.fetch_hourly_klines(binance_symbol)

        # Initial market scan — all scanners
        total_active = 0
        for scanner in self._scanners.values():
            await scanner.scan()
            for ticker in scanner.active_markets:
                await self._data_hub.subscribe_market(ticker)
            total_active += len(scanner.active_markets)

        # Initial settlement history fetch so the feature is available from cycle 1
        try:
            for asset in self._settings.kalshi.assets:
                settled = await self._kalshi_rest.get_settled_markets(
                    asset.series_ticker, limit=5
                )
                self._dashboard_state.settlement_history[asset.symbol] = settled
            logger.info("initial_settlement_history_loaded")
        except Exception:
            logger.warning("initial_settlement_history_failed")

        # Hydrate resting orders from exchange (recover state after restart)
        try:
            hydrated = await self._order_manager.hydrate_from_exchange()
            if hydrated:
                logger.info("orders_hydrated_on_startup", count=hydrated)
        except Exception:
            logger.warning("order_hydration_failed")

        # Fetch initial balance so it appears on dashboard immediately
        try:
            initial_balance = await self._get_balance(force=True)
            self._push_risk_to_dashboard(float(initial_balance))
            logger.info("initial_balance_fetched", balance=float(initial_balance))
        except Exception:
            logger.warning("initial_balance_fetch_failed")

        logger.info(
            "bot_started",
            active_markets=total_active,
            assets=asset_symbols,
        )

        # Run concurrent tasks
        tasks = [
            asyncio.create_task(self._strategy_loop(), name="strategy"),
            asyncio.create_task(self._market_scan_loop(), name="market_scan"),
            asyncio.create_task(self._position_monitor_loop(), name="position_monitor"),
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
        """Execute one strategy cycle across all assets."""
        self._cycle_count += 1
        ds = self._dashboard_state
        ds.cycle = self._cycle_count

        # Collect current market from each scanner
        markets_to_process = []
        for scanner in self._scanners.values():
            m = scanner.get_current_market()
            if m:
                markets_to_process.append(m)

        if not markets_to_process:
            ds.add_decision(self._cycle_count, "no_market", "No active market")
            return

        # Check trading pause toggle (dashboard UI)
        if self._dashboard_state.trading_paused:
            ds.add_decision(self._cycle_count, "reject", "Trading paused from dashboard")
            return

        # Sync quiet hours override from dashboard to signal combiner
        self._signal_combiner.quiet_hours_override = self._dashboard_state.quiet_hours_override

        # Sync strategy toggles from dashboard to config
        st = self._dashboard_state.strategy_toggles
        self._settings.strategy.directional_enabled = st.get("directional", True)
        self._settings.strategy.fomo_enabled = st.get("fomo", False)
        self._settings.strategy.certainty_scalp_enabled = st.get("certainty_scalp", True)
        self._settings.strategy.settlement_ride_enabled = st.get("settlement_ride", True)
        self._settings.strategy.use_market_maker = st.get("market_making", True)
        self._settings.strategy.trend_continuation_enabled = st.get("trend_continuation", True)
        self._settings.strategy.phase_filter_enabled = st.get("phase_filter", True)
        self._settings.strategy.trend_guard_enabled = st.get("trend_guard", False)
        self._settings.strategy.mm_vol_filter_enabled = st.get("mm_vol_filter", False)

        await asyncio.gather(*(self._process_market(m) for m in markets_to_process))
        await self._db.flush()

    async def _process_market(self, market) -> None:
        """Process a single market within a strategy cycle."""
        ds = self._dashboard_state
        ticker = market.ticker
        # Resolve asset symbol for per-asset dashboard state
        asset_symbol = self._data_hub._ticker_to_symbol(ticker)

        # Asset killswitches: skip markets when disabled from dashboard
        if self._dashboard_state.btc_disabled and asset_symbol == "BTC":
            return
        if self._dashboard_state.eth_disabled and asset_symbol == "ETH":
            return
        close_time = market.close_time or market.expected_expiration_time or market.expiration_time
        # Use local variables to avoid race conditions with asyncio.gather
        local_market = {
            "ticker": ticker,
            "event_ticker": market.event_ticker,
            "title": market.title,
            "yes_sub_title": market.yes_sub_title,
            "expiry": str(market.expiration_time) if market.expiration_time else None,
            "close_time": close_time.isoformat() if close_time else None,
            "volume": market.volume,
        }
        ds.market = local_market

        # Build snapshot
        snapshot = await self._data_hub.get_snapshot(ticker)
        if snapshot is None:
            ds.add_decision(self._cycle_count, "no_market", f"No snapshot for {ticker}")
            return

        ob = snapshot.orderbook
        try:
            local_snapshot = {
                "spot_price": float(snapshot.spot_price),
                "implied_prob": float(snapshot.implied_yes_prob) if snapshot.implied_yes_prob else None,
                "time_to_expiry": snapshot.time_to_expiry_seconds,
                "strike_price": float(snapshot.strike_price) if snapshot.strike_price else None,
                "statistical_fair_value": snapshot.statistical_fair_value,
                "secondary_spot_price": (
                    float(snapshot.secondary_spot_price) if snapshot.secondary_spot_price else None
                ),
                "cross_exchange_spread": snapshot.cross_exchange_spread,
                "cross_exchange_lead": snapshot.cross_exchange_lead,
                "taker_buy_volume": snapshot.taker_buy_volume,
                "taker_sell_volume": snapshot.taker_sell_volume,
                "chainlink_oracle_price": float(snapshot.chainlink_oracle_price) if snapshot.chainlink_oracle_price else None,
                "chainlink_divergence": snapshot.chainlink_divergence,
                "chainlink_round_updated": snapshot.chainlink_round_updated,
                "time_elapsed": snapshot.time_elapsed_seconds,
                "window_phase": snapshot.window_phase,
                "orderbook": {
                    "best_yes_bid": str(ob.best_yes_bid) if ob.best_yes_bid else None,
                    "best_no_bid": str(ob.best_no_bid) if ob.best_no_bid else None,
                    "spread": str(ob.spread) if ob.spread else None,
                    "yes_depth": ob.yes_bid_depth,
                    "no_depth": ob.no_bid_depth,
                    "implied_prob": float(ob.implied_yes_prob) if ob.implied_yes_prob else None,
                },
            }
        except Exception:
            logger.warning("snapshot_dict_failed", ticker=ticker)
            local_snapshot = {}
        ds.snapshot = local_snapshot

        # Compute features
        features = self._feature_engine.compute(snapshot)

        # Cross-asset implied probability divergence
        if self._settings.strategy.cross_asset_divergence_enabled:
            other_prob = self._get_other_asset_implied_prob(asset_symbol)
            if other_prob is not None and snapshot.implied_yes_prob is not None:
                this_implied = float(snapshot.implied_yes_prob)
                raw_divergence = other_prob - this_implied
                features.cross_asset_divergence = math.tanh(raw_divergence / 0.15)

        local_features = {
            name: getattr(features, name, 0.0) or 0.0
            for name in features.feature_names()
        }
        ds.features = local_features

        # Update volatility tracker
        self._vol_tracker.update(features.realized_vol_5min)

        # Dampen Chainlink in high vol (stale oracle creates false signals)
        chainlink_mult = {"low": 1.0, "normal": 1.0, "high": 0.5, "extreme": 0.25}.get(
            self._vol_tracker.current_regime, 1.0
        )
        if self._time_profiler is not None and self._time_profiler.loaded:
            session = self._time_profiler.get_current_session()
            multipliers = self._time_profiler.get_weight_multipliers(session)
            multipliers["chainlink"] = multipliers.get("chainlink", 1.0) * chainlink_mult
            self._model.set_weight_multipliers(multipliers)
        elif chainlink_mult != 1.0:
            self._model.set_weight_multipliers({"chainlink": chainlink_mult})

        # Get model prediction
        prediction = self._model.predict(features, market_ticker=ticker)

        local_prediction = {
            "probability": prediction.probability_yes,
            "confidence": prediction.confidence,
            "model": prediction.model_name,
            "signals": {
                "momentum": prediction.features_used.get("mom_signal", 0),
                "technical": prediction.features_used.get("tech_signal", 0),
                "flow": prediction.features_used.get("flow_signal", 0),
                "mean_reversion": prediction.features_used.get("mr_signal", 0),
                "cross_exchange": prediction.features_used.get("cross_exchange_signal", 0),
                "taker_flow": prediction.features_used.get("taker_signal", 0),
                "settlement": prediction.features_used.get("settlement_signal", 0),
                "cross_asset": prediction.features_used.get("cross_asset_signal", 0),
                "chainlink": prediction.features_used.get("chainlink_signal", 0),
                "btc_beta": prediction.features_used.get("btc_beta_signal", 0),
                "funding_rate": prediction.features_used.get("funding_signal", 0),
                "liquidation": prediction.features_used.get("liquidation_signal", 0),
                "time_decay": prediction.features_used.get("time_decay_signal", 0),
            },
        }
        ds.prediction = local_prediction

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

        # Write per-asset dashboard state for tabbed UI
        # Use local variables to avoid cross-market contamination from asyncio.gather
        ds.per_asset[asset_symbol] = {
            "market": local_market,
            "snapshot": local_snapshot,
            "features": dict(local_features),
            "prediction": dict(local_prediction),
            "edge": dict(ds.edge) if ds.edge else {},
            "fomo": dict(ds.fomo) if ds.fomo else {},
        }

        # Take-profit cooldown: block all re-entry after TP (except MM, certainty scalp, trend cont)
        tp_cooldown = self._settings.strategy.take_profit_cooldown_seconds
        if ticker in self._take_profit_markets and tp_cooldown > 0:
            tp_exit_time, tp_strategy = self._take_profit_markets[ticker]
            elapsed = (datetime.now(timezone.utc) - tp_exit_time).total_seconds()
            if elapsed < tp_cooldown:
                exempt = ("market_making", "certainty_scalp", "trend_continuation")
                buy_signals = [s for s in signals if s.action == "buy" and s.signal_type not in exempt]
                if buy_signals:
                    logger.info(
                        "take_profit_cooldown_blocked",
                        ticker=ticker,
                        elapsed=round(elapsed, 1),
                        cooldown=tp_cooldown,
                        blocked_count=len(buy_signals),
                        blocked_types=[s.signal_type for s in buy_signals],
                    )
                    signals = [s for s in signals if s.action != "buy" or s.signal_type in exempt]
            else:
                # Cooldown expired, remove from tracking
                del self._take_profit_markets[ticker]

        # Stop-loss cooldown: block directional re-entry after SL (one shot per market)
        if ticker in self._stop_loss_markets and signals:
            exempt = ("market_making", "certainty_scalp")
            buy_signals = [s for s in signals if s.action == "buy" and s.signal_type not in exempt]
            if buy_signals:
                logger.info(
                    "stop_loss_cooldown_blocked",
                    ticker=ticker,
                    blocked_count=len(buy_signals),
                    blocked_types=[s.signal_type for s in buy_signals],
                )
                signals = [s for s in signals if s.action != "buy" or s.signal_type in exempt]

        # Thesis-break cooldown: only block re-entry from the same strategy that got stopped out
        if ticker in self._thesis_break_markets and signals:
            tb_strategy = self._thesis_break_markets[ticker]
            exempt = ("market_making", "settlement_ride", "certainty_scalp")
            buy_signals = [s for s in signals if s.action == "buy" and s.signal_type not in exempt and s.signal_type == tb_strategy]
            if buy_signals:
                logger.info(
                    "thesis_break_cooldown_blocked",
                    ticker=ticker,
                    blocked_count=len(buy_signals),
                    cooldown_strategy=tb_strategy,
                )
                signals = [s for s in signals if s.action != "buy" or s.signal_type in exempt or s.signal_type != tb_strategy]

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
                block_reasons = self._signal_combiner.last_block_reasons
                if block_reasons:
                    reason = " | ".join(block_reasons)
                    logger.info(
                        "cycle_no_trade",
                        ticker=ticker,
                        blocks=block_reasons,
                        phase=snapshot.window_phase,
                    )
                ds.add_decision(self._cycle_count, "reject", reason)
                return

        # Guard: don't buy opposite side when already holding a position
        existing_pos = self._position_tracker.get_position(ticker)
        if existing_pos and existing_pos.count > 0:
            held_side = existing_pos.side
            before_count = len(signals)
            signals = [s for s in signals if not (s.action == "buy" and s.side != held_side)]
            blocked = before_count - len(signals)
            if blocked > 0:
                logger.info(
                    "opposite_side_blocked",
                    ticker=ticker,
                    held_side=held_side,
                    blocked_count=blocked,
                )
            if not signals:
                ds.add_decision(
                    self._cycle_count, "reject",
                    f"Opposite-side guard: holding {held_side.upper()}",
                )
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

        # Per-cycle contract cap (with per-asset override)
        cycle_contracts_placed = 0
        max_per_cycle = self._settings.risk.max_contracts_per_cycle
        if self._settings.risk.asset_max_per_cycle:
            ticker_upper = ticker.upper()
            for asset, cap in self._settings.risk.asset_max_per_cycle.items():
                if asset.upper() in ticker_upper:
                    max_per_cycle = cap
                    break

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
                time_to_expiry=snapshot.time_to_expiry_seconds,
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
                if self._settings.risk.asset_max_position:
                    t_upper = ticker.upper()
                    for a_sym, a_lim in self._settings.risk.asset_max_position.items():
                        if a_sym.upper() in t_upper:
                            max_pos = a_lim
                            break
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
                current_exposure_dollars=self._position_tracker.total_exposure_dollars,
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
                        signal_type=signal_item.signal_type,
                        market_volume=local_market.get("volume"),
                        cycle=self._cycle_count,
                        spot_price=float(local_snapshot.spot_price),
                        strike=float(local_snapshot.strike_price) if local_snapshot.strike_price else None,
                    )
                    # Mark trend continuation market as entered after fill
                    if signal_item.signal_type == "trend_continuation":
                        self._signal_combiner._trend_detector.mark_entered(ticker)
                    # Log MM fill event for immediate market-making fills
                    if signal_item.signal_type == "market_making":
                        logger.info(
                            "mm_fill",
                            ticker=ticker,
                            side=signal_item.side,
                            price=signal_item.suggested_price_dollars,
                            count=order_state.filled_count,
                            raw_edge=signal_item.raw_edge,
                            net_edge=signal_item.net_edge,
                            model_prob=signal_item.model_probability,
                            implied_prob=signal_item.implied_probability,
                        )
                    # Track buy fee in position for accurate PnL
                    buy_fee = EdgeDetector.compute_fee_dollars(
                        order_state.filled_count,
                        float(signal_item.suggested_price_dollars),
                        is_maker=True,
                    )
                    self._total_fees += buy_fee
                    pos_after_fill = self._position_tracker.get_position(ticker)
                    if pos_after_fill:
                        pos_after_fill.fees_paid += buy_fee
                        if local_snapshot.strike_price is not None:
                            pos_after_fill.strike_price = local_snapshot.strike_price
                    # Log trade to database
                    try:
                        from src.data.models import CompletedTrade
                        fee = buy_fee
                        completed = CompletedTrade(
                            order_id=order_id,
                            market_ticker=ticker,
                            side=signal_item.side,
                            action="buy",
                            count=order_state.filled_count,
                            price_dollars=Decimal(signal_item.suggested_price_dollars),
                            fees_dollars=fee,
                            model_probability=signal_item.model_probability,
                            implied_probability=signal_item.implied_probability,
                            entry_time=datetime.now(timezone.utc),
                            strategy_tag=signal_item.signal_type,
                            market_volume=local_market.get("volume"),
                            won=None,
                        )
                        await self._db.insert_trade(completed)
                    except Exception:
                        logger.warning("trade_logging_failed", ticker=ticker)
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
                    # Force-refresh balance after fill
                    await self._get_balance(force=True)
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

                # Scan all scanners and aggregate tickers
                prev_tickers: set[str] = set()
                for scanner in self._scanners.values():
                    prev_tickers.update(scanner.active_markets.keys())

                for scanner in self._scanners.values():
                    await scanner.scan()

                new_tickers: set[str] = set()
                for scanner in self._scanners.values():
                    new_tickers.update(scanner.active_markets.keys())

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
        ds = self._dashboard_state
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

                # Clean up completed background settlement tasks
                done = [t for t, task in self._pending_settlements.items() if task.done()]
                for t in done:
                    del self._pending_settlements[t]

                if exits:
                    actually_settled = []
                    for exit_ticker in exits:
                        pos = self._position_tracker.get_position(exit_ticker)
                        if not pos:
                            actually_settled.append(exit_ticker)
                            continue

                        cost = pos.avg_entry_price * pos.count
                        exit_snap = snapshots.get(exit_ticker)
                        exit_volume = exit_snap.volume if exit_snap else None

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
                                pnl = payout - cost - pos.fees_paid
                                logger.info(
                                    "position_settled_actual",
                                    ticker=exit_ticker,
                                    side=pos.side,
                                    result=settlement_result,
                                    won=won,
                                    count=pos.count,
                                    entry_price=float(pos.avg_entry_price),
                                    fees=float(pos.fees_paid),
                                    pnl=float(pnl),
                                )
                                logger.info(
                                    "calibration_data_point",
                                    ticker=exit_ticker,
                                    side=pos.side,
                                    model_probability=pos.model_probability,
                                    implied_probability=pos.implied_probability,
                                    won=won,
                                    signal_type=pos.strategy_tag,
                                    entry_price=float(pos.avg_entry_price),
                                )
                                self._risk_manager.record_trade(pnl)
                                try:
                                    ds.add_trade_result(
                                        self._data_hub._ticker_to_symbol(exit_ticker),
                                        "settle", pos.side, float(pnl), exit_ticker,
                                        size_dollars=float(cost),
                                        signal_type=pos.strategy_tag,
                                        entry_price=float(pos.avg_entry_price),
                                    )
                                except Exception:
                                    logger.warning("dashboard_trade_log_failed", ticker=exit_ticker)
                                # Log settlement to database
                                try:
                                    from src.data.models import CompletedTrade
                                    completed = CompletedTrade(
                                        order_id="settlement",
                                        market_ticker=exit_ticker,
                                        side=pos.side,
                                        action="settle",
                                        count=pos.count,
                                        price_dollars=pos.avg_entry_price,
                                        fees_dollars=pos.fees_paid,
                                        pnl_dollars=pnl,
                                        entry_time=pos.entry_time,
                                        exit_time=datetime.now(timezone.utc),
                                        strategy_tag=pos.strategy_tag,
                                        market_volume=exit_volume,
                                        model_probability=pos.model_probability,
                                        implied_probability=pos.implied_probability,
                                        won=won,
                                    )
                                    await self._db.insert_trade(completed)
                                except Exception:
                                    logger.warning("settlement_logging_failed", ticker=exit_ticker)
                                actually_settled.append(exit_ticker)
                            else:
                                # Result not available yet — poll in background to avoid blocking
                                if exit_ticker not in self._pending_settlements:
                                    self._pending_settlements[exit_ticker] = asyncio.create_task(
                                        self._poll_settlement(exit_ticker, pos, cost, exit_volume)
                                    )
                                continue  # Don't block — check on next iteration
                        else:
                            # Paper mode: simulate binary settlement
                            # Primary: spot vs strike (actual settlement logic)
                            # Use cached strike from position (survives market expiry)
                            # Fallback: implied prob > 0.50 → YES wins
                            snap = snapshots.get(exit_ticker)
                            yes_wins: bool | None = None
                            strike = pos.strike_price
                            spot = snap.spot_price if snap else None
                            if spot is not None and strike is not None:
                                yes_wins = float(spot) >= float(strike)
                            elif snap and snap.implied_yes_prob is not None:
                                yes_wins = float(snap.implied_yes_prob) > 0.50
                            if yes_wins is not None:
                                won = (yes_wins and pos.side == "yes") or (not yes_wins and pos.side == "no")
                                payout = Decimal(str(pos.count)) if won else Decimal("0")
                                pnl = payout - cost - pos.fees_paid
                            else:
                                pnl = -cost - pos.fees_paid  # Paper mode fallback
                            settle_method = (
                                "spot_vs_strike" if spot is not None and strike is not None
                                else "implied_prob"
                            )
                            logger.info(
                                "position_settled_paper",
                                ticker=exit_ticker,
                                side=pos.side,
                                count=pos.count,
                                entry_price=float(pos.avg_entry_price),
                                implied_prob=float(snap.implied_yes_prob) if snap and snap.implied_yes_prob is not None else None,
                                spot_price=float(spot) if spot is not None else None,
                                strike_price=float(strike) if strike is not None else None,
                                settle_method=settle_method,
                                won=pnl > 0,
                                pnl=float(pnl),
                                market_volume=exit_volume,
                            )
                            logger.info(
                                "calibration_data_point",
                                ticker=exit_ticker,
                                side=pos.side,
                                model_probability=pos.model_probability,
                                implied_probability=pos.implied_probability,
                                won=won if yes_wins is not None else None,
                                signal_type=pos.strategy_tag,
                                entry_price=float(pos.avg_entry_price),
                            )
                            self._risk_manager.record_trade(pnl)
                            try:
                                ds.add_trade_result(
                                    self._data_hub._ticker_to_symbol(exit_ticker),
                                    "settle", pos.side, float(pnl), exit_ticker,
                                    size_dollars=float(cost),
                                    signal_type=pos.strategy_tag,
                                    entry_price=float(pos.avg_entry_price),
                                )
                            except Exception:
                                logger.warning("dashboard_trade_log_failed", ticker=exit_ticker)
                            # Log settlement to database
                            try:
                                from src.data.models import CompletedTrade
                                completed = CompletedTrade(
                                    order_id="settlement",
                                    market_ticker=exit_ticker,
                                    side=pos.side,
                                    action="settle",
                                    count=pos.count,
                                    price_dollars=pos.avg_entry_price,
                                    fees_dollars=pos.fees_paid,
                                    pnl_dollars=pnl,
                                    entry_time=pos.entry_time,
                                    exit_time=datetime.now(timezone.utc),
                                    strategy_tag=pos.strategy_tag,
                                    market_volume=exit_volume,
                                    model_probability=pos.model_probability,
                                    implied_probability=pos.implied_probability,
                                    won=won if yes_wins is not None else None,
                                )
                                await self._db.insert_trade(completed)
                            except Exception:
                                logger.warning("settlement_logging_failed", ticker=exit_ticker)
                            actually_settled.append(exit_ticker)

                    if actually_settled:
                        self._position_tracker.remove_expired_positions(actually_settled)
                        self._update_dashboard_positions()

                # Check for pre-expiry exits (sell before settlement)
                if self._settings.strategy.pre_expiry_exit_enabled:
                    pe_signals = self._position_tracker.check_pre_expiry_exits(
                        snapshots,
                        pre_expiry_seconds=self._settings.strategy.pre_expiry_exit_seconds,
                        min_pnl_per_contract=self._settings.strategy.pre_expiry_exit_min_pnl_cents,
                        hold_to_settle_seconds=self._settings.strategy.hold_to_settle_seconds,
                        hold_to_settle_min_profit_cents=self._settings.strategy.hold_to_settle_min_profit_cents,
                    )
                    for pe_ticker, sell_price in pe_signals:
                        pos = self._position_tracker.get_position(pe_ticker)
                        if not pos:
                            continue
                        from src.data.models import TradeSignal

                        sell_signal = TradeSignal(
                            market_ticker=pe_ticker,
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
                            post_only=True,
                        )
                        order_id = await self._order_manager.submit(sell_signal, pos.count)
                        if order_id:
                            entry_cost = pos.avg_entry_price * pos.count
                            exit_revenue = Decimal(sell_price) * pos.count
                            sell_fee = EdgeDetector.compute_fee_dollars(
                                pos.count, float(sell_price), is_maker=True
                            )
                            pnl = exit_revenue - entry_cost - sell_fee - pos.fees_paid
                            self._total_fees += sell_fee
                            pe_snap = snapshots.get(pe_ticker)
                            logger.info(
                                "pre_expiry_exit_executed",
                                ticker=pe_ticker,
                                side=pos.side,
                                count=pos.count,
                                entry_price=float(pos.avg_entry_price),
                                exit_price=sell_price,
                                pnl=float(pnl),
                                spot_price=float(pe_snap.spot_price) if pe_snap else None,
                                strike=float(pe_snap.strike_price) if pe_snap and pe_snap.strike_price else None,
                            )
                            self._risk_manager.record_trade(pnl)
                            try:
                                ds.add_trade_result(
                                    self._data_hub._ticker_to_symbol(pe_ticker),
                                    "pre_expiry", pos.side, float(pnl), pe_ticker,
                                    size_dollars=float(entry_cost),
                                    signal_type=pos.strategy_tag,
                                    entry_price=float(pos.avg_entry_price),
                                    spot_price=float(pe_snap.spot_price) if pe_snap else None,
                                    strike=float(pe_snap.strike_price) if pe_snap and pe_snap.strike_price else None,
                                )
                            except Exception:
                                logger.warning("dashboard_trade_log_failed", ticker=pe_ticker)
                            # Log pre-expiry exit to database
                            try:
                                from src.data.models import CompletedTrade
                                pe_snap = snapshots.get(pe_ticker)
                                completed = CompletedTrade(
                                    order_id=order_id,
                                    market_ticker=pe_ticker,
                                    side=pos.side,
                                    action="pre_expiry_exit",
                                    count=pos.count,
                                    price_dollars=Decimal(sell_price),
                                    fees_dollars=sell_fee,
                                    pnl_dollars=pnl,
                                    entry_time=pos.entry_time,
                                    exit_time=datetime.now(timezone.utc),
                                    strategy_tag=pos.strategy_tag,
                                    market_volume=pe_snap.volume if pe_snap else None,
                                    model_probability=pos.model_probability,
                                    implied_probability=pos.implied_probability,
                                )
                                await self._db.insert_trade(completed)
                            except Exception:
                                logger.warning("trade_logging_failed", ticker=pe_ticker)
                            self._position_tracker.remove_expired_positions([pe_ticker])
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
                            post_only=True,  # TP exits use maker orders (1.75% vs 7% fee)
                        )
                        order_id = await self._order_manager.submit(sell_signal, pos.count)
                        if order_id:
                            entry_cost = pos.avg_entry_price * pos.count
                            exit_revenue = Decimal(sell_price) * pos.count
                            sell_fee = EdgeDetector.compute_fee_dollars(
                                pos.count, float(sell_price), is_maker=True
                            )
                            pnl = exit_revenue - entry_cost - sell_fee - pos.fees_paid
                            self._total_fees += sell_fee
                            tp_snap = snapshots.get(tp_ticker)
                            logger.info(
                                "take_profit_executed",
                                ticker=tp_ticker,
                                side=pos.side,
                                count=pos.count,
                                entry_price=float(pos.avg_entry_price),
                                exit_price=sell_price,
                                pnl=float(pnl),
                                fee=float(sell_fee),
                                spot_price=float(tp_snap.spot_price) if tp_snap else None,
                                strike=float(tp_snap.strike_price) if tp_snap and tp_snap.strike_price else None,
                            )
                            self._risk_manager.record_trade(pnl)
                            try:
                                ds.add_trade_result(
                                    self._data_hub._ticker_to_symbol(tp_ticker),
                                    "take_profit", pos.side, float(pnl), tp_ticker,
                                    size_dollars=float(entry_cost),
                                    signal_type=pos.strategy_tag,
                                    entry_price=float(pos.avg_entry_price),
                                    spot_price=float(tp_snap.spot_price) if tp_snap else None,
                                    strike=float(tp_snap.strike_price) if tp_snap and tp_snap.strike_price else None,
                                )
                            except Exception:
                                logger.warning("dashboard_trade_log_failed", ticker=tp_ticker)
                            # Log take-profit to database
                            try:
                                from src.data.models import CompletedTrade
                                tp_snap = snapshots.get(tp_ticker)
                                completed = CompletedTrade(
                                    order_id=order_id,
                                    market_ticker=tp_ticker,
                                    side=pos.side,
                                    action="take_profit",
                                    count=pos.count,
                                    price_dollars=Decimal(sell_price),
                                    fees_dollars=sell_fee,
                                    pnl_dollars=pnl,
                                    entry_time=pos.entry_time,
                                    exit_time=datetime.now(timezone.utc),
                                    strategy_tag=pos.strategy_tag,
                                    market_volume=tp_snap.volume if tp_snap else None,
                                    model_probability=pos.model_probability,
                                    implied_probability=pos.implied_probability,
                                )
                                await self._db.insert_trade(completed)
                            except Exception:
                                logger.warning("trade_logging_failed", ticker=tp_ticker)
                            self._position_tracker.remove_expired_positions([tp_ticker])
                            self._take_profit_markets[tp_ticker] = (datetime.now(timezone.utc), pos.strategy_tag)
                            self._update_dashboard_positions()

                # Check for stop-loss exits
                if self._settings.strategy.stop_loss_enabled:
                    sl_signals = self._position_tracker.check_stop_loss(
                        snapshots,
                        stop_loss_pct=self._settings.strategy.stop_loss_pct,
                        min_bid=self._settings.strategy.stop_loss_min_bid,
                        min_hold_seconds=self._settings.strategy.stop_loss_min_hold_seconds,
                        asset_stop_loss_pct=self._settings.strategy.asset_stop_loss_pct or None,
                        max_dollar_loss=self._settings.strategy.stop_loss_max_dollar_loss,
                        directional_stop_loss_pct=self._settings.strategy.directional_stop_loss_pct,
                        directional_max_dollar_loss=self._settings.strategy.directional_stop_loss_max_dollar,
                    )
                    for sl_ticker, sell_price in sl_signals:
                        pos = self._position_tracker.get_position(sl_ticker)
                        if not pos:
                            continue
                        from src.data.models import TradeSignal

                        sell_signal = TradeSignal(
                            market_ticker=sl_ticker,
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
                            post_only=True,  # Maker order to reduce SL fee (1.75% vs 7%)
                        )
                        order_id = await self._order_manager.submit(sell_signal, pos.count)
                        if order_id:
                            entry_cost = pos.avg_entry_price * pos.count
                            exit_revenue = Decimal(sell_price) * pos.count
                            sell_fee = EdgeDetector.compute_fee_dollars(
                                pos.count, float(sell_price), is_maker=True
                            )
                            pnl = exit_revenue - entry_cost - sell_fee - pos.fees_paid
                            self._total_fees += sell_fee
                            sl_snap = snapshots.get(sl_ticker)
                            logger.info(
                                "stop_loss_executed",
                                ticker=sl_ticker,
                                side=pos.side,
                                count=pos.count,
                                entry_price=float(pos.avg_entry_price),
                                exit_price=sell_price,
                                pnl=float(pnl),
                                fee=float(sell_fee),
                                spot_price=float(sl_snap.spot_price) if sl_snap else None,
                                strike=float(sl_snap.strike_price) if sl_snap and sl_snap.strike_price else None,
                            )
                            self._risk_manager.record_trade(pnl)
                            try:
                                ds.add_trade_result(
                                    self._data_hub._ticker_to_symbol(sl_ticker),
                                    "stop_loss", pos.side, float(pnl), sl_ticker,
                                    size_dollars=float(entry_cost),
                                    signal_type=pos.strategy_tag,
                                    entry_price=float(pos.avg_entry_price),
                                    spot_price=float(sl_snap.spot_price) if sl_snap else None,
                                    strike=float(sl_snap.strike_price) if sl_snap and sl_snap.strike_price else None,
                                )
                            except Exception:
                                logger.warning("dashboard_trade_log_failed", ticker=sl_ticker)
                            # Log stop-loss to database
                            try:
                                from src.data.models import CompletedTrade
                                sl_snap = snapshots.get(sl_ticker)
                                completed = CompletedTrade(
                                    order_id=order_id,
                                    market_ticker=sl_ticker,
                                    side=pos.side,
                                    action="stop_loss",
                                    count=pos.count,
                                    price_dollars=Decimal(sell_price),
                                    fees_dollars=sell_fee,
                                    pnl_dollars=pnl,
                                    entry_time=pos.entry_time,
                                    exit_time=datetime.now(timezone.utc),
                                    strategy_tag=pos.strategy_tag,
                                    market_volume=sl_snap.volume if sl_snap else None,
                                    model_probability=pos.model_probability,
                                    implied_probability=pos.implied_probability,
                                )
                                await self._db.insert_trade(completed)
                            except Exception:
                                logger.warning("trade_logging_failed", ticker=sl_ticker)
                            self._stop_loss_markets.add(sl_ticker)
                            self._position_tracker.remove_expired_positions([sl_ticker])
                            self._update_dashboard_positions()

                # Check for thesis breaks — sell positions where model flipped
                if self._settings.strategy.thesis_break_enabled and self._last_predictions:
                    thesis_breaks = self._position_tracker.check_thesis_breaks(
                        self._last_predictions,
                        threshold=self._settings.strategy.thesis_break_threshold,
                        min_hold_seconds=self._settings.strategy.thesis_break_min_hold_seconds,
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
                            post_only=True,
                        )
                        order_id = await self._order_manager.submit(sell_signal, pos.count)
                        if order_id:
                            entry_cost = pos.avg_entry_price * pos.count
                            exit_revenue = Decimal(sell_price) * pos.count
                            sell_fee = EdgeDetector.compute_fee_dollars(
                                pos.count, float(sell_price), is_maker=True
                            )
                            pnl = exit_revenue - entry_cost - sell_fee - pos.fees_paid
                            self._total_fees += sell_fee
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
                            tb_snap = snapshots.get(tb_ticker)
                            try:
                                ds.add_trade_result(
                                    self._data_hub._ticker_to_symbol(tb_ticker),
                                    "thesis_break", pos.side, float(pnl), tb_ticker,
                                    size_dollars=float(entry_cost),
                                    signal_type=pos.strategy_tag,
                                    entry_price=float(pos.avg_entry_price),
                                    spot_price=float(tb_snap.spot_price) if tb_snap else None,
                                    strike=float(tb_snap.strike_price) if tb_snap and tb_snap.strike_price else None,
                                )
                            except Exception:
                                logger.warning("dashboard_trade_log_failed", ticker=tb_ticker)
                            # Log thesis-break exit to database
                            try:
                                from src.data.models import CompletedTrade
                                tb_snap = snapshots.get(tb_ticker)
                                completed = CompletedTrade(
                                    order_id=order_id,
                                    market_ticker=tb_ticker,
                                    side=pos.side,
                                    action="thesis_break",
                                    count=pos.count,
                                    price_dollars=Decimal(sell_price),
                                    fees_dollars=sell_fee,
                                    pnl_dollars=pnl,
                                    entry_time=pos.entry_time,
                                    exit_time=datetime.now(timezone.utc),
                                    strategy_tag=pos.strategy_tag,
                                    market_volume=tb_snap.volume if tb_snap else None,
                                    model_probability=pos.model_probability,
                                    implied_probability=pos.implied_probability,
                                )
                                await self._db.insert_trade(completed)
                            except Exception:
                                logger.warning("trade_logging_failed", ticker=tb_ticker)
                            self._position_tracker.remove_expired_positions([tb_ticker])
                            self._thesis_break_markets[tb_ticker] = pos.strategy_tag
                            self._update_dashboard_positions()

                # Check for fills on resting orders (live mode only)
                newly_filled = await self._order_manager.check_resting_fills()
                for filled_state in newly_filled:
                    self._position_tracker.update_on_fill(filled_state)
                    self._risk_manager._trades_today += 1
                    # Track buy fee for resting fills (same as immediate fills)
                    if filled_state.signal.action == "buy" and filled_state.filled_count > 0:
                        buy_fee = EdgeDetector.compute_fee_dollars(
                            filled_state.filled_count,
                            float(filled_state.signal.suggested_price_dollars),
                            is_maker=True,
                        )
                        self._total_fees += buy_fee
                        pos = self._position_tracker.get_position(filled_state.signal.market_ticker)
                        if pos:
                            pos.fees_paid += buy_fee
                    logger.info(
                        "resting_order_fill_detected",
                        ticker=filled_state.signal.market_ticker,
                        side=filled_state.signal.side,
                        filled=filled_state.filled_count,
                    )
                    # Log MM fill event for market-making orders
                    if filled_state.signal.signal_type == "market_making":
                        logger.info(
                            "mm_fill",
                            ticker=filled_state.signal.market_ticker,
                            side=filled_state.signal.side,
                            price=filled_state.signal.suggested_price_dollars,
                            count=filled_state.filled_count,
                            raw_edge=filled_state.signal.raw_edge,
                            net_edge=filled_state.signal.net_edge,
                            model_prob=filled_state.signal.model_probability,
                            implied_prob=filled_state.signal.implied_probability,
                        )
                    # Force-refresh balance after resting fill
                    await self._get_balance(force=True)
                    self._update_dashboard_positions()

                # Compute and push unrealized PNL to dashboard
                unrealized_pnl = self._position_tracker.compute_unrealized_pnl(snapshots)
                self._dashboard_state.risk["unrealized_pnl"] = float(unrealized_pnl)
                self._dashboard_state.risk["total_pnl"] = float(
                    self._risk_manager.session_pnl + unrealized_pnl
                )

                # Track cumulative time with open positions
                if self._position_tracker._positions:
                    self._dashboard_state.active_trading_seconds += 10

                # Quote refresh: cancel and re-quote when fair value moves >$0.03
                for qticker, pred in self._last_predictions.items():
                    mm = self._signal_combiner._market_maker
                    fair_value = Decimal(str(round(pred.probability_yes, 2)))
                    if mm.should_requote(qticker, fair_value):
                        await self._order_manager.cancel_market_orders(qticker)
                        mm.clear_quote_state(qticker)
                        logger.info(
                            "mm_requote_triggered",
                            ticker=qticker,
                            fair_value=float(fair_value),
                        )

                # Cancel stale resting orders (older than 90s)
                await self._order_manager.cancel_stale_orders(max_age_seconds=90)

                # Cleanup old terminal orders
                self._order_manager.cleanup_terminal_orders()

                # Flush any pending DB writes from this monitor cycle
                await self._db.flush()

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("position_monitor_error")

    async def _time_profile_refresh_loop(self) -> None:
        """Re-fetch Binance klines every 6 hours to keep profiles fresh."""
        while self._running:
            try:
                await asyncio.sleep(6 * 3600)
                if not self._running:
                    break
                for asset in self._settings.kalshi.assets:
                    await self._time_profiler.fetch_hourly_klines(asset.symbol + "USDT")
                logger.info("time_profile_refreshed")
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("time_profile_refresh_error")

    async def _poll_settlement(self, ticker: str, pos, cost: Decimal, market_volume: int | None = None) -> None:
        """Background task to poll for settlement result (non-blocking)."""
        ds = self._dashboard_state
        settlement_result = None
        for _retry in range(10):
            await asyncio.sleep(2)
            try:
                settlement_result = await self._kalshi_rest.get_market_result(ticker)
            except Exception:
                pass
            if settlement_result is not None:
                break

        if settlement_result is not None:
            won = (settlement_result == pos.side)
            payout = Decimal(str(pos.count)) if won else Decimal("0")
            pnl = payout - cost - pos.fees_paid
            logger.info(
                "position_settled_actual",
                ticker=ticker,
                side=pos.side,
                result=settlement_result,
                won=won,
                count=pos.count,
                entry_price=float(pos.avg_entry_price),
                fees=float(pos.fees_paid),
                pnl=float(pnl),
                expedited=True,
            )
            logger.info(
                "calibration_data_point",
                ticker=ticker,
                side=pos.side,
                model_probability=pos.model_probability,
                implied_probability=pos.implied_probability,
                won=won,
                signal_type=pos.strategy_tag,
                entry_price=float(pos.avg_entry_price),
            )
            self._risk_manager.record_trade(pnl)
            try:
                ds.add_trade_result(
                    self._data_hub._ticker_to_symbol(ticker),
                    "settle", pos.side, float(pnl), ticker,
                    size_dollars=float(cost),
                    signal_type=pos.strategy_tag,
                    entry_price=float(pos.avg_entry_price),
                )
            except Exception:
                logger.warning("dashboard_trade_log_failed", ticker=ticker)
            try:
                from src.data.models import CompletedTrade
                completed = CompletedTrade(
                    order_id="settlement",
                    market_ticker=ticker,
                    side=pos.side,
                    action="settle",
                    count=pos.count,
                    price_dollars=pos.avg_entry_price,
                    fees_dollars=pos.fees_paid,
                    pnl_dollars=pnl,
                    entry_time=pos.entry_time,
                    exit_time=datetime.now(timezone.utc),
                    strategy_tag=pos.strategy_tag,
                    market_volume=market_volume,
                    model_probability=pos.model_probability,
                    implied_probability=pos.implied_probability,
                    won=won,
                )
                await self._db.insert_trade(completed)
            except Exception:
                logger.warning("settlement_logging_failed", ticker=ticker)
            self._position_tracker.remove_expired_positions([ticker])
            self._update_dashboard_positions()
        else:
            logger.info(
                "settlement_pending",
                ticker=ticker,
                side=pos.side,
                count=pos.count,
            )

    async def _health_check_loop(self) -> None:
        """Log bot health status every 60 seconds."""
        first_run = True
        while self._running:
            try:
                if first_run:
                    # First tick after 5s so startup balance is already pushed
                    await asyncio.sleep(5)
                    first_run = False
                else:
                    await asyncio.sleep(60)
                if not self._running:
                    break

                balance = await self._get_balance()
                positions = self._position_tracker.get_all_positions()

                # Count active markets across all scanners
                total_active_markets = sum(
                    len(s.active_markets) for s in self._scanners.values()
                )
                # Collect prices from all feeds
                feed_prices = {
                    sym: float(f.latest_price or 0)
                    for sym, f in self._feeds.items()
                }
                chainlink_prices = {
                    sym: float(f.latest_price or 0)
                    for sym, f in self._chainlink_feeds.items()
                    if f.latest_price is not None
                }
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
                    active_markets=total_active_markets,
                    cycles=self._cycle_count,
                    feed_prices=feed_prices,
                    chainlink_prices=chainlink_prices,
                    vol_regime=self._vol_tracker.current_regime,
                    consecutive_losses=self._risk_manager.consecutive_losses,
                )

                # Update dashboard risk/position state
                self._push_risk_to_dashboard(float(balance))
                self._dashboard_state.positions = [
                    {
                        "ticker": p.market_ticker,
                        "side": p.side,
                        "count": p.count,
                        "avg_price": str(p.avg_entry_price),
                    }
                    for p in positions
                ]

                # Fetch recent Kalshi settlement results for dashboard
                try:
                    for asset in self._settings.kalshi.assets:
                        settled = await self._kalshi_rest.get_settled_markets(
                            asset.series_ticker, limit=5
                        )
                        self._dashboard_state.settlement_history[asset.symbol] = settled
                except Exception:
                    pass  # Non-critical

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
            paper_start = Decimal(str(self._settings.risk.max_total_exposure_dollars * 2))
            return paper_start + self._risk_manager.session_pnl

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

    def _get_other_asset_implied_prob(self, current_symbol: str) -> float | None:
        """Get the implied YES probability from the other asset's orderbook cache.

        Returns None when only one asset is active or data is unavailable.
        """
        for symbol, scanner in self._scanners.items():
            if symbol == current_symbol:
                continue
            market = scanner.get_current_market()
            if market is None:
                continue
            ob = self._data_hub._orderbook_cache.get(market.ticker)
            if ob is None:
                continue
            implied = ob.implied_yes_prob
            if implied is not None:
                return float(implied)
        return None

    def _push_risk_to_dashboard(self, balance: float | None = None) -> None:
        """Build and push risk stats to dashboard state.

        If *balance* is None, uses the cached balance (paper mode calculates it,
        live mode falls back to the last cached value).
        """
        if balance is None:
            if self._settings.mode == "paper":
                paper_start = Decimal(str(self._settings.risk.max_total_exposure_dollars * 2))
                balance = float(paper_start + self._risk_manager.session_pnl)
            elif self._cached_balance is not None:
                balance = float(self._cached_balance)
            else:
                balance = self._dashboard_state.risk.get("balance", 0.0)

        prev_unrealized = self._dashboard_state.risk.get("unrealized_pnl", 0.0)
        daily_pnl = float(self._risk_manager.daily_pnl)
        session_pnl = float(self._risk_manager.session_pnl)
        self._dashboard_state.risk = {
            "balance": balance,
            "daily_pnl": daily_pnl,
            "unrealized_pnl": prev_unrealized,
            "total_pnl": session_pnl + prev_unrealized,
            "trades_today": self._risk_manager.trades_today,
            "consecutive_losses": self._risk_manager.consecutive_losses,
            "consecutive_wins": self._risk_manager.consecutive_wins,
            "win_rate": self._risk_manager.win_rate,
            "total_settled": self._risk_manager.total_settled,
            "last_pnl": float(self._risk_manager.last_pnl) if self._risk_manager.last_pnl is not None else None,
            "vol_regime": self._vol_tracker.current_regime,
            "exposure": float(self._position_tracker.total_exposure_dollars),
            "total_fees": float(self._total_fees),
            "daily_pnl_peak": float(self._risk_manager.daily_pnl_peak),
        }

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
        self._push_risk_to_dashboard()

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

    # Open persistent log file (append mode, line-buffered)
    log_fh = open(log_file, "a", buffering=1)  # noqa: SIM115

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
        logger_factory=structlog.WriteLoggerFactory(file=log_fh),
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
        help="shortcut for --mode paper",
    )
    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    # Load config from YAML
    settings = load_settings(args.config)

    # Apply --dry-run default (overridden by explicit --mode)
    if args.dry_run:
        if args.mode is None:
            settings.mode = "paper"

    # Apply explicit CLI overrides
    if args.mode is not None:
        settings.mode = args.mode
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
        assets=[a.symbol for a in settings.kalshi.assets],
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
