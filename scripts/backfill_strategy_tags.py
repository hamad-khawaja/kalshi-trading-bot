"""Backfill strategy_tag column in trades table from bot log data.

Usage: python scripts/backfill_strategy_tags.py
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "bot.db"
LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "bot.log"


def build_mappings(log_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Parse log file to build order_id -> signal_type and ticker -> signal_type mappings."""
    order_mapping: dict[str, str] = {}
    ticker_mapping: dict[str, str] = {}

    with open(log_path) as f:
        for line in f:
            try:
                obj = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue

            signal_type = obj.get("signal_type")
            if not signal_type:
                continue

            event = obj.get("event", "")
            if event in ("paper_order_filled", "trade_filled"):
                order_id = obj.get("order_id")
                ticker = obj.get("ticker")
                if order_id:
                    order_mapping[order_id] = signal_type
                if ticker:
                    ticker_mapping[ticker] = signal_type

    return order_mapping, ticker_mapping


def backfill(db_path: Path, log_path: Path) -> None:
    """Backfill strategy_tag for existing trades."""
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return
    if not log_path.exists():
        print(f"Log file not found: {log_path}")
        return

    order_mapping, ticker_mapping = build_mappings(log_path)
    print(f"Loaded {len(order_mapping)} order_id mappings, {len(ticker_mapping)} ticker mappings from logs")

    conn = sqlite3.connect(str(db_path))

    # Ensure column exists
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    if "strategy_tag" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN strategy_tag TEXT NOT NULL DEFAULT 'directional'")
        conn.commit()
        print("Added strategy_tag column")

    # Get all trades
    rows = conn.execute("SELECT id, order_id, market_ticker, action FROM trades").fetchall()
    print(f"Total trades to process: {len(rows)}")

    updates = []
    stats = Counter()

    for trade_id, order_id, market_ticker, action in rows:
        tag = None

        # 1. Try matching buy trades by order_id
        if action == "buy" and order_id in order_mapping:
            tag = order_mapping[order_id]
            stats["matched_by_order_id"] += 1
        # 2. For exit trades, try matching by ticker (buy trade for same ticker)
        elif action != "buy" and market_ticker in ticker_mapping:
            tag = ticker_mapping[market_ticker]
            stats["matched_by_ticker"] += 1
        # 3. For exit trades, try matching by their own order_id
        elif order_id in order_mapping:
            tag = order_mapping[order_id]
            stats["matched_by_exit_order_id"] += 1
        else:
            tag = "directional"  # default fallback
            stats["default_fallback"] += 1

        updates.append((tag, trade_id))

    # Batch update
    conn.executemany("UPDATE trades SET strategy_tag = ? WHERE id = ?", updates)
    conn.commit()
    conn.close()

    print(f"\nBackfill complete:")
    for key, count in sorted(stats.items()):
        print(f"  {key}: {count}")

    # Verify
    conn = sqlite3.connect(str(db_path))
    dist = conn.execute(
        "SELECT strategy_tag, COUNT(*) FROM trades GROUP BY strategy_tag ORDER BY COUNT(*) DESC"
    ).fetchall()
    print(f"\nStrategy tag distribution:")
    for tag, count in dist:
        print(f"  {tag}: {count}")
    conn.close()


if __name__ == "__main__":
    backfill(DB_PATH, LOG_PATH)
