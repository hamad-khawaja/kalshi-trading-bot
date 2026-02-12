"""Scans for active KXBTC15M markets and manages lifecycle."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import structlog

from src.config import KalshiConfig
from src.data.kalshi_client import KalshiRestClient
from src.data.models import Market

logger = structlog.get_logger()

# Month abbreviation mapping for ticker parsing
MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Pattern: kxbtc15m-26feb121345  (YYmonDDHHMM)
TICKER_PATTERN = re.compile(
    r"^[a-z0-9]+-(\d{2})([a-z]{3})(\d{2})(\d{4})$", re.IGNORECASE
)


class MarketScanner:
    """Scans for active KXBTC15M markets and tracks their lifecycle.

    Identifies which markets are currently tradeable (open, not too close to expiry),
    and which are upcoming.
    """

    MIN_TIME_TO_EXPIRY_SECONDS = 60  # Don't trade if < 60s to expiry
    MAX_TIME_TO_EXPIRY_SECONDS = 3600  # Only look at markets expiring within 1 hour

    def __init__(self, client: KalshiRestClient, config: KalshiConfig):
        self._client = client
        self._config = config
        self._active_markets: dict[str, Market] = {}

    @property
    def active_markets(self) -> dict[str, Market]:
        """Currently tracked active markets."""
        return dict(self._active_markets)

    async def scan(self) -> list[Market]:
        """Fetch open markets for the configured series and filter by time."""
        try:
            markets = await self._client.get_markets(
                series_ticker=self._config.series_ticker,
                status="open",
                limit=50,
            )
        except Exception:
            logger.exception("market_scan_error")
            return list(self._active_markets.values())

        now = datetime.now(timezone.utc)
        active = []

        for market in markets:
            expiry = market.expiration_time
            if not expiry:
                # Try parsing from ticker
                expiry = self.parse_ticker_expiry(market.ticker)
                if expiry:
                    market.expiration_time = expiry

            if not expiry:
                continue

            # Ensure timezone-aware
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)

            seconds_to_expiry = (expiry - now).total_seconds()

            if (
                seconds_to_expiry > self.MIN_TIME_TO_EXPIRY_SECONDS
                and seconds_to_expiry <= self.MAX_TIME_TO_EXPIRY_SECONDS
            ):
                active.append(market)
                self._active_markets[market.ticker] = market

        # Remove expired markets
        expired = [
            ticker
            for ticker, m in self._active_markets.items()
            if m.expiration_time
            and (
                m.expiration_time.replace(tzinfo=timezone.utc)
                if m.expiration_time.tzinfo is None
                else m.expiration_time
            )
            <= now
        ]
        for ticker in expired:
            del self._active_markets[ticker]
            logger.info("market_expired", ticker=ticker)

        if active:
            logger.info(
                "markets_scanned",
                active_count=len(active),
                tickers=[m.ticker for m in active],
            )

        return active

    def get_current_market(self) -> Market | None:
        """Return the market closest to expiry that still has time to trade."""
        now = datetime.now(timezone.utc)
        best: Market | None = None
        best_expiry: datetime | None = None

        for market in self._active_markets.values():
            expiry = market.expiration_time
            if not expiry:
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)

            seconds_left = (expiry - now).total_seconds()
            if seconds_left < self.MIN_TIME_TO_EXPIRY_SECONDS:
                continue

            if best_expiry is None or expiry < best_expiry:
                best = market
                best_expiry = expiry

        return best

    def get_next_market(self) -> Market | None:
        """Return the next market after the current one."""
        current = self.get_current_market()
        if not current:
            return None

        now = datetime.now(timezone.utc)
        best: Market | None = None
        best_expiry: datetime | None = None

        for market in self._active_markets.values():
            if market.ticker == current.ticker:
                continue
            expiry = market.expiration_time
            if not expiry:
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)

            seconds_left = (expiry - now).total_seconds()
            if seconds_left < self.MIN_TIME_TO_EXPIRY_SECONDS:
                continue

            if best_expiry is None or expiry < best_expiry:
                best = market
                best_expiry = expiry

        return best

    @staticmethod
    def parse_ticker_expiry(ticker: str) -> datetime | None:
        """Parse expiry time from a ticker like kxbtc15m-26feb121345.

        Format: SERIES-YYmonDDHHMM
        Example: kxbtc15m-26feb121345 -> 2026-02-12 13:45 UTC
        """
        match = TICKER_PATTERN.match(ticker)
        if not match:
            return None

        year_short = match.group(1)
        month_str = match.group(2).lower()
        day = match.group(3)
        time_str = match.group(4)

        month = MONTH_MAP.get(month_str)
        if not month:
            return None

        year = 2000 + int(year_short)
        hour = int(time_str[:2])
        minute = int(time_str[2:])

        try:
            return datetime(year, month, int(day), hour, minute, tzinfo=timezone.utc)
        except ValueError:
            return None
