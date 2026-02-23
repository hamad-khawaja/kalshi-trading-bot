Stop the trading bot completely:

1. Check if the bot is running using `pgrep -f kalshi-bot`
   - If not running, report "Bot is not running" and stop
2. Kill the kalshi-bot process using `pkill -f kalshi-bot`
3. Kill any process on port 8080 (dashboard) using `lsof -ti:8080 | xargs kill -9`
4. Wait 2 seconds for cleanup
5. Verify the bot is stopped: `pgrep -f kalshi-bot` should return nothing
6. Verify port 8080 is free: `lsof -ti:8080` should return nothing
7. Show the last 5 lines of `logs/bot.log` to confirm clean shutdown
8. Report: "Bot stopped. Dashboard on port 8080 released."

If the bot refuses to stop, escalate to `kill -9` and warn the user.
