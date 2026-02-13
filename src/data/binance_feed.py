"""Real-time BTC price feed from exchange WebSockets.

Supports Coinbase, Binance, and Kraken WebSocket formats (auto-detected from URL).
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal

import structlog
import websockets
from websockets.exceptions import ConnectionClosed

from src.config import BinanceConfig
from src.data.models import PriceTick

logger = structlog.get_logger()


class BinanceFeed:
    """Streams real-time BTC price ticks from an exchange WebSocket.

    Supports Coinbase, Binance, and Kraken message formats, auto-detected from URL.
    Maintains a ring buffer of recent price ticks for feature computation.
    """

    MAX_BUFFER_SIZE = 50_000
    RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30]

    def __init__(self, config: BinanceConfig):
        self._url = config.ws_url
        self._symbol = config.symbol
        self._provider = self._detect_provider(self._url)
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._price_buffer: deque[PriceTick] = deque(maxlen=self.MAX_BUFFER_SIZE)
        self._callbacks: list[Callable[[PriceTick], None]] = []
        self._running = False
        self._task: asyncio.Task | None = None

    @staticmethod
    def _detect_provider(url: str) -> str:
        """Auto-detect exchange from WebSocket URL."""
        url_lower = url.lower()
        if "coinbase" in url_lower:
            return "coinbase"
        if "kraken" in url_lower:
            return "kraken"
        return "binance"

    @property
    def latest_price(self) -> Decimal | None:
        """Most recent BTC price."""
        return self._price_buffer[-1].price if self._price_buffer else None

    @property
    def latest_tick(self) -> PriceTick | None:
        """Most recent price tick."""
        return self._price_buffer[-1] if self._price_buffer else None

    @property
    def price_history(self) -> deque[PriceTick]:
        """Ring buffer of recent price ticks."""
        return self._price_buffer

    def get_prices_since(self, seconds_ago: float) -> list[PriceTick]:
        """Get price ticks from the last N seconds."""
        if not self._price_buffer:
            return []
        cutoff = datetime.now(timezone.utc).timestamp() - seconds_ago
        result = []
        for tick in reversed(self._price_buffer):
            if tick.timestamp.timestamp() < cutoff:
                break
            result.append(tick)
        result.reverse()
        return result

    def on_price(self, callback: Callable[[PriceTick], None]) -> None:
        """Register a callback for each new price tick."""
        self._callbacks.append(callback)

    async def connect(self) -> None:
        """Connect to trade stream and start message loop."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("btc_feed_started", url=self._url, provider=self._provider)

    async def _run_loop(self) -> None:
        """Main loop with reconnection logic."""
        reconnect_attempt = 0

        while self._running:
            try:
                async with websockets.connect(
                    self._url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    reconnect_attempt = 0
                    logger.info("btc_ws_connected", provider=self._provider)

                    # Some exchanges require a subscribe message after connecting
                    if self._provider == "coinbase":
                        await self._coinbase_subscribe(ws)
                    elif self._provider == "kraken":
                        await self._kraken_subscribe(ws)

                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            self._handle_message(raw_msg)
                        except Exception:
                            logger.exception("btc_feed_message_error")

            except ConnectionClosed as e:
                logger.warning("btc_ws_disconnected", code=e.code, provider=self._provider)
                self._ws = None
            except Exception:
                logger.exception("btc_ws_error", provider=self._provider)
                self._ws = None

            if self._running:
                delay = self.RECONNECT_DELAYS[
                    min(reconnect_attempt, len(self.RECONNECT_DELAYS) - 1)
                ]
                logger.info("btc_ws_reconnecting", delay=delay, provider=self._provider)
                await asyncio.sleep(delay)
                reconnect_attempt += 1

    async def _coinbase_subscribe(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Send Coinbase subscription message for ticker channel."""
        product_id = "BTC-USD"
        sub_msg = json.dumps({
            "type": "subscribe",
            "channels": [{"name": "ticker", "product_ids": [product_id]}],
        })
        await ws.send(sub_msg)
        logger.info("coinbase_subscribed", product_id=product_id)

    async def _kraken_subscribe(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Send Kraken v2 subscription message for trade channel."""
        sub_msg = json.dumps({
            "method": "subscribe",
            "params": {
                "channel": "trade",
                "symbol": ["BTC/USD"],
                "snapshot": False,
            },
        })
        await ws.send(sub_msg)
        logger.info("kraken_subscribed", symbol="BTC/USD")

    def _handle_message(self, raw_msg: str | bytes) -> None:
        """Parse an exchange message and store/dispatch."""
        data = json.loads(raw_msg)

        if self._provider == "coinbase":
            ticks = [self._parse_coinbase(data)]
        elif self._provider == "kraken":
            ticks = self._parse_kraken(data)
        else:
            ticks = [self._parse_binance(data)]

        for tick in ticks:
            if tick is None:
                continue

            self._price_buffer.append(tick)

            for cb in self._callbacks:
                try:
                    cb(tick)
                except Exception:
                    logger.exception("btc_feed_callback_error")

    @staticmethod
    def _parse_coinbase(data: dict) -> PriceTick | None:
        """Parse a Coinbase ticker message."""
        if data.get("type") != "ticker":
            return None
        price = data.get("price")
        size = data.get("last_size", "0")
        time_str = data.get("time")
        if not price:
            return None
        ts = datetime.now(timezone.utc)
        if time_str:
            try:
                ts = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        return PriceTick(
            price=Decimal(str(price)),
            volume=Decimal(str(size)),
            timestamp=ts,
        )

    @staticmethod
    def _parse_kraken(data: dict) -> list[PriceTick | None]:
        """Parse a Kraken v2 trade message.

        Kraken sends batches of trades in a single message.
        The 'side' field directly indicates taker direction:
        - side='buy': taker is buying (bullish aggression)
        - side='sell': taker is selling (bearish aggression)
        """
        if data.get("channel") != "trade" or data.get("type") not in ("update", "snapshot"):
            return [None]

        trades = data.get("data", [])
        ticks = []
        for trade in trades:
            price = trade.get("price")
            qty = trade.get("qty")
            if price is None or qty is None:
                continue
            ts = datetime.now(timezone.utc)
            time_str = trade.get("timestamp")
            if time_str:
                try:
                    ts = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            side = trade.get("side")
            ticks.append(PriceTick(
                price=Decimal(str(price)),
                volume=Decimal(str(qty)),
                timestamp=ts,
                is_taker_buy=side == "buy" if side else None,
            ))
        return ticks if ticks else [None]

    def get_taker_volume_since(self, seconds_ago: float) -> tuple[float, float]:
        """Get taker buy and sell volumes (in base asset) over the last N seconds.

        Returns:
            (taker_buy_volume, taker_sell_volume) in BTC terms.
            Multiply by price for USD approximation.
        """
        if not self._price_buffer:
            return 0.0, 0.0
        cutoff = datetime.now(timezone.utc).timestamp() - seconds_ago
        buy_vol = 0.0
        sell_vol = 0.0
        for tick in reversed(self._price_buffer):
            if tick.timestamp.timestamp() < cutoff:
                break
            if tick.is_taker_buy is None:
                continue
            vol = float(tick.volume)
            if tick.is_taker_buy:
                buy_vol += vol
            else:
                sell_vol += vol
        return buy_vol, sell_vol

    @staticmethod
    def _parse_binance(data: dict) -> PriceTick | None:
        """Parse a Binance trade message.

        The 'm' field indicates if the buyer is the market maker:
        - m=true: buyer is maker -> trade is a taker SELL
        - m=false: seller is maker -> trade is a taker BUY
        """
        if "p" not in data:
            return None
        is_maker = data.get("m")
        return PriceTick(
            price=Decimal(data["p"]),
            volume=Decimal(data["q"]),
            timestamp=datetime.fromtimestamp(
                data["T"] / 1000, tz=timezone.utc
            ),
            is_taker_buy=not is_maker if is_maker is not None else None,
        )

    async def close(self) -> None:
        """Gracefully close the connection."""
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
        logger.info("btc_feed_closed", provider=self._provider)
