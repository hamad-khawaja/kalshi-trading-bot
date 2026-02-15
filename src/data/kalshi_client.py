"""Async REST client for Kalshi Trade API v2."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import aiohttp
import structlog

from src.config import KalshiConfig
from src.data.kalshi_auth import KalshiAuth
from src.data.models import (
    Market,
    Orderbook,
    OrderbookLevel,
    OrderRequest,
    OrderResponse,
    Position,
)

logger = structlog.get_logger()


class KalshiAPIError(Exception):
    """Raised when the Kalshi API returns an error."""

    def __init__(self, status: int, message: str, retry_after: float | None = None):
        super().__init__(f"Kalshi API error {status}: {message}")
        self.status = status
        self.retry_after = retry_after


class KalshiRestClient:
    """Async REST client for Kalshi Trade API v2.

    Handles authentication, rate limiting, and retry logic.
    All prices use dollar-denominated fields (yes_price_dollars, no_price_dollars).
    """

    MAX_RETRIES = 3
    RETRY_BACKOFF_BASE = 1.0

    def __init__(self, config: KalshiConfig, auth: KalshiAuth):
        self._config = config
        self._auth = auth
        self._base_url = config.base_url
        self._session: aiohttp.ClientSession | None = None
        self._last_request_time = 0.0

    async def connect(self) -> None:
        """Create HTTP session."""
        self._session = aiohttp.ClientSession(
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=30),
        )
        logger.info("kalshi_rest_connected", base_url=self._base_url)

    async def close(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None

    async def _rate_limit(self) -> None:
        """Enforce minimum interval between requests."""
        now = asyncio.get_event_loop().time()
        elapsed_ms = (now - self._last_request_time) * 1000
        if elapsed_ms < self._config.rate_limit_ms:
            wait_s = (self._config.rate_limit_ms - elapsed_ms) / 1000
            await asyncio.sleep(wait_s)
        self._last_request_time = asyncio.get_event_loop().time()

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """Execute an authenticated request with rate limiting and retry."""
        if not self._session:
            raise RuntimeError("Client not connected. Call connect() first.")

        await self._rate_limit()

        url = f"{self._base_url}{path}"
        # Signature must include the full API path (e.g. /trade-api/v2/markets)
        base_path = "/trade-api/v2"
        full_path = f"{base_path}{path}"
        query_path = full_path
        if params:
            query_str = "&".join(f"{k}={v}" for k, v in params.items())
            query_path = f"{full_path}?{query_str}"

        for attempt in range(self.MAX_RETRIES):
            auth_headers = self._auth.get_headers(method.upper(), query_path)

            try:
                async with self._session.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                    headers=auth_headers,
                ) as resp:
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", "5"))
                        logger.warning(
                            "kalshi_rate_limited",
                            retry_after=retry_after,
                            attempt=attempt,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    if resp.status >= 500:
                        logger.warning(
                            "kalshi_server_error",
                            status=resp.status,
                            attempt=attempt,
                        )
                        await asyncio.sleep(self.RETRY_BACKOFF_BASE * (2**attempt))
                        continue

                    body = await resp.json()

                    if resp.status >= 400:
                        msg = body.get("message", body.get("error", str(body)))
                        raise KalshiAPIError(resp.status, msg)

                    return body

            except aiohttp.ClientError as e:
                logger.warning(
                    "kalshi_connection_error",
                    error=str(e),
                    attempt=attempt,
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_BACKOFF_BASE * (2**attempt))
                else:
                    raise

        raise KalshiAPIError(500, "Max retries exceeded")

    # ---- Market Data ----

    async def get_markets(
        self,
        series_ticker: str | None = None,
        status: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> list[Market]:
        """Get markets, optionally filtered by series and status."""
        params: dict[str, Any] = {"limit": str(limit)}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor

        data = await self._request("GET", "/markets", params=params)
        markets = []
        for m in data.get("markets", []):
            markets.append(self._parse_market(m))
        return markets

    async def get_market(self, ticker: str) -> Market:
        """Get a single market by ticker."""
        data = await self._request("GET", f"/markets/{ticker}")
        return self._parse_market(data.get("market", data))

    async def get_orderbook(self, ticker: str) -> Orderbook:
        """Get orderbook for a market. Uses dollar-denominated fields."""
        data = await self._request("GET", f"/markets/{ticker}/orderbook")
        ob = data.get("orderbook", data)
        now = datetime.now(timezone.utc)

        yes_levels = []
        no_levels = []

        # Parse orderbook levels.
        # API returns two formats:
        #   - Dollar: "yes_dollars" / "no_dollars" as [[price_str, qty], ...]
        #   - Cent: "yes" / "no" as [[price_cents, qty], ...]
        # Note: API may return null instead of [] for empty sides.
        for entry in ob.get("yes_dollars") or []:
            if isinstance(entry, list) and len(entry) >= 2:
                yes_levels.append(
                    OrderbookLevel(
                        price_dollars=Decimal(str(entry[0])),
                        quantity=int(entry[1]),
                    )
                )

        for entry in ob.get("no_dollars") or []:
            if isinstance(entry, list) and len(entry) >= 2:
                no_levels.append(
                    OrderbookLevel(
                        price_dollars=Decimal(str(entry[0])),
                        quantity=int(entry[1]),
                    )
                )

        # Fallback: parse cent-based fields
        if not yes_levels and not no_levels:
            for entry in ob.get("yes") or []:
                if isinstance(entry, list) and len(entry) >= 2:
                    yes_levels.append(
                        OrderbookLevel(
                            price_dollars=Decimal(str(entry[0])) / 100,
                            quantity=int(entry[1]),
                        )
                    )
            for entry in ob.get("no") or []:
                if isinstance(entry, list) and len(entry) >= 2:
                    no_levels.append(
                        OrderbookLevel(
                            price_dollars=Decimal(str(entry[0])) / 100,
                            quantity=int(entry[1]),
                        )
                    )

        return Orderbook(
            ticker=ticker,
            yes_levels=yes_levels,
            no_levels=no_levels,
            timestamp=now,
        )

    async def get_market_result(self, ticker: str) -> str | None:
        """Get the settlement result for a market ('yes', 'no', or None if not settled)."""
        try:
            market = await self.get_market(ticker)
            if market.status == "settled":
                # The result is in the raw API data
                data = await self._request("GET", f"/markets/{ticker}")
                m = data.get("market", data)
                return m.get("result", None)
        except Exception:
            pass
        return None

    async def get_settled_markets(
        self, series_ticker: str, limit: int = 5
    ) -> list[dict]:
        """Get recently settled markets with their results."""
        params = {
            "series_ticker": series_ticker,
            "status": "settled",
            "limit": str(limit),
        }
        data = await self._request("GET", "/markets", params=params)
        results = []
        for m in data.get("markets", []):
            results.append({
                "ticker": m.get("ticker", ""),
                "title": m.get("title", ""),
                "yes_sub_title": m.get("yes_sub_title", ""),
                "result": m.get("result", ""),
                "close_time": m.get("close_time", ""),
                "volume": m.get("volume", 0),
            })
        return results

    async def get_trades(self, ticker: str, limit: int = 100) -> list[dict]:
        """Get recent trades for a market."""
        params = {"ticker": ticker, "limit": str(limit)}
        data = await self._request("GET", "/markets/trades", params=params)
        return data.get("trades", [])

    # ---- Portfolio ----

    async def get_balance(self) -> Decimal:
        """Get account balance in dollars."""
        data = await self._request("GET", "/portfolio/balance")
        balance = data.get("balance", 0)
        return Decimal(str(balance)) / 100  # API returns cents

    async def create_order(self, order: OrderRequest) -> OrderResponse:
        """Place a new order."""
        data = await self._request(
            "POST", "/portfolio/orders", json_body=order.to_api_dict()
        )
        order_data = data.get("order", data)
        return OrderResponse(
            order_id=order_data.get("order_id", ""),
            client_order_id=order_data.get("client_order_id", ""),
            ticker=order_data.get("ticker", ""),
            status=order_data.get("status", ""),
            side=order_data.get("side", ""),
            action=order_data.get("action", ""),
            yes_price_dollars=(
                Decimal(str(order_data["yes_price_dollars"]))
                if order_data.get("yes_price_dollars")
                else None
            ),
            no_price_dollars=(
                Decimal(str(order_data["no_price_dollars"]))
                if order_data.get("no_price_dollars")
                else None
            ),
            count=order_data.get("count", 0),
            fill_count=order_data.get("fill_count", 0),
            remaining_count=order_data.get("remaining_count", 0),
            taker_fees_dollars=(
                Decimal(str(order_data["taker_fees_dollars"]))
                if order_data.get("taker_fees_dollars")
                else None
            ),
            maker_fees_dollars=(
                Decimal(str(order_data["maker_fees_dollars"]))
                if order_data.get("maker_fees_dollars")
                else None
            ),
            created_time=(
                datetime.fromisoformat(order_data["created_time"])
                if order_data.get("created_time")
                else None
            ),
        )

    async def cancel_order(self, order_id: str) -> None:
        """Cancel an active order."""
        await self._request("DELETE", f"/portfolio/orders/{order_id}")

    async def get_positions(self) -> list[Position]:
        """Get current open positions."""
        data = await self._request("GET", "/portfolio/positions")
        positions = []
        for p in data.get("market_positions", []):
            positions.append(
                Position(
                    ticker=p.get("ticker", ""),
                    position=p.get("position", 0),
                    market_exposure=p.get("market_exposure", 0),
                    resting_orders_count=p.get("resting_orders_count", 0),
                    fees_paid=Decimal(str(p.get("fees_paid", 0))) / 100,
                    total_traded=Decimal(str(p.get("total_traded", 0))) / 100,
                    realized_pnl=Decimal(str(p.get("realized_pnl", 0))) / 100,
                )
            )
        return positions

    async def get_order(self, order_id: str) -> dict:
        """Get a single order by ID."""
        data = await self._request("GET", f"/portfolio/orders/{order_id}")
        return data.get("order", data)

    async def get_orders(
        self,
        ticker: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """Get orders, optionally filtered."""
        params: dict[str, str] = {}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        data = await self._request("GET", "/portfolio/orders", params=params)
        return data.get("orders", [])

    # ---- Helpers ----

    @staticmethod
    def _parse_market(m: dict) -> Market:
        """Parse a market dict from the API into a Market model."""

        def _parse_dt(val: Any) -> datetime | None:
            if not val:
                return None
            if isinstance(val, datetime):
                return val
            try:
                return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None

        def _parse_decimal(val: Any) -> Decimal | None:
            if val is None:
                return None
            try:
                return Decimal(str(val))
            except Exception:
                return None

        return Market(
            ticker=m.get("ticker", ""),
            event_ticker=m.get("event_ticker", ""),
            title=m.get("title", ""),
            subtitle=m.get("subtitle", ""),
            yes_sub_title=m.get("yes_sub_title", ""),
            status=m.get("status", ""),
            yes_bid=_parse_decimal(m.get("yes_bid_dollars", m.get("yes_bid"))),
            yes_ask=_parse_decimal(m.get("yes_ask_dollars", m.get("yes_ask"))),
            no_bid=_parse_decimal(m.get("no_bid_dollars", m.get("no_bid"))),
            no_ask=_parse_decimal(m.get("no_ask_dollars", m.get("no_ask"))),
            last_price=_parse_decimal(
                m.get("last_price_dollars", m.get("last_price"))
            ),
            volume=m.get("volume", 0),
            volume_24h=m.get("volume_24h", 0),
            open_interest=m.get("open_interest", 0),
            open_time=_parse_dt(m.get("open_time")),
            close_time=_parse_dt(m.get("close_time")),
            expiration_time=_parse_dt(m.get("expiration_time")),
            expected_expiration_time=_parse_dt(m.get("expected_expiration_time")),
        )
