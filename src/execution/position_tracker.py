"""Position tracking, P&L computation, and exit signal generation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import structlog

from src.config import StrategyConfig
from src.data.database import Database
from src.data.kalshi_client import KalshiRestClient
from src.data.models import CompletedTrade, MarketSnapshot, Position, PredictionResult, TradeSignal
from src.execution.order_manager import OrderManager, OrderState
from src.strategy.edge_detector import EdgeDetector

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
        self.add_count: int = 0
        self.last_fill_time: datetime = entry_time
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
        self._expired_tickers: set[str] = set()  # Tickers already settled/expired

    def update_on_fill(self, order_state: OrderState) -> None:
        """Update position state when an order fills."""
        signal = order_state.signal
        ticker = signal.market_ticker
        price = Decimal(signal.suggested_price_dollars)
        count = order_state.filled_count

        if count <= 0:
            return

        existing = self._positions.get(ticker)

        if signal.action == "sell":
            # Sell order — always reduces or closes position
            if existing is not None:
                if count >= existing.count:
                    self._close_position(existing, price, count)
                else:
                    existing.count -= count
            logger.info(
                "position_updated",
                ticker=ticker,
                side=signal.side,
                action="sell",
                fill_count=count,
                total_count=self._positions.get(ticker, PositionState(ticker, "", 0, Decimal("0"), datetime.now(timezone.utc))).count,
            )
            return

        now = datetime.now(timezone.utc)
        if existing is None:
            # New position
            self._positions[ticker] = PositionState(
                market_ticker=ticker,
                side=signal.side,
                count=count,
                avg_entry_price=price,
                entry_time=now,
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
            existing.add_count += 1
            existing.last_fill_time = now
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
            # Track which tickers the exchange reports with active exposure
            exchange_active_tickers: set[str] = set()
            for p in exchange_positions:
                # Use `position` field for contract count (signed: +YES, -NO)
                # `market_exposure` is cost in cents, NOT contract count
                contract_count = abs(p.position) if p.position != 0 else 0
                if contract_count > 0:
                    exchange_active_tickers.add(p.ticker)
                    if p.ticker in self._expired_tickers:
                        # Already settled — Kalshi may still report it briefly
                        logger.debug(
                            "position_sync_skipped_expired",
                            ticker=p.ticker,
                        )
                        continue
                    if p.ticker not in self._positions:
                        # Position exists on exchange but not locally — adopt it
                        side = "yes" if p.position > 0 else "no"
                        # Compute avg entry price from market_exposure (cents) / count
                        if p.market_exposure != 0 and contract_count > 0:
                            avg_price = Decimal(str(abs(p.market_exposure))) / 100 / contract_count
                        else:
                            avg_price = Decimal("0.50")
                        self._positions[p.ticker] = PositionState(
                            market_ticker=p.ticker,
                            side=side,
                            count=contract_count,
                            avg_entry_price=avg_price,
                            entry_time=datetime.now(timezone.utc),
                        )
                        logger.info(
                            "position_synced_from_exchange",
                            ticker=p.ticker,
                            side=side,
                            count=contract_count,
                            avg_entry_price=float(avg_price),
                            market_exposure_cents=p.market_exposure,
                        )
                    else:
                        # Update count if exchange disagrees
                        local = self._positions[p.ticker]
                        if local.count != contract_count:
                            local.count = contract_count
                            logger.info(
                                "position_count_synced",
                                ticker=p.ticker,
                                local_count=local.count,
                                exchange_count=contract_count,
                            )
                elif p.ticker in self._positions:
                    # Exchange says no position but we have one locally — remove
                    removed = self._positions.pop(p.ticker)
                    logger.info(
                        "position_removed_by_sync",
                        ticker=p.ticker,
                        was_count=removed.count,
                    )

            # Detect local positions not reported by exchange at all
            # (settled markets are omitted from API response, not reported with 0)
            for ticker in list(self._positions.keys()):
                if ticker not in exchange_active_tickers and ticker not in self._expired_tickers:
                    pos = self._positions[ticker]
                    age = (datetime.now(timezone.utc) - pos.entry_time).total_seconds()
                    if age > 120:  # Only flag after 2 min to avoid race on fresh positions
                        logger.info(
                            "position_missing_from_exchange",
                            ticker=ticker,
                            side=pos.side,
                            count=pos.count,
                            age_seconds=round(age),
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

    def check_thesis_breaks(
        self,
        predictions: dict[str, PredictionResult],
        threshold: float = 0.02,
    ) -> list[str]:
        """Return tickers where model has flipped against our position.

        For YES positions, thesis breaks when probability_yes < 0.50 - threshold.
        For NO positions, thesis breaks when probability_yes > 0.50 + threshold.
        """
        breaks = []
        for ticker, position in list(self._positions.items()):
            prediction = predictions.get(ticker)
            if prediction is None:
                continue

            if position.side == "yes":
                if prediction.probability_yes < 0.50 - threshold:
                    logger.info(
                        "thesis_break_detected",
                        ticker=ticker,
                        side="yes",
                        model_prob=round(prediction.probability_yes, 4),
                        threshold=threshold,
                        count=position.count,
                    )
                    breaks.append(ticker)
            else:
                if prediction.probability_yes > 0.50 + threshold:
                    logger.info(
                        "thesis_break_detected",
                        ticker=ticker,
                        side="no",
                        model_prob=round(prediction.probability_yes, 4),
                        threshold=threshold,
                        count=position.count,
                    )
                    breaks.append(ticker)

        return breaks

    def check_take_profit(
        self,
        snapshots: dict[str, MarketSnapshot],
        strategy_config: StrategyConfig,
    ) -> list[tuple[str, str]]:
        """Check positions for take-profit opportunities.

        Returns list of (ticker, sell_price) for positions meeting take-profit conditions.
        """
        results: list[tuple[str, str]] = []
        now = datetime.now(timezone.utc)

        for ticker, position in list(self._positions.items()):
            snapshot = snapshots.get(ticker)
            if snapshot is None:
                continue

            ob = snapshot.orderbook

            # Get the current bid for our side
            if position.side == "yes":
                current_bid = ob.best_yes_bid
            else:
                current_bid = ob.best_no_bid

            if current_bid is None:
                continue

            # Compute unrealized profit per contract
            profit_per_contract = current_bid - position.avg_entry_price
            if profit_per_contract <= 0:
                continue

            # Check minimum hold time
            hold_seconds = (now - position.entry_time).total_seconds()
            if hold_seconds < strategy_config.take_profit_min_hold_seconds:
                continue

            # Compute dynamic profit threshold with time decay
            time_to_expiry = snapshot.time_to_expiry_seconds
            decay_start = strategy_config.take_profit_time_decay_start_seconds
            min_profit = strategy_config.take_profit_min_profit_cents
            floor_cents = strategy_config.take_profit_time_decay_floor_cents

            if time_to_expiry < 30:
                # Too close to expiry — let it ride to settlement
                continue
            elif time_to_expiry >= decay_start:
                threshold = min_profit
            else:
                # Linear interpolation from min_profit down to floor_cents
                # as time_to_expiry goes from decay_start down to 30s
                t = (time_to_expiry - 30) / (decay_start - 30)
                threshold = floor_cents + t * (min_profit - floor_cents)

            # Compute sell fee (taker for guaranteed execution)
            sell_fee = EdgeDetector.compute_fee_dollars(
                position.count, float(current_bid), is_maker=False
            )
            fee_per_contract = sell_fee / position.count if position.count > 0 else sell_fee

            # Check: profit - fee >= threshold
            if float(profit_per_contract) - float(fee_per_contract) >= threshold:
                results.append((ticker, str(current_bid)))
                logger.info(
                    "take_profit_signal",
                    ticker=ticker,
                    side=position.side,
                    entry_price=float(position.avg_entry_price),
                    current_bid=float(current_bid),
                    profit=float(profit_per_contract),
                    fee_per_contract=float(fee_per_contract),
                    threshold=round(threshold, 4),
                    time_to_expiry=round(time_to_expiry, 1),
                )

        return results

    def compute_unrealized_pnl(
        self, snapshots: dict[str, MarketSnapshot]
    ) -> Decimal:
        """Compute total unrealized PNL across all positions using current bids.

        For each position, unrealized PNL = (current_bid - avg_entry_price) * count.
        If no bid is available, assumes mark-to-market at entry price (PNL = 0).
        """
        total = Decimal("0")
        for ticker, position in self._positions.items():
            snapshot = snapshots.get(ticker)
            if snapshot is None:
                continue

            ob = snapshot.orderbook
            if position.side == "yes":
                current_bid = ob.best_yes_bid
            else:
                current_bid = ob.best_no_bid

            if current_bid is None:
                continue

            unrealized = (current_bid - position.avg_entry_price) * position.count
            total += unrealized

        return total

    def remove_expired_positions(self, expired_tickers: list[str]) -> None:
        """Remove positions in expired markets (they settled)."""
        for ticker in expired_tickers:
            if ticker in self._positions:
                pos = self._positions.pop(ticker)
                # Remember this ticker so sync_from_exchange won't re-adopt it
                self._expired_tickers.add(ticker)
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
