Review trading performance by querying the bot's SQLite database at `data/bot.db`.

Run SQL queries against the `trades` table to produce a performance report:

1. **Today's summary**: Total trades, wins, losses, win rate, total P&L, total fees
2. **By strategy**: Group by `strategy_tag` — show count, wins, losses, win rate, avg P&L, total P&L for each (directional, market_making, settlement_ride, certainty_scalp, monte_carlo, etc.)
3. **By asset**: Group by ticker prefix (KXBTC vs KXETH) — show count, P&L, win rate per asset
4. **Recent trades**: Last 10 trades with ticker, side, action, count, price, P&L, strategy, timestamp
5. **Risk stats**: Largest win, largest loss, max consecutive losses, avg hold time if available

Use `.venv/bin/python3 -c "import sqlite3; ..."` or a small inline script to query the database. The trades table has columns: order_id, market_ticker, side, action, count, price_dollars, fees_dollars, pnl_dollars, model_probability, implied_probability, entry_time, exit_time, strategy_tag, market_volume.

Present results in clean markdown tables.
