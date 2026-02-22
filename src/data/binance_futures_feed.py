"""Futures feed: funding rate + liquidation data via Bybit WebSocket.

Uses Bybit v5 public WebSocket (no auth required, US-accessible):
- wss://stream.bybit.com/v5/public/linear
- Topics: tickers.{symbol} (funding rate) + allLiquidation.{symbol}
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque

import structlog

logger = structlog.get_logger()

# Backoff schedule in seconds (shared with ChainlinkFeed pattern)
BACKOFF_SCHEDULE = [1, 2, 4, 8, 16, 30]


@dataclass(slots=True)
class LiquidationEvent:
    """A single forced liquidation from the futures stream."""

    symbol: str
    side: str  # "SELL" (long liquidated) or "BUY" (short liquidated)
    quantity: float
    price: float
    usd_value: float
    timestamp: float  # time.monotonic()


class BinanceFuturesFeed:
    """Streams Bybit funding rates and liquidation events via WebSocket.

    Single WS connection subscribes to both:
    - tickers.{symbol}: real-time funding rate (pushes every 100ms)
    - allLiquidation.{symbol}: liquidation events (pushes every 500ms)

    Follows the ChainlinkFeed pattern: reconnect loop + exponential backoff.
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        funding_poll_interval: float = 45.0,  # kept for config compat, not used
        liquidation_ws_url: str = "wss://stream.bybit.com/v5/public/linear",
        funding_api_base: str = "https://api.bybit.com",  # kept for config compat
    ):
        self._symbols = [s.upper() for s in (symbols or ["BTCUSDT", "ETHUSDT"])]
        self._ws_url = liquidation_ws_url

        self._ws_task: asyncio.Task | None = None
        self._running = False

        # Funding rate state: symbol -> latest values
        self._funding_rates: dict[str, float] = {}
        self._predicted_funding_rates: dict[str, float] = {}

        # Liquidation state: symbol -> deque of events
        self._liquidations: dict[str, Deque[LiquidationEvent]] = {
            s: deque(maxlen=50_000) for s in self._symbols
        }
        self._consecutive_ws_errors: int = 0

    # --- Public API ---

    def get_funding_rate(self, symbol: str) -> float | None:
        """Return the last funding rate for *symbol*, or None if unavailable."""
        return self._funding_rates.get(symbol.upper())

    def get_predicted_funding_rate(self, symbol: str) -> float | None:
        """Return the predicted next funding rate for *symbol*, or None."""
        return self._predicted_funding_rates.get(symbol.upper())

    def get_liquidation_stats_since(
        self, symbol: str, seconds: float
    ) -> tuple[float, float]:
        """Return (long_liq_usd, short_liq_usd) in the last *seconds*.

        Long liquidations have side == "SELL" (forced to sell -> long closed).
        Short liquidations have side == "BUY" (forced to buy -> short closed).
        """
        cutoff = time.monotonic() - seconds
        long_usd = 0.0
        short_usd = 0.0
        for ev in self._liquidations.get(symbol.upper(), []):
            if ev.timestamp < cutoff:
                continue
            if ev.side == "SELL":
                long_usd += ev.usd_value
            else:
                short_usd += ev.usd_value
        return long_usd, short_usd

    # --- Lifecycle ---

    async def start(self) -> None:
        """Launch the combined WS task."""
        if self._running:
            return
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        logger.info(
            "futures_feed_started",
            symbols=self._symbols,
            source="bybit",
        )

    async def stop(self) -> None:
        """Cancel task."""
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

    # --- Combined WebSocket (tickers + liquidations) ---

    async def _ws_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.warning("websockets_not_installed_skipping_futures_feed")
            return

        while self._running:
            try:
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=None,  # We handle pings manually for Bybit
                    close_timeout=5,
                ) as ws:
                    self._consecutive_ws_errors = 0

                    # Subscribe to tickers (funding rate) + liquidations
                    topics = []
                    for s in self._symbols:
                        topics.append(f"tickers.{s}")
                        topics.append(f"allLiquidation.{s}")

                    sub_msg = {"op": "subscribe", "args": topics}
                    await ws.send(json.dumps(sub_msg))
                    logger.info(
                        "futures_ws_connected",
                        source="bybit",
                        topics=topics,
                    )

                    # Launch a ping task (Bybit requires ping every 20s)
                    ping_task = asyncio.create_task(self._ws_ping_loop(ws))
                    try:
                        async for raw_msg in ws:
                            if not self._running:
                                break
                            try:
                                msg = json.loads(raw_msg)
                                self._handle_msg(msg)
                            except Exception:
                                logger.debug("futures_msg_parse_error", exc_info=True)
                    finally:
                        ping_task.cancel()
                        try:
                            await ping_task
                        except asyncio.CancelledError:
                            pass

            except asyncio.CancelledError:
                break
            except Exception:
                self._consecutive_ws_errors += 1
                idx = min(
                    self._consecutive_ws_errors - 1,
                    len(BACKOFF_SCHEDULE) - 1,
                )
                backoff = BACKOFF_SCHEDULE[idx]
                logger.warning(
                    "futures_ws_error",
                    consecutive_errors=self._consecutive_ws_errors,
                    backoff=backoff,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)

    @staticmethod
    async def _ws_ping_loop(ws) -> None:
        """Send Bybit-format ping every 20 seconds."""
        try:
            while True:
                await asyncio.sleep(20)
                await ws.send(json.dumps({"op": "ping"}))
        except asyncio.CancelledError:
            pass
        except Exception:
            pass  # Connection closed, outer loop will reconnect

    def _handle_msg(self, msg: dict) -> None:
        """Route incoming WS messages to the appropriate handler."""
        topic = msg.get("topic", "")
        if not topic:
            return  # Subscription confirm, pong, etc.

        if topic.startswith("tickers."):
            self._handle_ticker_msg(msg)
        elif topic.startswith("allLiquidation."):
            self._handle_liquidation_msg(msg)

    def _handle_ticker_msg(self, msg: dict) -> None:
        """Extract funding rate from a tickers push.

        Bybit tickers format:
        {
            "topic": "tickers.BTCUSDT",
            "type": "snapshot" | "delta",
            "data": {"symbol": "BTCUSDT", "fundingRate": "0.0001", ...}
        }
        """
        data = msg.get("data", {})
        symbol = data.get("symbol", "").upper()
        rate_str = data.get("fundingRate")
        if rate_str is not None and symbol in self._funding_rates or symbol in [s for s in self._symbols]:
            try:
                rate = float(rate_str)
                self._funding_rates[symbol] = rate
                self._predicted_funding_rates[symbol] = rate
            except (TypeError, ValueError):
                pass

    def _handle_liquidation_msg(self, msg: dict) -> None:
        """Parse a Bybit allLiquidation message and append to the relevant deque.

        Bybit format:
        {
            "topic": "allLiquidation.BTCUSDT",
            "data": [{"T": 1739502302929, "s": "BTCUSDT", "S": "Sell", "v": "0.125", "p": "67450.30"}]
        }

        Side convention: "Sell" = long liquidated, "Buy" = short liquidated.
        We normalize to uppercase "SELL"/"BUY" for internal consistency.
        """
        for item in msg.get("data", []):
            symbol = item.get("s", "").upper()
            if symbol not in self._liquidations:
                continue
            raw_side = item.get("S", "")
            side = raw_side.upper()  # "SELL" or "BUY"
            try:
                qty = float(item.get("v", 0))
                price = float(item.get("p", 0))
            except (TypeError, ValueError):
                continue
            usd_value = qty * price
            if usd_value <= 0:
                continue
            self._liquidations[symbol].append(
                LiquidationEvent(
                    symbol=symbol,
                    side=side,
                    quantity=qty,
                    price=price,
                    usd_value=usd_value,
                    timestamp=time.monotonic(),
                )
            )
