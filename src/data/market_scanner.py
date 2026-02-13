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

# Pattern: KXBTC15M-26FEB121345-45  (YYmonDDHHMM with optional suffix)
TICKER_PATTERN = re.compile(
    r"^[a-z0-9]+-(\d{2})([a-z]{3})(\d{2})(\d{4})(?:-\w+)?$", re.IGNORECASE
)


class MarketScanner:
    """Scans for active KXBTC15M markets and tracks their lifecycle.

    Identifies which markets are currently tradeable (open, not too close to expiry),
    and which are upcoming.
    """

    MIN_TIME_TO_EXPIRY_SECONDS = 60  # Don't trade if < 60s to expiry
    MAX_TIME_TO_EXPIRY_SECONDS = 24 * 3600  # Look up to 24 hours ahead

    @staticmethod
    def _effective_close(market: Market) -> datetime | None:
        """Get the actual close/settlement time for a market.

        Kalshi BTC 15-min markets have:
        - close_time: when the market stops trading (the real deadline)
        - expected_expiration_time: when the market actually settles
        - expiration_time: the latest possible expiry (up to 7 days out)

        We use close_time for filtering since that's when trading ends.
        """
        return market.close_time or market.expected_expiration_time or market.expiration_time

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
            # Query for active/open markets first (the default query returns
            # newest-first which can be all "initialized" future markets).
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
            # Only include markets that are actually open for trading
            if market.status not in ("active", "open"):
                continue

            # Skip markets that haven't opened yet
            if market.open_time:
                open_time = market.open_time
                if open_time.tzinfo is None:
                    open_time = open_time.replace(tzinfo=timezone.utc)
                if open_time > now:
                    continue

            close = self._effective_close(market)
            if not close:
                # Fallback: try parsing from ticker
                close = self.parse_ticker_expiry(market.ticker)
                if close:
                    market.close_time = close

            if not close:
                continue

            # Ensure timezone-aware
            if close.tzinfo is None:
                close = close.replace(tzinfo=timezone.utc)

            seconds_to_close = (close - now).total_seconds()

            if (
                seconds_to_close > self.MIN_TIME_TO_EXPIRY_SECONDS
                and seconds_to_close <= self.MAX_TIME_TO_EXPIRY_SECONDS
            ):
                active.append(market)
                self._active_markets[market.ticker] = market

        # Remove closed markets
        expired = [
            ticker
            for ticker, m in self._active_markets.items()
            if self._effective_close(m)
            and (
                self._effective_close(m).replace(tzinfo=timezone.utc)
                if self._effective_close(m).tzinfo is None
                else self._effective_close(m)
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
        """Return the market closest to close that still has time to trade."""
        now = datetime.now(timezone.utc)
        best: Market | None = None
        best_close: datetime | None = None

        for market in self._active_markets.values():
            close = self._effective_close(market)
            if not close:
                continue
            if close.tzinfo is None:
                close = close.replace(tzinfo=timezone.utc)

            seconds_left = (close - now).total_seconds()
            if seconds_left < self.MIN_TIME_TO_EXPIRY_SECONDS:
                continue

            if best_close is None or close < best_close:
                best = market
                best_close = close

        return best

    def get_next_market(self) -> Market | None:
        """Return the next market after the current one."""
        current = self.get_current_market()
        if not current:
            return None

        now = datetime.now(timezone.utc)
        best: Market | None = None
        best_close: datetime | None = None

        for market in self._active_markets.values():
            if market.ticker == current.ticker:
                continue
            close = self._effective_close(market)
            if not close:
                continue
            if close.tzinfo is None:
                close = close.replace(tzinfo=timezone.utc)

            seconds_left = (close - now).total_seconds()
            if seconds_left < self.MIN_TIME_TO_EXPIRY_SECONDS:
                continue

            if best_close is None or close < best_close:
                best = market
                best_close = close

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
