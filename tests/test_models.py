"""Tests for data models, orderbook parsing, and market scanning."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models import (
    FeatureVector,
    Market,
    Orderbook,
    OrderbookLevel,
    OrderRequest,
)
from src.data.market_scanner import MarketScanner


class TestOrderbook:
    def test_best_yes_bid(self):
        ob = Orderbook(
            ticker="test",
            yes_levels=[
                OrderbookLevel(price_dollars=Decimal("0.55"), quantity=100),
                OrderbookLevel(price_dollars=Decimal("0.52"), quantity=200),
            ],
            no_levels=[],
            timestamp=datetime.now(timezone.utc),
        )
        assert ob.best_yes_bid == Decimal("0.55")

    def test_best_no_bid(self):
        ob = Orderbook(
            ticker="test",
            yes_levels=[],
            no_levels=[
                OrderbookLevel(price_dollars=Decimal("0.48"), quantity=100),
            ],
            timestamp=datetime.now(timezone.utc),
        )
        assert ob.best_no_bid == Decimal("0.48")

    def test_best_yes_ask(self):
        """YES ask = 1 - best NO bid."""
        ob = Orderbook(
            ticker="test",
            yes_levels=[],
            no_levels=[
                OrderbookLevel(price_dollars=Decimal("0.45"), quantity=100),
            ],
            timestamp=datetime.now(timezone.utc),
        )
        assert ob.best_yes_ask == Decimal("0.55")

    def test_implied_probability_from_midpoint(self):
        ob = Orderbook(
            ticker="test",
            yes_levels=[
                OrderbookLevel(price_dollars=Decimal("0.50"), quantity=100),
            ],
            no_levels=[
                OrderbookLevel(price_dollars=Decimal("0.48"), quantity=100),
            ],
            timestamp=datetime.now(timezone.utc),
        )
        # YES bid = 0.50, YES ask = 1 - 0.48 = 0.52
        # Midpoint = 0.51
        assert ob.implied_yes_prob == Decimal("0.51")

    def test_spread_computation(self):
        ob = Orderbook(
            ticker="test",
            yes_levels=[
                OrderbookLevel(price_dollars=Decimal("0.50"), quantity=100),
            ],
            no_levels=[
                OrderbookLevel(price_dollars=Decimal("0.48"), quantity=100),
            ],
            timestamp=datetime.now(timezone.utc),
        )
        # Spread = YES ask - YES bid = 0.52 - 0.50 = 0.02
        assert ob.spread == Decimal("0.02")

    def test_empty_orderbook(self):
        ob = Orderbook(
            ticker="test",
            timestamp=datetime.now(timezone.utc),
        )
        assert ob.best_yes_bid is None
        assert ob.best_no_bid is None
        assert ob.best_yes_ask is None
        assert ob.implied_yes_prob is None
        assert ob.spread is None
        assert ob.yes_bid_depth == 0
        assert ob.no_bid_depth == 0

    def test_depth_calculation(self):
        ob = Orderbook(
            ticker="test",
            yes_levels=[
                OrderbookLevel(price_dollars=Decimal("0.55"), quantity=100),
                OrderbookLevel(price_dollars=Decimal("0.52"), quantity=200),
            ],
            no_levels=[
                OrderbookLevel(price_dollars=Decimal("0.48"), quantity=150),
            ],
            timestamp=datetime.now(timezone.utc),
        )
        assert ob.yes_bid_depth == 300
        assert ob.no_bid_depth == 150


class TestOrderRequest:
    def test_to_api_dict_yes(self):
        req = OrderRequest(
            ticker="kxbtc15m-test",
            side="yes",
            action="buy",
            count=10,
            yes_price_dollars="0.55",
            client_order_id="test-uuid",
        )
        d = req.to_api_dict()
        assert d["ticker"] == "kxbtc15m-test"
        assert d["side"] == "yes"
        assert d["yes_price_dollars"] == "0.55"
        assert "no_price_dollars" not in d
        assert d["client_order_id"] == "test-uuid"

    def test_to_api_dict_no(self):
        req = OrderRequest(
            ticker="kxbtc15m-test",
            side="no",
            action="buy",
            count=5,
            no_price_dollars="0.48",
            client_order_id="test-uuid",
        )
        d = req.to_api_dict()
        assert d["side"] == "no"
        assert d["no_price_dollars"] == "0.48"
        assert "yes_price_dollars" not in d

    def test_post_only(self):
        req = OrderRequest(
            ticker="test",
            side="yes",
            action="buy",
            count=1,
            yes_price_dollars="0.50",
            client_order_id="test",
            post_only=True,
        )
        d = req.to_api_dict()
        assert d["post_only"] is True


class TestMarketScanner:
    def test_parse_ticker_expiry(self):
        """Parse kxbtc15m-26feb121345 -> datetime."""
        result = MarketScanner.parse_ticker_expiry("kxbtc15m-26feb121345")
        assert result is not None
        assert result.year == 2026
        assert result.month == 2
        assert result.day == 12
        assert result.hour == 13
        assert result.minute == 45

    def test_parse_ticker_different_months(self):
        for ticker, expected_month in [
            ("kxbtc15m-26jan051200", 1),
            ("kxbtc15m-26mar201530", 3),
            ("kxbtc15m-26dec311800", 12),
        ]:
            result = MarketScanner.parse_ticker_expiry(ticker)
            assert result is not None
            assert result.month == expected_month

    def test_parse_invalid_ticker(self):
        assert MarketScanner.parse_ticker_expiry("invalid") is None
        assert MarketScanner.parse_ticker_expiry("") is None

    def test_parse_ticker_case_insensitive(self):
        result = MarketScanner.parse_ticker_expiry("KXBTC15M-26FEB121345")
        assert result is not None
        assert result.month == 2


class TestFeatureVector:
    def test_to_array_length(self, sample_feature_vector):
        arr = sample_feature_vector.to_array()
        assert len(arr) == 23

    def test_feature_names_length(self):
        names = FeatureVector.feature_names()
        assert len(names) == 23

    def test_to_array_handles_defaults(self):
        fv = FeatureVector(
            timestamp=datetime.now(timezone.utc),
            market_ticker="test",
        )
        arr = fv.to_array()
        assert all(isinstance(v, float) for v in arr)
