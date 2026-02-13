"""Async client for Coinglass API — funding rates and open interest."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import aiohttp
import structlog

from src.config import CoinglassConfig
from src.data.models import FundingRate, LiquidationData, LongShortRatio, OpenInterest

logger = structlog.get_logger()


class CoinglassClient:
    """Polls Coinglass API for BTC derivatives market data.

    Data refreshed every ~30 seconds (not time-critical for 15-min contracts).
    Includes simple TTL cache to avoid redundant requests.
    """

    CACHE_TTL_SECONDS = 25.0

    def __init__(self, config: CoinglassConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[str, tuple[float, object]] = {}

    async def connect(self) -> None:
        """Create HTTP session."""
        headers = {}
        if self._config.api_key:
            headers["CG-API-KEY"] = self._config.api_key
        self._session = aiohttp.ClientSession(
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        )
        logger.info("coinglass_connected")

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    def _cache_get(self, key: str) -> object | None:
        """Get cached value if still fresh."""
        if key in self._cache:
            cached_time, value = self._cache[key]
            if time.time() - cached_time < self.CACHE_TTL_SECONDS:
                return value
        return None

    def _cache_set(self, key: str, value: object) -> None:
        """Store value in cache."""
        self._cache[key] = (time.time(), value)

    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 1.0  # seconds

    async def _request(self, endpoint: str, params: dict | None = None) -> dict:
        """Make authenticated GET request to Coinglass with retry and backoff."""
        if not self._session:
            raise RuntimeError("Client not connected")

        url = f"{self._config.base_url}{endpoint}"
        for attempt in range(self.MAX_RETRIES):
            try:
                async with self._session.get(url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    logger.debug(
                        "coinglass_retry",
                        status=resp.status,
                        endpoint=endpoint,
                        attempt=attempt + 1,
                    )
            except aiohttp.ClientError as e:
                logger.debug(
                    "coinglass_retry_error",
                    error=str(e),
                    attempt=attempt + 1,
                )

            if attempt < self.MAX_RETRIES - 1:
                delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                await asyncio.sleep(delay)

        logger.warning("coinglass_request_failed", endpoint=endpoint)
        return {}

    async def get_funding_rate(self, symbol: str = "BTC") -> FundingRate:
        """Get current aggregate BTC funding rate."""
        cached = self._cache_get(f"funding_{symbol}")
        if cached:
            return cached  # type: ignore

        data = await self._request(
            "/futures/funding/current", params={"symbol": symbol}
        )
        now = datetime.now(timezone.utc)

        rate = 0.0
        result_data = data.get("data", [])
        if isinstance(result_data, list) and result_data:
            # Average across exchanges
            rates = []
            for item in result_data:
                r = item.get("rate", item.get("fundingRate"))
                if r is not None:
                    try:
                        rates.append(float(r))
                    except (ValueError, TypeError):
                        pass
            if rates:
                rate = sum(rates) / len(rates)
        elif isinstance(result_data, dict):
            rate = float(result_data.get("rate", 0))

        result = FundingRate(rate=rate, timestamp=now)
        self._cache_set(f"funding_{symbol}", result)
        return result

    async def get_open_interest(self, symbol: str = "BTC") -> OpenInterest:
        """Get current BTC futures open interest."""
        cached = self._cache_get(f"oi_{symbol}")
        if cached:
            return cached  # type: ignore

        data = await self._request(
            "/futures/openInterest/chart", params={"symbol": symbol, "interval": "1h"}
        )
        now = datetime.now(timezone.utc)

        value = 0.0
        change = 0.0
        result_data = data.get("data", [])
        if isinstance(result_data, list) and len(result_data) >= 2:
            latest = result_data[-1]
            prev = result_data[-2]
            value = float(latest.get("openInterest", latest.get("value", 0)))
            prev_val = float(prev.get("openInterest", prev.get("value", 0)))
            if prev_val > 0:
                change = (value - prev_val) / prev_val * 100

        result = OpenInterest(value=value, change_24h=change, timestamp=now)
        self._cache_set(f"oi_{symbol}", result)
        return result

    async def get_long_short_ratio(self, symbol: str = "BTC") -> LongShortRatio:
        """Get current BTC long/short ratio."""
        cached = self._cache_get(f"lsr_{symbol}")
        if cached:
            return cached  # type: ignore

        data = await self._request(
            "/futures/longShortRate", params={"symbol": symbol, "interval": "1h"}
        )
        now = datetime.now(timezone.utc)

        ratio = 1.0
        long_pct = 50.0
        short_pct = 50.0
        result_data = data.get("data", [])
        if isinstance(result_data, list) and result_data:
            latest = result_data[-1]
            long_pct = float(latest.get("longRate", latest.get("longAccount", 50)))
            short_pct = float(latest.get("shortRate", latest.get("shortAccount", 50)))
            if short_pct > 0:
                ratio = long_pct / short_pct

        result = LongShortRatio(
            ratio=ratio, long_pct=long_pct, short_pct=short_pct, timestamp=now
        )
        self._cache_set(f"lsr_{symbol}", result)
        return result

    async def get_liquidation_data(self, symbol: str = "BTC") -> LiquidationData:
        """Get recent aggregated BTC liquidation data (last 5-minute interval)."""
        cached = self._cache_get(f"liq_{symbol}")
        if cached:
            return cached  # type: ignore

        data = await self._request(
            "/futures/liquidation/aggregated-history",
            params={"symbol": symbol, "interval": "5m", "limit": 3},
        )
        now = datetime.now(timezone.utc)

        long_usd = 0.0
        short_usd = 0.0
        result_data = data.get("data", [])
        if isinstance(result_data, list) and result_data:
            # Sum the most recent intervals for a rolling view
            for item in result_data:
                long_usd += float(item.get("longLiquidationUsd", item.get("long_liquidation_usd", 0)))
                short_usd += float(item.get("shortLiquidationUsd", item.get("short_liquidation_usd", 0)))

        result = LiquidationData(
            long_usd=long_usd,
            short_usd=short_usd,
            total_usd=long_usd + short_usd,
            timestamp=now,
        )
        self._cache_set(f"liq_{symbol}", result)
        return result

    async def refresh_all(self, symbol: str = "BTC") -> None:
        """Refresh all data points concurrently."""
        await asyncio.gather(
            self.get_funding_rate(symbol),
            self.get_open_interest(symbol),
            self.get_long_short_ratio(symbol),
            self.get_liquidation_data(symbol),
            return_exceptions=True,
        )
