"""Position tracking, P&L computation, and exit signal generation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import structlog

from src.data.database import Database
from src.data.kalshi_client import KalshiRestClient
from src.data.models import CompletedTrade, MarketSnapshot, Position, TradeSignal
from src.execution.order_manager import OrderManager, OrderState

logger = structlog.get_logger()


class PositionState:
    """Internal position tracking for a single market."""

    def __init__(
        self,
        market_ticker: str,
        side: str,
        count: int,
        avg_entry_price: Decimal,
        entry_time: datetime,
    ):
        self.market_ticker = market_ticker
        self.side = side
        self.count = count
        self.avg_entry_price = avg_entry_price
        self.entry_time = entry_time
        self.fees_paid = Decimal("0")
        self.realized_pnl = Decimal("0")
        self.order_ids: list[str] = []

    @property
    def exposure_dollars(self) -> Decimal:
        """Dollar exposure of this position."""
        return self.avg_entry_price * self.count

    def __repr__(self) -> str:
        return (
            f"PositionState({self.market_ticker}, {self.side}, "
            f"count={self.count}, price={self.avg_entry_price})"
        )


class PositionTracker:
    """Tracks positions, computes P&L, and generates exit signals.

    Maintains local position state and periodically syncs with
    the exchange to ensure consistency.
    """

    def __init__(
        self,
        kalshi_client: KalshiRestClient,
        db: Database,
        paper_mode: bool = True,
    ):
        self._client = kalshi_client
        self._db = db
        self._paper_mode = paper_mode
        self._positions: dict[str, PositionState] = {}

    def update_on_fill(self, order_state: OrderState) -> None:
        """Update position state when an order fills."""
        signal = order_state.signal
        ticker = signal.market_ticker
        price = Decimal(signal.suggested_price_dollars)
        count = order_state.filled_count

        if count <= 0:
            return

        existing = self._positions.get(ticker)

        if existing is None:
            # New position
            self._positions[ticker] = PositionState(
                market_ticker=ticker,
                side=signal.side,
                count=count,
                avg_entry_price=price,
                entry_time=datetime.now(timezone.utc),
            )
            self._positions[ticker].order_ids.append(order_state.order_id)
        elif existing.side == signal.side:
            # Adding to existing position — compute weighted average price
            total_count = existing.count + count
            if total_count > 0:
                existing.avg_entry_price = (
                    existing.avg_entry_price * existing.count + price * count
                ) / total_count
            existing.count = total_count
            existing.order_ids.append(order_state.order_id)
        else:
            # Opposite side — reduces or closes position
            if count >= existing.count:
                # Position closed (or reversed)
                self._close_position(existing, price, count)
            else:
                existing.count -= count

        logger.info(
            "position_updated",
            ticker=ticker,
            side=signal.side,
            fill_count=count,
            total_count=self._positions.get(ticker, PositionState(ticker, "", 0, Decimal("0"), datetime.now(timezone.utc))).count,
        )

    def _close_position(
        self, position: PositionState, exit_price: Decimal, exit_count: int
    ) -> None:
        """Close out a position and compute P&L."""
        # P&L for binary: if we bought YES at X and it resolves YES, we get 1-X profit
        # For now, just track the position closure
        ticker = position.market_ticker
        if ticker in self._positions:
            del self._positions[ticker]

    async def sync_from_exchange(self) -> None:
        """Fetch positions from Kalshi and reconcile with local state."""
        if self._paper_mode:
            return

        try:
            exchange_positions = await self._client.get_positions()
            for p in exchange_positions:
                if abs(p.market_exposure) > 0:
                    if p.ticker not in self._positions:
                        # Position exists on exchange but not locally — adopt it
                        side = "yes" if p.market_exposure > 0 else "no"
                        count = abs(p.market_exposure)
                        self._positions[p.ticker] = PositionState(
                            market_ticker=p.ticker,
                            side=side,
                            count=count,
                            avg_entry_price=Decimal("0.50"),  # Unknown, estimate midpoint
                            entry_time=datetime.now(timezone.utc),
                        )
                        logger.info(
                            "position_synced_from_exchange",
                            ticker=p.ticker,
                            side=side,
                            count=count,
                            exchange_exposure=p.market_exposure,
                        )
                    else:
                        # Update count if exchange disagrees
                        local = self._positions[p.ticker]
                        exchange_count = abs(p.market_exposure)
                        if local.count != exchange_count:
                            local.count = exchange_count
                            logger.info(
                                "position_count_synced",
                                ticker=p.ticker,
                                local_count=local.count,
                                exchange_count=exchange_count,
                            )
                elif p.ticker in self._positions:
                    # Exchange says no position but we have one locally — remove
                    removed = self._positions.pop(p.ticker)
                    logger.info(
                        "position_removed_by_sync",
                        ticker=p.ticker,
                        was_count=removed.count,
                    )
        except Exception:
            logger.exception("position_sync_error")

    def get_position(self, market_ticker: str) -> PositionState | None:
        """Get current position for a market."""
        return self._positions.get(market_ticker)

    def get_all_positions(self) -> list[PositionState]:
        """Get all open positions."""
        return list(self._positions.values())

    @property
    def total_exposure_dollars(self) -> Decimal:
        """Sum of all position exposure in dollars."""
        return sum(
            (p.exposure_dollars for p in self._positions.values()),
            Decimal("0"),
        )

    @property
    def position_count(self) -> int:
        """Number of open positions."""
        return len(self._positions)

    def get_market_position_count(self, market_ticker: str) -> int:
        """Get contract count for a specific market (signed: positive=YES, negative=NO)."""
        pos = self._positions.get(market_ticker)
        if pos is None:
            return 0
        return pos.count if pos.side == "yes" else -pos.count

    def check_exits(
        self, snapshots: dict[str, MarketSnapshot]
    ) -> list[str]:
        """Check if any positions should be exited.

        Returns list of market tickers that should be exited.
        Reasons for exit:
        1. Time-based: < 30s to expiry, let it ride to settlement
        2. Position in expired market
        """
        exits = []
        now = datetime.now(timezone.utc)

        for ticker, position in list(self._positions.items()):
            snapshot = snapshots.get(ticker)
            if snapshot is None:
                continue

            # Near expiry — let positions ride to settlement
            # (binary contracts settle automatically)
            if snapshot.time_to_expiry_seconds <= 0:
                exits.append(ticker)

        return exits

    def remove_expired_positions(self, expired_tickers: list[str]) -> None:
        """Remove positions in expired markets (they settled)."""
        for ticker in expired_tickers:
            if ticker in self._positions:
                pos = self._positions.pop(ticker)
                logger.info(
                    "position_expired",
                    ticker=ticker,
                    side=pos.side,
                    count=pos.count,
                    avg_price=float(pos.avg_entry_price),
                )

    async def persist_trade(self, trade: CompletedTrade) -> None:
        """Save completed trade to database."""
        try:
            await self._db.insert_trade(trade)
        except Exception:
            logger.exception("trade_persist_error")
