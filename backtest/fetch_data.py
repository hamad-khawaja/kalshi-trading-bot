"""Fetch historical BTC 1-minute candles from Binance REST API.

Uses GET https://api.binance.com/api/v3/klines (free, no auth, up to 1000 per request).
Caches per-day CSVs in data/candles/ to avoid re-fetching.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import pandas as pd

# Try Binance US first (works from US IPs), fall back to global
BINANCE_URLS = [
    "https://api.binance.us/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]
CACHE_DIR = Path("data/candles")
MS_PER_MINUTE = 60_000
MS_PER_DAY = 86_400_000
CANDLES_PER_REQUEST = 1000
REQUEST_DELAY_S = 0.25  # 250ms between requests to respect rate limits

# Module-level state for which URL works
_working_url: str | None = None


async def fetch_candles(
    days: int,
    symbol: str = "BTCUSDT",
    verbose: bool = True,
) -> pd.DataFrame:
    """Fetch 1-minute candles for the last N days.

    Returns DataFrame with columns:
        timestamp, open, high, low, close, volume, taker_buy_volume

    Caches per-day CSVs in data/candles/ so subsequent runs are instant.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    all_frames: list[pd.DataFrame] = []

    # Determine which days we need
    for day_offset in range(days, 0, -1):
        day_start_ms = int(
            (now.replace(hour=0, minute=0, second=0, microsecond=0)).timestamp() * 1000
        ) - day_offset * MS_PER_DAY
        day_end_ms = day_start_ms + MS_PER_DAY - 1
        day_date = datetime.fromtimestamp(day_start_ms / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d"
        )

        cache_file = CACHE_DIR / f"{symbol}_{day_date}.csv"
        if cache_file.exists():
            df = pd.read_csv(cache_file, parse_dates=["timestamp"])
            if len(df) > 0:
                all_frames.append(df)
                continue

        # Fetch this day from Binance
        if verbose:
            print(f"  Fetching {symbol} candles for {day_date}...")

        day_candles = await _fetch_day(symbol, day_start_ms, day_end_ms)
        if day_candles:
            df = _candles_to_dataframe(day_candles)
            df.to_csv(cache_file, index=False)
            all_frames.append(df)

    if not all_frames:
        return pd.DataFrame(
            columns=[
                "timestamp", "open", "high", "low", "close",
                "volume", "taker_buy_volume",
            ]
        )

    result = pd.concat(all_frames, ignore_index=True)
    result.sort_values("timestamp", inplace=True)
    result.reset_index(drop=True, inplace=True)

    # Also fetch today's partial data (don't cache — still in progress)
    today_start_ms = int(
        now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
    )
    today_end_ms = int(now.timestamp() * 1000)

    if today_end_ms > today_start_ms + MS_PER_MINUTE:
        if verbose:
            print("  Fetching today's partial candles...")
        today_candles = await _fetch_day(symbol, today_start_ms, today_end_ms)
        if today_candles:
            today_df = _candles_to_dataframe(today_candles)
            result = pd.concat([result, today_df], ignore_index=True)
            result.sort_values("timestamp", inplace=True)
            result.reset_index(drop=True, inplace=True)

    if verbose:
        print(
            f"  Total: {len(result)} candles "
            f"({result['timestamp'].min()} to {result['timestamp'].max()})"
        )

    return result


async def _resolve_url(session: aiohttp.ClientSession, symbol: str) -> str | None:
    """Find a working Binance API URL (US vs global)."""
    global _working_url
    if _working_url is not None:
        return _working_url

    for url in BINANCE_URLS:
        try:
            params = {"symbol": symbol, "interval": "1m", "limit": 1}
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    _working_url = url
                    return url
        except Exception:
            continue
    return None


async def _fetch_day(
    symbol: str, start_ms: int, end_ms: int
) -> list[list]:
    """Fetch all 1m candles for a time range, paginating as needed."""
    all_candles: list[list] = []
    current_start = start_ms

    async with aiohttp.ClientSession() as session:
        base_url = await _resolve_url(session, symbol)
        if base_url is None:
            print("  Warning: No working Binance API endpoint found")
            return []

        while current_start < end_ms:
            params = {
                "symbol": symbol,
                "interval": "1m",
                "startTime": current_start,
                "endTime": end_ms,
                "limit": CANDLES_PER_REQUEST,
            }

            try:
                async with session.get(
                    base_url, params=params, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 429:
                        # Rate limited — back off
                        await asyncio.sleep(5)
                        continue
                    resp.raise_for_status()
                    candles = await resp.json()
            except Exception as e:
                print(f"  Warning: Binance API error: {e}")
                break

            if not candles:
                break

            all_candles.extend(candles)

            # Move past the last candle's open time
            last_open_ms = candles[-1][0]
            current_start = last_open_ms + MS_PER_MINUTE

            if len(candles) < CANDLES_PER_REQUEST:
                break

            await asyncio.sleep(REQUEST_DELAY_S)

    return all_candles


def _candles_to_dataframe(candles: list[list]) -> pd.DataFrame:
    """Convert Binance kline response to DataFrame.

    Binance kline format:
    [0] Open time (ms), [1] Open, [2] High, [3] Low, [4] Close,
    [5] Volume, [6] Close time, [7] Quote asset volume,
    [8] Number of trades, [9] Taker buy base asset volume,
    [10] Taker buy quote asset volume, [11] Ignore
    """
    rows = []
    for c in candles:
        rows.append(
            {
                "timestamp": pd.Timestamp(c[0], unit="ms", tz="UTC"),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
                "taker_buy_volume": float(c[9]),
            }
        )
    return pd.DataFrame(rows)
