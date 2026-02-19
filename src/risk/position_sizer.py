"""Position sizing using fractional Kelly Criterion."""

from __future__ import annotations

import math
from decimal import Decimal

import structlog

from src.config import RiskConfig, StrategyConfig
from src.data.models import TradeSignal
from src.risk.volatility import VolatilityTracker

logger = structlog.get_logger()


class PositionSizer:
    """Determines position size for trade signals using Kelly Criterion.

    Uses fractional Kelly (default quarter-Kelly) for conservative sizing.
    Applies multiple caps: per-market limit, total exposure limit,
    and percentage-of-bankroll limit.
    """

    def __init__(self, config: RiskConfig, strategy_config: StrategyConfig | None = None):
        self._config = config
        self._strategy_config = strategy_config
        self._kelly_fraction = config.kelly_fraction

    def size(
        self,
        signal: TradeSignal,
        balance_dollars: Decimal,
        current_exposure_dollars: Decimal,
        current_market_position: int = 0,
        vol_tracker: VolatilityTracker | None = None,
        time_to_expiry: float | None = None,
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

        # Apply fractional Kelly (settlement_ride / certainty_scalp use custom fractions)
        if (
            signal.signal_type == "certainty_scalp"
            and self._strategy_config is not None
        ):
            effective_kelly = self._strategy_config.certainty_scalp_kelly_fraction
        elif (
            signal.signal_type == "settlement_ride"
            and self._strategy_config is not None
        ):
            effective_kelly = self._strategy_config.settlement_ride_kelly_fraction
            # Scale with implied distance: bigger when more certain, smaller when marginal
            # Linear from 0.5x at min_distance to 1.5x at distance=0.45
            implied_dist = abs(signal.implied_probability - 0.5)
            min_dist = self._strategy_config.settlement_ride_min_implied_distance
            max_dist = 0.45
            if max_dist > min_dist:
                t = min(1.0, max(0.0, (implied_dist - min_dist) / (max_dist - min_dist)))
                dist_mult = 0.5 + t * 1.0  # 0.5x → 1.5x
                effective_kelly *= dist_mult
        else:
            effective_kelly = self._kelly_fraction
        f = kelly_f * effective_kelly

        # Adjust Kelly fraction for volatility regime
        if vol_tracker is not None:
            vol_adjusted = vol_tracker.adjust_kelly_fraction(effective_kelly)
            f *= vol_adjusted / effective_kelly

        # Zone-based Kelly scaling: cheap zones get more, expensive zones get less
        if signal.entry_zone > 0 and signal.entry_zone <= len(self._config.zone_kelly_multipliers):
            zone_mult = self._config.zone_kelly_multipliers[signal.entry_zone - 1]
            f *= zone_mult

        # Scale by confidence (linear for stronger differentiation)
        f *= signal.confidence

        # Fee-aware boost: increase position at extreme prices where fees
        # are negligible (~0.2% at 20c vs ~1.56% at 50c)
        price = float(signal.suggested_price_dollars)
        fee_threshold = self._config.fee_extreme_price_threshold
        if price < fee_threshold or price > (1.0 - fee_threshold):
            distance_from_mid = abs(price - 0.50)
            max_distance = 0.50 - fee_threshold
            if max_distance > 0:
                extremity = (distance_from_mid - max_distance) / (fee_threshold - 0.01)
                extremity = max(0.0, min(1.0, extremity))
                max_mult = self._config.fee_extreme_kelly_multiplier
                multiplier = 1.0 + extremity * (max_mult - 1.0)
                f *= min(multiplier, max_mult)

        # Time-based scaling: reduce size when less time for take-profit
        if (
            self._config.time_scale_enabled
            and time_to_expiry is not None
            and signal.signal_type not in ("market_making", "certainty_scalp")
        ):
            full_time = self._config.time_scale_full_seconds
            min_mult = self._config.time_scale_min_multiplier
            if time_to_expiry >= full_time:
                time_mult = 1.0
            else:
                # Linear scale from 1.0 down to min_mult
                time_mult = min_mult + (1.0 - min_mult) * (time_to_expiry / full_time)
            f *= time_mult

        # Convert to dollar amount
        bet_dollars = f * float(balance_dollars)

        # Convert to contract count (each contract costs `price` dollars)
        count = int(bet_dollars / price)

        # Small bankroll floor: if Kelly says to trade but count rounds to 0,
        # use 1 contract when we can afford it (< 10% of bankroll)
        if count <= 0 and kelly_f > 0 and price < float(balance_dollars) * 0.10:
            count = 1

        # Apply caps
        pre_cap_count = count
        count = self._apply_caps(
            count,
            balance_dollars,
            current_exposure_dollars,
            current_market_position,
            price,
            ticker=signal.market_ticker,
        )

        # Minimum position size: skip tiny positions that statistically lose
        min_size = self._config.min_position_size
        if 0 < count < min_size:
            logger.info(
                "position_below_minimum",
                ticker=signal.market_ticker,
                count=count,
                min_size=min_size,
            )
            count = 0

        if count > 0:
            logger.debug(
                "position_sized",
                ticker=signal.market_ticker,
                kelly_f=round(kelly_f, 4),
                fractional_f=round(f, 4),
                bet_dollars=round(bet_dollars, 2),
                count=count,
            )
        else:
            logger.debug(
                "position_size_zero",
                ticker=signal.market_ticker,
                kelly_f=round(kelly_f, 4),
                fractional_f=round(f, 4),
                bet_dollars=round(bet_dollars, 2),
                pre_cap_count=pre_cap_count,
                price=price,
                balance=float(balance_dollars),
                model_prob=round(model_prob, 4),
                current_position=current_market_position,
                exposure=float(current_exposure_dollars),
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
        ticker: str = "",
    ) -> int:
        """Apply all position size caps."""
        if count <= 0:
            return 0

        # Cap 1: Max contracts per market (with per-asset override)
        max_position = self._config.max_position_per_market
        if self._config.asset_max_position and ticker:
            ticker_upper = ticker.upper()
            for asset, limit in self._config.asset_max_position.items():
                if asset.upper() in ticker_upper:
                    max_position = limit
                    break
        max_for_market = max_position - abs(current_market_position)
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
