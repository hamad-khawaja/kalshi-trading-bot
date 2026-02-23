Restart the trading bot cleanly:

1. Kill any process on port 8080 (dashboard) using `lsof -ti:8080 | xargs kill -9`
2. Kill any running kalshi-bot process using `pkill -f kalshi-bot`
3. Wait 2 seconds for cleanup
4. Verify port 8080 is free
5. Start the bot in the background with `.venv/bin/kalshi-bot --dry-run`
6. Wait 8 seconds for startup
7. Tail the last 30 lines of `logs/bot.log` and confirm:
   - `bot_started` event appears
   - No fatal errors
   - Feeds are connected
   - Markets are scanned
8. Report the startup status: mode, active markets, balance, vol regime

If the user passes arguments like `--mode live`, use those instead of `--dry-run`.
