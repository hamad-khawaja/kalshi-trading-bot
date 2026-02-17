"""Chainlink on-chain oracle price feed via raw eth_call (no web3.py dependency)."""

from __future__ import annotations

import asyncio
import struct
from decimal import Decimal

import aiohttp
import structlog

logger = structlog.get_logger()

# Chainlink price feed contracts on Ethereum mainnet
DEFAULT_CONTRACTS: dict[str, str] = {
    "BTC": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c",
    "ETH": "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419",
}

DEFAULT_RPC_URL = "https://eth.llamarpc.com"

# Function selector for latestRoundData() = keccak256("latestRoundData()")[:4]
LATEST_ROUND_DATA_SELECTOR = "0xfeaf968c"

# Backoff schedule in seconds
BACKOFF_SCHEDULE = [1, 2, 4, 8, 16, 30]


class ChainlinkReading:
    """Decoded result from latestRoundData()."""

    __slots__ = ("round_id", "price", "started_at", "updated_at", "answered_in_round")

    def __init__(
        self,
        round_id: int,
        price: Decimal,
        started_at: int,
        updated_at: int,
        answered_in_round: int,
    ):
        self.round_id = round_id
        self.price = price
        self.started_at = started_at
        self.updated_at = updated_at
        self.answered_in_round = answered_in_round


def decode_latest_round_data(hex_data: str) -> ChainlinkReading:
    """ABI-decode the 160-byte response from latestRoundData().

    Returns (roundId, answer, startedAt, updatedAt, answeredInRound).
    All are uint80/int256/uint256 packed as 5 x 32-byte words.
    Price = answer / 10^8 (Chainlink uses 8 decimals for USD feeds).
    """
    # Strip 0x prefix
    raw = hex_data[2:] if hex_data.startswith("0x") else hex_data

    if len(raw) < 320:  # 5 * 64 hex chars = 320
        raise ValueError(f"Response too short: {len(raw)} hex chars, expected 320")

    # Each word is 32 bytes = 64 hex chars
    round_id = int(raw[0:64], 16)
    # answer is int256 (signed) — handle two's complement for negative values
    answer_raw = int(raw[64:128], 16)
    if answer_raw >= (1 << 255):
        answer_raw -= 1 << 256
    started_at = int(raw[128:192], 16)
    updated_at = int(raw[192:256], 16)
    answered_in_round = int(raw[256:320], 16)

    price = Decimal(answer_raw) / Decimal(10**8)

    return ChainlinkReading(
        round_id=round_id,
        price=price,
        started_at=started_at,
        updated_at=updated_at,
        answered_in_round=answered_in_round,
    )


class ChainlinkFeed:
    """Polls a Chainlink on-chain price feed via JSON-RPC eth_call.

    Properties:
        latest_price: Most recent oracle price (Decimal) or None.
        latest_reading: Full ChainlinkReading or None.
        round_just_updated: True if the roundId changed on the last poll.
    """

    def __init__(
        self,
        symbol: str,
        contract_address: str | None = None,
        rpc_url: str | None = None,
        poll_interval: float = 10.0,
    ):
        self._symbol = symbol
        self._contract = contract_address or DEFAULT_CONTRACTS.get(symbol, "")
        self._rpc_url = rpc_url or DEFAULT_RPC_URL
        self._poll_interval = poll_interval

        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._running = False

        self._latest_reading: ChainlinkReading | None = None
        self._prev_round_id: int | None = None
        self._round_just_updated: bool = False
        self._consecutive_errors: int = 0

    @property
    def latest_price(self) -> Decimal | None:
        if self._latest_reading is None:
            return None
        return self._latest_reading.price

    @property
    def latest_reading(self) -> ChainlinkReading | None:
        return self._latest_reading

    @property
    def round_just_updated(self) -> bool:
        """True if the oracle's roundId changed on the most recent poll.

        Resets to False on the next poll where roundId stays the same.
        """
        return self._round_just_updated

    async def start(self) -> None:
        if self._running or not self._contract:
            return
        self._running = True
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "chainlink_feed_started",
            symbol=self._symbol,
            contract=self._contract,
            rpc=self._rpc_url,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._session:
            await self._session.close()
            self._session = None

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await self._poll_once()
                self._consecutive_errors = 0
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                self._consecutive_errors += 1
                backoff_idx = min(
                    self._consecutive_errors - 1, len(BACKOFF_SCHEDULE) - 1
                )
                backoff = BACKOFF_SCHEDULE[backoff_idx]
                logger.warning(
                    "chainlink_poll_error",
                    symbol=self._symbol,
                    consecutive_errors=self._consecutive_errors,
                    backoff=backoff,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)

    async def _poll_once(self) -> None:
        if not self._session:
            return

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [
                {"to": self._contract, "data": LATEST_ROUND_DATA_SELECTOR},
                "latest",
            ],
        }

        async with self._session.post(
            self._rpc_url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            result = await resp.json()

        if "error" in result:
            raise RuntimeError(f"RPC error: {result['error']}")

        hex_data = result.get("result", "")
        if not hex_data or hex_data == "0x":
            raise RuntimeError("Empty response from eth_call")

        reading = decode_latest_round_data(hex_data)

        # Detect round change
        if self._prev_round_id is not None and reading.round_id != self._prev_round_id:
            self._round_just_updated = True
            logger.info(
                "chainlink_round_update",
                symbol=self._symbol,
                old_round=self._prev_round_id,
                new_round=reading.round_id,
                price=float(reading.price),
            )
        else:
            self._round_just_updated = False

        self._prev_round_id = reading.round_id
        self._latest_reading = reading
