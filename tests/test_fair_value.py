"""Tests for statistical fair value pricing."""

import numpy as np
import pytest
from decimal import Decimal

from src.strategy.fair_value import (
    parse_strike_price,
    compute_fair_value,
    compute_fair_value_from_prices,
)


class TestParseStrikePrice:
    def test_basic_price(self):
        assert parse_strike_price("$66,357.71 or above") == Decimal("66357.71")

    def test_no_cents(self):
        assert parse_strike_price("$97,500 or above") == Decimal("97500")

    def test_large_price(self):
        assert parse_strike_price("$100,000.00 or above") == Decimal("100000.00")

    def test_no_comma(self):
        assert parse_strike_price("$500.50 or above") == Decimal("500.50")

    def test_no_match(self):
        assert parse_strike_price("some random text") is None

    def test_empty(self):
        assert parse_strike_price("") is None


class TestComputeFairValue:
    def test_at_the_money(self):
        """When BTC is exactly at strike, fair value should be ~0.50."""
        fv = compute_fair_value(
            spot_price=66000.0,
            strike_price=66000.0,
            realized_vol=0.0001,
            time_to_expiry_seconds=600,
            n_price_ticks=3000,
        )
        assert fv is not None
        assert 0.45 < fv < 0.55

    def test_deep_in_the_money(self):
        """When BTC is well above strike, fair value should be high."""
        fv = compute_fair_value(
            spot_price=67000.0,
            strike_price=66000.0,
            realized_vol=0.0001,
            time_to_expiry_seconds=300,
            n_price_ticks=3000,
        )
        assert fv is not None
        assert fv > 0.80

    def test_deep_out_of_the_money(self):
        """When BTC is well below strike, fair value should be low."""
        fv = compute_fair_value(
            spot_price=65000.0,
            strike_price=66000.0,
            realized_vol=0.0001,
            time_to_expiry_seconds=300,
            n_price_ticks=3000,
        )
        assert fv is not None
        assert fv < 0.20

    def test_more_time_increases_uncertainty(self):
        """With more time, ATM option should stay near 0.50 but OTM gets closer to 0.50."""
        fv_short = compute_fair_value(
            spot_price=65500.0,
            strike_price=66000.0,
            realized_vol=0.0001,
            time_to_expiry_seconds=60,
            n_price_ticks=600,
        )
        fv_long = compute_fair_value(
            spot_price=65500.0,
            strike_price=66000.0,
            realized_vol=0.0001,
            time_to_expiry_seconds=900,
            n_price_ticks=9000,
        )
        assert fv_short is not None and fv_long is not None
        # With more time, OTM option is closer to 0.50
        assert fv_long > fv_short

    def test_higher_vol_increases_uncertainty(self):
        """Higher vol should push OTM option closer to 0.50."""
        fv_low_vol = compute_fair_value(
            spot_price=65500.0,
            strike_price=66000.0,
            realized_vol=0.00005,
            time_to_expiry_seconds=300,
            n_price_ticks=3000,
        )
        fv_high_vol = compute_fair_value(
            spot_price=65500.0,
            strike_price=66000.0,
            realized_vol=0.0005,
            time_to_expiry_seconds=300,
            n_price_ticks=3000,
        )
        assert fv_low_vol is not None and fv_high_vol is not None
        # Higher vol gives more chance of reaching strike
        assert fv_high_vol > fv_low_vol

    def test_clamped_output(self):
        """Output should be clamped to [0.02, 0.98]."""
        # Very deep ITM
        fv = compute_fair_value(
            spot_price=70000.0,
            strike_price=60000.0,
            realized_vol=0.00001,
            time_to_expiry_seconds=60,
            n_price_ticks=600,
        )
        assert fv is not None
        assert fv <= 0.98

    def test_invalid_inputs(self):
        assert compute_fair_value(0, 66000, 0.001, 300, 3000) is None
        assert compute_fair_value(66000, 0, 0.001, 300, 3000) is None
        assert compute_fair_value(66000, 66000, 0, 300, 3000) is None
        assert compute_fair_value(66000, 66000, 0.001, 0, 3000) is None
        assert compute_fair_value(66000, 66000, 0.001, 300, 5) is None


class TestComputeFairValueFromPrices:
    def test_with_trending_prices(self):
        """Trending up above strike should give high fair value."""
        # Simulate BTC trending up from 65800 to 66200 over 300s
        prices = np.linspace(65800, 66200, 3000)
        # Add some noise
        rng = np.random.default_rng(42)
        prices = prices + rng.normal(0, 5, len(prices))

        fv = compute_fair_value_from_prices(
            spot_price=66200.0,
            strike_price=66000.0,
            price_history=prices,
            time_to_expiry_seconds=600,
        )
        assert fv is not None
        assert fv > 0.50  # Above strike, should be > 50%

    def test_insufficient_data(self):
        prices = np.array([66000.0] * 10)
        fv = compute_fair_value_from_prices(
            spot_price=66000.0,
            strike_price=66000.0,
            price_history=prices,
            time_to_expiry_seconds=300,
        )
        assert fv is None  # Not enough data points
