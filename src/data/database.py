"""Async SQLite database for persistence."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import aiosqlite
import structlog

from src.data.models import CompletedTrade, FeatureVector, PredictionResult

logger = structlog.get_logger()


class Database:
    """Async SQLite database for trade logs, predictions, and tick data.

    Tables:
    - trades: completed trade records with P&L
    - predictions: model predictions with features for analysis
    - outcomes: market settlement results
    - ticks: raw BTC price ticks for backtesting
    - daily_summary: daily P&L summary
    """

    def __init__(self, path: str):
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open database and create tables if needed."""
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._create_tables()
        logger.info("database_connected", path=self._path)

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_tables(self) -> None:
        """Create all tables if they don't exist."""
        assert self._db is not None

        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                market_ticker TEXT NOT NULL,
                side TEXT NOT NULL,
                action TEXT NOT NULL,
                count INTEGER NOT NULL,
                price_dollars REAL NOT NULL,
                fees_dollars REAL NOT NULL DEFAULT 0,
                pnl_dollars REAL,
                model_probability REAL,
                implied_probability REAL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                strategy_tag TEXT NOT NULL DEFAULT 'directional',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_ticker TEXT NOT NULL,
                model_probability REAL NOT NULL,
                implied_probability REAL NOT NULL,
                edge REAL NOT NULL,
                confidence REAL NOT NULL,
                model_name TEXT NOT NULL,
                features_json TEXT,
                timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_ticker TEXT NOT NULL UNIQUE,
                btc_price_at_open REAL,
                btc_price_at_close REAL,
                result TEXT,
                settlement_time TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                btc_price REAL NOT NULL,
                btc_volume REAL,
                kalshi_yes_bid REAL,
                kalshi_yes_ask REAL,
                kalshi_spread REAL,
                market_ticker TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS daily_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                trades_count INTEGER NOT NULL DEFAULT 0,
                win_count INTEGER NOT NULL DEFAULT 0,
                total_pnl REAL NOT NULL DEFAULT 0,
                max_drawdown REAL NOT NULL DEFAULT 0,
                starting_balance REAL,
                ending_balance REAL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(market_ticker);
            CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(entry_time);
            CREATE INDEX IF NOT EXISTS idx_predictions_ticker ON predictions(market_ticker);
            CREATE INDEX IF NOT EXISTS idx_ticks_time ON ticks(timestamp);
            CREATE INDEX IF NOT EXISTS idx_outcomes_ticker ON outcomes(market_ticker);
        """)
        await self._db.commit()
        # Migrate: add new columns to existing databases
        await self._migrate_strategy_tag()
        await self._migrate_market_volume()

    async def _migrate_strategy_tag(self) -> None:
        """Add strategy_tag column if missing (existing databases)."""
        assert self._db is not None
        cursor = await self._db.execute("PRAGMA table_info(trades)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "strategy_tag" not in columns:
            await self._db.execute(
                "ALTER TABLE trades ADD COLUMN strategy_tag TEXT NOT NULL DEFAULT 'directional'"
            )
            await self._db.commit()
            logger.info("migrated_strategy_tag_column")

    async def _migrate_market_volume(self) -> None:
        """Add market_volume column if missing (existing databases)."""
        assert self._db is not None
        cursor = await self._db.execute("PRAGMA table_info(trades)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "market_volume" not in columns:
            await self._db.execute(
                "ALTER TABLE trades ADD COLUMN market_volume INTEGER"
            )
            await self._db.commit()
            logger.info("migrated_market_volume_column")

    async def flush(self) -> None:
        """Commit any pending writes in a single batch."""
        if self._db:
            await self._db.commit()

    async def insert_trade(self, trade: CompletedTrade) -> None:
        """Insert a completed trade record."""
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO trades
            (order_id, market_ticker, side, action, count, price_dollars,
             fees_dollars, pnl_dollars, model_probability, implied_probability,
             entry_time, exit_time, strategy_tag, market_volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.order_id,
                trade.market_ticker,
                trade.side,
                trade.action,
                trade.count,
                float(trade.price_dollars),
                float(trade.fees_dollars),
                float(trade.pnl_dollars) if trade.pnl_dollars is not None else None,
                trade.model_probability,
                trade.implied_probability,
                trade.entry_time.isoformat(),
                trade.exit_time.isoformat() if trade.exit_time else None,
                trade.strategy_tag,
                trade.market_volume,
            ),
        )

    async def insert_prediction(
        self,
        market_ticker: str,
        prediction: PredictionResult,
        implied_prob: float,
        edge: float,
    ) -> None:
        """Insert a prediction record for analysis."""
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO predictions
            (market_ticker, model_probability, implied_probability, edge,
             confidence, model_name, features_json, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                market_ticker,
                prediction.probability_yes,
                implied_prob,
                edge,
                prediction.confidence,
                prediction.model_name,
                json.dumps(prediction.features_used),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    async def insert_outcome(
        self,
        market_ticker: str,
        btc_open: float | None,
        btc_close: float | None,
        result: str | None,
        settlement_time: datetime | None,
    ) -> None:
        """Insert or update market outcome."""
        assert self._db is not None
        await self._db.execute(
            """INSERT OR REPLACE INTO outcomes
            (market_ticker, btc_price_at_open, btc_price_at_close,
             result, settlement_time)
            VALUES (?, ?, ?, ?, ?)""",
            (
                market_ticker,
                btc_open,
                btc_close,
                result,
                settlement_time.isoformat() if settlement_time else None,
            ),
        )

    async def insert_tick(
        self,
        timestamp: datetime,
        btc_price: float,
        btc_volume: float | None = None,
        kalshi_yes_bid: float | None = None,
        kalshi_yes_ask: float | None = None,
        kalshi_spread: float | None = None,
        market_ticker: str | None = None,
    ) -> None:
        """Insert a raw tick for backtesting data collection."""
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO ticks
            (timestamp, btc_price, btc_volume, kalshi_yes_bid,
             kalshi_yes_ask, kalshi_spread, market_ticker)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp.isoformat(),
                btc_price,
                btc_volume,
                kalshi_yes_bid,
                kalshi_yes_ask,
                kalshi_spread,
                market_ticker,
            ),
        )

    async def get_daily_pnl(self, target_date: date) -> float:
        """Get total P&L for a specific date."""
        assert self._db is not None
        date_str = target_date.isoformat()
        cursor = await self._db.execute(
            """SELECT COALESCE(SUM(pnl_dollars), 0) FROM trades
            WHERE date(entry_time) = ?""",
            (date_str,),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else 0.0

    async def get_trade_count_today(self) -> int:
        """Get number of trades placed today."""
        assert self._db is not None
        today = date.today().isoformat()
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM trades WHERE date(entry_time) = ?",
            (today,),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def get_recent_trades(self, limit: int = 50) -> list[dict]:
        """Get most recent trades."""
        assert self._db is not None
        cursor = await self._db.execute(
            """SELECT order_id, market_ticker, side, action, count,
                      price_dollars, fees_dollars, pnl_dollars,
                      model_probability, implied_probability,
                      entry_time, exit_time, strategy_tag
            FROM trades
            WHERE strategy_tag != 'monte_carlo'
            ORDER BY entry_time DESC LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        columns = [
            "order_id", "market_ticker", "side", "action", "count",
            "price_dollars", "fees_dollars", "pnl_dollars",
            "model_probability", "implied_probability",
            "entry_time", "exit_time", "strategy_tag",
        ]
        return [dict(zip(columns, row)) for row in rows]

    async def update_daily_summary(
        self,
        target_date: date,
        trades_count: int,
        win_count: int,
        total_pnl: float,
        max_drawdown: float,
        starting_balance: float | None = None,
        ending_balance: float | None = None,
    ) -> None:
        """Update or insert daily summary."""
        assert self._db is not None
        await self._db.execute(
            """INSERT OR REPLACE INTO daily_summary
            (date, trades_count, win_count, total_pnl, max_drawdown,
             starting_balance, ending_balance)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                target_date.isoformat(),
                trades_count,
                win_count,
                total_pnl,
                max_drawdown,
                starting_balance,
                ending_balance,
            ),
        )
        await self._db.commit()
