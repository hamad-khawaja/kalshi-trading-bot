Show current bot status by reading recent logs:

1. Check if the bot process is running (`pgrep -f kalshi-bot`)
2. Extract the most recent `health_check` event from `logs/bot.log` and display:
   - Uptime, mode, balance
   - Daily P&L, trades today, win rate
   - Active markets, vol regime
   - Consecutive losses/wins
   - Feed prices (BTC, ETH)
3. Extract the last 5 trade-related events (`trade_filled`, `resting_order_fill_detected`, `mm_fill`, `position_settled_paper`, `position_settled_actual`, `take_profit_executed`, `stop_loss_executed`)
4. Show any recent errors from the last 50 log lines
5. Check if the dashboard is accessible on port 8080

Present everything in a clean summary format.
