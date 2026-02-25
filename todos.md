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

- [x] **Calibration tracking** — Log predicted probability vs actual settlement outcome; track calibration curve over hundreds of trades
- [ ] **Auto-calibration** — Feedback loop that reads calibration data from DB and auto-adjusts heuristic model signal weights to reduce Brier score / ECE
- [ ] **Regime detection** — Detect market microstructure changes (liquidity drying up, spread widening) beyond vol regimes
- [ ] **Backtest framework** — Replay historical data through the strategy pipeline to validate changes before deploying

### Fix Directional Trading (47.8% WR, -$107 all-time)

Data: avg SL -$6.02, avg TP +$5.21 (inverted risk/reward). $0.50+ entries = 95.7% WR. Losers oversized (40.8 vs 33.8 contracts). Full analysis in `.claude/plans/hazy-shimmying-salamander.md`.

- [ ] **Raise max_directional_price 0.60 → 0.70** (`config/settings.yaml`) — Opens Zone 4 ($0.60-$0.70), 100% historical WR, 0.7x Kelly caps size
- [ ] **Add directional Kelly fraction = 1.0** (`src/config.py`, `src/risk/position_sizer.py`, `config/settings.yaml`) — Half global 2.0; add `elif signal.signal_type == "directional"` branch in position_sizer
- [ ] **Add directional-specific stop-loss: 12% / $10 cap** (`src/config.py`, `src/execution/position_tracker.py`, `src/bot.py`, `config/settings.yaml`) — New params `directional_stop_loss_pct`, `directional_stop_loss_max_dollar`; override in `check_stop_loss()` when `strategy_tag == "directional"`; wire from bot.py (line ~1334)
- [ ] **Disable directional high-price boost** (`config/settings.yaml`) — `directional_high_price_boost: 1.5 → 1.0`
- [ ] **Run tests + linter** after all changes

### Operational

- [ ] **Config hot-reload** — Adjust edge thresholds, Kelly fraction, etc. without restarting and dropping positions
- [ ] **Structured trade journal** — Link every trade to: model prediction, features at entry, market conditions, and outcome for systematic improvement
- [ ] **Deployment automation** — Docker/systemd with log rotation, auto-restart, and proper env var management

### Testing

- [x] **Integration tests against paper mode** — End-to-end flow test: connect → scan → signal → order → fill → exit
- [ ] **Chaos testing** — Simulate feed disconnects, API timeouts, partial fills, and order rejections to verify graceful degradation

---
