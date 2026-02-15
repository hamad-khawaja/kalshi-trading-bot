"""Central data aggregator for all market data sources."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from src.config import AssetConfig, BotSettings, StrategyConfig
from src.data.binance_feed import BinanceFeed
from src.data.kalshi_client import KalshiRestClient
from src.data.kalshi_ws import KalshiWebSocket
from src.data.market_scanner import MarketScanner
from src.data.models import MarketSnapshot, Orderbook, OrderbookLevel
from src.strategy.fair_value import compute_fair_value_from_prices, parse_strike_price

logger = structlog.get_logger()


class DataHub:
    """Central data aggregator providing unified snapshots for the strategy layer.

    Combines data from:
    - Kalshi REST + WebSocket (orderbooks, market data)
    - Coinbase/Kraken WebSocket (price feeds)
    """

    def __init__(
        self,
        kalshi_rest: KalshiRestClient,
        kalshi_ws: KalshiWebSocket,
        feeds: dict[str, BinanceFeed],
        scanners: dict[str, MarketScanner],
        secondary_feeds: dict[str, BinanceFeed] | None = None,
        strategy_config: StrategyConfig | None = None,
        asset_configs: list[AssetConfig] | None = None,
    ):
        self._kalshi_rest = kalshi_rest
        self._kalshi_ws = kalshi_ws
        self._feeds = feeds
        self._secondary_feeds = secondary_feeds or {}
        self._scanners = scanners
        self._strategy_config = strategy_config or StrategyConfig()
        self._asset_configs = asset_configs or []
        # Build series_ticker -> symbol mapping for routing
        self._series_to_symbol: dict[str, str] = {
            ac.series_ticker: ac.symbol for ac in self._asset_configs
        }
        self._orderbook_cache: dict[str, Orderbook] = {}
        self._ws_subscribed_tickers: set[str] = set()
        self._ws_seq: dict[str, int] = {}  # Last processed seq per ticker

    def _ticker_to_symbol(self, market_ticker: str) -> str:
        """Map a market ticker like 'KXBTC15M-...' to its asset symbol ('BTC').

        Matches by checking if the ticker starts with a known series_ticker prefix.
        Falls back to the first asset symbol if no match.
        """
        upper = market_ticker.upper()
        for series, symbol in self._series_to_symbol.items():
            if upper.startswith(series):
                return symbol
        # Fallback: return first asset symbol
        if self._asset_configs:
            return self._asset_configs[0].symbol
        return "BTC"

    async def start(self) -> None:
        """Connect all data sources. Kalshi failures are non-fatal."""
        # Price feeds are critical, others are best-effort
        for symbol, feed in self._feeds.items():
            await feed.connect()
            logger.info("primary_feed_connected", symbol=symbol)

        best_effort = [
            ("kalshi_rest", self._kalshi_rest.connect()),
            ("kalshi_ws", self._kalshi_ws.connect()),
        ]
        for symbol, feed in self._secondary_feeds.items():
            best_effort.append((f"secondary_feed_{symbol}", feed.connect()))

        for name, coro in best_effort:
            try:
                await coro
            except Exception:
                logger.warning("data_source_connect_failed", source=name)

        logger.info("data_hub_started", assets=list(self._feeds.keys()))

    async def stop(self) -> None:
        """Gracefully close all connections."""
        coros = [
            self._kalshi_rest.close(),
            self._kalshi_ws.close(),
        ]
        for feed in self._feeds.values():
            coros.append(feed.close())
        for feed in self._secondary_feeds.values():
            coros.append(feed.close())
        await asyncio.gather(*coros, return_exceptions=True)
        logger.info("data_hub_stopped")

    async def subscribe_market(self, ticker: str) -> None:
        """Subscribe to WebSocket channels for a market."""
        if ticker in self._ws_subscribed_tickers:
            return

        await self._kalshi_ws.subscribe_orderbook(
            ticker, lambda msg: self._on_orderbook_update(ticker, msg)
        )
        self._ws_subscribed_tickers.add(ticker)
        logger.info("data_hub_subscribed", ticker=ticker)

    def _on_orderbook_update(self, ticker: str, msg: dict) -> None:
        """Handle orderbook updates from WebSocket.

        Processes both orderbook_snapshot (full book) and orderbook_delta
        (incremental quantity changes). Uses seq numbers for deduplication
        since all channel callbacks fire for every message.
        """
        msg_type = msg.get("type", "")
        payload = msg.get("msg", {})
        actual_ticker = payload.get("market_ticker")
        if not actual_ticker:
            return

        # Dedup: only process each message once (all callbacks fire for every msg)
        seq = msg.get("seq", 0)
        last_seq = self._ws_seq.get(actual_ticker, -1)
        if seq <= last_seq:
            return
        self._ws_seq[actual_ticker] = seq

        now = datetime.now(timezone.utc)

        if msg_type == "orderbook_snapshot":
            self._handle_orderbook_snapshot(actual_ticker, payload, now)
        elif msg_type == "orderbook_delta":
            self._handle_orderbook_delta(actual_ticker, payload)

    def _handle_orderbook_snapshot(
        self, ticker: str, payload: dict, now: datetime
    ) -> None:
        """Parse a full orderbook snapshot into the cache."""
        yes_levels: list[OrderbookLevel] = []
        no_levels: list[OrderbookLevel] = []

        # Prefer dollar format, fall back to cents
        for entry in payload.get("yes_dollars") or []:
            if isinstance(entry, list) and len(entry) >= 2:
                yes_levels.append(
                    OrderbookLevel(
                        price_dollars=Decimal(str(entry[0])),
                        quantity=int(entry[1]),
                    )
                )
        for entry in payload.get("no_dollars") or []:
            if isinstance(entry, list) and len(entry) >= 2:
                no_levels.append(
                    OrderbookLevel(
                        price_dollars=Decimal(str(entry[0])),
                        quantity=int(entry[1]),
                    )
                )

        if not yes_levels and not no_levels:
            for entry in payload.get("yes") or []:
                if isinstance(entry, list) and len(entry) >= 2:
                    yes_levels.append(
                        OrderbookLevel(
                            price_dollars=Decimal(str(entry[0])) / 100,
                            quantity=int(entry[1]),
                        )
                    )
            for entry in payload.get("no") or []:
                if isinstance(entry, list) and len(entry) >= 2:
                    no_levels.append(
                        OrderbookLevel(
                            price_dollars=Decimal(str(entry[0])) / 100,
                            quantity=int(entry[1]),
                        )
                    )

        self._orderbook_cache[ticker] = Orderbook(
            ticker=ticker,
            yes_levels=yes_levels,
            no_levels=no_levels,
            timestamp=now,
        )
        logger.debug(
            "ws_orderbook_snapshot",
            ticker=ticker,
            yes_levels=len(yes_levels),
            no_levels=len(no_levels),
        )

    def _handle_orderbook_delta(self, ticker: str, payload: dict) -> None:
        """Apply an incremental delta to the cached orderbook."""
        cached = self._orderbook_cache.get(ticker)
        if cached is None:
            return  # No snapshot yet — can't apply delta

        side = payload.get("side")  # "yes" or "no"
        delta = payload.get("delta", 0)

        # Parse price — prefer dollars, fall back to cents
        price_str = payload.get("price_dollars")
        if price_str is not None:
            price = Decimal(str(price_str))
        elif payload.get("price") is not None:
            price = Decimal(str(payload["price"])) / 100
        else:
            return

        levels = cached.yes_levels if side == "yes" else cached.no_levels

        # Find existing level at this price
        found = False
        for i, lvl in enumerate(levels):
            if lvl.price_dollars == price:
                new_qty = lvl.quantity + delta
                if new_qty > 0:
                    levels[i] = OrderbookLevel(
                        price_dollars=price, quantity=new_qty
                    )
                else:
                    levels.pop(i)
                found = True
                break

        if not found and delta > 0:
            # New price level — insert and keep sorted descending by price
            levels.append(OrderbookLevel(price_dollars=price, quantity=delta))
            levels.sort(key=lambda l: l.price_dollars, reverse=True)

    async def get_snapshot(self, market_ticker: str) -> MarketSnapshot | None:
        """Build a complete market snapshot for the strategy layer.

        Returns None if critical data (underlying price) is unavailable.
        """
        now = datetime.now(timezone.utc)

        # Resolve asset symbol for this ticker
        symbol = self._ticker_to_symbol(market_ticker)
        feed = self._feeds.get(symbol)
        if feed is None:
            logger.warning("snapshot_no_feed", symbol=symbol, ticker=market_ticker)
            return None

        # Underlying price data
        btc_price = feed.latest_price
        if btc_price is None:
            logger.warning("snapshot_no_btc_price", symbol=symbol)
            return None

        # Recent price history
        ticks_1min = feed.get_prices_since(60)
        ticks_5min = feed.get_prices_since(300)
        prices_1min = [t.price for t in ticks_1min]
        prices_5min = [t.price for t in ticks_5min]
        volumes_1min = [t.volume for t in ticks_1min]

        # Kalshi orderbook — use WS-maintained cache, REST fallback on cache miss
        orderbook = self._orderbook_cache.get(market_ticker)
        if orderbook is None:
            try:
                orderbook = await self._kalshi_rest.get_orderbook(market_ticker)
                self._orderbook_cache[market_ticker] = orderbook
            except Exception:
                logger.warning("snapshot_orderbook_error", ticker=market_ticker)
                orderbook = Orderbook(ticker=market_ticker, timestamp=now)

        # Kalshi market info — search across all scanners
        market = None
        for scanner in self._scanners.values():
            market = scanner.active_markets.get(market_ticker)
            if market:
                break
        time_to_expiry = 0.0
        volume = 0
        strike_price = None
        if market:
            # Use close_time (actual trading deadline), not expiration_time
            close = market.close_time or market.expected_expiration_time or market.expiration_time
            if close:
                if close.tzinfo is None:
                    close = close.replace(tzinfo=timezone.utc)
                time_to_expiry = max(0.0, (close - now).total_seconds())
            volume = market.volume
            # Parse strike price from market title
            if market.yes_sub_title:
                strike_price = parse_strike_price(market.yes_sub_title)

        # Compute statistical fair value when we have enough data
        import numpy as np
        stat_fair_value = None
        if strike_price and time_to_expiry > 0 and len(prices_5min) >= 20:
            price_arr = np.array([float(p) for p in prices_5min], dtype=np.float64)
            stat_fair_value = compute_fair_value_from_prices(
                btc_price=float(btc_price),
                strike_price=float(strike_price),
                price_history=price_arr,
                time_to_expiry_seconds=time_to_expiry,
                price_window_seconds=300.0,
            )

        # Cross-exchange data (secondary feed for this asset)
        secondary_feed = self._secondary_feeds.get(symbol)
        binance_btc_price = None
        cross_exchange_spread = None
        cross_exchange_lead = None
        if secondary_feed and secondary_feed.latest_price is not None:
            binance_btc_price = secondary_feed.latest_price
            # Spread: (secondary - primary) / primary as percentage
            cb_price = float(btc_price)
            bn_price = float(binance_btc_price)
            if cb_price > 0:
                cross_exchange_spread = (bn_price - cb_price) / cb_price
            # Lead signal: compare short-term momentum across exchanges
            bn_ticks = secondary_feed.get_prices_since(15)
            cb_ticks = feed.get_prices_since(15)
            if len(bn_ticks) >= 2 and len(cb_ticks) >= 2:
                bn_start = float(bn_ticks[0].price)
                bn_end = float(bn_ticks[-1].price)
                cb_start = float(cb_ticks[0].price)
                cb_end = float(cb_ticks[-1].price)
                if bn_start > 0 and cb_start > 0:
                    bn_mom = (bn_end - bn_start) / bn_start
                    cb_mom = (cb_end - cb_start) / cb_start
                    # Positive lead = secondary moving up faster (bullish)
                    cross_exchange_lead = bn_mom - cb_mom

        # Taker buy/sell volume (real-time from secondary feed)
        taker_buy = None
        taker_sell = None
        if secondary_feed:
            buy, sell = secondary_feed.get_taker_volume_since(300)  # 5 min window
            if buy > 0 or sell > 0:
                # Convert from base asset to USD
                price_usd = float(btc_price)
                taker_buy = buy * price_usd
                taker_sell = sell * price_usd

        # Compute time elapsed and window phase
        time_elapsed = max(0.0, 900.0 - time_to_expiry)
        cfg = self._strategy_config
        if time_elapsed < cfg.phase_observation_end:
            window_phase = 1
        elif time_elapsed < cfg.phase_confirmation_end:
            window_phase = 2
        elif time_elapsed < cfg.phase_active_end:
            window_phase = 3
        elif time_elapsed < cfg.phase_late_end:
            window_phase = 4
        else:
            window_phase = 5

        return MarketSnapshot(
            timestamp=now,
            market_ticker=market_ticker,
            btc_price=btc_price,
            btc_prices_1min=prices_1min,
            btc_prices_5min=prices_5min,
            btc_volumes_1min=volumes_1min,
            orderbook=orderbook,
            implied_yes_prob=orderbook.implied_yes_prob,
            spread=orderbook.spread,
            strike_price=strike_price,
            statistical_fair_value=stat_fair_value,
            binance_btc_price=binance_btc_price,
            cross_exchange_spread=cross_exchange_spread,
            cross_exchange_lead=cross_exchange_lead,
            taker_buy_volume=taker_buy,
            taker_sell_volume=taker_sell,
            time_to_expiry_seconds=time_to_expiry,
            time_elapsed_seconds=time_elapsed,
            window_phase=window_phase,
            volume=volume,
        )
