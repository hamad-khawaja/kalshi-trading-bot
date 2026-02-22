Start the trading bot in paper (dry-run) mode:

1. Check if the bot is already running using `pgrep -f kalshi-bot`
   - If running, report "Bot is already running (PID: ...)" and ask the user if they want to restart. Do NOT proceed unless they confirm.
2. Verify port 8080 is free using `lsof -ti:8080`
   - If occupied, kill the process on that port
3. Start the bot in paper mode: `nohup .venv/bin/kalshi-bot --dry-run > /dev/null 2>&1 &`
4. Wait 8 seconds for startup
5. Tail the last 30 lines of `logs/bot.log` and confirm:
   - `bot_started` event appears
   - Mode is `paper`
   - No fatal errors
   - Feeds are connected
   - Markets are scanned
6. Report startup status in a table:
   - Mode, PID, assets, balance, active markets, vol regime, feed status, errors
