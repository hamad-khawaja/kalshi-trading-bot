Smart log viewer for the trading bot. Filter and display recent logs from `logs/bot.log`.

If the user provides a filter argument like "trades", "mm", "errors", "edges", or "settlements", apply it:

- **trades**: Show `trade_filled`, `resting_order_fill_detected`, `order_resting` events
- **mm**: Show `mm_quotes_generated`, `mm_fill`, `mm_skipped_extreme_vol`, `mm_requote_triggered`, `signal_market_making` events
- **errors**: Show `error` and `exception` level events plus `fatal_error`
- **edges**: Show `edge_detected`, `trend_guard_blocked`, `edge_streak_building`, `signal_directional` events
- **settlements**: Show `position_settled_paper`, `position_settled_actual`, `settlement_ride_signal` events
- **signals**: Show all `signal_*` events and `*_blocked` events
- **health**: Show `health_check` events

If no filter is provided, show the last 40 lines of the log.

For each matching event, format it readably: timestamp, event name, and key fields. Don't dump raw JSON — extract the important fields and present them in a table or list.

Use `$ARGUMENTS` as the filter.
