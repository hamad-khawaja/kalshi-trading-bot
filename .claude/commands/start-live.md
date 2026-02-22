Start the trading bot in LIVE production mode:

**CRITICAL: This trades real money. Confirm with the user before proceeding.**

1. Ask the user to confirm: "This will start the bot in LIVE mode with real money. Are you sure?"
   - Do NOT proceed unless the user explicitly confirms
2. Check if the bot is already running using `pgrep -f kalshi-bot`
   - If running, report "Bot is already running (PID: ...)" and ask the user if they want to restart. Do NOT proceed unless they confirm. Kill the existing process first.
3. Verify prerequisites:
   - `config/settings.yaml` exists
   - Environment variables `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` are set (check with `echo $KALSHI_API_KEY_ID | head -c4` to show first 4 chars only — never print full keys)
   - Private key file exists at the configured path
   - If any prerequisite is missing, report the issue and stop
4. Verify port 8080 is free using `lsof -ti:8080`
   - If occupied, kill the process on that port
5. Start the bot in live mode: `nohup .venv/bin/kalshi-bot --mode live > /dev/null 2>&1 &`
6. Wait 10 seconds for startup (live mode takes longer to authenticate)
7. Tail the last 40 lines of `logs/bot.log` and confirm:
   - `bot_started` event appears
   - Mode is `live`
   - No fatal errors or authentication failures
   - Feeds are connected
   - Markets are scanned
   - Balance is fetched (report the actual balance)
8. Report startup status in a table:
   - Mode, PID, assets, balance, active markets, vol regime, feed status, errors
9. Remind the user: "Bot is LIVE. Monitor with /bot-status. Stop with /stop-bot."
