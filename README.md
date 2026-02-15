# Kalshi Trading Bot

Automated trading bot for [Kalshi](https://kalshi.com) prediction markets, targeting **15-minute price movement contracts** (`KXBTC15M`, `KXETH15M`). Monitors real-time price feeds from multiple exchanges, estimates settlement probability via an 11-signal heuristic model, identifies mispriced contracts, and executes trades with strict risk management.

**Multi-asset** · **Real-time dashboard** · **Paper & live modes** · **Fee-aware Kelly sizing**

---

## Architecture

```mermaid
flowchart TB
    subgraph SOURCES["Data Sources"]
        CB["Coinbase WS"]
        KR["Kraken WS"]
        KWS["Kalshi WS"]
        KREST["Kalshi REST"]
        CG["Coinglass REST"]
    end

    subgraph DATA["Data Layer"]
        MS["Market Scanner"]
        DH["DataHub"]
        TP["Time Profiler"]
    end

    CB & KR & KWS & KREST & CG --> DH
    MS --> DH
    TP -.-> DH

    subgraph STRATEGY["Strategy Pipeline"]
        FE["Feature Engine"] --> PM["Heuristic Model"] --> SC["Signal Combiner"]
        SC --> ED["Edge Detector"]
        SC --> FD["FOMO Detector"]
        SC --> MM["Market Maker"]
        SC --> AV["Averager"]
    end

    DH --> FE

    subgraph RISK["Risk & Sizing"]
        PS["Position Sizer"]
        RM["Risk Manager"]
        VT["Volatility Tracker"]
    end

    ED & FD & MM & AV --> PS --> RM
    VT -.-> RM

    subgraph EXEC["Execution"]
        OM["Order Manager"]
        PT["Position Tracker"]
    end

    RM --> OM <--> PT
    OM --> KREST

    subgraph MONITOR["Monitoring"]
        DS["Dashboard (SSE)"]
        DB["SQLite"]
    end

    PT --> DB
    DH & PM & PT --> DS

    style SOURCES fill:#1a2733,stroke:#58a6ff,color:#c9d1d9
    style DATA fill:#0d1117,stroke:#30363d,color:#c9d1d9
    style STRATEGY fill:#0d1117,stroke:#30363d,color:#c9d1d9
    style RISK fill:#0d1117,stroke:#30363d,color:#c9d1d9
    style EXEC fill:#0d1117,stroke:#30363d,color:#c9d1d9
    style MONITOR fill:#0d1117,stroke:#30363d,color:#c9d1d9
```

### Signal Model Detail

```mermaid
flowchart LR
    subgraph INPUTS["Feature Inputs"]
        direction TB
        M["Momentum 30%\n15s · 60s · 180s · 600s"]
        T["Technical 14%\nRSI · BB · MACD · ROC"]
        TF["Taker Flow 10%\nNet aggressive buying"]
        MR["Mean Reversion 10%\nRSI extremes\nsuppressed in trends"]
        TD["Time Decay 10%\nDampen near expiry"]
        SB["Settlement 8%\nRecent YES/NO bias"]
        CA["Cross-Asset 7%\nOther asset divergence"]
        FL["Order Flow 6%\nKalshi book imbalance"]
        XE["Cross-Exchange 5%\nBinance lead-lag"]
        FU["Funding 0%\nDisabled"]
        LQ["Liquidation 0%\nDisabled"]
    end

    subgraph MODEL["Model"]
        direction TB
        WS["Weighted Sum\nclamp ±0.18\nboost ±0.35 on\nconsensus"]
        EMA["EMA Smooth\nalpha = 0.5"]
        CONF["Confidence\nspread + vol +\ntime + depth"]
    end

    M & T & TF & MR & TD & SB & CA & FL & XE --> WS
    WS --> EMA
    EMA -->|"P(YES)"| OUT["PredictionResult"]
    CONF -->|"Confidence"| OUT

    style INPUTS fill:#161b22,stroke:#30363d,color:#c9d1d9
    style MODEL fill:#161b22,stroke:#30363d,color:#c9d1d9
```

### Concurrent Task Architecture

```mermaid
flowchart LR
    BOT["TradingBot.start()"]

    BOT --> S["Strategy Loop\nevery 4s"]
    BOT --> MS["Market Scan\nevery 1-10s"]
    BOT --> PM["Position Monitor\nevery 10s"]
    BOT --> CG["Coinglass Poll\nevery 30s"]
    BOT --> HC["Health Check\n5s then 60s"]
    BOT --> TP["Time Profile\nevery 1h"]

    S -->|"For each active market"| CYCLE["Snapshot → Features\n→ Predict → Edge\n→ Size → Risk → Order"]
    PM -->|"For each position"| EXIT["Take-profit\nTrailing stop\nThesis break\nTime decay exit"]
    HC -->|"Push to dashboard"| DASH["Balance · P&L\nPositions · Settlements"]

    style BOT fill:#1a3a1a,stroke:#3fb950,color:#c9d1d9
```

---

## Quick Start

### Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.11+** | `python3 --version` |
| **Kalshi account** | [kalshi.com](https://kalshi.com) |
| **Kalshi API key** | RSA key pair (see below) |
| **Coinglass API key** | *Optional* — funding rate data |

### Setup

```bash
# 1. Generate Kalshi API key
openssl genrsa -out kalshi_key.pem 4096
openssl rsa -in kalshi_key.pem -pubout -out kalshi_key_pub.pem
# Upload kalshi_key_pub.pem at kalshi.com account settings

# 2. Install
git clone <repo-url> && cd kalshi-btc-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 3. Environment variables
export KALSHI_API_KEY_ID="your-api-key-id"
export KALSHI_PRIVATE_KEY_PATH="/path/to/kalshi_key.pem"
export COINGLASS_API_KEY="your-coinglass-key"  # optional

# 4. Run (paper mode, safe default)
kalshi-bot --dry-run
```

### Running Live

```bash
# Production
kalshi-bot --mode live --env prod

# With risk overrides
kalshi-bot --mode live --env prod --max-exposure 1000 --max-daily-loss 200
```

### CLI Reference

| Flag | Short | Description |
|------|-------|-------------|
| `--config PATH` | `-c` | Settings YAML (default: `config/settings.yaml`) |
| `--mode {paper,live}` | `-m` | Trading mode |
| `--env {demo,prod}` | `-e` | Kalshi environment |
| `--log-level LEVEL` | `-l` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `--max-exposure $` | | Max total exposure |
| `--max-daily-loss $` | | Max daily loss |
| `--dry-run` | | Shortcut: `--mode paper --env demo` |

---

## How It Works

### Main Loop (every ~4 seconds)

1. **Snapshot** — Aggregate prices (Coinbase, Kraken), Kalshi orderbook, Coinglass derivatives data
2. **Features** — Compute 29 features: momentum, technicals, order flow, cross-exchange signals, time decay
3. **Predict** — 11 weighted signals → P(YES) estimate with confidence score
4. **Edge** — Compare model probability vs Kalshi implied probability, subtract fees
5. **Risk** — 9 independent safety checks (daily loss, exposure cap, streak limits, vol regime)
6. **Execute** — Kelly-sized limit order if edge > threshold and all risk checks pass
7. **Monitor** — Track positions for take-profit, trailing stop, thesis break, time decay exit

### Strategy Types

| Strategy | Trigger | Description |
|----------|---------|-------------|
| **Directional** | `net_edge > threshold` | Model vs market probability mismatch |
| **FOMO** | Retail panic + extreme implied prob | Contrarian bet against crowd |
| **Market Making** | Wide spread (5-30%) | Resting limit orders both sides |
| **Averaging** | Position at -10/20/35% discount | Pyramid into discounted positions |

### Edge Calculation

```
model_prob = heuristic_model(features)     # e.g., 0.62
market_prob = kalshi_midpoint              # e.g., 0.55
raw_edge = |model_prob - market_prob|      # 0.07
net_edge = raw_edge - fee_drag             # ~0.04
→ Trade if net_edge > min_threshold (default 3%)
```

### Risk Checks (all must pass)

1. Balance ≥ minimum ($50)
2. Daily P&L above loss limit
3. Position count < per-market cap
4. Total exposure < cap
5. Concurrent positions < limit
6. Consecutive losses < streak max
7. Trades today < daily limit
8. Not in cooldown period
9. Time to expiry > 60 seconds

### Position Sizing

**Fractional Kelly Criterion** with adjustments:
- Zone scaling: better risk:reward zones → larger size
- Time scaling: reduce size as expiry approaches
- Vol regime: tighter sizing in high volatility
- Per-cycle cap: max contracts per single order

---

## Dashboard

Real-time browser dashboard at `http://localhost:8080` via Server-Sent Events (SSE).

**Summary bar** — Balance, Total P&L, Trades Today, Win Rate at a glance
**Per-asset tabs** — BTC/ETH with independent market, price, prediction, and edge panels
**Live data** — Countdown timer, price ticker with delta, 11 signal bars, orderbook stats
**Positions** — Color-coded YES/NO with risk stats
**Settlements** — Compact inline badges showing recent market outcomes
**Trade history** — Per-asset with P&L coloring
**Features** — Collapsible 29-feature grid
**Decision log** — Last 15 cycle decisions

---

## Configuration

All settings in `config/settings.yaml`:

| Setting | Default | Description |
|---|---|---|
| `mode` | `live` | `paper` or `live` |
| `kalshi.environment` | `prod` | `demo` or `prod` |
| `strategy.poll_interval_seconds` | `4` | Main loop interval |
| `strategy.min_edge_threshold` | `0.03` | Minimum edge to trade |
| `risk.max_position_per_market` | `15` | Max contracts per market |
| `risk.max_total_exposure_dollars` | `50` | Total capital at risk |
| `risk.max_daily_loss_dollars` | `5` | Daily loss stop |
| `risk.max_concurrent_positions` | `3` | Max open positions |
| `risk.kelly_fraction` | `0.15` | Kelly fraction for sizing |

### Per-Asset Config

```yaml
kalshi:
  assets:
    - series_ticker: "KXBTC15M"
      symbol: "BTC"
      primary_ws_url: "wss://ws-feed.exchange.coinbase.com"
      primary_symbol: "BTC-USD"
      secondary_ws_url: "wss://ws.kraken.com/v2"
      secondary_symbol: "BTC/USD"
      coinglass_symbol: "BTC"
    - series_ticker: "KXETH15M"
      symbol: "ETH"
      # ...
```

---

## Project Structure

```
├── config/
│   └── settings.yaml              # All configurable parameters
├── src/
│   ├── bot.py                     # Main orchestrator — 5 concurrent loops
│   ├── config.py                  # Pydantic settings with validation
│   ├── data/
│   │   ├── binance_feed.py        # WS price feeds (Coinbase/Kraken/Binance)
│   │   ├── kalshi_client.py       # Kalshi REST client (markets, orders, balance)
│   │   ├── kalshi_ws.py           # Kalshi WS (orderbook deltas, fills)
│   │   ├── kalshi_auth.py         # RSA-PSS authentication
│   │   ├── coinglass_client.py    # Funding, OI, liquidations, L/S ratio
│   │   ├── data_hub.py            # Unified aggregator → MarketSnapshot
│   │   ├── market_scanner.py      # Active market discovery
│   │   ├── time_profile.py        # Historical kline profiling
│   │   ├── database.py            # SQLite persistence (async)
│   │   └── models.py              # Data models (Tick, Snapshot, Order, etc.)
│   ├── features/
│   │   ├── feature_engine.py      # Snapshot → 29 features
│   │   └── indicators.py          # RSI, BB, MACD, ROC, VWAP
│   ├── model/
│   │   ├── predict.py             # Heuristic model (11 signals → P(YES))
│   │   ├── calibrate.py           # Probability calibration
│   │   └── train.py               # LightGBM training pipeline
│   ├── strategy/
│   │   ├── signal_combiner.py     # Signal prioritization
│   │   ├── edge_detector.py       # Model vs market edge calculation
│   │   ├── fomo_detector.py       # Contrarian retail panic detector
│   │   ├── market_maker.py        # Spread capture strategy
│   │   └── averager.py            # Asymmetric pyramiding
│   ├── risk/
│   │   ├── risk_manager.py        # 9 safety checks
│   │   ├── position_sizer.py      # Fractional Kelly sizing
│   │   └── volatility.py          # Vol regime tracking
│   ├── execution/
│   │   ├── order_manager.py       # Order lifecycle (paper + live)
│   │   └── position_tracker.py    # Position state, exits, P&L
│   └── dashboard/
│       ├── server.py              # aiohttp SSE server
│       └── page.py                # Inline HTML/CSS/JS dashboard
├── backtest/
│   ├── data_collector.py          # Historical data collection
│   ├── backtester.py              # Strategy backtesting engine
│   └── analysis.py                # Performance analysis
├── scripts/                       # Utility scripts
├── data/                          # SQLite database + candle cache
└── pyproject.toml                 # Dependencies
```

---

## Database Schema

| Table | Purpose |
|-------|---------|
| `trades` | Completed trades (order_id, ticker, side, price, fees, pnl) |
| `predictions` | Model predictions with features for backtesting |
| `outcomes` | Market settlement results |
| `ticks` | Raw price ticks for replay |
| `daily_summary` | Aggregated daily P&L |

---

## Optional Extras

```bash
pip install -e ".[ml]"        # LightGBM model training
pip install -e ".[backtest]"  # Backtesting visualization
pip install -e ".[dev]"       # pytest, mypy, ruff
```

## Testing

```bash
pytest tests/ -v
pytest tests/ --cov=src --cov-report=term-missing
```

## API References

- **Kalshi REST**: `https://api.elections.kalshi.com/trade-api/v2/`
- **Kalshi Demo**: `https://demo-api.kalshi.co/trade-api/v2/`
- **Auth**: RSA-PSS signatures (SHA256)
- **Docs**: [trading-api.readme.io/reference](https://trading-api.readme.io/reference)
