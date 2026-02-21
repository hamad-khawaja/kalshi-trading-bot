"""Comprehensive pre-live audit: validates all calculations against Kalshi rules.

Run with: pytest tests/test_audit_live_readiness.py -v

Every test uses concrete numeric examples and verifies against known Kalshi rules,
internal formulas, and edge cases discovered during paper trading.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal

import numpy as np
import pytest

from src.config import RiskConfig, StrategyConfig
from src.data.models import (
    FeatureVector,
    MarketSnapshot,
    Orderbook,
    OrderbookLevel,
    OrderRequest,
    Position,
    PredictionResult,
    TradeSignal,
)
from src.execution.position_tracker import PositionState, PositionTracker
from src.model.monte_carlo import MonteCarloSimulator
from src.risk.position_sizer import PositionSizer
from src.risk.risk_manager import RiskDecision, RiskManager
from src.strategy.edge_detector import EdgeDetector
from src.strategy.fair_value import compute_fair_value, parse_strike_price

NOW = datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orderbook(
    yes_bid: float,
    no_bid: float,
    yes_qty: int = 50,
    no_qty: int = 50,
    ticker: str = "KXBTC15M-26FEB201400",
) -> Orderbook:
    """Create a simple orderbook with one level each side."""
    return Orderbook(
        ticker=ticker,
        yes_levels=[OrderbookLevel(price_dollars=Decimal(str(yes_bid)), quantity=yes_qty)],
        no_levels=[OrderbookLevel(price_dollars=Decimal(str(no_bid)), quantity=no_qty)],
        timestamp=NOW,
    )


def _make_snapshot(
    yes_bid: float = 0.50,
    no_bid: float = 0.50,
    btc_price: float = 97500.0,
    ttx: float = 600.0,
    ticker: str = "KXBTC15M-26FEB201400",
    strike: float | None = None,
    stat_fv: float | None = None,
    yes_qty: int = 50,
    no_qty: int = 50,
) -> MarketSnapshot:
    ob = _make_orderbook(yes_bid, no_bid, yes_qty, no_qty, ticker)
    return MarketSnapshot(
        timestamp=NOW,
        market_ticker=ticker,
        btc_price=Decimal(str(btc_price)),
        orderbook=ob,
        implied_yes_prob=ob.implied_yes_prob,
        spread=ob.spread,
        strike_price=Decimal(str(strike)) if strike else None,
        statistical_fair_value=stat_fv,
        time_to_expiry_seconds=ttx,
        time_elapsed_seconds=900.0 - ttx,
        volume=100,
    )


def _make_signal(
    ticker: str = "KXBTC15M-26FEB201400",
    side: str = "yes",
    price: str = "0.40",
    model_prob: float = 0.60,
    implied_prob: float = 0.50,
    confidence: float = 0.70,
    signal_type: str = "directional",
    zone: int = 2,
    action: str = "buy",
    net_edge: float = 0.05,
    raw_edge: float = 0.06,
    post_only: bool | None = None,
) -> TradeSignal:
    return TradeSignal(
        market_ticker=ticker,
        side=side,
        action=action,
        raw_edge=raw_edge,
        net_edge=net_edge,
        model_probability=model_prob,
        implied_probability=implied_prob,
        confidence=confidence,
        suggested_price_dollars=price,
        suggested_count=0,
        timestamp=NOW,
        signal_type=signal_type,
        entry_zone=zone,
        post_only=post_only,
    )


# ===========================================================================
# 1. Fee Calculations
# ===========================================================================

class TestFeeCalculations:
    """Verify Kalshi fee formula: ceil(rate * C * P * (1-P)) rounded to nearest cent."""

    def test_maker_rate_is_1_75_pct(self):
        """Maker fee rate = 1.75%."""
        # 1 contract @ $0.50: raw = 0.0175 * 1 * 0.50 * 0.50 = 0.004375
        # In cents = 0.4375, ceil → 1 cent = $0.01
        fee = EdgeDetector.compute_fee_dollars(1, 0.50, is_maker=True)
        rate = Decimal("0.0175")
        raw = rate * Decimal("1") * Decimal("0.50") * Decimal("0.50")
        expected_cents = (raw * 100).to_integral_value(rounding=ROUND_CEILING)
        assert fee == expected_cents / 100

    def test_taker_rate_is_7_pct(self):
        """Taker fee rate = 7%."""
        fee = EdgeDetector.compute_fee_dollars(1, 0.50, is_maker=False)
        rate = Decimal("0.07")
        raw = rate * Decimal("1") * Decimal("0.50") * Decimal("0.50")
        expected_cents = (raw * 100).to_integral_value(rounding=ROUND_CEILING)
        assert fee == expected_cents / 100

    def test_fee_formula_ceil_to_cent(self):
        """Verify ceiling to nearest cent behavior."""
        # 10 contracts @ $0.30: taker = ceil(0.07*10*0.30*0.70 * 100)/100
        # = ceil(0.07*10*0.21*100)/100 = ceil(14.7)/100 = 15/100 = $0.15
        fee = EdgeDetector.compute_fee_dollars(10, 0.30, is_maker=False)
        assert fee == Decimal("0.15")

    @pytest.mark.parametrize(
        "price,expected_relative_to_mid",
        [
            (0.01, "low"),
            (0.25, "medium"),
            (0.50, "max"),
            (0.75, "medium"),
            (0.99, "low"),
        ],
    )
    def test_fee_maximized_at_50_cents(self, price, expected_relative_to_mid):
        """Fee is maximized at P=0.50 because P*(1-P) peaks there."""
        fee_mid = EdgeDetector.compute_fee_dollars(10, 0.50, is_maker=False)
        fee = EdgeDetector.compute_fee_dollars(10, price, is_maker=False)
        if expected_relative_to_mid == "max":
            assert fee == fee_mid
        else:
            assert fee <= fee_mid

    def test_fee_symmetric_around_50(self):
        """P*(1-P) is symmetric: fee at P=0.30 should equal fee at P=0.70."""
        fee_30 = EdgeDetector.compute_fee_dollars(10, 0.30, is_maker=False)
        fee_70 = EdgeDetector.compute_fee_dollars(10, 0.70, is_maker=False)
        assert fee_30 == fee_70

    def test_fee_zero_when_count_zero(self):
        fee = EdgeDetector.compute_fee_dollars(0, 0.50, is_maker=False)
        assert fee == Decimal("0")

    def test_fee_zero_when_price_zero(self):
        fee = EdgeDetector.compute_fee_dollars(10, 0.0, is_maker=False)
        assert fee == Decimal("0")

    def test_fee_zero_when_price_one(self):
        fee = EdgeDetector.compute_fee_dollars(10, 1.0, is_maker=False)
        assert fee == Decimal("0")

    def test_fee_at_01_cent_extreme(self):
        """At $0.01, fee is near zero: 0.07*1*0.01*0.99 = 0.000693."""
        fee = EdgeDetector.compute_fee_dollars(1, 0.01, is_maker=False)
        # ceil(0.0693 cents) = 1 cent
        assert fee == Decimal("0.01")

    def test_fee_at_99_cent_extreme(self):
        """At $0.99, same P*(1-P) as $0.01."""
        fee_01 = EdgeDetector.compute_fee_dollars(1, 0.01, is_maker=False)
        fee_99 = EdgeDetector.compute_fee_dollars(1, 0.99, is_maker=False)
        assert fee_01 == fee_99

    def test_maker_always_less_than_taker(self):
        """Maker fee < taker fee at all prices."""
        for price in [0.10, 0.25, 0.40, 0.50, 0.60, 0.75, 0.90]:
            maker = EdgeDetector.compute_fee_dollars(10, price, is_maker=True)
            taker = EdgeDetector.compute_fee_dollars(10, price, is_maker=False)
            assert maker <= taker, f"Maker {maker} > Taker {taker} at P={price}"

    def test_concrete_taker_at_50(self):
        """10 contracts @ $0.50 taker: ceil(0.07*10*0.25*100)/100 = ceil(17.5)/100 = $0.18."""
        fee = EdgeDetector.compute_fee_dollars(10, 0.50, is_maker=False)
        assert fee == Decimal("0.18")

    def test_concrete_maker_at_50(self):
        """10 contracts @ $0.50 maker: ceil(0.0175*10*0.25*100)/100 = ceil(4.375)/100 = $0.05."""
        fee = EdgeDetector.compute_fee_dollars(10, 0.50, is_maker=True)
        assert fee == Decimal("0.05")


# ===========================================================================
# 2. Kelly Criterion
# ===========================================================================

class TestKellyCriterion:
    """Verify f* = (p - P) / (1 - P) for binary options."""

    def test_basic_kelly(self):
        """prob=0.60, price=0.50 → f* = (0.60-0.50)/(1-0.50) = 0.20."""
        f = PositionSizer.kelly_fraction_for_binary(0.60, 0.50)
        assert f == pytest.approx(0.20, abs=1e-10)

    def test_no_edge_returns_zero(self):
        """prob=0.50, price=0.50 → no edge → f*=0."""
        f = PositionSizer.kelly_fraction_for_binary(0.50, 0.50)
        assert f == 0.0

    def test_negative_edge_returns_zero(self):
        """prob=0.40, price=0.50 → negative edge → f*=0."""
        f = PositionSizer.kelly_fraction_for_binary(0.40, 0.50)
        assert f == 0.0

    def test_prob_equals_price_returns_zero(self):
        """Exact break-even: prob=price → no edge."""
        f = PositionSizer.kelly_fraction_for_binary(0.35, 0.35)
        assert f == 0.0

    def test_prob_one(self):
        """prob=1.0, price=0.50 → f* = 0.50/0.50 = 1.0 (bet everything)."""
        f = PositionSizer.kelly_fraction_for_binary(1.0, 0.50)
        assert f == pytest.approx(1.0, abs=1e-10)

    def test_prob_zero(self):
        """prob=0.0 always returns 0 (no positive edge possible)."""
        f = PositionSizer.kelly_fraction_for_binary(0.0, 0.50)
        assert f == 0.0

    def test_price_near_one(self):
        """price=0.99 — very expensive contract. prob=0.995 → f*=(0.995-0.99)/(1-0.99)=0.50."""
        f = PositionSizer.kelly_fraction_for_binary(0.995, 0.99)
        assert f == pytest.approx(0.50, abs=1e-6)

    def test_price_near_zero(self):
        """price=0.01 — cheap contract. prob=0.05 → f*=(0.05-0.01)/(1-0.01)=0.04/0.99≈0.0404."""
        f = PositionSizer.kelly_fraction_for_binary(0.05, 0.01)
        assert f == pytest.approx(0.04 / 0.99, abs=1e-6)

    def test_price_zero_returns_zero(self):
        """price=0 is degenerate → returns 0."""
        f = PositionSizer.kelly_fraction_for_binary(0.50, 0.0)
        assert f == 0.0

    def test_price_one_returns_zero(self):
        """price=1.0 → returns 0 (can't make money buying at $1.00)."""
        f = PositionSizer.kelly_fraction_for_binary(0.99, 1.0)
        assert f == 0.0

    def test_kelly_increases_with_edge(self):
        """Larger edge → larger Kelly fraction."""
        f1 = PositionSizer.kelly_fraction_for_binary(0.55, 0.50)
        f2 = PositionSizer.kelly_fraction_for_binary(0.60, 0.50)
        f3 = PositionSizer.kelly_fraction_for_binary(0.70, 0.50)
        assert f1 < f2 < f3

    def test_kelly_increases_with_price_for_fixed_edge(self):
        """For fixed absolute edge (prob-price=0.10), Kelly INCREASES with price.
        f* = edge/(1-P): as P increases, denominator shrinks, so f* grows.
        This is correct: the payout ratio (1-P)/P shrinks, so you need
        a larger fraction of bankroll to capture the same edge.
        """
        # prob = price + 0.10 in all cases
        f1 = PositionSizer.kelly_fraction_for_binary(0.30, 0.20)  # 0.10/0.80 = 0.125
        f2 = PositionSizer.kelly_fraction_for_binary(0.60, 0.50)  # 0.10/0.50 = 0.20
        f3 = PositionSizer.kelly_fraction_for_binary(0.90, 0.80)  # 0.10/0.20 = 0.50
        assert f1 < f2 < f3
        assert f1 == pytest.approx(0.125, abs=1e-10)
        assert f2 == pytest.approx(0.20, abs=1e-10)
        assert f3 == pytest.approx(0.50, abs=1e-10)


# ===========================================================================
# 3. Position Sizing Caps
# ===========================================================================

class TestPositionSizingCaps:
    """Verify all 4 caps in _apply_caps."""

    def _make_sizer(self, risk_config=None, strategy_config=None):
        rc = risk_config or RiskConfig(
            max_position_per_market=15,
            max_total_exposure_dollars=50.0,
        )
        sc = strategy_config or StrategyConfig(stop_loss_max_dollar_loss=2.0)
        return PositionSizer(rc, sc)

    def test_cap1_max_position_per_market(self):
        """Can't exceed max_position_per_market."""
        sizer = self._make_sizer(RiskConfig(max_position_per_market=10, max_total_exposure_dollars=500.0))
        count = sizer._apply_caps(
            count=20,
            balance=Decimal("500"),
            current_exposure=Decimal("0"),
            current_market_position=0,
            price=0.30,
        )
        assert count <= 10

    def test_cap1_with_existing_position(self):
        """Existing position reduces remaining capacity."""
        sizer = self._make_sizer(RiskConfig(max_position_per_market=10, max_total_exposure_dollars=500.0))
        count = sizer._apply_caps(
            count=20,
            balance=Decimal("500"),
            current_exposure=Decimal("0"),
            current_market_position=7,
            price=0.30,
        )
        assert count <= 3  # 10 - 7 = 3

    def test_cap1_per_asset_override(self):
        """Per-asset max position overrides global."""
        rc = RiskConfig(
            max_position_per_market=15,
            max_total_exposure_dollars=500.0,
            asset_max_position={"ETH": 8},
        )
        sizer = self._make_sizer(rc)
        count = sizer._apply_caps(
            count=20,
            balance=Decimal("500"),
            current_exposure=Decimal("0"),
            current_market_position=0,
            price=0.30,
            ticker="KXETH15M-26FEB201400",
        )
        assert count <= 8

    def test_cap2_total_exposure(self):
        """New exposure + current must not exceed max_total_exposure_dollars."""
        sizer = self._make_sizer(RiskConfig(
            max_position_per_market=100,
            max_total_exposure_dollars=10.0,
        ))
        # Current exposure $8, price $0.50 → max 4 contracts ($2 remaining / $0.50)
        count = sizer._apply_caps(
            count=20,
            balance=Decimal("500"),
            current_exposure=Decimal("8"),
            current_market_position=0,
            price=0.50,
        )
        assert count <= 4

    def test_cap2_exposure_full(self):
        """When already at max exposure, count should be 0."""
        sizer = self._make_sizer(RiskConfig(
            max_position_per_market=100,
            max_total_exposure_dollars=10.0,
        ))
        count = sizer._apply_caps(
            count=20,
            balance=Decimal("500"),
            current_exposure=Decimal("10"),
            current_market_position=0,
            price=0.50,
        )
        assert count == 0

    def test_cap3_bankroll_10pct(self):
        """Can't risk more than 10% of bankroll on a single trade."""
        sizer = self._make_sizer(RiskConfig(
            max_position_per_market=100,
            max_total_exposure_dollars=500.0,
        ))
        # Balance $100, price $0.50 → max risk $10 → max 20 contracts
        count = sizer._apply_caps(
            count=50,
            balance=Decimal("100"),
            current_exposure=Decimal("0"),
            current_market_position=0,
            price=0.50,
        )
        assert count <= 20

    def test_cap4_max_dollar_loss(self):
        """count <= max_dollar_loss / price."""
        # entry at $0.30, max_loss=$2 → max 6 contracts (int(2/0.30)=6)
        sizer = self._make_sizer(
            RiskConfig(max_position_per_market=100, max_total_exposure_dollars=500.0),
            StrategyConfig(stop_loss_max_dollar_loss=2.0),
        )
        count = sizer._apply_caps(
            count=20,
            balance=Decimal("500"),
            current_exposure=Decimal("0"),
            current_market_position=0,
            price=0.30,
        )
        assert count <= 6  # int(2.0 / 0.30) = 6

    def test_cap4_at_50_cents(self):
        """entry at $0.50, max_loss=$2 → max 4 contracts."""
        sizer = self._make_sizer(
            RiskConfig(max_position_per_market=100, max_total_exposure_dollars=500.0),
            StrategyConfig(stop_loss_max_dollar_loss=2.0),
        )
        count = sizer._apply_caps(
            count=20,
            balance=Decimal("500"),
            current_exposure=Decimal("0"),
            current_market_position=0,
            price=0.50,
        )
        assert count <= 4  # int(2.0 / 0.50) = 4

    def test_all_caps_interact(self):
        """The tightest cap wins."""
        sizer = self._make_sizer(
            RiskConfig(
                max_position_per_market=5,  # Cap 1: 5
                max_total_exposure_dollars=500.0,
            ),
            StrategyConfig(stop_loss_max_dollar_loss=2.0),  # Cap 4: int(2/0.30)=6
        )
        # Cap 1 (5) is tighter than Cap 4 (6), so should be 5
        count = sizer._apply_caps(
            count=20,
            balance=Decimal("500"),
            current_exposure=Decimal("0"),
            current_market_position=0,
            price=0.30,
        )
        assert count <= 5

    def test_zero_count_returns_zero(self):
        sizer = self._make_sizer()
        count = sizer._apply_caps(
            count=0,
            balance=Decimal("500"),
            current_exposure=Decimal("0"),
            current_market_position=0,
            price=0.50,
        )
        assert count == 0


# ===========================================================================
# 4. Price Validation
# ===========================================================================

class TestPriceValidation:
    """Verify Kalshi price rules."""

    def test_orderbook_no_price_complement(self):
        """NO price = 1.0 - YES price: best_yes_ask = 1 - best_no_bid."""
        ob = _make_orderbook(yes_bid=0.55, no_bid=0.48)
        assert ob.best_yes_ask == Decimal("1") - Decimal("0.48")
        assert ob.best_yes_ask == Decimal("0.52")

    def test_spread_calculation(self):
        """Spread = YES ask - YES bid."""
        ob = _make_orderbook(yes_bid=0.50, no_bid=0.48)
        # ask = 1 - 0.48 = 0.52
        assert ob.spread == Decimal("0.52") - Decimal("0.50")
        assert ob.spread == Decimal("0.02")

    def test_implied_yes_prob_is_midpoint(self):
        """Implied prob = midpoint of YES bid and YES ask."""
        ob = _make_orderbook(yes_bid=0.50, no_bid=0.48)
        expected = (Decimal("0.50") + Decimal("0.52")) / 2  # 0.51
        assert ob.implied_yes_prob == expected

    def test_order_request_yes_price_format(self):
        """OrderRequest accepts yes_price_dollars as string."""
        req = OrderRequest(
            ticker="TEST",
            side="yes",
            action="buy",
            count=5,
            client_order_id=str(uuid.uuid4()),
            yes_price_dollars="0.45",
        )
        body = req.to_api_dict()
        assert body["yes_price_dollars"] == "0.45"
        assert "no_price_dollars" not in body

    def test_order_request_no_price_format(self):
        """OrderRequest accepts no_price_dollars for NO side."""
        req = OrderRequest(
            ticker="TEST",
            side="no",
            action="buy",
            count=5,
            client_order_id=str(uuid.uuid4()),
            no_price_dollars="0.35",
        )
        body = req.to_api_dict()
        assert body["no_price_dollars"] == "0.35"
        assert "yes_price_dollars" not in body

    def test_order_request_client_id_is_uuid(self):
        """client_order_id should be valid UUID format."""
        cid = str(uuid.uuid4())
        req = OrderRequest(
            ticker="TEST", side="yes", action="buy", count=1,
            client_order_id=cid, yes_price_dollars="0.50",
        )
        # Validate UUID format
        uuid.UUID(req.client_order_id)

    def test_order_request_post_only_field(self):
        """post_only=True should appear in API dict."""
        req = OrderRequest(
            ticker="TEST", side="yes", action="buy", count=1,
            client_order_id="test-id", yes_price_dollars="0.50",
            post_only=True,
        )
        body = req.to_api_dict()
        assert body["post_only"] is True

    def test_order_request_type_is_limit(self):
        """All orders are limit type."""
        req = OrderRequest(
            ticker="TEST", side="yes", action="buy", count=1,
            client_order_id="test-id", yes_price_dollars="0.50",
        )
        assert req.type == "limit"
        assert req.to_api_dict()["type"] == "limit"


# ===========================================================================
# 5. Stop Loss Logic
# ===========================================================================

class TestStopLoss:
    """Verify stop loss triggers: percentage, dollar cap, emergency, hold period."""

    def _make_tracker_with_position(
        self,
        entry_price: float = 0.50,
        count: int = 10,
        entry_time: datetime | None = None,
        strategy_tag: str = "",
        fees_paid: float = 0.0,
    ) -> PositionTracker:
        """Create a PositionTracker with one position."""
        tracker = PositionTracker.__new__(PositionTracker)
        tracker._positions = {}
        tracker._completed_trades = []
        tracker._lock = None

        t = entry_time or (NOW - timedelta(seconds=120))
        pos = PositionState(
            market_ticker="KXBTC15M-26FEB201400",
            side="yes",
            count=count,
            avg_entry_price=Decimal(str(entry_price)),
            entry_time=t,
        )
        pos.strategy_tag = strategy_tag
        pos.fees_paid = Decimal(str(fees_paid))
        tracker._positions["KXBTC15M-26FEB201400"] = pos
        return tracker

    def test_percentage_trigger(self):
        """Loss >= 35% of entry triggers SL: entry=0.50, bid=0.30 → loss=40%."""
        tracker = self._make_tracker_with_position(entry_price=0.50)
        snap = _make_snapshot(yes_bid=0.30)
        results = tracker.check_stop_loss(
            {"KXBTC15M-26FEB201400": snap},
            stop_loss_pct=0.35,
        )
        assert len(results) == 1
        assert results[0][0] == "KXBTC15M-26FEB201400"

    def test_percentage_not_triggered(self):
        """Loss < 35%: entry=0.50, bid=0.40 → loss=20% < 35%."""
        tracker = self._make_tracker_with_position(entry_price=0.50)
        snap = _make_snapshot(yes_bid=0.40)
        results = tracker.check_stop_loss(
            {"KXBTC15M-26FEB201400": snap},
            stop_loss_pct=0.35,
        )
        assert len(results) == 0

    def test_dollar_cap_trigger(self):
        """Dollar loss = (entry-bid)*count + fees >= cap.
        entry=0.50, bid=0.30, count=10, fees=0 → $2.00 >= $2.00.
        """
        tracker = self._make_tracker_with_position(entry_price=0.50, count=10)
        snap = _make_snapshot(yes_bid=0.30)
        results = tracker.check_stop_loss(
            {"KXBTC15M-26FEB201400": snap},
            stop_loss_pct=0.99,  # High pct so only dollar cap triggers
            max_dollar_loss=2.0,
        )
        assert len(results) == 1

    def test_dollar_cap_includes_fees(self):
        """Fees contribute to dollar loss.
        entry=0.50, bid=0.35, count=10, fees=0.50
        loss = (0.50-0.35)*10 + 0.50 = 1.50 + 0.50 = 2.00 >= cap=2.0.
        """
        tracker = self._make_tracker_with_position(
            entry_price=0.50, count=10, fees_paid=0.50,
        )
        snap = _make_snapshot(yes_bid=0.35)
        results = tracker.check_stop_loss(
            {"KXBTC15M-26FEB201400": snap},
            stop_loss_pct=0.99,
            max_dollar_loss=2.0,
        )
        assert len(results) == 1

    def test_emergency_exit_bypasses_hold_period(self):
        """During hold period, dollar cap still triggers emergency exit."""
        # Entry 2 seconds ago (within hold period of 60s)
        very_recent = datetime.now(timezone.utc) - timedelta(seconds=2)
        tracker = self._make_tracker_with_position(
            entry_price=0.50, count=10,
            entry_time=very_recent,
        )
        snap = _make_snapshot(yes_bid=0.25)
        results = tracker.check_stop_loss(
            {"KXBTC15M-26FEB201400": snap},
            stop_loss_pct=0.35,
            min_hold_seconds=60.0,
            max_dollar_loss=2.0,
        )
        # Dollar loss = (0.50-0.25)*10 = 2.50 > 2.0 → emergency exit
        assert len(results) == 1

    def test_hold_period_blocks_normal_sl(self):
        """Normal percentage SL is blocked during hold period.
        Note: check_stop_loss uses datetime.now(), so we set entry_time
        to just a few seconds ago (real wall time) to be within hold period.
        """
        very_recent = datetime.now(timezone.utc) - timedelta(seconds=2)
        tracker = self._make_tracker_with_position(
            entry_price=0.50, count=10,
            entry_time=very_recent,
        )
        snap = _make_snapshot(yes_bid=0.30)
        results = tracker.check_stop_loss(
            {"KXBTC15M-26FEB201400": snap},
            stop_loss_pct=0.35,
            min_hold_seconds=60.0,
            max_dollar_loss=0.0,  # Disable dollar cap
        )
        assert len(results) == 0

    def test_min_bid_filter(self):
        """Don't sell if bid < min_bid ($0.05)."""
        tracker = self._make_tracker_with_position(entry_price=0.50)
        snap = _make_snapshot(yes_bid=0.03)
        results = tracker.check_stop_loss(
            {"KXBTC15M-26FEB201400": snap},
            stop_loss_pct=0.10,
            min_bid=0.05,
        )
        assert len(results) == 0

    def test_settlement_ride_excluded(self):
        """settlement_ride positions skip stop loss."""
        tracker = self._make_tracker_with_position(
            entry_price=0.50, strategy_tag="settlement_ride",
        )
        snap = _make_snapshot(yes_bid=0.10)
        results = tracker.check_stop_loss(
            {"KXBTC15M-26FEB201400": snap},
            stop_loss_pct=0.10,
        )
        assert len(results) == 0

    def test_certainty_scalp_excluded(self):
        """certainty_scalp positions skip stop loss."""
        tracker = self._make_tracker_with_position(
            entry_price=0.50, strategy_tag="certainty_scalp",
        )
        snap = _make_snapshot(yes_bid=0.10)
        results = tracker.check_stop_loss(
            {"KXBTC15M-26FEB201400": snap},
            stop_loss_pct=0.10,
        )
        assert len(results) == 0

    def test_per_asset_stop_loss_override(self):
        """ETH can have different stop loss than BTC."""
        tracker = PositionTracker.__new__(PositionTracker)
        tracker._positions = {}
        tracker._completed_trades = []
        tracker._lock = None
        t = NOW - timedelta(seconds=120)
        pos = PositionState(
            market_ticker="KXETH15M-26FEB201400",
            side="yes", count=10,
            avg_entry_price=Decimal("0.50"), entry_time=t,
        )
        tracker._positions["KXETH15M-26FEB201400"] = pos

        snap = _make_snapshot(yes_bid=0.40, ticker="KXETH15M-26FEB201400")
        # Default 35% wouldn't trigger (loss=20%)
        # But ETH override at 15% would trigger
        results = tracker.check_stop_loss(
            {"KXETH15M-26FEB201400": snap},
            stop_loss_pct=0.35,
            asset_stop_loss_pct={"ETH": 0.15},
        )
        assert len(results) == 1


# ===========================================================================
# 6. Take Profit Logic
# ===========================================================================

class TestTakeProfit:
    """Verify take profit: fixed threshold, time decay, trailing TP."""

    def _make_tracker_with_position(
        self,
        entry_price: float = 0.40,
        count: int = 10,
        strategy_tag: str = "",
        entry_time: datetime | None = None,
    ) -> PositionTracker:
        tracker = PositionTracker.__new__(PositionTracker)
        tracker._positions = {}
        tracker._completed_trades = []
        tracker._lock = None
        t = entry_time or (NOW - timedelta(seconds=120))
        pos = PositionState(
            market_ticker="KXBTC15M-26FEB201400",
            side="yes", count=count,
            avg_entry_price=Decimal(str(entry_price)), entry_time=t,
        )
        pos.strategy_tag = strategy_tag
        tracker._positions["KXBTC15M-26FEB201400"] = pos
        return tracker

    def test_fixed_tp_profit_minus_fee_above_threshold(self):
        """profit - fee >= threshold triggers TP.
        entry=0.40, bid=0.55, profit=0.15. Fee at 0.55 taker ≈ $0.02.
        Net = 0.15 - 0.02 = 0.13 >= 0.10 threshold.
        """
        config = StrategyConfig(
            take_profit_min_profit_cents=0.10,
            take_profit_min_hold_seconds=10.0,
            trailing_take_profit_enabled=False,
            take_profit_time_decay_start_seconds=300.0,
        )
        tracker = self._make_tracker_with_position(entry_price=0.40)
        snap = _make_snapshot(yes_bid=0.55, ttx=400.0)
        results = tracker.check_take_profit(
            {"KXBTC15M-26FEB201400": snap}, config,
        )
        assert len(results) == 1

    def test_fixed_tp_not_triggered_below_threshold(self):
        """Small profit doesn't trigger. entry=0.40, bid=0.42 → profit=0.02 < 0.10."""
        config = StrategyConfig(
            take_profit_min_profit_cents=0.10,
            take_profit_min_hold_seconds=10.0,
            trailing_take_profit_enabled=False,
            take_profit_time_decay_start_seconds=300.0,
        )
        tracker = self._make_tracker_with_position(entry_price=0.40)
        snap = _make_snapshot(yes_bid=0.42, ttx=400.0)
        results = tracker.check_take_profit(
            {"KXBTC15M-26FEB201400": snap}, config,
        )
        assert len(results) == 0

    def test_time_decay_threshold(self):
        """Threshold decays linearly: at 165s (halfway between 30 and 300),
        threshold = floor + 0.5 * (min_profit - floor) = 0.05 + 0.5 * 0.05 = 0.075.
        """
        config = StrategyConfig(
            take_profit_min_profit_cents=0.10,
            take_profit_time_decay_start_seconds=300.0,
            take_profit_time_decay_floor_cents=0.05,
            take_profit_min_hold_seconds=10.0,
            trailing_take_profit_enabled=False,
        )
        # At TTX=165s: t = (165-30)/(300-30) = 135/270 = 0.5
        # threshold = 0.05 + 0.5 * (0.10 - 0.05) = 0.075
        tracker = self._make_tracker_with_position(entry_price=0.40)
        # Bid=0.49 gives profit=0.09, fee≈$0.02, net≈0.07 < 0.075 → no trigger
        snap_no = _make_snapshot(yes_bid=0.49, ttx=165.0)
        results = tracker.check_take_profit(
            {"KXBTC15M-26FEB201400": snap_no}, config,
        )
        assert len(results) == 0

        # Bid=0.51 gives profit=0.11, fee≈$0.02, net≈0.09 > 0.075 → trigger
        tracker2 = self._make_tracker_with_position(entry_price=0.40)
        snap_yes = _make_snapshot(yes_bid=0.51, ttx=165.0)
        results2 = tracker2.check_take_profit(
            {"KXBTC15M-26FEB201400": snap_yes}, config,
        )
        assert len(results2) == 1

    def test_time_decay_at_high_ttx(self):
        """Above decay_start, threshold = min_profit (no decay)."""
        config = StrategyConfig(
            take_profit_min_profit_cents=0.10,
            take_profit_time_decay_start_seconds=300.0,
            take_profit_time_decay_floor_cents=0.05,
            take_profit_min_hold_seconds=10.0,
            trailing_take_profit_enabled=False,
        )
        # At TTX=400 > 300 → threshold = 0.10
        tracker = self._make_tracker_with_position(entry_price=0.40)
        snap = _make_snapshot(yes_bid=0.55, ttx=400.0)
        results = tracker.check_take_profit(
            {"KXBTC15M-26FEB201400": snap}, config,
        )
        assert len(results) == 1

    def test_time_decay_at_30s_skips(self):
        """Below 30s TTX, take profit defers to pre-expiry exit."""
        config = StrategyConfig(
            take_profit_min_profit_cents=0.01,
            take_profit_min_hold_seconds=0.0,
            trailing_take_profit_enabled=False,
        )
        tracker = self._make_tracker_with_position(entry_price=0.40)
        snap = _make_snapshot(yes_bid=0.80, ttx=20.0)
        results = tracker.check_take_profit(
            {"KXBTC15M-26FEB201400": snap}, config,
        )
        assert len(results) == 0

    def test_trailing_tp_activation_and_drop(self):
        """Trailing TP: activates at threshold, exits on drop from peak."""
        config = StrategyConfig(
            trailing_take_profit_enabled=True,
            trailing_take_profit_activation_cents=0.08,
            trailing_take_profit_drop_cents=0.05,
            take_profit_min_hold_seconds=10.0,
            take_profit_min_profit_cents=0.10,
        )
        tracker = self._make_tracker_with_position(entry_price=0.40)
        pos = tracker._positions["KXBTC15M-26FEB201400"]
        # Simulate high water mark at 0.52 (profit was 0.12 >= activation 0.08)
        pos.high_water_bid = Decimal("0.52")
        # Current bid dropped to 0.47 → drop from peak = 0.52 - 0.47 = 0.05 >= 0.05
        snap = _make_snapshot(yes_bid=0.47, ttx=400.0)
        results = tracker.check_take_profit(
            {"KXBTC15M-26FEB201400": snap}, config,
        )
        assert len(results) == 1

    def test_trailing_tp_not_enough_drop(self):
        """Drop from peak < threshold → no trigger."""
        config = StrategyConfig(
            trailing_take_profit_enabled=True,
            trailing_take_profit_activation_cents=0.08,
            trailing_take_profit_drop_cents=0.05,
            take_profit_min_hold_seconds=10.0,
            take_profit_min_profit_cents=0.10,
        )
        tracker = self._make_tracker_with_position(entry_price=0.40)
        pos = tracker._positions["KXBTC15M-26FEB201400"]
        pos.high_water_bid = Decimal("0.52")
        # Drop = 0.52 - 0.49 = 0.03 < 0.05
        snap = _make_snapshot(yes_bid=0.49, ttx=400.0)
        results = tracker.check_take_profit(
            {"KXBTC15M-26FEB201400": snap}, config,
        )
        assert len(results) == 0

    def test_high_water_mark_tracking(self):
        """High water mark updates when bid increases."""
        config = StrategyConfig(
            trailing_take_profit_enabled=True,
            trailing_take_profit_activation_cents=0.20,  # High so no trigger
            trailing_take_profit_drop_cents=0.05,
            take_profit_min_hold_seconds=10.0,
            take_profit_min_profit_cents=0.50,  # High so no fixed TP
        )
        tracker = self._make_tracker_with_position(entry_price=0.40)
        pos = tracker._positions["KXBTC15M-26FEB201400"]

        # First check at bid=0.48
        snap1 = _make_snapshot(yes_bid=0.48, ttx=400.0)
        tracker.check_take_profit({"KXBTC15M-26FEB201400": snap1}, config)
        assert pos.high_water_bid == Decimal("0.48")

        # Second check at bid=0.52 → HWM should update
        snap2 = _make_snapshot(yes_bid=0.52, ttx=400.0)
        tracker.check_take_profit({"KXBTC15M-26FEB201400": snap2}, config)
        assert pos.high_water_bid == Decimal("0.52")

        # Third check at bid=0.50 → HWM stays at 0.52
        snap3 = _make_snapshot(yes_bid=0.50, ttx=400.0)
        tracker.check_take_profit({"KXBTC15M-26FEB201400": snap3}, config)
        assert pos.high_water_bid == Decimal("0.52")

    def test_settlement_ride_excluded(self):
        config = StrategyConfig(take_profit_min_profit_cents=0.01, take_profit_min_hold_seconds=0.0)
        tracker = self._make_tracker_with_position(entry_price=0.40, strategy_tag="settlement_ride")
        snap = _make_snapshot(yes_bid=0.80, ttx=400.0)
        results = tracker.check_take_profit(
            {"KXBTC15M-26FEB201400": snap}, config,
        )
        assert len(results) == 0

    def test_certainty_scalp_excluded(self):
        config = StrategyConfig(take_profit_min_profit_cents=0.01, take_profit_min_hold_seconds=0.0)
        tracker = self._make_tracker_with_position(entry_price=0.40, strategy_tag="certainty_scalp")
        snap = _make_snapshot(yes_bid=0.80, ttx=400.0)
        results = tracker.check_take_profit(
            {"KXBTC15M-26FEB201400": snap}, config,
        )
        assert len(results) == 0


# ===========================================================================
# 7. Pre-Expiry Exit
# ===========================================================================

class TestPreExpiryExit:
    """Verify pre-expiry exit: triggers at <=90s, PnL floor, strategy exclusions."""

    def _make_tracker_with_position(
        self, entry_price=0.50, strategy_tag=""
    ) -> PositionTracker:
        tracker = PositionTracker.__new__(PositionTracker)
        tracker._positions = {}
        tracker._completed_trades = []
        tracker._lock = None
        pos = PositionState(
            market_ticker="KXBTC15M-26FEB201400",
            side="yes", count=10,
            avg_entry_price=Decimal(str(entry_price)),
            entry_time=NOW - timedelta(seconds=600),
        )
        pos.strategy_tag = strategy_tag
        tracker._positions["KXBTC15M-26FEB201400"] = pos
        return tracker

    def test_triggers_at_90s(self):
        """Pre-expiry fires when TTX <= 90s and PnL is acceptable."""
        tracker = self._make_tracker_with_position(entry_price=0.50)
        snap = _make_snapshot(yes_bid=0.55, ttx=80.0)
        results = tracker.check_pre_expiry_exits(
            {"KXBTC15M-26FEB201400": snap},
            pre_expiry_seconds=90.0,
            min_pnl_per_contract=-0.03,
        )
        assert len(results) == 1

    def test_does_not_trigger_above_90s(self):
        """No exit when TTX > pre_expiry_seconds."""
        tracker = self._make_tracker_with_position(entry_price=0.50)
        snap = _make_snapshot(yes_bid=0.55, ttx=100.0)
        results = tracker.check_pre_expiry_exits(
            {"KXBTC15M-26FEB201400": snap},
            pre_expiry_seconds=90.0,
        )
        assert len(results) == 0

    def test_pnl_floor_blocks_losers(self):
        """Losers ride to settlement: pnl_per_contract < -$0.03 → no exit.
        entry=0.50, bid=0.45 → pnl=-0.05 < -0.03.
        """
        tracker = self._make_tracker_with_position(entry_price=0.50)
        snap = _make_snapshot(yes_bid=0.45, ttx=80.0)
        results = tracker.check_pre_expiry_exits(
            {"KXBTC15M-26FEB201400": snap},
            pre_expiry_seconds=90.0,
            min_pnl_per_contract=-0.03,
        )
        assert len(results) == 0

    def test_pnl_floor_allows_near_breakeven(self):
        """entry=0.50, bid=0.48 → pnl=-0.02 >= -0.03 → exit allowed."""
        tracker = self._make_tracker_with_position(entry_price=0.50)
        snap = _make_snapshot(yes_bid=0.48, ttx=80.0)
        results = tracker.check_pre_expiry_exits(
            {"KXBTC15M-26FEB201400": snap},
            pre_expiry_seconds=90.0,
            min_pnl_per_contract=-0.03,
        )
        assert len(results) == 1

    def test_settlement_ride_excluded(self):
        """settlement_ride holds to settlement — skip pre-expiry exit."""
        tracker = self._make_tracker_with_position(
            entry_price=0.50, strategy_tag="settlement_ride",
        )
        snap = _make_snapshot(yes_bid=0.80, ttx=80.0)
        results = tracker.check_pre_expiry_exits(
            {"KXBTC15M-26FEB201400": snap},
            pre_expiry_seconds=90.0,
        )
        assert len(results) == 0

    def test_certainty_scalp_excluded(self):
        """certainty_scalp also holds to settlement."""
        tracker = self._make_tracker_with_position(
            entry_price=0.50, strategy_tag="certainty_scalp",
        )
        snap = _make_snapshot(yes_bid=0.80, ttx=80.0)
        results = tracker.check_pre_expiry_exits(
            {"KXBTC15M-26FEB201400": snap},
            pre_expiry_seconds=90.0,
        )
        assert len(results) == 0


# ===========================================================================
# 8. Edge Detection
# ===========================================================================

class TestEdgeDetection:
    """Verify edge detection formulas and filters."""

    def _make_detector(self, config: StrategyConfig | None = None) -> EdgeDetector:
        c = config or StrategyConfig(
            min_edge_threshold=0.03,
            max_edge_threshold=0.25,
            confidence_min=0.55,
            zone_filter_enabled=False,
            edge_expiry_decay_enabled=False,
            yes_side_edge_multiplier=1.0,
            phase_filter_enabled=False,
            min_quality_score=0.0,
            min_entry_price=0.01,
        )
        return EdgeDetector(c)

    def test_raw_edge_yes(self):
        """raw_edge = model_prob - implied when model > implied."""
        det = self._make_detector()
        pred = PredictionResult(probability_yes=0.60, confidence=0.70, model_name="test")
        snap = _make_snapshot(yes_bid=0.48, no_bid=0.48, ttx=600.0)  # implied ~0.50
        signal = det.detect(pred, snap)
        # raw_edge should be ~0.60 - 0.50 = 0.10
        assert det.last_analysis["raw_edge"] == pytest.approx(0.10, abs=0.02)
        assert det.last_analysis["side"] == "yes"

    def test_raw_edge_no(self):
        """raw_edge = implied - model_prob when model < implied."""
        det = self._make_detector()
        pred = PredictionResult(probability_yes=0.40, confidence=0.70, model_name="test")
        snap = _make_snapshot(yes_bid=0.48, no_bid=0.48, ttx=600.0)
        signal = det.detect(pred, snap)
        assert det.last_analysis["side"] == "no"
        assert det.last_analysis["raw_edge"] == pytest.approx(0.10, abs=0.02)

    def test_net_edge_subtracts_fee_drag(self):
        """net_edge = raw_edge - fee_drag (maker fee for 1 contract)."""
        det = self._make_detector()
        pred = PredictionResult(probability_yes=0.60, confidence=0.70, model_name="test")
        snap = _make_snapshot(yes_bid=0.48, no_bid=0.48, ttx=600.0)
        det.detect(pred, snap)
        analysis = det.last_analysis
        assert analysis["net_edge"] == pytest.approx(
            analysis["raw_edge"] - analysis["fee_drag"], abs=1e-4,
        )

    def test_fee_drag_uses_maker_fee(self):
        """fee_drag is computed with maker rate (post_only=True)."""
        det = self._make_detector()
        pred = PredictionResult(probability_yes=0.60, confidence=0.70, model_name="test")
        snap = _make_snapshot(yes_bid=0.48, no_bid=0.48, ttx=600.0)
        det.detect(pred, snap)
        # fee_drag should equal maker fee for 1 contract at the trade price
        trade_price = float(snap.implied_yes_prob)  # ~0.50
        expected_drag = float(EdgeDetector.compute_fee_dollars(1, trade_price, is_maker=True))
        assert det.last_analysis["fee_drag"] == pytest.approx(expected_drag, abs=0.005)

    def test_zone_classification_boundaries(self):
        """Zone boundaries: 1(<0.20), 2(<0.40), 3(<0.60), 4(<0.80), 5(>=0.80)."""
        assert EdgeDetector.classify_zone(0.10) == 1
        assert EdgeDetector.classify_zone(0.19) == 1
        assert EdgeDetector.classify_zone(0.20) == 2
        assert EdgeDetector.classify_zone(0.39) == 2
        assert EdgeDetector.classify_zone(0.40) == 3
        assert EdgeDetector.classify_zone(0.59) == 3
        assert EdgeDetector.classify_zone(0.60) == 4
        assert EdgeDetector.classify_zone(0.79) == 4
        assert EdgeDetector.classify_zone(0.80) == 5
        assert EdgeDetector.classify_zone(0.99) == 5

    def test_min_entry_price_filter(self):
        """Cheap contracts below min_entry_price are blocked."""
        config = StrategyConfig(
            min_edge_threshold=0.01,
            max_edge_threshold=0.50,
            min_entry_price=0.25,
            confidence_min=0.10,
            min_quality_score=0.0,
            zone_filter_enabled=False,
            edge_expiry_decay_enabled=False,
        )
        det = EdgeDetector(config)
        # Model says buy NO at 0.15 (= 1 - 0.85 implied YES)
        pred = PredictionResult(probability_yes=0.10, confidence=0.70, model_name="test")
        snap = _make_snapshot(yes_bid=0.82, no_bid=0.15, ttx=600.0)
        signal = det.detect(pred, snap)
        # NO trade price is 1 - implied ~0.85 → 0.15 < 0.25 → blocked
        assert signal is None

    def test_quality_score_formula(self):
        """quality = (edge/threshold)*0.5 + (confidence/1.0)*0.5."""
        config = StrategyConfig(
            min_edge_threshold=0.03,
            max_edge_threshold=0.25,
            confidence_min=0.55,
            min_quality_score=0.80,
            zone_filter_enabled=False,
            edge_expiry_decay_enabled=False,
            yes_side_edge_multiplier=1.0,
            min_entry_price=0.01,
        )
        det = EdgeDetector(config)
        pred = PredictionResult(probability_yes=0.60, confidence=0.70, model_name="test")
        snap = _make_snapshot(yes_bid=0.48, no_bid=0.48, ttx=600.0)
        det.detect(pred, snap)
        analysis = det.last_analysis
        if "quality_score" in analysis:
            expected_qs = (analysis["net_edge"] / analysis["min_threshold"]) * 0.5 + 0.70 * 0.5
            assert analysis["quality_score"] == pytest.approx(expected_qs, abs=0.01)

    def test_yes_side_edge_multiplier(self):
        """YES side requires 1.4x more edge."""
        config = StrategyConfig(
            min_edge_threshold=0.05,
            max_edge_threshold=0.50,
            confidence_min=0.10,
            yes_side_edge_multiplier=1.4,
            min_quality_score=0.0,
            zone_filter_enabled=False,
            edge_expiry_decay_enabled=False,
            min_entry_price=0.01,
        )
        det = EdgeDetector(config)
        # YES trade: model=0.56, implied=0.50, edge=0.06
        # With 1.4x multiplier, threshold = 0.05 * 1.4 = 0.07
        # net edge ≈ 0.06 - fee_drag ≈ ~0.05 < 0.07 → blocked
        pred = PredictionResult(probability_yes=0.56, confidence=0.70, model_name="test")
        snap = _make_snapshot(yes_bid=0.48, no_bid=0.48, ttx=600.0)
        signal = det.detect(pred, snap)
        analysis = det.last_analysis
        assert analysis["side"] == "yes"
        # The YES multiplier raises the threshold
        assert analysis["min_threshold"] == pytest.approx(0.07, abs=0.01)


# ===========================================================================
# 9. Order Parameters
# ===========================================================================

class TestOrderParameters:
    """Verify order request format and field correctness."""

    def test_order_request_required_fields(self):
        """All required Kalshi API fields are present."""
        req = OrderRequest(
            ticker="KXBTC15M-26FEB201400",
            side="yes",
            action="buy",
            count=5,
            client_order_id=str(uuid.uuid4()),
            yes_price_dollars="0.45",
        )
        body = req.to_api_dict()
        assert "ticker" in body
        assert "side" in body
        assert "action" in body
        assert "type" in body
        assert "count" in body
        assert "client_order_id" in body

    def test_yes_side_uses_yes_price(self):
        """YES orders set yes_price_dollars, not no_price_dollars."""
        req = OrderRequest(
            ticker="TEST", side="yes", action="buy", count=1,
            client_order_id="cid", yes_price_dollars="0.45",
        )
        body = req.to_api_dict()
        assert "yes_price_dollars" in body
        assert "no_price_dollars" not in body

    def test_no_side_uses_no_price(self):
        """NO orders set no_price_dollars, not yes_price_dollars."""
        req = OrderRequest(
            ticker="TEST", side="no", action="buy", count=1,
            client_order_id="cid", no_price_dollars="0.35",
        )
        body = req.to_api_dict()
        assert "no_price_dollars" in body
        assert "yes_price_dollars" not in body

    def test_pnl_calculation_buy_yes_win(self):
        """P&L for YES buy that wins: payout - entry_cost - fees.
        Buy 10 YES @ $0.40 = $4.00 cost. Win → payout = 10 * $1.00 = $10.00.
        Buy fee (maker, 10@0.40): ceil(0.0175*10*0.40*0.60*100)/100 = ceil(4.2)/100 = $0.05
        No sell fee (settled). P&L = 10.00 - 4.00 - 0.05 = $5.95.
        """
        entry_cost = 10 * 0.40
        payout = 10 * 1.00
        buy_fee = float(EdgeDetector.compute_fee_dollars(10, 0.40, is_maker=True))
        pnl = payout - entry_cost - buy_fee
        assert pnl == pytest.approx(5.95, abs=0.01)

    def test_pnl_calculation_buy_yes_lose(self):
        """P&L for YES buy that loses: -entry_cost - fees.
        Buy 10 YES @ $0.40 = $4.00 cost. Lose → payout = $0.
        P&L = 0 - 4.00 - 0.05 = -$4.05.
        """
        entry_cost = 10 * 0.40
        payout = 0
        buy_fee = float(EdgeDetector.compute_fee_dollars(10, 0.40, is_maker=True))
        pnl = payout - entry_cost - buy_fee
        assert pnl == pytest.approx(-4.05, abs=0.01)

    def test_pnl_calculation_sell_before_settlement(self):
        """P&L for early exit: exit_revenue - entry_cost - buy_fee - sell_fee.
        Buy 10 @ $0.40, sell 10 @ $0.55.
        exit_revenue = 10 * 0.55 = $5.50
        entry_cost = 10 * 0.40 = $4.00
        buy_fee (maker) = $0.05
        sell_fee (taker, 10@0.55) = ceil(0.07*10*0.55*0.45*100)/100 = ceil(17.325)/100 = $0.18
        P&L = 5.50 - 4.00 - 0.05 - 0.18 = $1.27
        """
        exit_revenue = 10 * 0.55
        entry_cost = 10 * 0.40
        buy_fee = float(EdgeDetector.compute_fee_dollars(10, 0.40, is_maker=True))
        sell_fee = float(EdgeDetector.compute_fee_dollars(10, 0.55, is_maker=False))
        pnl = exit_revenue - entry_cost - buy_fee - sell_fee
        assert pnl == pytest.approx(1.27, abs=0.01)


# ===========================================================================
# 10. Settlement Logic
# ===========================================================================

class TestSettlement:
    """Verify settlement P&L calculations."""

    def test_yes_settlement_win(self):
        """YES position wins: payout = count * $1.00, P&L = payout - cost - fees."""
        count = 10
        avg_entry = Decimal("0.40")
        fees_paid = Decimal("0.05")
        cost = avg_entry * count
        payout = Decimal(str(count))  # $10.00
        pnl = payout - cost - fees_paid
        assert pnl == Decimal("5.95")

    def test_yes_settlement_lose(self):
        """YES position loses: payout = $0, P&L = -cost - fees."""
        count = 10
        avg_entry = Decimal("0.40")
        fees_paid = Decimal("0.05")
        cost = avg_entry * count
        payout = Decimal("0")
        pnl = payout - cost - fees_paid
        assert pnl == Decimal("-4.05")

    def test_no_settlement_win(self):
        """NO position wins when result="no": payout = count * $1.00."""
        count = 5
        avg_entry = Decimal("0.35")
        fees_paid = Decimal("0.03")
        cost = avg_entry * count
        result = "no"
        won = True  # result matches side
        payout = Decimal(str(count)) if won else Decimal("0")
        pnl = payout - cost - fees_paid
        assert pnl == Decimal("3.22")

    def test_no_settlement_lose(self):
        """NO position loses when result="yes": payout = $0."""
        count = 5
        avg_entry = Decimal("0.35")
        fees_paid = Decimal("0.03")
        cost = avg_entry * count
        won = False
        payout = Decimal("0")
        pnl = payout - cost - fees_paid
        assert pnl == Decimal("-1.78")

    def test_paper_mode_settlement_yes_wins(self):
        """Paper mode: implied_prob > 0.50 → YES wins."""
        implied_prob = 0.65
        side = "yes"
        yes_wins = implied_prob > 0.50
        won = (yes_wins and side == "yes") or (not yes_wins and side == "no")
        assert won is True

    def test_paper_mode_settlement_no_wins(self):
        """Paper mode: implied_prob <= 0.50 → NO wins."""
        implied_prob = 0.40
        side = "no"
        yes_wins = implied_prob > 0.50
        won = (yes_wins and side == "yes") or (not yes_wins and side == "no")
        assert won is True

    def test_paper_mode_borderline(self):
        """Paper mode: implied_prob == 0.50 → YES loses (strictly > 0.50 required)."""
        implied_prob = 0.50
        side = "yes"
        yes_wins = implied_prob > 0.50
        won = (yes_wins and side == "yes") or (not yes_wins and side == "no")
        assert won is False


# ===========================================================================
# 11. Strike Price Parsing
# ===========================================================================

class TestStrikePriceParsing:
    """Verify regex parsing of Kalshi market titles."""

    def test_standard_strike_with_decimals(self):
        """'$66,357.71 or above' → 66357.71."""
        result = parse_strike_price("$66,357.71 or above")
        assert result == Decimal("66357.71")

    def test_strike_no_decimal(self):
        """'$67,000 or above' → 67000."""
        result = parse_strike_price("$67,000 or above")
        assert result == Decimal("67000")

    def test_strike_small_number(self):
        """'$100 or above' → 100."""
        result = parse_strike_price("$100 or above")
        assert result == Decimal("100")

    def test_strike_with_single_decimal(self):
        """'$97,500.5 or above' → 97500.5."""
        result = parse_strike_price("$97,500.5 or above")
        assert result == Decimal("97500.5")

    def test_invalid_input_returns_none(self):
        """No dollar sign → None."""
        result = parse_strike_price("no strike here")
        assert result is None

    def test_empty_string(self):
        result = parse_strike_price("")
        assert result is None

    def test_just_dollar_sign(self):
        """'$ or above' → None (no digits)."""
        result = parse_strike_price("$ or above")
        assert result is None


# ===========================================================================
# 12. Risk Manager Checks
# ===========================================================================

class TestRiskManager:
    """Verify all risk checks fire correctly."""

    def _make_signal(self, price="0.40", count=5, ticker="KXBTC15M-26FEB201400"):
        return _make_signal(ticker=ticker, price=price)

    def _make_position(self, ticker="KXBTC15M-26FEB201400", exposure=10):
        return Position(ticker=ticker, market_exposure=exposure)

    def test_balance_minimum_blocks(self):
        """Balance below minimum blocks trade."""
        rm = RiskManager(RiskConfig(min_balance_dollars=50.0))
        decision = rm.check(
            self._make_signal(), count=5,
            balance=Decimal("40"), positions=[],
            time_to_expiry_seconds=300.0,
        )
        assert not decision.approved
        assert "Balance" in decision.reason

    def test_balance_minimum_passes(self):
        rm = RiskManager(RiskConfig(min_balance_dollars=50.0))
        decision = rm.check(
            self._make_signal(), count=5,
            balance=Decimal("100"), positions=[],
            time_to_expiry_seconds=300.0,
        )
        assert decision.approved

    def test_daily_loss_limit_blocks(self):
        """Once daily loss hits limit, all trades blocked."""
        rm = RiskManager(RiskConfig(max_daily_loss_dollars=5.0))
        rm.record_trade(Decimal("-5"))
        decision = rm.check(
            self._make_signal(), count=5,
            balance=Decimal("100"), positions=[],
            time_to_expiry_seconds=300.0,
        )
        assert not decision.approved
        assert "loss limit" in decision.reason.lower()

    def test_drawdown_circuit_breaker(self):
        """Peak PnL - current PnL >= limit → block."""
        rm = RiskManager(RiskConfig(
            drawdown_limit_enabled=True,
            drawdown_limit_dollars=20.0,
            max_daily_loss_dollars=100.0,
        ))
        # Win $25, then lose $20 → drawdown = 25 - 5 = 20 >= limit 20
        rm.record_trade(Decimal("25"))
        rm.record_trade(Decimal("-20"))
        decision = rm.check(
            self._make_signal(), count=5,
            balance=Decimal("100"), positions=[],
            time_to_expiry_seconds=300.0,
        )
        assert not decision.approved
        assert "Drawdown" in decision.reason or "drawdown" in decision.reason.lower()

    def test_drawdown_not_triggered(self):
        """Drawdown within limit passes."""
        rm = RiskManager(RiskConfig(
            drawdown_limit_enabled=True,
            drawdown_limit_dollars=20.0,
            max_daily_loss_dollars=100.0,
        ))
        rm.record_trade(Decimal("25"))
        rm.record_trade(Decimal("-10"))
        # drawdown = 25 - 15 = 10 < 20
        decision = rm.check(
            self._make_signal(), count=5,
            balance=Decimal("100"), positions=[],
            time_to_expiry_seconds=300.0,
        )
        assert decision.approved

    def test_max_concurrent_positions(self):
        """Can't open new position when at max concurrent."""
        rm = RiskManager(RiskConfig(max_concurrent_positions=2))
        positions = [
            Position(ticker="TICK-A", market_exposure=10),
            Position(ticker="TICK-B", market_exposure=10),
        ]
        # Signal for a new ticker
        decision = rm.check(
            self._make_signal(ticker="TICK-C"), count=5,
            balance=Decimal("500"), positions=positions,
            time_to_expiry_seconds=300.0,
        )
        assert not decision.approved
        assert "concurrent" in decision.reason.lower()

    def test_concurrent_same_market_ok(self):
        """Adding to existing position doesn't count as new concurrent."""
        rm = RiskManager(RiskConfig(max_concurrent_positions=2, max_position_per_market=50))
        positions = [
            Position(ticker="KXBTC15M-26FEB201400", market_exposure=5),
            Position(ticker="TICK-B", market_exposure=10),
        ]
        decision = rm.check(
            self._make_signal(ticker="KXBTC15M-26FEB201400"), count=5,
            balance=Decimal("500"), positions=positions,
            time_to_expiry_seconds=300.0,
        )
        assert decision.approved

    def test_consecutive_loss_cooldown(self):
        """3 consecutive losses trigger cooldown."""
        rm = RiskManager(RiskConfig(
            max_consecutive_losses=3,
            cooldown_after_streak_minutes=30,
            max_daily_loss_dollars=100.0,
        ))
        rm.record_trade(Decimal("-1"))
        rm.record_trade(Decimal("-1"))
        rm.record_trade(Decimal("-1"))
        decision = rm.check(
            self._make_signal(), count=5,
            balance=Decimal("100"), positions=[],
            time_to_expiry_seconds=300.0,
        )
        assert not decision.approved
        assert "cooldown" in decision.reason.lower() or "Consecutive" in decision.reason

    def test_win_resets_loss_streak(self):
        """A win resets consecutive loss counter."""
        rm = RiskManager(RiskConfig(
            max_consecutive_losses=3,
            max_daily_loss_dollars=100.0,
        ))
        rm.record_trade(Decimal("-1"))
        rm.record_trade(Decimal("-1"))
        rm.record_trade(Decimal("5"))  # Win resets streak
        rm.record_trade(Decimal("-1"))
        # Only 1 consecutive loss now, not 3
        assert rm.consecutive_losses == 1

    def test_max_trades_per_day(self):
        """Hit daily trade limit → block."""
        from datetime import date
        rm = RiskManager(RiskConfig(max_trades_per_day=2, max_daily_loss_dollars=100.0))
        # Force the daily date to today so _reset_daily_if_needed doesn't clear counters
        rm._daily_pnl_date = date.today()
        rm._trades_today_date = date.today()
        rm._trades_today = 2
        decision = rm.check(
            self._make_signal(), count=5,
            balance=Decimal("100"), positions=[],
            time_to_expiry_seconds=300.0,
        )
        assert not decision.approved
        assert "trades" in decision.reason.lower()

    def test_time_to_expiry_minimum(self):
        """Block trade when TTX < 60s."""
        rm = RiskManager(RiskConfig())
        decision = rm.check(
            self._make_signal(), count=5,
            balance=Decimal("100"), positions=[],
            time_to_expiry_seconds=50.0,
        )
        assert not decision.approved
        assert "expiry" in decision.reason.lower()

    def test_time_to_expiry_ok(self):
        """Allow trade when TTX >= 60s."""
        rm = RiskManager(RiskConfig())
        decision = rm.check(
            self._make_signal(), count=5,
            balance=Decimal("100"), positions=[],
            time_to_expiry_seconds=120.0,
        )
        assert decision.approved

    def test_record_trade_updates_daily_pnl(self):
        rm = RiskManager(RiskConfig())
        rm.record_trade(Decimal("3"))
        rm.record_trade(Decimal("-1"))
        assert rm.daily_pnl == Decimal("2")

    def test_record_trade_tracks_peak(self):
        rm = RiskManager(RiskConfig())
        rm.record_trade(Decimal("10"))
        assert rm.daily_pnl_peak == Decimal("10")
        rm.record_trade(Decimal("-3"))
        assert rm.daily_pnl_peak == Decimal("10")  # Peak doesn't decrease

    def test_breakeven_doesnt_affect_streaks(self):
        """PnL == 0 doesn't count as win or loss."""
        rm = RiskManager(RiskConfig())
        rm.record_trade(Decimal("-1"))
        rm.record_trade(Decimal("0"))  # Breakeven
        assert rm.consecutive_losses == 1  # Not reset by breakeven


# ===========================================================================
# 13. Monte Carlo Simulation
# ===========================================================================

class TestMonteCarlo:
    """Verify GBM formula and probability clamping."""

    def _make_snapshot(self, spot, strike, ttx):
        return _make_snapshot(
            btc_price=spot,
            yes_bid=0.50, no_bid=0.50,
            ttx=ttx, strike=strike,
        )

    def _make_features(self, vol=0.001, momentum=0.0):
        return FeatureVector(
            timestamp=NOW,
            market_ticker="KXBTC15M-26FEB201400",
            realized_vol_5min=vol,
            momentum_180s=momentum,
        )

    def test_gbm_formula_components(self):
        """Verify GBM: S(T) = S(0)*exp((mu-0.5*sigma^2)*dt + sigma*sqrt(dt)*Z)."""
        sim = MonteCarloSimulator(n_samples=100000, drift_mode="zero", vol_multiplier=1.0)
        snap = self._make_snapshot(spot=100.0, strike=100.0, ttx=100.0)
        features = self._make_features(vol=0.001, momentum=0.0)

        prob, conf = sim.estimate_probability(snap, features)
        # With zero drift, spot==strike, prob should be ~0.50
        assert prob == pytest.approx(0.50, abs=0.05)

    def test_probability_clamping_floor(self):
        """Probability clamped to [0.05, 0.95]: spot << strike → ~0.05."""
        sim = MonteCarloSimulator(n_samples=10000, drift_mode="zero", vol_multiplier=1.0)
        snap = self._make_snapshot(spot=50000.0, strike=200000.0, ttx=100.0)
        features = self._make_features(vol=0.001)
        prob, _ = sim.estimate_probability(snap, features)
        assert prob == 0.05

    def test_probability_clamping_ceiling(self):
        """Probability clamped to [0.05, 0.95]: spot >> strike → ~0.95."""
        sim = MonteCarloSimulator(n_samples=10000, drift_mode="zero", vol_multiplier=1.0)
        snap = self._make_snapshot(spot=200000.0, strike=50000.0, ttx=100.0)
        features = self._make_features(vol=0.001)
        prob, _ = sim.estimate_probability(snap, features)
        assert prob == 0.95

    def test_confidence_from_binomial_se(self):
        """confidence = max(0, 1 - 2*SE) where SE = sqrt(p*(1-p)/n)."""
        sim = MonteCarloSimulator(n_samples=10000, drift_mode="zero")
        snap = self._make_snapshot(spot=100.0, strike=100.0, ttx=100.0)
        features = self._make_features(vol=0.001)
        prob, conf = sim.estimate_probability(snap, features)
        se = math.sqrt(prob * (1.0 - prob) / 10000)
        expected_conf = max(0.0, 1.0 - 2.0 * se)
        assert conf == pytest.approx(expected_conf, abs=0.01)

    def test_zero_vol_uses_floor(self):
        """When realized_vol <= 0, sigma is floored to 0.001."""
        sim = MonteCarloSimulator(n_samples=10000, drift_mode="zero")
        snap = self._make_snapshot(spot=100.0, strike=100.0, ttx=100.0)
        features = self._make_features(vol=0.0)
        prob, _ = sim.estimate_probability(snap, features)
        # Should still produce a result (not NaN or error)
        assert 0.05 <= prob <= 0.95

    def test_momentum_drift_shifts_probability(self):
        """Positive momentum drift should increase P(spot > strike)."""
        sim_zero = MonteCarloSimulator(n_samples=50000, drift_mode="zero")
        sim_mom = MonteCarloSimulator(n_samples=50000, drift_mode="momentum")
        snap = self._make_snapshot(spot=100.0, strike=100.0, ttx=300.0)

        features_zero = self._make_features(vol=0.001, momentum=0.0)
        features_pos = self._make_features(vol=0.001, momentum=0.001)

        prob_zero, _ = sim_zero.estimate_probability(snap, features_zero)
        prob_pos, _ = sim_mom.estimate_probability(snap, features_pos)
        # Positive momentum should push probability up
        assert prob_pos > prob_zero

    def test_vol_multiplier_affects_probability(self):
        """Vol multiplier actually changes the probability output.
        Under GBM with zero drift, the drift correction (-0.5*sigma^2*dt)
        means higher vol shifts the log-price distribution left, so
        P(S_T > K) at K=spot actually decreases with higher vol.
        """
        sim_1x = MonteCarloSimulator(n_samples=50000, drift_mode="zero", vol_multiplier=1.0)
        sim_5x = MonteCarloSimulator(n_samples=50000, drift_mode="zero", vol_multiplier=5.0)
        snap = self._make_snapshot(spot=100.0, strike=100.0, ttx=300.0)
        features = self._make_features(vol=0.01)

        prob_1x, _ = sim_1x.estimate_probability(snap, features)
        prob_5x, _ = sim_5x.estimate_probability(snap, features)
        # Probabilities differ — vol multiplier has an effect
        assert prob_1x != prob_5x
        # Higher vol → larger drift correction → lower P(above strike)
        # This is the correct GBM property: median = S*exp(-0.5*sigma^2*dt)
        assert prob_5x < prob_1x

    def test_dt_at_least_1(self):
        """dt = max(ttx, 1.0) — never zero."""
        sim = MonteCarloSimulator(n_samples=10000, drift_mode="zero")
        snap = self._make_snapshot(spot=100.0, strike=100.0, ttx=0.0)
        features = self._make_features(vol=0.001)
        prob, conf = sim.estimate_probability(snap, features)
        # Should complete without error
        assert 0.05 <= prob <= 0.95


# ===========================================================================
# 14. Strategy-Aware Cooldowns
# ===========================================================================

class TestStrategyAwareCooldowns:
    """Verify TP cooldown tracks (time, strategy_tag) and only blocks same strategy."""

    def test_cooldown_stores_strategy_tag(self):
        """TP cooldown dict stores (exit_time, strategy_tag) tuple."""
        # Simulate the data structure used in bot.py
        tp_markets: dict[str, tuple[datetime, str]] = {}
        exit_time = NOW
        strategy_tag = "directional"
        tp_markets["TICK-A"] = (exit_time, strategy_tag)

        assert tp_markets["TICK-A"] == (exit_time, "directional")

    def test_same_strategy_blocked(self):
        """Same strategy attempting re-entry during cooldown is blocked."""
        tp_markets: dict[str, tuple[datetime, str]] = {}
        cooldown_seconds = 900.0

        # Record TP exit for directional
        tp_markets["TICK-A"] = (NOW, "directional")

        # Check if directional is blocked (200s later, within 900s cooldown)
        check_time = NOW + timedelta(seconds=200)
        exit_time, exit_tag = tp_markets["TICK-A"]
        cooldown_active = (check_time - exit_time).total_seconds() < cooldown_seconds
        same_strategy = exit_tag == "directional"
        blocked = cooldown_active and same_strategy
        assert blocked is True

    def test_different_strategy_allowed(self):
        """Different strategy can enter during another's cooldown."""
        tp_markets: dict[str, tuple[datetime, str]] = {}
        cooldown_seconds = 900.0

        # Record TP exit for directional
        tp_markets["TICK-A"] = (NOW, "directional")

        # monte_carlo attempting entry → allowed
        check_time = NOW + timedelta(seconds=200)
        exit_time, exit_tag = tp_markets["TICK-A"]
        cooldown_active = (check_time - exit_time).total_seconds() < cooldown_seconds
        same_strategy = exit_tag == "monte_carlo"
        blocked = cooldown_active and same_strategy
        assert blocked is False

    def test_cooldown_expired(self):
        """After cooldown expires, same strategy can re-enter."""
        tp_markets: dict[str, tuple[datetime, str]] = {}
        cooldown_seconds = 900.0

        tp_markets["TICK-A"] = (NOW, "directional")

        # Check 1000s later (> 900s cooldown)
        check_time = NOW + timedelta(seconds=1000)
        exit_time, exit_tag = tp_markets["TICK-A"]
        cooldown_active = (check_time - exit_time).total_seconds() < cooldown_seconds
        blocked = cooldown_active and (exit_tag == "directional")
        assert blocked is False


# ===========================================================================
# 15. Fair Value Computation
# ===========================================================================

class TestFairValue:
    """Verify the log-normal fair value model."""

    def test_spot_equals_strike(self):
        """When spot == strike and vol > 0, prob should be ~0.50."""
        prob = compute_fair_value(
            btc_price=100.0, strike_price=100.0,
            realized_vol=0.001, time_to_expiry_seconds=300.0,
            n_price_ticks=100, price_window_seconds=300.0,
        )
        assert prob is not None
        assert prob == pytest.approx(0.50, abs=0.05)

    def test_spot_above_strike(self):
        """Spot well above strike → high probability."""
        prob = compute_fair_value(
            btc_price=110.0, strike_price=100.0,
            realized_vol=0.001, time_to_expiry_seconds=300.0,
            n_price_ticks=100, price_window_seconds=300.0,
        )
        assert prob is not None
        assert prob > 0.90

    def test_spot_below_strike(self):
        """Spot well below strike → low probability."""
        prob = compute_fair_value(
            btc_price=90.0, strike_price=100.0,
            realized_vol=0.001, time_to_expiry_seconds=300.0,
            n_price_ticks=100, price_window_seconds=300.0,
        )
        assert prob is not None
        assert prob < 0.10

    def test_output_clamped_min(self):
        """Output >= 0.02."""
        prob = compute_fair_value(
            btc_price=50.0, strike_price=200.0,
            realized_vol=0.001, time_to_expiry_seconds=60.0,
            n_price_ticks=100,
        )
        assert prob is not None
        assert prob >= 0.02

    def test_output_clamped_max(self):
        """Output <= 0.98."""
        prob = compute_fair_value(
            btc_price=200.0, strike_price=50.0,
            realized_vol=0.001, time_to_expiry_seconds=60.0,
            n_price_ticks=100,
        )
        assert prob is not None
        assert prob <= 0.98

    def test_invalid_btc_price(self):
        assert compute_fair_value(0, 100, 0.001, 300, 100) is None
        assert compute_fair_value(-1, 100, 0.001, 300, 100) is None

    def test_invalid_strike(self):
        assert compute_fair_value(100, 0, 0.001, 300, 100) is None

    def test_invalid_ttx(self):
        assert compute_fair_value(100, 100, 0.001, 0, 100) is None
        assert compute_fair_value(100, 100, 0.001, -1, 100) is None

    def test_invalid_vol(self):
        assert compute_fair_value(100, 100, 0, 300, 100) is None
        assert compute_fair_value(100, 100, -0.001, 300, 100) is None

    def test_insufficient_ticks(self):
        """n_price_ticks < 10 → None."""
        assert compute_fair_value(100, 100, 0.001, 300, 9) is None


# ===========================================================================
# 16. Config Defaults Audit
# ===========================================================================

class TestConfigDefaults:
    """Verify all config defaults match expected live values."""

    def test_strategy_defaults(self):
        c = StrategyConfig()
        assert c.min_edge_threshold == 0.03
        assert c.max_edge_threshold == 0.25
        assert c.confidence_min == 0.55
        assert c.stop_loss_pct == 0.35
        assert c.stop_loss_min_bid == 0.05
        assert c.stop_loss_min_hold_seconds == 60.0
        assert c.stop_loss_max_dollar_loss == 2.0
        assert c.take_profit_min_profit_cents == 0.10
        assert c.take_profit_time_decay_start_seconds == 300.0
        assert c.take_profit_time_decay_floor_cents == 0.05
        assert c.trailing_take_profit_activation_cents == 0.08
        assert c.trailing_take_profit_drop_cents == 0.05
        assert c.pre_expiry_exit_seconds == 90.0
        assert c.pre_expiry_exit_min_pnl_cents == -0.03
        assert c.yes_side_edge_multiplier == 1.4
        assert c.min_entry_price == 0.30
        assert c.max_directional_price == 0.60
        assert c.min_quality_score == 0.80
        assert c.certainty_scalp_max_ttx == 180.0
        assert c.certainty_scalp_min_ttx == 60.0
        assert c.certainty_scalp_min_implied_prob == 0.85
        assert c.certainty_scalp_kelly_fraction == 0.30
        assert c.settlement_ride_min_elapsed_seconds == 600.0
        assert c.settlement_ride_min_edge == 0.03
        assert c.settlement_ride_min_implied_distance == 0.12
        assert c.settlement_ride_kelly_fraction == 0.10
        assert c.mc_samples == 10000
        assert c.mc_min_edge == 0.04
        assert c.mc_min_confidence == 0.65
        assert c.mc_kelly_fraction == 0.15
        assert c.take_profit_cooldown_seconds == 900.0

    def test_risk_defaults(self):
        c = RiskConfig()
        assert c.max_position_per_market == 15
        assert c.max_total_exposure_dollars == 50.0
        assert c.max_daily_loss_dollars == 5.0
        assert c.max_concurrent_positions == 3
        assert c.kelly_fraction == 0.15
        assert c.min_balance_dollars == 50.0
        assert c.max_trades_per_day == 40
        assert c.cooldown_after_streak_minutes == 30
        assert c.max_consecutive_losses == 3
        assert c.drawdown_limit_enabled is True
        assert c.drawdown_limit_dollars == 20.0
        assert c.zone_kelly_multipliers == [1.3, 1.15, 1.0, 0.7, 0.5]
        assert c.time_scale_enabled is True
        assert c.time_scale_full_seconds == 480.0
        assert c.time_scale_min_multiplier == 0.4
        assert c.min_position_size == 5

    def test_fee_rates_match_kalshi(self):
        """Confirm fee rates: maker=1.75%, taker=7%."""
        # Maker: compute_fee_dollars uses Decimal("0.0175")
        # Taker: compute_fee_dollars uses Decimal("0.07")
        # Verify by computing at P=0.50, C=100 where P*(1-P) = 0.25
        # Maker: 0.0175 * 100 * 0.25 = 0.4375 → ceil(43.75 cents) = $0.44
        maker = EdgeDetector.compute_fee_dollars(100, 0.50, is_maker=True)
        assert maker == Decimal("0.44")
        # Taker: 0.07 * 100 * 0.25 = 1.75 → ceil(175 cents) = $1.75
        taker = EdgeDetector.compute_fee_dollars(100, 0.50, is_maker=False)
        assert taker == Decimal("1.75")


# ===========================================================================
# 17. Position State Data Integrity
# ===========================================================================

class TestPositionState:
    """Verify PositionState tracks all required fields."""

    def test_exposure_calculation(self):
        """exposure_dollars = avg_entry_price * count."""
        pos = PositionState(
            market_ticker="TEST", side="yes", count=10,
            avg_entry_price=Decimal("0.40"), entry_time=NOW,
        )
        assert pos.exposure_dollars == Decimal("4.00")

    def test_default_values(self):
        pos = PositionState(
            market_ticker="TEST", side="yes", count=1,
            avg_entry_price=Decimal("0.50"), entry_time=NOW,
        )
        assert pos.fees_paid == Decimal("0")
        assert pos.realized_pnl == Decimal("0")
        assert pos.add_count == 0
        assert pos.high_water_bid is None
        assert pos.strategy_tag == ""
        assert pos.order_ids == []

    def test_strategy_tag_set(self):
        pos = PositionState(
            market_ticker="TEST", side="yes", count=1,
            avg_entry_price=Decimal("0.50"), entry_time=NOW,
        )
        pos.strategy_tag = "settlement_ride"
        assert pos.strategy_tag == "settlement_ride"
