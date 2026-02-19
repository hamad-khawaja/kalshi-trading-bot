"""Risk management: enforces all safety limits before trade execution."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import structlog

from src.config import RiskConfig
from src.data.models import Position, TradeSignal

logger = structlog.get_logger()


class RiskDecision:
    """Result of a risk check."""

    def __init__(self, approved: bool, reason: str, adjusted_count: int | None = None):
        self.approved = approved
        self.reason = reason
        self.adjusted_count = adjusted_count

    def __repr__(self) -> str:
        return f"RiskDecision(approved={self.approved}, reason={self.reason!r})"


class RiskManager:
    """Enforces all risk limits before trade execution.

    Checks performed (in order):
    1. Balance minimum
    2. Daily loss limit
    3. Position count per market
    4. Total exposure cap
    5. Concurrent positions limit
    6. Consecutive loss streak
    7. Trades per day limit
    8. Time to expiry minimum
    """

    def __init__(self, config: RiskConfig):
        self._config = config
        self._daily_pnl = Decimal("0")
        self._daily_pnl_date: date | None = None
        self._daily_pnl_peak = Decimal("0")  # High-water mark for drawdown tracking
        self._trades_today = 0
        self._trades_today_date: date | None = None
        self._consecutive_losses = 0
        self._consecutive_wins = 0
        self._total_wins = 0
        self._total_settled = 0
        self._last_pnl: Decimal | None = None
        self._cooldown_until: datetime | None = None

    def check(
        self,
        signal: TradeSignal,
        count: int,
        balance: Decimal,
        positions: list[Position],
        time_to_expiry_seconds: float,
        current_exposure_dollars: Decimal | None = None,
    ) -> RiskDecision:
        """Run all risk checks sequentially. Returns first failure or approval."""
        self._reset_daily_if_needed()

        # 1. Balance minimum
        if balance < Decimal(str(self._config.min_balance_dollars)):
            return RiskDecision(
                False,
                f"Balance ${balance} below minimum ${self._config.min_balance_dollars}",
            )

        # 2. Daily loss limit
        if self._daily_pnl <= -Decimal(str(self._config.max_daily_loss_dollars)):
            return RiskDecision(
                False,
                f"Daily loss limit hit: ${self._daily_pnl}",
            )

        # 2b. Drawdown circuit breaker: stop if daily P&L drops X from peak
        if self._config.drawdown_limit_enabled:
            drawdown = self._daily_pnl_peak - self._daily_pnl
            limit = Decimal(str(self._config.drawdown_limit_dollars))
            if drawdown >= limit:
                return RiskDecision(
                    False,
                    f"Drawdown limit: PnL dropped ${float(drawdown):.2f} from peak ${float(self._daily_pnl_peak):.2f}",
                )

        # 3. Position count per market (with per-asset override)
        max_position = self._config.max_position_per_market
        if self._config.asset_max_position:
            ticker_upper = signal.market_ticker.upper()
            for asset, limit in self._config.asset_max_position.items():
                if asset.upper() in ticker_upper:
                    max_position = limit
                    break
        market_position = 0
        for p in positions:
            if p.ticker == signal.market_ticker:
                market_position = abs(p.market_exposure)
                break
        if market_position + count > max_position:
            return RiskDecision(
                False,
                f"Market position limit: {market_position} + {count} > {max_position}",
            )

        # 4. Total exposure cap (use actual exposure from position tracker when available)
        if current_exposure_dollars is not None:
            total_exposure = current_exposure_dollars
        else:
            total_exposure = sum(
                abs(p.market_exposure) * Decimal("0.50")
                for p in positions
            )
        new_exposure = Decimal(str(count)) * Decimal(signal.suggested_price_dollars)
        if total_exposure + new_exposure > Decimal(
            str(self._config.max_total_exposure_dollars)
        ):
            return RiskDecision(
                False,
                f"Total exposure limit: ${total_exposure + new_exposure} > ${self._config.max_total_exposure_dollars}",
            )

        # 5. Concurrent positions limit
        active_tickers = {p.ticker for p in positions if abs(p.market_exposure) > 0}
        if (
            signal.market_ticker not in active_tickers
            and len(active_tickers) >= self._config.max_concurrent_positions
        ):
            return RiskDecision(
                False,
                f"Max concurrent positions: {len(active_tickers)} >= {self._config.max_concurrent_positions}",
            )

        # 6. Consecutive loss streak
        if self._consecutive_losses >= self._config.max_consecutive_losses:
            if self._cooldown_until and datetime.now(timezone.utc) < self._cooldown_until:
                return RiskDecision(
                    False,
                    f"Consecutive loss cooldown until {self._cooldown_until}",
                )
            # Cooldown expired, reset
            self._consecutive_losses = 0
            self._cooldown_until = None

        # 7. Trades per day limit
        if self._trades_today >= self._config.max_trades_per_day:
            return RiskDecision(
                False,
                f"Max trades per day: {self._trades_today} >= {self._config.max_trades_per_day}",
            )

        # 8. Time to expiry minimum
        if time_to_expiry_seconds < 60:
            return RiskDecision(
                False,
                f"Too close to expiry: {time_to_expiry_seconds:.0f}s remaining",
            )

        return RiskDecision(True, "OK", adjusted_count=count)

    def record_trade(self, pnl: Decimal) -> None:
        """Record a settled position for risk tracking.

        Note: _trades_today is incremented at order fill time in bot.py,
        not here, to avoid double-counting entries vs settlements.
        """
        self._reset_daily_if_needed()

        self._daily_pnl += pnl
        if self._daily_pnl > self._daily_pnl_peak:
            self._daily_pnl_peak = self._daily_pnl
        self._total_settled += 1
        self._last_pnl = pnl

        if pnl > 0:
            self._consecutive_losses = 0
            self._consecutive_wins += 1
            self._total_wins += 1
        elif pnl < 0:
            self._consecutive_losses += 1
            self._consecutive_wins = 0
            if self._consecutive_losses >= self._config.max_consecutive_losses:
                from datetime import timedelta

                self._cooldown_until = datetime.now(timezone.utc) + timedelta(
                    minutes=self._config.cooldown_after_streak_minutes
                )
                logger.warning(
                    "consecutive_loss_cooldown",
                    losses=self._consecutive_losses,
                    cooldown_until=self._cooldown_until.isoformat(),
                )
        # pnl == 0: breakeven — don't count as win or loss, don't reset streaks

        logger.info(
            "risk_trade_recorded",
            pnl=float(pnl),
            daily_pnl=float(self._daily_pnl),
            trades_today=self._trades_today,
            consecutive_losses=self._consecutive_losses,
        )

    def _reset_daily_if_needed(self) -> None:
        """Reset daily counters at midnight."""
        today = date.today()
        if self._daily_pnl_date != today:
            if self._daily_pnl_date is not None:
                logger.info(
                    "daily_reset",
                    previous_date=self._daily_pnl_date.isoformat(),
                    previous_pnl=float(self._daily_pnl),
                    previous_trades=self._trades_today,
                )
            self._daily_pnl = Decimal("0")
            self._daily_pnl_peak = Decimal("0")
            self._daily_pnl_date = today
            self._trades_today = 0
            self._trades_today_date = today

    @property
    def daily_pnl(self) -> Decimal:
        """Current daily P&L."""
        self._reset_daily_if_needed()
        return self._daily_pnl

    @property
    def trades_today(self) -> int:
        """Number of trades placed today."""
        self._reset_daily_if_needed()
        return self._trades_today

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def consecutive_wins(self) -> int:
        return self._consecutive_wins

    @property
    def win_rate(self) -> float:
        if self._total_settled == 0:
            return 0.0
        return self._total_wins / self._total_settled

    @property
    def total_settled(self) -> int:
        return self._total_settled

    @property
    def daily_pnl_peak(self) -> Decimal:
        self._reset_daily_if_needed()
        return self._daily_pnl_peak

    @property
    def last_pnl(self) -> Decimal | None:
        return self._last_pnl
