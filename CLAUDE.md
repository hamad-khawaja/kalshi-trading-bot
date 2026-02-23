# CLAUDE.md

## Project Overview

Automated trading bot for Kalshi prediction markets targeting 15-minute price movement contracts (KXBTC15M, KXETH15M). Monitors real-time price feeds from Coinbase, Kraken, Bybit futures, and Chainlink oracles; estimates settlement probability via a 16-signal heuristic model; and executes trades with strict risk management.

## Quick Reference

```bash
# Run tests (always do this before committing)
.venv/bin/pytest tests/ -x --tb=short

# Start bot (paper mode)
.venv/bin/kalshi-bot --dry-run

# Start bot (live)
.venv/bin/kalshi-bot --mode live

# Lint
.venv/bin/ruff check src/ tests/
```

## Architecture

The bot runs 5 concurrent asyncio loops from `src/bot.py`:
1. **strategy_loop** — core trading logic every 4s
2. **market_scan_loop** — discover new markets
3. **position_monitor_loop** — exits, fills, P&L every 10s
4. **health_check_loop** — status every 60s
5. **time_profile_refresh_loop** — kline refresh every 6h (optional)

### Data Flow

```
Price Feeds → DataHub → FeatureEngine (33 features) → HeuristicModel (16 signals)
→ SignalCombiner (prioritize) → PositionSizer (Kelly) → RiskManager (9 checks)
→ OrderManager (submit) → PositionTracker (monitor)
```

### Key Modules

| Path | Purpose |
|------|---------|
| `src/bot.py` | Main orchestrator, wires all components |
| `src/config.py` | Pydantic settings (StrategyConfig, RiskConfig, etc.) |
| `src/strategy/signal_combiner.py` | Signal prioritization: directional > FOMO > certainty > settlement > MC > MM |
| `src/strategy/edge_detector.py` | Model vs market edge calculation, fee computation |
| `src/strategy/market_maker.py` | Spread capture with vol-aware quotes, non-linear inventory skew |
| `src/strategy/mc_detector.py` | Monte Carlo simulation strategy |
| `src/model/predict.py` | HeuristicModel: 16 weighted signals → P(YES) |
| `src/features/feature_engine.py` | Snapshot → 33 features |
| `src/risk/volatility.py` | Vol regime tracking (low/normal/high/extreme) |
| `src/risk/position_sizer.py` | Fractional Kelly sizing with adjustments |
| `src/risk/risk_manager.py` | 9 independent safety checks |
| `src/data/data_hub.py` | Aggregates all feeds into MarketSnapshot |
| `src/execution/order_manager.py` | Order lifecycle (paper + live modes) |
| `src/execution/position_tracker.py` | Position state, exits, P&L tracking |
| `src/dashboard/server.py` | aiohttp SSE dashboard at localhost:8080 |

## Code Conventions

- **Python 3.11+** with `from __future__ import annotations`
- **Pydantic v2** for all config/data models
- **structlog** for structured JSON logging — use `logger.info("event_name", key=value)` style
- **Decimal** for all prices and monetary values (never float for money)
- **async/await** throughout — no blocking I/O in the event loop
- **Type hints** on all function signatures
- Ruff enforced: line length 100, rules E/F/W/I/N/UP
- Fee formula: `ceil(rate * C * P * (1-P))` where maker rate=0.0175, taker rate=0.07

## Testing

- **382 tests** in `tests/` using pytest with pytest-asyncio
- `asyncio_mode = "auto"` — async tests don't need `@pytest.mark.asyncio`
- Shared fixtures in `tests/conftest.py` (sample_snapshot, sample_prediction, bot_settings, etc.)
- Test files mirror source structure: `test_strategy.py`, `test_risk.py`, `test_features.py`, etc.
- `test_audit_live_readiness.py` — comprehensive pre-live safety checks
- CI runs on Python 3.11 and 3.12 via GitHub Actions

## Configuration

All settings in `config/settings.yaml`, loaded via `src/config.py`. Key config classes:
- `StrategyConfig` — edge thresholds, phase gating, per-asset overrides
- `RiskConfig` — position limits, Kelly fraction, per-asset caps
- `FeatureConfig` — feature weights and toggles

Per-asset overrides use dicts keyed by asset symbol (e.g., `asset_edge_multipliers: {ETH: 1.4}`).

## Strategy Priority Order

1. **Directional** — model vs market edge with streak confirmation
2. **FOMO** — contrarian retail panic (only when no directional)
3. **Certainty scalp** — near-certain outcome in last 3 min (only when no directional)
4. **Settlement ride** — late-window hold-to-settlement (only when no directional)
5. **Monte Carlo** — simulation-based probability (only when no directional)
6. **Market making** — always runs, alongside directional or standalone; filters to opposite side when directional present

## Common Patterns

- **Adding a new strategy**: Create detector in `src/strategy/`, wire into `SignalCombiner.evaluate()`, return `TradeSignal` with appropriate `signal_type`
- **Adding a feature**: Add field to `FeatureVector` in `src/data/models.py`, compute in `FeatureEngine.compute()`, add signal weight in `HeuristicModel`
- **Per-asset config**: Add field to `StrategyConfig`/`RiskConfig` as `dict[str, T]`, check with ticker string matching in the relevant module
- **Logging**: Always use structlog with snake_case event names. Include `ticker=`, `side=`, `net_edge=` for trade-related events
- **Dashboard state**: Update `DashboardState` fields in `bot.py`; the SSE server streams changes automatically

## Important Notes

- Never commit `config/kalshi_key.pem` or any private keys
- The bot has paper and live modes — always test with `--dry-run` first
- `EdgeDetector.compute_fee_dollars()` is the single source of truth for Kalshi fee calculations
- Vol tracker regimes affect spread offsets, Kelly sizing, and edge thresholds across the system
- The position monitor loop handles all exits (settlement, take-profit, stop-loss, thesis-break, pre-expiry)
