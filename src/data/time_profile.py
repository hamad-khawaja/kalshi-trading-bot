"""Time-based trading profiles using historical BTC volatility/volume data."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import aiohttp
import structlog

logger = structlog.get_logger()


class SessionType(Enum):
    """Trading session classification based on UTC hour."""

    ASIA = "asia"
    EUROPE = "europe"
    US = "us"
    OVERLAP_EU_US = "overlap_eu_us"


@dataclass
class HourlyProfile:
    """Per-hour volatility and volume statistics."""

    hour: int
    avg_volatility: float = 0.0
    avg_volume: float = 0.0
    vol_ratio: float = 1.0  # ratio vs. 24h mean


# Weight multipliers by session: {signal_name: multiplier}
_SESSION_WEIGHTS: dict[SessionType, dict[str, float]] = {
    SessionType.ASIA: {
        "momentum": 0.7,
        "technical": 1.0,
        "orderflow": 0.8,
        "mean_reversion": 1.4,
        "funding": 1.0,
        "time_decay": 1.0,
    },
    SessionType.EUROPE: {
        "momentum": 1.0,
        "technical": 1.0,
        "orderflow": 1.0,
        "mean_reversion": 1.0,
        "funding": 1.0,
        "time_decay": 1.0,
    },
    SessionType.US: {
        "momentum": 1.5,
        "technical": 1.2,
        "orderflow": 1.3,
        "mean_reversion": 0.7,
        "funding": 1.0,
        "time_decay": 1.0,
    },
    SessionType.OVERLAP_EU_US: {
        "momentum": 1.5,
        "technical": 1.2,
        "orderflow": 1.3,
        "mean_reversion": 0.7,
        "funding": 1.0,
        "time_decay": 1.0,
    },
}

# Edge threshold multipliers: lower = easier entry during high-vol
_EDGE_THRESHOLD_MULTIPLIERS: dict[SessionType, float] = {
    SessionType.ASIA: 1.2,
    SessionType.EUROPE: 1.0,
    SessionType.US: 0.8,
    SessionType.OVERLAP_EU_US: 0.8,
}

# Sessions where market-making should be disabled (high vol / overlap)
_MM_DISABLED_SESSIONS: set[SessionType] = {SessionType.OVERLAP_EU_US}

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


class TimeProfiler:
    """Builds per-hour BTC volatility/volume profiles from Binance kline data."""

    def __init__(self, lookback_days: int = 30) -> None:
        self._lookback_days = lookback_days
        self._profiles: dict[int, HourlyProfile] = {}
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def profiles(self) -> dict[int, HourlyProfile]:
        return self._profiles

    async def fetch_hourly_klines(self, symbol: str = "BTCUSDT") -> None:
        """Fetch hourly klines from Binance and build per-hour profiles."""
        limit = self._lookback_days * 24
        params = {
            "symbol": symbol,
            "interval": "1h",
            "limit": min(limit, 1000),
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    BINANCE_KLINES_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        logger.warning("time_profile_fetch_failed", status=resp.status)
                        return
                    klines = await resp.json()
        except Exception:
            logger.exception("time_profile_fetch_error")
            return

        self._build_profiles(klines)

    def _build_profiles(self, klines: list) -> None:
        """Compute per-hour avg volatility and volume from raw kline data.

        Kline format: [open_time, open, high, low, close, volume, ...]
        """
        hour_vols: dict[int, list[float]] = {h: [] for h in range(24)}
        hour_volumes: dict[int, list[float]] = {h: [] for h in range(24)}

        for k in klines:
            try:
                open_time_ms = int(k[0])
                open_price = float(k[1])
                high = float(k[2])
                low = float(k[3])
                volume = float(k[5])

                dt = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
                hour = dt.hour

                volatility = (high - low) / open_price if open_price > 0 else 0.0
                hour_vols[hour].append(volatility)
                hour_volumes[hour].append(volume)
            except (IndexError, ValueError, TypeError):
                continue

        # Compute averages
        profiles: dict[int, HourlyProfile] = {}
        all_vols = []
        for h in range(24):
            avg_vol = sum(hour_vols[h]) / len(hour_vols[h]) if hour_vols[h] else 0.0
            avg_volume = sum(hour_volumes[h]) / len(hour_volumes[h]) if hour_volumes[h] else 0.0
            profiles[h] = HourlyProfile(hour=h, avg_volatility=avg_vol, avg_volume=avg_volume)
            all_vols.append(avg_vol)

        # Compute vol_ratio (hour_vol / mean_vol across all hours)
        mean_vol = sum(all_vols) / len(all_vols) if all_vols else 1.0
        if mean_vol > 0:
            for h in range(24):
                profiles[h].vol_ratio = profiles[h].avg_volatility / mean_vol

        self._profiles = profiles
        self._loaded = True
        logger.info("time_profile_loaded", hour_count=len(profiles))

    @staticmethod
    def get_current_session() -> SessionType:
        """Classify the current UTC hour into a trading session."""
        hour = datetime.now(timezone.utc).hour
        return TimeProfiler.classify_hour(hour)

    @staticmethod
    def classify_hour(hour: int) -> SessionType:
        """Classify a UTC hour into a trading session."""
        if 13 <= hour < 16:
            return SessionType.OVERLAP_EU_US
        if 16 <= hour < 21:
            return SessionType.US
        if 8 <= hour < 13:
            return SessionType.EUROPE
        # 21-00 and 00-08 treated as Asia
        return SessionType.ASIA

    @staticmethod
    def get_weight_multipliers(session: SessionType) -> dict[str, float]:
        """Return signal weight multipliers for the given session."""
        return _SESSION_WEIGHTS.get(session, _SESSION_WEIGHTS[SessionType.EUROPE]).copy()

    @staticmethod
    def get_edge_threshold_multiplier(session: SessionType) -> float:
        """Return edge threshold multiplier for the given session.

        < 1.0 means lower threshold (easier entry) for high-vol sessions.
        > 1.0 means higher threshold for low-vol sessions.
        """
        return _EDGE_THRESHOLD_MULTIPLIERS.get(session, 1.0)

    @staticmethod
    def should_market_make(session: SessionType) -> bool:
        """Return whether market-making should be active for this session."""
        return session not in _MM_DISABLED_SESSIONS
