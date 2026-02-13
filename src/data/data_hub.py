"""Central data aggregator for all market data sources."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from src.config import BotSettings
from src.data.binance_feed import BinanceFeed
from src.data.coinglass_client import CoinglassClient
from src.data.kalshi_client import KalshiRestClient
from src.data.kalshi_ws import KalshiWebSocket
from src.data.market_scanner import MarketScanner
from src.data.models import MarketSnapshot, Orderbook
from src.strategy.fair_value import compute_fair_value_from_prices, parse_strike_price

logger = structlog.get_logger()


class DataHub:
    """Central data aggregator providing unified snapshots for the strategy layer.

    Combines data from:
    - Kalshi REST + WebSocket (orderbooks, market data)
    - Binance WebSocket (BTC price)
    - Coinglass REST (funding rates, open interest)
    """

    def __init__(
        self,
        kalshi_rest: KalshiRestClient,
        kalshi_ws: KalshiWebSocket,
        binance: BinanceFeed,
        coinglass: CoinglassClient,
        scanner: MarketScanner,
        secondary_feed: BinanceFeed | None = None,
    ):
        self._kalshi_rest = kalshi_rest
        self._kalshi_ws = kalshi_ws
        self._binance = binance
        self._secondary_feed = secondary_feed
        self._coinglass = coinglass
        self._scanner = scanner
        self._orderbook_cache: dict[str, Orderbook] = {}
        self._ws_subscribed_tickers: set[str] = set()

    async def start(self) -> None:
        """Connect all data sources. Kalshi/Coinglass failures are non-fatal."""
        # Binance is critical (BTC price), others are best-effort
        await self._binance.connect()

        best_effort = [
            ("kalshi_rest", self._kalshi_rest.connect()),
            ("kalshi_ws", self._kalshi_ws.connect()),
            ("coinglass", self._coinglass.connect()),
        ]
        if self._secondary_feed:
            best_effort.append(("secondary_feed", self._secondary_feed.connect()))

        for name, coro in best_effort:
            try:
                await coro
            except Exception:
                logger.warning("data_source_connect_failed", source=name)

        logger.info("data_hub_started")

    async def stop(self) -> None:
        """Gracefully close all connections."""
        coros = [
            self._kalshi_rest.close(),
            self._kalshi_ws.close(),
            self._binance.close(),
            self._coinglass.close(),
        ]
        if self._secondary_feed:
            coros.append(self._secondary_feed.close())
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
        """Handle orderbook updates from WebSocket."""
        # Store the latest orderbook snapshot
        # The WebSocket sends initial snapshot + deltas
        msg_type = msg.get("type", "")
        if msg_type in ("orderbook_snapshot", "orderbook_delta"):
            # For simplicity, we'll refresh via REST on each strategy cycle
            # WebSocket updates are used primarily for latency-sensitive detection
            pass

    async def get_snapshot(self, market_ticker: str) -> MarketSnapshot | None:
        """Build a complete market snapshot for the strategy layer.

        Returns None if critical data (BTC price) is unavailable.
        """
        now = datetime.now(timezone.utc)

        # BTC price data
        btc_price = self._binance.latest_price
        if btc_price is None:
            logger.warning("snapshot_no_btc_price")
            return None

        # Recent BTC price history
        ticks_1min = self._binance.get_prices_since(60)
        ticks_5min = self._binance.get_prices_since(300)
        prices_1min = [t.price for t in ticks_1min]
        prices_5min = [t.price for t in ticks_5min]
        volumes_1min = [t.volume for t in ticks_1min]

        # Kalshi orderbook (REST for reliability, WS for speed)
        try:
            orderbook = await self._kalshi_rest.get_orderbook(market_ticker)
            self._orderbook_cache[market_ticker] = orderbook
        except Exception:
            logger.warning("snapshot_orderbook_error", ticker=market_ticker)
            orderbook = self._orderbook_cache.get(
                market_ticker,
                Orderbook(ticker=market_ticker, timestamp=now),
            )

        # Kalshi market info
        market = self._scanner.active_markets.get(market_ticker)
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

        # Cross-exchange data (Binance secondary feed)
        binance_btc_price = None
        cross_exchange_spread = None
        cross_exchange_lead = None
        if self._secondary_feed and self._secondary_feed.latest_price is not None:
            binance_btc_price = self._secondary_feed.latest_price
            # Spread: (binance - coinbase) / coinbase as percentage
            cb_price = float(btc_price)
            bn_price = float(binance_btc_price)
            if cb_price > 0:
                cross_exchange_spread = (bn_price - cb_price) / cb_price
            # Lead signal: compare short-term momentum across exchanges
            # If Binance moved more than Coinbase recently, it's leading
            bn_ticks = self._secondary_feed.get_prices_since(15)
            cb_ticks = self._binance.get_prices_since(15)
            if len(bn_ticks) >= 2 and len(cb_ticks) >= 2:
                bn_start = float(bn_ticks[0].price)
                bn_end = float(bn_ticks[-1].price)
                cb_start = float(cb_ticks[0].price)
                cb_end = float(cb_ticks[-1].price)
                if bn_start > 0 and cb_start > 0:
                    bn_mom = (bn_end - bn_start) / bn_start
                    cb_mom = (cb_end - cb_start) / cb_start
                    # Positive lead = Binance moving up faster (bullish)
                    cross_exchange_lead = bn_mom - cb_mom

        # Coinglass data (cached, non-blocking)
        funding_rate = None
        open_interest = None
        oi_change = None
        long_short = None
        try:
            fr = await self._coinglass.get_funding_rate()
            funding_rate = fr.rate
        except Exception:
            pass
        try:
            oi = await self._coinglass.get_open_interest()
            open_interest = oi.value
            oi_change = oi.change_24h
        except Exception:
            pass
        try:
            lsr = await self._coinglass.get_long_short_ratio()
            long_short = lsr.ratio
        except Exception:
            pass

        # Liquidation data (from Coinglass, cached)
        liq_long_usd = None
        liq_short_usd = None
        try:
            liq = await self._coinglass.get_liquidation_data()
            liq_long_usd = liq.long_usd
            liq_short_usd = liq.short_usd
        except Exception:
            pass

        # Taker buy/sell volume (real-time from Binance secondary feed)
        taker_buy = None
        taker_sell = None
        if self._secondary_feed:
            buy, sell = self._secondary_feed.get_taker_volume_since(300)  # 5 min window
            if buy > 0 or sell > 0:
                # Convert from BTC to USD
                price_usd = float(btc_price)
                taker_buy = buy * price_usd
                taker_sell = sell * price_usd

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
            funding_rate=funding_rate,
            open_interest=open_interest,
            open_interest_change=oi_change,
            long_short_ratio=long_short,
            liquidation_long_usd=liq_long_usd,
            liquidation_short_usd=liq_short_usd,
            taker_buy_volume=taker_buy,
            taker_sell_volume=taker_sell,
            time_to_expiry_seconds=time_to_expiry,
            volume=volume,
        )
