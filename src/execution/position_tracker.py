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
        self.high_water_bid: Decimal | None = None  # Best bid seen since entry
        self.strategy_tag: str = ""  # "settlement_ride" = hold to settlement, skip all exits
        self.strike_price: Decimal | None = None  # Cached for paper settlement
        self.model_probability: float | None = None  # Cached for calibration tracking
        self.implied_probability: float | None = None  # Cached for calibration tracking

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
            new_pos = PositionState(
                market_ticker=ticker,
                side=signal.side,
                count=count,
                avg_entry_price=price,
                entry_time=now,
            )
            new_pos.strategy_tag = signal.signal_type
            new_pos.model_probability = signal.model_probability
            new_pos.implied_probability = signal.implied_probability
            new_pos.order_ids.append(order_state.order_id)
            self._positions[ticker] = new_pos
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
        1. Time-based: expired market
        """
        exits = []

        for ticker, position in list(self._positions.items()):
            snapshot = snapshots.get(ticker)
            if snapshot is None:
                continue

            if snapshot.time_to_expiry_seconds <= 0:
                exits.append(ticker)

        return exits

    def check_pre_expiry_exits(
        self,
        snapshots: dict[str, MarketSnapshot],
        pre_expiry_seconds: float = 90.0,
        min_pnl_per_contract: float = -0.03,
        hold_to_settle_seconds: float = 0.0,
        hold_to_settle_min_profit_cents: float = 0.15,
    ) -> list[tuple[str, str]]:
        """Check for positions that should be sold before settlement.

        Returns list of (ticker, sell_price) for positions within
        pre_expiry_seconds of expiry. Only exits if position is profitable
        or near-breakeven (PnL per contract >= min_pnl_per_contract).
        Losing positions ride to settlement instead of selling at rock bottom.
        """
        results: list[tuple[str, str]] = []

        for ticker, position in list(self._positions.items()):
            if position.strategy_tag in ("settlement_ride", "certainty_scalp"):
                continue
            snapshot = snapshots.get(ticker)
            if snapshot is None:
                continue

            tte = snapshot.time_to_expiry_seconds
            if tte <= 0 or tte > pre_expiry_seconds:
                continue

            ob = snapshot.orderbook
            if position.side == "yes":
                current_bid = ob.best_yes_bid
            else:
                current_bid = ob.best_no_bid

            if current_bid is None or current_bid <= 0:
                continue

            # Only pre-expiry exit if PnL per contract is acceptable
            # Losers ride to settlement — selling at 0.02-0.10 is worse than the binary gamble
            pnl_per_contract = float(current_bid) - float(position.avg_entry_price)

            # Hold-to-settle: profitable positions near expiry settle fee-free
            if (
                hold_to_settle_seconds > 0
                and tte <= hold_to_settle_seconds
                and pnl_per_contract >= hold_to_settle_min_profit_cents
            ):
                logger.info(
                    "hold_to_settle_skip_pre_expiry",
                    ticker=ticker,
                    side=position.side,
                    profit_per_contract=round(pnl_per_contract, 4),
                    time_to_expiry=round(tte, 1),
                )
                continue

            if pnl_per_contract < min_pnl_per_contract:
                logger.info(
                    "pre_expiry_exit_skipped_losing",
                    ticker=ticker,
                    side=position.side,
                    entry_price=float(position.avg_entry_price),
                    exit_bid=float(current_bid),
                    pnl_per_contract=round(pnl_per_contract, 4),
                    min_pnl=min_pnl_per_contract,
                    time_to_expiry=round(tte, 1),
                )
                continue

            logger.info(
                "pre_expiry_exit_signal",
                ticker=ticker,
                side=position.side,
                count=position.count,
                entry_price=float(position.avg_entry_price),
                exit_bid=float(current_bid),
                pnl_per_contract=round(pnl_per_contract, 4),
                time_to_expiry=round(tte, 1),
            )
            results.append((ticker, str(current_bid)))

        return results

    def check_thesis_breaks(
        self,
        predictions: dict[str, PredictionResult],
        threshold: float = 0.05,
        min_hold_seconds: float = 60.0,
    ) -> list[str]:
        """Return tickers where model has flipped against our position.

        For YES positions, thesis breaks when probability_yes < 0.50 - threshold.
        For NO positions, thesis breaks when probability_yes > 0.50 + threshold.
        Requires minimum hold time before thesis break can fire.
        """
        breaks = []
        now = datetime.now(timezone.utc)
        for ticker, position in list(self._positions.items()):
            if position.strategy_tag in ("settlement_ride", "certainty_scalp"):
                continue
            prediction = predictions.get(ticker)
            if prediction is None:
                continue

            # Minimum hold time — don't exit on model noise
            hold_seconds = (now - position.last_fill_time).total_seconds()
            if hold_seconds < min_hold_seconds:
                continue

            if position.side == "yes":
                if prediction.probability_yes < 0.50 - threshold:
                    logger.info(
                        "thesis_break_detected",
                        ticker=ticker,
                        side="yes",
                        model_prob=round(prediction.probability_yes, 4),
                        threshold=threshold,
                        hold_seconds=round(hold_seconds, 1),
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
                        hold_seconds=round(hold_seconds, 1),
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

        Includes both fixed threshold take-profit and trailing take-profit.
        Returns list of (ticker, sell_price) for positions meeting take-profit conditions.
        """
        results: list[tuple[str, str]] = []
        now = datetime.now(timezone.utc)

        for ticker, position in list(self._positions.items()):
            if position.strategy_tag in ("settlement_ride", "certainty_scalp"):
                continue
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

            # Update high-water mark for trailing take-profit
            if position.high_water_bid is None or current_bid > position.high_water_bid:
                position.high_water_bid = current_bid

            # Compute unrealized profit per contract
            profit_per_contract = current_bid - position.avg_entry_price

            # Check minimum hold time
            hold_seconds = (now - position.entry_time).total_seconds()
            if hold_seconds < strategy_config.take_profit_min_hold_seconds:
                continue

            # Trailing take-profit: if we've gained enough, exit when price drops from peak
            if (
                strategy_config.trailing_take_profit_enabled
                and position.high_water_bid is not None
                and profit_per_contract > 0
            ):
                peak_profit = float(position.high_water_bid - position.avg_entry_price)
                activation = strategy_config.trailing_take_profit_activation_cents
                drop_threshold = strategy_config.trailing_take_profit_drop_cents

                if peak_profit >= activation:
                    drop_from_peak = float(position.high_water_bid - current_bid)
                    if drop_from_peak >= drop_threshold:
                        results.append((ticker, str(current_bid)))
                        logger.info(
                            "trailing_take_profit_signal",
                            ticker=ticker,
                            side=position.side,
                            entry_price=float(position.avg_entry_price),
                            current_bid=float(current_bid),
                            high_water=float(position.high_water_bid),
                            peak_profit=round(peak_profit, 4),
                            drop_from_peak=round(drop_from_peak, 4),
                        )
                        continue  # Skip fixed TP check — trailing already triggered

            if profit_per_contract <= 0:
                continue

            # Compute dynamic profit threshold with time decay
            time_to_expiry = snapshot.time_to_expiry_seconds

            # Hold-to-settle: profitable positions near expiry settle fee-free
            if (
                strategy_config.hold_to_settle_seconds > 0
                and time_to_expiry < strategy_config.hold_to_settle_seconds
                and float(profit_per_contract) >= strategy_config.hold_to_settle_min_profit_cents
            ):
                logger.info(
                    "hold_to_settle_skip_tp",
                    ticker=ticker,
                    side=position.side,
                    profit_per_contract=round(float(profit_per_contract), 4),
                    time_to_expiry=round(time_to_expiry, 1),
                )
                continue

            decay_start = strategy_config.take_profit_time_decay_start_seconds
            min_profit = strategy_config.take_profit_min_profit_cents
            floor_cents = strategy_config.take_profit_time_decay_floor_cents

            if time_to_expiry < 30:
                # Too close to expiry — pre-expiry exit handles this
                continue
            elif time_to_expiry >= decay_start:
                threshold = min_profit
            else:
                # Linear interpolation from min_profit down to floor_cents
                # as time_to_expiry goes from decay_start down to 30s
                t = (time_to_expiry - 30) / (decay_start - 30)
                threshold = floor_cents + t * (min_profit - floor_cents)

            # Compute sell fee per contract (maker — TP exits use post_only orders)
            fee_per_contract = EdgeDetector.compute_fee_dollars(
                1, float(current_bid), is_maker=True
            )

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

    def check_stop_loss(
        self,
        snapshots: dict[str, MarketSnapshot],
        stop_loss_pct: float = 0.35,
        min_bid: float = 0.05,
        min_hold_seconds: float = 30.0,
        asset_stop_loss_pct: dict[str, float] | None = None,
        max_dollar_loss: float = 0.0,
    ) -> list[tuple[str, str]]:
        """Check positions for stop-loss exits.

        Returns list of (ticker, sell_price) for positions where:
        - Loss exceeds stop_loss_pct of entry price, OR
        - Unrealized dollar loss exceeds max_dollar_loss (if > 0)
        - Current bid >= min_bid (worth selling vs. fee)
        - Position held for at least min_hold_seconds (avoid momentary dips)
        """
        results: list[tuple[str, str]] = []
        now = datetime.now(timezone.utc)

        for ticker, position in list(self._positions.items()):
            if position.strategy_tag in ("settlement_ride", "certainty_scalp"):
                continue
            snapshot = snapshots.get(ticker)
            if snapshot is None:
                continue

            # Check minimum hold time (but allow emergency exit for catastrophic losses)
            hold_seconds = (now - position.entry_time).total_seconds()
            within_hold_period = hold_seconds < min_hold_seconds

            ob = snapshot.orderbook
            if position.side == "yes":
                current_bid = ob.best_yes_bid
            else:
                current_bid = ob.best_no_bid

            if current_bid is None:
                continue

            bid_float = float(current_bid)
            entry_float = float(position.avg_entry_price)

            # Skip if bid is too low to be worth selling
            if bid_float < min_bid:
                continue

            # Compute loss percentage relative to entry
            if entry_float <= 0:
                continue
            loss_pct = (entry_float - bid_float) / entry_float

            # Per-asset stop-loss override
            effective_sl = stop_loss_pct
            if asset_stop_loss_pct:
                ticker_upper = ticker.upper()
                for asset, sl in asset_stop_loss_pct.items():
                    if asset.upper() in ticker_upper:
                        effective_sl = sl
                        break

            # Compute unrealized dollar loss (entry cost - current value - fees)
            dollar_loss = (entry_float - bid_float) * position.count + float(position.fees_paid)

            # Emergency exit: bypass hold period if loss exceeds dollar cap
            # This prevents catastrophic losses during the min hold window
            if within_hold_period:
                if max_dollar_loss > 0 and dollar_loss >= max_dollar_loss:
                    logger.warning(
                        "emergency_stop_loss",
                        ticker=ticker,
                        side=position.side,
                        count=position.count,
                        entry_price=entry_float,
                        current_bid=bid_float,
                        dollar_loss=round(dollar_loss, 2),
                        max_dollar_loss=max_dollar_loss,
                        hold_seconds=round(hold_seconds, 1),
                    )
                else:
                    continue

            triggered_by = None
            if loss_pct >= effective_sl:
                triggered_by = "pct"
            elif max_dollar_loss > 0 and dollar_loss >= max_dollar_loss:
                triggered_by = "dollar_cap"

            if triggered_by:
                logger.info(
                    "stop_loss_signal",
                    ticker=ticker,
                    side=position.side,
                    count=position.count,
                    entry_price=entry_float,
                    current_bid=bid_float,
                    loss_pct=round(loss_pct, 4),
                    dollar_loss=round(dollar_loss, 2),
                    threshold=effective_sl,
                    max_dollar_loss=max_dollar_loss,
                    triggered_by=triggered_by,
                    hold_seconds=round(hold_seconds, 1),
                )
                results.append((ticker, str(current_bid)))

        return results

    def compute_unrealized_pnl(
        self, snapshots: dict[str, MarketSnapshot]
    ) -> Decimal:
        """Compute total unrealized PNL across all positions using current bids.

        For each position, unrealized PNL accounts for:
        - Gross P&L: (current_bid - avg_entry_price) * count
        - Buy fees already paid (pos.fees_paid)
        - Estimated sell fees to exit (taker rate)
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

            gross = (current_bid - position.avg_entry_price) * position.count
            sell_fee = EdgeDetector.compute_fee_dollars(
                position.count, float(current_bid), is_maker=False
            )
            unrealized = gross - sell_fee - position.fees_paid
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
