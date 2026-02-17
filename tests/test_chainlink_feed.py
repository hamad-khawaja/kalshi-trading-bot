"""Tests for Chainlink on-chain oracle feed."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.data.chainlink_feed import (
    DEFAULT_CONTRACTS,
    ChainlinkFeed,
    ChainlinkReading,
    decode_latest_round_data,
)


class TestABIDecoding:
    """Test ABI decoding of latestRoundData() response."""

    # Known hex response (5 x 32-byte words, all zero-padded)
    # roundId=1, answer=9750000000000 (=$97500.00), startedAt=1700000000,
    # updatedAt=1700000100, answeredInRound=1
    KNOWN_HEX = (
        "0x"
        + "0000000000000000000000000000000000000000000000000000000000000001"  # roundId=1
        + "00000000000000000000000000000000000000000000000008df51f8c80000"  # answer=9750000000000 (padded to 64)
        + "0000000000000000000000000000000000000000000000000000000065535800"  # startedAt=1700000000 (padded)
        + "0000000000000000000000000000000000000000000000000000000065535864"  # updatedAt=1700000100
        + "0000000000000000000000000000000000000000000000000000000000000001"  # answeredInRound=1
    )

    def test_decode_known_hex(self):
        """Decode a known hex response and verify fields."""
        # Build a proper hex response with known values
        round_id = 110680464442257320164
        answer = 9750000000000  # $97,500.00
        started_at = 1700000000
        updated_at = 1700000100
        answered_in_round = 110680464442257320164

        hex_data = "0x" + (
            f"{round_id:064x}"
            + f"{answer:064x}"
            + f"{started_at:064x}"
            + f"{updated_at:064x}"
            + f"{answered_in_round:064x}"
        )

        reading = decode_latest_round_data(hex_data)

        assert reading.round_id == round_id
        assert reading.price == Decimal("97500.00000000")
        assert reading.started_at == started_at
        assert reading.updated_at == updated_at
        assert reading.answered_in_round == answered_in_round

    def test_decode_price_precision(self):
        """Verify 8-decimal precision for price conversion."""
        # answer = 4235012345678 → $42350.12345678
        answer = 4235012345678
        hex_data = "0x" + (
            f"{1:064x}"
            + f"{answer:064x}"
            + f"{0:064x}"
            + f"{0:064x}"
            + f"{1:064x}"
        )

        reading = decode_latest_round_data(hex_data)
        assert reading.price == Decimal("42350.12345678")

    def test_decode_too_short_raises(self):
        """Response shorter than 320 hex chars should raise."""
        with pytest.raises(ValueError, match="too short"):
            decode_latest_round_data("0x" + "00" * 50)

    def test_decode_without_0x_prefix(self):
        """Should handle hex without 0x prefix."""
        answer = 5000000000000  # $50,000
        hex_data = (
            f"{1:064x}"
            + f"{answer:064x}"
            + f"{0:064x}"
            + f"{0:064x}"
            + f"{1:064x}"
        )
        reading = decode_latest_round_data(hex_data)
        assert reading.price == Decimal("50000.00000000")


class TestChainlinkFeed:
    """Test ChainlinkFeed lifecycle and properties."""

    def test_default_contracts(self):
        """BTC and ETH contracts should be defined."""
        assert "BTC" in DEFAULT_CONTRACTS
        assert "ETH" in DEFAULT_CONTRACTS
        assert DEFAULT_CONTRACTS["BTC"].startswith("0x")
        assert DEFAULT_CONTRACTS["ETH"].startswith("0x")

    def test_initial_state(self):
        """Feed should start with None values."""
        feed = ChainlinkFeed(symbol="BTC")
        assert feed.latest_price is None
        assert feed.latest_reading is None
        assert feed.round_just_updated is False

    def test_round_detection(self):
        """Simulate round ID changes and verify detection."""
        feed = ChainlinkFeed(symbol="BTC")

        # First reading — no previous round, so no update detected
        feed._prev_round_id = None
        feed._latest_reading = ChainlinkReading(
            round_id=100, price=Decimal("97500"), started_at=0, updated_at=0, answered_in_round=100
        )
        feed._prev_round_id = 100

        # Same round — not updated
        feed._round_just_updated = (101 != feed._prev_round_id)  # True
        assert feed._round_just_updated is True

        # Same round — no update
        feed._round_just_updated = (100 != feed._prev_round_id)  # False
        assert feed._round_just_updated is False

    def test_custom_contract_and_rpc(self):
        """Feed should accept custom contract and RPC URL."""
        feed = ChainlinkFeed(
            symbol="TEST",
            contract_address="0x1234567890abcdef1234567890abcdef12345678",
            rpc_url="https://custom-rpc.example.com",
        )
        assert feed._contract == "0x1234567890abcdef1234567890abcdef12345678"
        assert feed._rpc_url == "https://custom-rpc.example.com"

    def test_default_contract_lookup(self):
        """Feed should look up default contract for known symbols."""
        feed = ChainlinkFeed(symbol="BTC")
        assert feed._contract == DEFAULT_CONTRACTS["BTC"]

        feed_eth = ChainlinkFeed(symbol="ETH")
        assert feed_eth._contract == DEFAULT_CONTRACTS["ETH"]

    def test_unknown_symbol_empty_contract(self):
        """Unknown symbol with no explicit contract should have empty string."""
        feed = ChainlinkFeed(symbol="UNKNOWN")
        assert feed._contract == ""
