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
    ):
        self._kalshi_rest = kalshi_rest
        self._kalshi_ws = kalshi_ws
        self._binance = binance
        self._coinglass = coinglass
        self._scanner = scanner
        self._orderbook_cache: dict[str, Orderbook] = {}
        self._ws_subscribed_tickers: set[str] = set()

    async def start(self) -> None:
        """Connect all data sources."""
        await asyncio.gather(
            self._kalshi_rest.connect(),
            self._kalshi_ws.connect(),
            self._binance.connect(),
            self._coinglass.connect(),
        )
        logger.info("data_hub_started")

    async def stop(self) -> None:
        """Gracefully close all connections."""
        await asyncio.gather(
            self._kalshi_rest.close(),
            self._kalshi_ws.close(),
            self._binance.close(),
            self._coinglass.close(),
            return_exceptions=True,
        )
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
        if market:
            if market.expiration_time:
                expiry = market.expiration_time
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                time_to_expiry = max(0.0, (expiry - now).total_seconds())
            volume = market.volume

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
            funding_rate=funding_rate,
            open_interest=open_interest,
            open_interest_change=oi_change,
            long_short_ratio=long_short,
            time_to_expiry_seconds=time_to_expiry,
            volume=volume,
        )
