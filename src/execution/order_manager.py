"""Order lifecycle management: create, monitor, cancel, retry."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import structlog

from src.config import BotSettings
from src.data.kalshi_client import KalshiAPIError, KalshiRestClient
from src.data.models import OrderRequest, OrderResponse, TradeSignal

logger = structlog.get_logger()


class OrderState:
    """Internal order tracking state."""

    def __init__(
        self,
        order_id: str,
        client_order_id: str,
        signal: TradeSignal,
        requested_count: int,
    ):
        self.order_id = order_id
        self.client_order_id = client_order_id
        self.signal = signal
        self.requested_count = requested_count
        self.filled_count = 0
        self.status: Literal[
            "pending", "active", "partially_filled", "filled", "canceled", "error"
        ] = "pending"
        self.created_at = datetime.now(timezone.utc)
        self.last_updated = self.created_at
        self.response: OrderResponse | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in ("filled", "canceled", "error")


class OrderManager:
    """Manages the full lifecycle of orders on Kalshi.

    Handles:
    - Order submission with idempotent client_order_id
    - Paper mode simulation
    - Order cancellation
    - State tracking
    """

    def __init__(
        self,
        kalshi_client: KalshiRestClient,
        settings: BotSettings,
    ):
        self._client = kalshi_client
        self._settings = settings
        self._paper_mode = settings.mode == "paper"
        self._pending_orders: dict[str, OrderState] = {}
        self._paper_order_counter = 0

    async def submit(self, signal: TradeSignal, count: int) -> str | None:
        """Submit a trade signal as an order.

        Returns order_id on success, None on failure.
        """
        if count <= 0:
            return None

        client_order_id = str(uuid.uuid4())

        order_req = OrderRequest(
            ticker=signal.market_ticker,
            side=signal.side,
            action=signal.action,
            count=count,
            client_order_id=client_order_id,
            post_only=(
                signal.post_only
                if signal.post_only is not None
                else (signal.action != "sell")
            ),  # Respect signal override, else taker for exits, maker for entries
        )

        # Set price based on side
        if signal.side == "yes":
            order_req.yes_price_dollars = signal.suggested_price_dollars
        else:
            order_req.no_price_dollars = signal.suggested_price_dollars

        if self._paper_mode:
            return await self._submit_paper(signal, order_req, count)

        return await self._submit_live(signal, order_req, count)

    async def _submit_paper(
        self,
        signal: TradeSignal,
        order_req: OrderRequest,
        count: int,
    ) -> str:
        """Simulate order fill in paper mode."""
        self._paper_order_counter += 1
        order_id = f"paper-{self._paper_order_counter}"

        state = OrderState(
            order_id=order_id,
            client_order_id=order_req.client_order_id,
            signal=signal,
            requested_count=count,
        )
        state.filled_count = count
        state.status = "filled"
        state.response = OrderResponse(
            order_id=order_id,
            client_order_id=order_req.client_order_id,
            ticker=signal.market_ticker,
            status="filled",
            side=signal.side,
            action=signal.action,
            yes_price_dollars=(
                Decimal(signal.suggested_price_dollars) if signal.side == "yes" else None
            ),
            no_price_dollars=(
                Decimal(signal.suggested_price_dollars) if signal.side == "no" else None
            ),
            count=count,
            fill_count=count,
            remaining_count=0,
            created_time=datetime.now(timezone.utc),
        )
        self._pending_orders[order_id] = state

        logger.info(
            "paper_order_filled",
            order_id=order_id,
            ticker=signal.market_ticker,
            side=signal.side,
            price=signal.suggested_price_dollars,
            count=count,
            signal_type=signal.signal_type,
            edge=signal.net_edge,
        )

        return order_id

    async def _submit_live(
        self,
        signal: TradeSignal,
        order_req: OrderRequest,
        count: int,
    ) -> str | None:
        """Submit a real order to Kalshi."""
        state = OrderState(
            order_id="",
            client_order_id=order_req.client_order_id,
            signal=signal,
            requested_count=count,
        )

        try:
            response = await self._client.create_order(order_req)
            state.order_id = response.order_id
            state.response = response
            state.status = (
                "filled"
                if response.remaining_count == 0
                else "partially_filled"
                if response.fill_count > 0
                else "active"
            )
            state.filled_count = response.fill_count
            self._pending_orders[response.order_id] = state

            logger.info(
                "live_order_submitted",
                order_id=response.order_id,
                ticker=signal.market_ticker,
                side=signal.side,
                price=signal.suggested_price_dollars,
                count=count,
                filled=response.fill_count,
                status=response.status,
            )

            return response.order_id

        except KalshiAPIError as e:
            if e.status == 409:
                # Duplicate client_order_id — look up the existing order
                logger.warning(
                    "order_duplicate_looking_up",
                    client_order_id=order_req.client_order_id,
                    ticker=signal.market_ticker,
                )
                try:
                    existing_orders = await self._client.get_orders(
                        ticker=signal.market_ticker
                    )
                    for od in existing_orders:
                        if od.get("client_order_id") == order_req.client_order_id:
                            oid = od["order_id"]
                            state.order_id = oid
                            state.filled_count = od.get("fill_count", 0)
                            state.status = (
                                "filled"
                                if od.get("remaining_count", 0) == 0
                                else "active"
                            )
                            self._pending_orders[oid] = state
                            logger.info(
                                "order_duplicate_recovered",
                                order_id=oid,
                                filled=state.filled_count,
                            )
                            return oid
                except Exception:
                    logger.warning("order_duplicate_lookup_failed")
                return None
            logger.error(
                "order_submit_error",
                error=str(e),
                status=e.status,
                ticker=signal.market_ticker,
            )
            return None
        except Exception:
            logger.exception("order_submit_unexpected_error")
            return None

    async def hydrate_from_exchange(self) -> int:
        """Fetch active orders from Kalshi and populate internal tracking.

        Call this on startup to recover state from previous sessions.
        Returns the number of orders hydrated.
        """
        if self._paper_mode:
            return 0

        try:
            active_orders = await self._client.get_orders(status="resting")
        except Exception:
            logger.warning("hydrate_orders_failed")
            return 0

        count = 0
        for od in active_orders:
            oid = od.get("order_id", "")
            if not oid or oid in self._pending_orders:
                continue

            # Build a minimal TradeSignal to satisfy OrderState
            ticker = od.get("ticker", "")
            side = od.get("side", "yes")
            action = od.get("action", "buy")
            yes_price = od.get("yes_price", 0)
            no_price = od.get("no_price", 0)
            price = yes_price if side == "yes" else no_price
            # Kalshi returns prices in cents; convert to dollars
            price_dollars = str(price / 100) if isinstance(price, (int, float)) and price > 1 else str(price)

            signal = TradeSignal(
                market_ticker=ticker,
                side=side,
                action=action,
                raw_edge=0.0,
                net_edge=0.0,
                model_probability=0.0,
                implied_probability=0.0,
                confidence=0.0,
                suggested_price_dollars=price_dollars,
                suggested_count=od.get("count", 0),
                timestamp=datetime.now(timezone.utc),
                signal_type="directional",
            )

            req_count = od.get("count", 0)
            fill_count = od.get("fill_count", 0)

            state = OrderState(
                order_id=oid,
                client_order_id=od.get("client_order_id", ""),
                signal=signal,
                requested_count=req_count,
            )
            state.filled_count = fill_count
            status_str = od.get("status", "active")
            if status_str in ("filled", "canceled"):
                state.status = "filled" if fill_count > 0 else "canceled"
            elif fill_count > 0:
                state.status = "partially_filled"
            else:
                state.status = "active"

            self._pending_orders[oid] = state
            count += 1

        if count:
            logger.info("orders_hydrated", count=count)
        return count

    async def cancel(self, order_id: str) -> bool:
        """Cancel an active order. Returns True if successful."""
        if self._paper_mode:
            state = self._pending_orders.get(order_id)
            if state and not state.is_terminal:
                state.status = "canceled"
                logger.info("paper_order_canceled", order_id=order_id)
                return True
            return False

        try:
            await self._client.cancel_order(order_id)
            state = self._pending_orders.get(order_id)
            if state:
                state.status = "canceled"
            logger.info("order_canceled", order_id=order_id)
            return True
        except KalshiAPIError as e:
            logger.warning(
                "order_cancel_error",
                order_id=order_id,
                error=str(e),
            )
            return False

    async def cancel_all(self, market_ticker: str | None = None) -> int:
        """Cancel all active orders, optionally filtered by market."""
        canceled = 0
        for order_id, state in list(self._pending_orders.items()):
            if state.is_terminal:
                continue
            if market_ticker and state.signal.market_ticker != market_ticker:
                continue
            if await self.cancel(order_id):
                canceled += 1
        return canceled

    def get_active_orders(
        self, market_ticker: str | None = None
    ) -> list[OrderState]:
        """Get all active (non-terminal) orders."""
        result = []
        for state in self._pending_orders.values():
            if state.is_terminal:
                continue
            if market_ticker and state.signal.market_ticker != market_ticker:
                continue
            result.append(state)
        return result

    def get_resting_order_count(self, ticker: str, side: str | None = None) -> int:
        """Count unfilled contracts in resting orders for a ticker.

        Args:
            ticker: Market ticker to filter by
            side: Optional side filter ("yes" or "no")

        Returns:
            Total unfilled contract count across resting orders.
        """
        total = 0
        for state in self._pending_orders.values():
            if state.is_terminal:
                continue
            if state.signal.market_ticker != ticker:
                continue
            if side and state.signal.side != side:
                continue
            remaining = state.requested_count - state.filled_count
            total += max(0, remaining)
        return total

    async def cancel_market_orders(
        self, ticker: str, side: str | None = None
    ) -> int:
        """Cancel all resting orders for a ticker, optionally filtered by side.

        Returns number of orders canceled.
        """
        canceled = 0
        for order_id, state in list(self._pending_orders.items()):
            if state.is_terminal:
                continue
            if state.signal.market_ticker != ticker:
                continue
            if side and state.signal.side != side:
                continue
            if await self.cancel(order_id):
                canceled += 1
        if canceled:
            logger.info(
                "market_orders_canceled",
                ticker=ticker,
                side=side,
                count=canceled,
            )
        return canceled

    async def cancel_stale_orders(self, max_age_seconds: float = 90) -> int:
        """Cancel orders older than max_age_seconds that haven't fully filled."""
        now = datetime.now(timezone.utc)
        canceled = 0
        for order_id, state in list(self._pending_orders.items()):
            if state.is_terminal:
                continue
            age = (now - state.created_at).total_seconds()
            if age > max_age_seconds:
                if await self.cancel(order_id):
                    canceled += 1
        if canceled:
            logger.info("stale_orders_canceled", count=canceled)
        return canceled

    def get_order(self, order_id: str) -> OrderState | None:
        """Get order state by ID."""
        return self._pending_orders.get(order_id)

    async def check_resting_fills(self) -> list[OrderState]:
        """Poll Kalshi for fill updates on resting orders.

        Returns list of OrderStates that received new fills.
        """
        if self._paper_mode:
            return []

        newly_filled: list[OrderState] = []
        for order_id, state in list(self._pending_orders.items()):
            if state.is_terminal:
                continue
            try:
                order_data = await self._client.get_order(order_id)
                new_fill_count = order_data.get("fill_count", 0)
                remaining = order_data.get("remaining_count", 0)
                api_status = order_data.get("status", "")

                if new_fill_count > state.filled_count:
                    state.filled_count = new_fill_count
                    state.last_updated = datetime.now(timezone.utc)
                    newly_filled.append(state)
                    logger.info(
                        "resting_order_filled",
                        order_id=order_id,
                        ticker=state.signal.market_ticker,
                        filled=new_fill_count,
                        remaining=remaining,
                    )

                if remaining == 0 or api_status in ("filled", "canceled"):
                    state.status = "filled" if new_fill_count > 0 else "canceled"
                elif new_fill_count > 0:
                    state.status = "partially_filled"

            except Exception:
                logger.warning("resting_order_check_error", order_id=order_id, exc_info=True)

        return newly_filled

    def cleanup_terminal_orders(self, max_age_seconds: float = 3600) -> int:
        """Remove old terminal orders from memory."""
        now = datetime.now(timezone.utc)
        to_remove = []
        for order_id, state in self._pending_orders.items():
            if state.is_terminal:
                age = (now - state.created_at).total_seconds()
                if age > max_age_seconds:
                    to_remove.append(order_id)

        for order_id in to_remove:
            del self._pending_orders[order_id]

        return len(to_remove)
