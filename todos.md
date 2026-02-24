# Todos

Potential changes and improvements to revisit.

---

## ~~Per-market stop loss cooldown~~ DONE (ce89ba3)

Implemented: after a stop loss, block directional/settlement ride re-entry for that ticker. MM and certainty scalp still allowed.

---

## Production Readiness

### Resilience & Recovery

- [ ] **Graceful state persistence** — Persist open positions, `_pending_settlements`, `_thesis_break_markets`, `_take_profit_markets`, and `_stop_loss_markets` to disk/DB so the bot recovers state on restart
- [ ] **Connection retry with backoff** — Exponential backoff with jitter on WebSocket feeds (Kalshi, Binance, Chainlink) instead of simple reconnect
- [ ] **Stale data detection** — Max-age check on every snapshot before acting; refuse to trade on data older than a threshold

### Monitoring & Alerting

- [ ] **External alerting** — Push alerts (Telegram, SMS, PagerDuty) for: position opened, unexpected loss, feed disconnect, bot crash, daily P&L summary
- [ ] **Heartbeat monitoring** — External watchdog that restarts the bot if it dies (systemd unit file or cron health check)
- [ ] **Metric export** — Prometheus/Grafana for historical tracking of edge accuracy, model calibration, fill rates, latency

### Risk Management

- [ ] **Daily/weekly loss limits** — Hard circuit breaker that stops all trading after a cumulative daily drawdown (e.g., -$50)
- [ ] **Correlation-aware sizing** — Account for BTC/ETH correlation when both have open positions to avoid effective 2x exposure
- [ ] **Slippage tracking** — Log expected fill price vs actual fill price; alert if slippage consistently eats edge

### Model & Strategy

- [ ] **Calibration tracking** — Log predicted probability vs actual settlement outcome; track calibration curve over hundreds of trades
- [ ] **Regime detection** — Detect market microstructure changes (liquidity drying up, spread widening) beyond vol regimes
- [ ] **Backtest framework** — Replay historical data through the strategy pipeline to validate changes before deploying

### Operational

- [ ] **Config hot-reload** — Adjust edge thresholds, Kelly fraction, etc. without restarting and dropping positions
- [ ] **Structured trade journal** — Link every trade to: model prediction, features at entry, market conditions, and outcome for systematic improvement
- [ ] **Deployment automation** — Docker/systemd with log rotation, auto-restart, and proper env var management

### Testing

- [ ] **Integration tests against paper mode** — End-to-end flow test: connect → scan → signal → order → fill → exit
- [ ] **Chaos testing** — Simulate feed disconnects, API timeouts, partial fills, and order rejections to verify graceful degradation

---
