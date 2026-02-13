"""Position sizing using fractional Kelly Criterion."""

from __future__ import annotations

import math
from decimal import Decimal

import structlog

from src.config import RiskConfig
from src.data.models import TradeSignal

logger = structlog.get_logger()


class PositionSizer:
    """Determines position size for trade signals using Kelly Criterion.

    Uses fractional Kelly (default quarter-Kelly) for conservative sizing.
    Applies multiple caps: per-market limit, total exposure limit,
    and percentage-of-bankroll limit.
    """

    def __init__(self, config: RiskConfig):
        self._config = config
        self._kelly_fraction = config.kelly_fraction

    def size(
        self,
        signal: TradeSignal,
        balance_dollars: Decimal,
        current_exposure_dollars: Decimal,
        current_market_position: int = 0,
    ) -> int:
        """Calculate position size in number of contracts.

        Args:
            signal: Trade signal with model probability and price
            balance_dollars: Current account balance
            current_exposure_dollars: Total exposure across all positions
            current_market_position: Contracts already held in this market

        Returns:
            Number of contracts to trade (0 if no trade should be made)
        """
        if float(balance_dollars) <= 0:
            return 0

        price = float(signal.suggested_price_dollars)
        if price <= 0 or price >= 1:
            return 0

        # Kelly fraction for binary contract
        if signal.side == "yes":
            model_prob = signal.model_probability
        else:
            model_prob = 1.0 - signal.model_probability

        kelly_f = self.kelly_fraction_for_binary(model_prob, price)
        if kelly_f <= 0:
            return 0

        # Apply fractional Kelly
        f = kelly_f * self._kelly_fraction

        # Scale by confidence
        f *= signal.confidence

        # Convert to dollar amount
        bet_dollars = f * float(balance_dollars)

        # Convert to contract count (each contract costs `price` dollars)
        count = int(bet_dollars / price)

        # Small bankroll floor: if Kelly says to trade but count rounds to 0,
        # use 1 contract when we can afford it (< 10% of bankroll)
        if count <= 0 and kelly_f > 0 and price < float(balance_dollars) * 0.10:
            count = 1

        # Apply caps
        count = self._apply_caps(
            count,
            balance_dollars,
            current_exposure_dollars,
            current_market_position,
            price,
        )

        if count > 0:
            logger.debug(
                "position_sized",
                ticker=signal.market_ticker,
                kelly_f=round(kelly_f, 4),
                fractional_f=round(f, 4),
                bet_dollars=round(bet_dollars, 2),
                count=count,
            )

        return count

    @staticmethod
    def kelly_fraction_for_binary(prob: float, price: float) -> float:
        """Compute Kelly fraction for a binary contract.

        For a binary contract bought at price P with true probability p:
        - Win payout per dollar risked: (1 - P) / P
        - Kelly formula: f* = (p - P) / (1 - P)

        Returns 0 if no edge (prob <= price).
        """
        if prob <= price or price <= 0 or price >= 1:
            return 0.0
        return (prob - price) / (1.0 - price)

    def _apply_caps(
        self,
        count: int,
        balance: Decimal,
        current_exposure: Decimal,
        current_market_position: int,
        price: float,
    ) -> int:
        """Apply all position size caps."""
        if count <= 0:
            return 0

        # Cap 1: Max contracts per market
        max_for_market = self._config.max_position_per_market - abs(
            current_market_position
        )
        count = min(count, max(0, max_for_market))

        # Cap 2: Max total exposure
        new_exposure = Decimal(str(count * price))
        remaining_exposure = (
            Decimal(str(self._config.max_total_exposure_dollars))
            - current_exposure
        )
        if new_exposure > remaining_exposure and remaining_exposure > 0:
            count = int(float(remaining_exposure) / price)
        elif remaining_exposure <= 0:
            count = 0

        # Cap 3: Don't risk more than 10% of bankroll on a single trade
        max_risk_dollars = float(balance) * 0.10
        max_contracts_by_risk = max(1, int(max_risk_dollars / price)) if price > 0 else 0
        count = min(count, max_contracts_by_risk)

        return max(0, count)
