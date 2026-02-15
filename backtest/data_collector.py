"""Historical data collection for backtesting and model training."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import structlog

from src.config import BotSettings, load_settings
from src.data.binance_feed import BinanceFeed
from src.data.database import Database
from src.data.kalshi_auth import KalshiAuth
from src.data.kalshi_client import KalshiRestClient
from src.data.market_scanner import MarketScanner

logger = structlog.get_logger()


class DataCollector:
    """Collects and stores real-time data for backtesting and model training.

    Runs alongside the main bot (or standalone) to capture:
    - BTC price ticks from Binance
    - Kalshi orderbook snapshots
    - Market outcomes (settlement results)
    """

    def __init__(self, settings: BotSettings):
        self._settings = settings
        self._auth = KalshiAuth(
            settings.kalshi.api_key_id,
            settings.kalshi.private_key_path,
        )
        self._kalshi = KalshiRestClient(settings.kalshi, self._auth)
        self._binance = BinanceFeed(settings.binance)
        self._scanner = MarketScanner(self._kalshi, settings.kalshi)
        self._db = Database(settings.database.path)
        self._running = False
        self._tick_count = 0
        self._snapshot_count = 0

    async def collect(self, duration_hours: float = 24) -> None:
        """Run data collection for the specified duration.

        Collects:
        1. BTC price ticks (stored every 1 second)
        2. Kalshi orderbook snapshots (every 5 seconds per active market)
        3. Market outcomes when contracts expire
        """
        self._running = True
        end_time = datetime.now(timezone.utc).timestamp() + duration_hours * 3600

        await self._db.connect()
        await self._kalshi.connect()
        await self._binance.connect()

        logger.info(
            "data_collection_started",
            duration_hours=duration_hours,
            db_path=self._settings.database.path,
        )

        try:
            tasks = [
                asyncio.create_task(self._collect_ticks(end_time)),
                asyncio.create_task(self._collect_orderbooks(end_time)),
                asyncio.create_task(self._collect_outcomes(end_time)),
            ]
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            await self._binance.close()
            await self._kalshi.close()
            await self._db.close()
            logger.info(
                "data_collection_complete",
                ticks=self._tick_count,
                snapshots=self._snapshot_count,
            )

    async def _collect_ticks(self, end_time: float) -> None:
        """Store BTC price ticks at 1-second intervals."""
        while self._running and datetime.now(timezone.utc).timestamp() < end_time:
            tick = self._binance.latest_tick
            if tick:
                await self._db.insert_tick(
                    timestamp=tick.timestamp,
                    btc_price=float(tick.price),
                    btc_volume=float(tick.volume),
                )
                self._tick_count += 1
            await asyncio.sleep(1)

    async def _collect_orderbooks(self, end_time: float) -> None:
        """Store Kalshi orderbook snapshots every 5 seconds."""
        while self._running and datetime.now(timezone.utc).timestamp() < end_time:
            try:
                await self._scanner.scan()
                for ticker, market in self._scanner.active_markets.items():
                    ob = await self._kalshi.get_orderbook(ticker)
                    btc_price = self._binance.latest_price

                    await self._db.insert_tick(
                        timestamp=datetime.now(timezone.utc),
                        btc_price=float(btc_price or 0),
                        kalshi_yes_bid=float(ob.best_yes_bid or 0),
                        kalshi_yes_ask=float(ob.best_yes_ask or 0),
                        kalshi_spread=float(ob.spread or 0),
                        market_ticker=ticker,
                    )
                    self._snapshot_count += 1
            except Exception:
                logger.exception("orderbook_collection_error")

            await asyncio.sleep(5)

    async def _collect_outcomes(self, end_time: float) -> None:
        """Check for settled markets and record outcomes."""
        tracked_tickers: set[str] = set()

        while self._running and datetime.now(timezone.utc).timestamp() < end_time:
            try:
                await self._scanner.scan()

                for ticker in list(self._scanner.active_markets.keys()):
                    if ticker not in tracked_tickers:
                        tracked_tickers.add(ticker)

                # Check previously active markets for settlement
                for ticker in list(tracked_tickers):
                    if ticker not in self._scanner.active_markets:
                        # Market may have settled
                        try:
                            market = await self._kalshi.get_market(ticker)
                            if market.status in ("settled", "closed"):
                                await self._db.insert_outcome(
                                    market_ticker=ticker,
                                    btc_open=None,
                                    btc_close=None,
                                    result=market.status,
                                    settlement_time=datetime.now(timezone.utc),
                                )
                                tracked_tickers.discard(ticker)
                                logger.info("outcome_recorded", ticker=ticker)
                        except Exception:
                            pass

            except Exception:
                logger.exception("outcome_collection_error")

            await asyncio.sleep(60)

async def main() -> None:
    """Standalone data collection entry point."""
    import sys

    settings = load_settings()
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
    collector = DataCollector(settings)
    await collector.collect(duration_hours=duration)


if __name__ == "__main__":
    asyncio.run(main())
