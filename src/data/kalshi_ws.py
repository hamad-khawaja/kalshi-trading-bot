"""WebSocket client for Kalshi real-time market data."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import structlog
import websockets
from websockets.exceptions import ConnectionClosed

from src.config import KalshiConfig
from src.data.kalshi_auth import KalshiAuth

logger = structlog.get_logger()


class KalshiWebSocket:
    """Manages WebSocket connection to Kalshi for real-time market data.

    Supports channels: orderbook_delta, ticker, trade, fill, user_orders.
    Handles authentication, reconnection, and message dispatching.
    """

    RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30]

    def __init__(self, config: KalshiConfig, auth: KalshiAuth):
        self._config = config
        self._auth = auth
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._callbacks: dict[str, list[Callable]] = {}
        self._subscriptions: list[dict] = []
        self._running = False
        self._task: asyncio.Task | None = None
        self._sub_id_counter = 0

    async def connect(self) -> None:
        """Establish authenticated WebSocket connection and start message loop."""
        self._running = True
        await self._connect_ws()
        self._task = asyncio.create_task(self._message_loop())

    async def _connect_ws(self) -> None:
        """Open WebSocket with authentication headers."""
        ws_path = "/trade-api/ws/v2"
        headers = self._auth.get_headers("GET", ws_path)
        url = self._config.ws_url

        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        )
        logger.info("kalshi_ws_connected", url=url)

        # Resubscribe to all channels after reconnect
        for sub in self._subscriptions:
            await self._send_subscribe(sub)

    async def subscribe_orderbook(
        self, market_ticker: str, callback: Callable[[dict], None]
    ) -> int:
        """Subscribe to orderbook_delta channel for a market."""
        return await self._subscribe(
            "orderbook_delta", {"market_ticker": market_ticker}, callback
        )

    async def subscribe_ticker(
        self, market_ticker: str, callback: Callable[[dict], None]
    ) -> int:
        """Subscribe to ticker channel for price updates."""
        return await self._subscribe(
            "ticker", {"market_ticker": market_ticker}, callback
        )

    async def subscribe_fills(self, callback: Callable[[dict], None]) -> int:
        """Subscribe to fill notifications for the authenticated user."""
        return await self._subscribe("fill", {}, callback)

    async def subscribe_trades(
        self, market_ticker: str, callback: Callable[[dict], None]
    ) -> int:
        """Subscribe to trade channel for a market."""
        return await self._subscribe(
            "trade", {"market_ticker": market_ticker}, callback
        )

    async def _subscribe(
        self, channel: str, params: dict, callback: Callable
    ) -> int:
        """Internal: subscribe to a channel with params."""
        self._sub_id_counter += 1
        sub_id = self._sub_id_counter

        sub = {
            "id": sub_id,
            "cmd": "subscribe",
            "params": {"channels": [channel], **params},
        }

        if channel not in self._callbacks:
            self._callbacks[channel] = []
        self._callbacks[channel].append(callback)

        self._subscriptions.append(sub)

        if self._ws:
            await self._send_subscribe(sub)

        return sub_id

    async def _send_subscribe(self, sub: dict) -> None:
        """Send a subscribe command over the WebSocket."""
        if self._ws:
            try:
                await self._ws.send(json.dumps(sub))
                logger.debug("kalshi_ws_subscribed", sub=sub)
            except ConnectionClosed:
                logger.warning("kalshi_ws_send_failed_closed")

    async def _message_loop(self) -> None:
        """Main loop: receive and dispatch messages with reconnection."""
        reconnect_attempt = 0

        while self._running:
            try:
                if self._ws is None:
                    await self._connect_ws()
                    reconnect_attempt = 0

                async for raw_msg in self._ws:  # type: ignore
                    reconnect_attempt = 0
                    try:
                        msg = json.loads(raw_msg)
                        await self._dispatch(msg)
                    except json.JSONDecodeError:
                        logger.warning("kalshi_ws_invalid_json", raw=str(raw_msg)[:200])

            except ConnectionClosed as e:
                logger.warning("kalshi_ws_disconnected", code=e.code, reason=e.reason)
                self._ws = None
            except Exception:
                logger.exception("kalshi_ws_error")
                self._ws = None

            if self._running:
                delay = self.RECONNECT_DELAYS[
                    min(reconnect_attempt, len(self.RECONNECT_DELAYS) - 1)
                ]
                logger.info("kalshi_ws_reconnecting", delay=delay)
                await asyncio.sleep(delay)
                reconnect_attempt += 1

    # Map snapshot message types to their subscription channel
    _TYPE_TO_CHANNEL = {
        "orderbook_snapshot": "orderbook_delta",
    }

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        """Dispatch a message to registered callbacks."""
        msg_type = msg.get("type", "")
        channel = msg.get("channel", "")
        if not channel:
            # Map known snapshot types to their subscription channel
            channel = self._TYPE_TO_CHANNEL.get(msg_type, msg_type)

        # Handle subscription confirmations
        if msg_type == "subscribed":
            logger.debug("kalshi_ws_subscription_confirmed", msg=msg)
            return

        # Handle errors
        if msg_type == "error":
            logger.error("kalshi_ws_error_msg", msg=msg)
            return

        # Dispatch to channel callbacks
        callbacks = self._callbacks.get(channel, [])
        for cb in callbacks:
            try:
                result = cb(msg)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("kalshi_ws_callback_error", channel=channel)

    async def close(self) -> None:
        """Gracefully close WebSocket connection."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("kalshi_ws_closed")
