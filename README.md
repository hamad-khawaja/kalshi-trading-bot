# Kalshi Bitcoin 15-Minute Trading Bot

Automated trading bot for [Kalshi](https://kalshi.com) prediction markets, targeting **Bitcoin 15-minute price movement contracts** (`KXBTC15M` series). The bot monitors BTC price data and Kalshi order books, estimates the probability of BTC moving up/down in each 15-minute window, identifies mispriced contracts, and executes trades with strict risk management.

---

## Quick Start

### 1. Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.11+** | Check with `python3 --version` |
| **Kalshi account** | Sign up at [kalshi.com](https://kalshi.com) |
| **Kalshi API key** | RSA key pair — see step 2 below |
| **Coinglass API key** | *Optional* — adds funding rate data for better signals |

### 2. Generate your Kalshi API key

```bash
# Generate a 4096-bit RSA private key
openssl genrsa -out kalshi_key.pem 4096

# Extract the public key
openssl rsa -in kalshi_key.pem -pubout -out kalshi_key_pub.pem
```

Then go to your [Kalshi account settings](https://kalshi.com), upload `kalshi_key_pub.pem`, and copy the **API Key ID** it gives you.

### 3. Install

```bash
git clone <repo-url>
cd kalshi-btc-bot

# Create a virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 4. Set environment variables

```bash
export KALSHI_API_KEY_ID="your-api-key-id"
export KALSHI_PRIVATE_KEY_PATH="/path/to/kalshi_key.pem"
export COINGLASS_API_KEY="your-coinglass-key"  # optional
```

> **Tip:** Add these to your `~/.bashrc` or `~/.zshrc` so they persist across terminal sessions.

### 5. Configure

Open `config/settings.yaml` and review these settings before your first run:

```yaml
mode: paper          # "paper" = no real orders, "live" = real money

kalshi:
  environment: demo  # "demo" = sandbox, "prod" = real market
```

Start with `mode: paper` and `environment: demo` until you're comfortable.

### 6. Run the bot

```bash
# Paper mode on demo (safe default from config)
kalshi-bot

# Dry run — forces paper mode + demo environment
kalshi-bot --dry-run

# With verbose logging
kalshi-bot --dry-run --log-level DEBUG

# With a custom config file
kalshi-bot --config path/to/settings.yaml

# Or via Python directly
.venv/bin/python -m src.bot --dry-run
```

The bot will start logging to the terminal. You'll see it scanning for active markets, pulling BTC prices, and evaluating trades every ~4 seconds.

### Running in production

```bash
kalshi-bot --mode live --env prod
```

You can also override risk limits from the command line:

```bash
kalshi-bot --mode live --env prod --max-exposure 1000 --max-daily-loss 200
```

Review the risk limits in `config/settings.yaml` before going live — especially `max_daily_loss_dollars` and `max_total_exposure_dollars`. CLI flags override config file values.

### CLI Reference

| Flag | Short | Description |
|------|-------|-------------|
| `--config PATH` | `-c` | Path to settings YAML (default: `config/settings.yaml`) |
| `--mode {paper,live}` | `-m` | Trading mode — overrides config |
| `--env {demo,prod}` | `-e` | Kalshi environment — overrides config |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `-l` | Log verbosity — overrides config |
| `--max-exposure DOLLARS` | | Max total exposure — overrides config |
| `--max-daily-loss DOLLARS` | | Max daily loss — overrides config |
| `--dry-run` | | Shortcut for `--mode paper --env demo` |
| `--version` | `-v` | Print version and exit |
| `--help` | `-h` | Show help and exit |

---

## How It Works

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Data Ingestion   │────▶│  Signal Engine    │────▶│  Execution Layer │
│                   │     │                   │     │                   │
│ • Binance WS      │     │ • Feature Engine  │     │ • Kalshi API     │
│ • Kalshi REST/WS  │     │ • Heuristic Model │     │ • Order Manager  │
│ • Coinglass API   │     │ • Edge Detector   │     │ • Position Track │
│ • Market Scanner  │     │ • Market Maker    │     │                   │
└──────────────────┘     └──────────────────┘     └──────────────────┘
                                                          │
                                                          ▼
                                                 ┌──────────────────┐
                                                 │ Risk Management   │
                                                 │                   │
                                                 │ • Kelly Sizing    │
                                                 │ • Daily Loss Limit│
                                                 │ • Vol Regime      │
                                                 │ • Position Caps   │
                                                 └──────────────────┘
```

### Main Loop (every ~4 seconds)

1. Pull latest BTC price from Binance WebSocket
2. Fetch Kalshi orderbook for current 15-minute contract
3. Compute features (momentum, volatility, order flow, etc.)
4. Run probability model → estimated P(BTC up in next 15 min)
5. Compare model probability vs Kalshi implied probability
6. If net edge > threshold AND risk limits OK → place trade
7. Monitor open positions for exit / settlement

## Strategy

### Core: Implied Probability Mispricing

The bot builds its own probability estimate using real-time market data and compares it to the Kalshi contract's implied probability. When the gap exceeds a threshold (after accounting for fees), it trades.

```
model_prob = model.predict(features)       # e.g., 0.62
implied_prob = kalshi_midpoint_price       # e.g., 0.55
net_edge = abs(model_prob - implied_prob) - fee_drag

If net_edge > 0.03:  → Trade
```

### Secondary: Market Making

When Kalshi spreads are wide (>$0.05), the bot places resting limit orders on both sides to capture the spread. Uses `post_only` orders for lower maker fees (1.75% vs 7% taker).

### Fee-Aware Edge Calculation

All edge calculations account for Kalshi fees:
- **Taker fee**: `ceil(0.07 × C × P × (1-P))`
- **Maker fee**: `ceil(0.0175 × C × P × (1-P))`

### Risk Management

- **Quarter-Kelly position sizing** — conservative, avoids ruin
- **Daily loss limit** — stops trading after configurable daily loss
- **Max position per market** — prevents concentration
- **Total exposure cap** — limits capital at risk
- **Consecutive loss cooldown** — pauses after loss streaks
- **Volatility regime adjustment** — wider edge requirements in high-vol
- **Time-to-expiry gate** — no new trades within 60s of settlement

## Configuration Reference

All settings live in `config/settings.yaml`. Key parameters:

| Setting | Default | Description |
|---|---|---|
| `mode` | `live` | `paper` or `live` |
| `kalshi.environment` | `prod` | `demo` or `prod` |
| `strategy.poll_interval_seconds` | `4` | How often the main loop runs |
| `strategy.min_edge_threshold` | `0.03` | Minimum edge (3%) to trigger a trade |
| `risk.max_position_per_market` | `50` | Max contracts per market |
| `risk.max_total_exposure_dollars` | `500` | Total capital at risk cap |
| `risk.max_daily_loss_dollars` | `100` | Stop trading after this daily loss |
| `risk.max_concurrent_positions` | `5` | Max open positions at once |
| `risk.kelly_fraction` | `0.25` | Kelly fraction (0.25 = quarter-Kelly) |

## Project Structure

```
├── config/
│   └── settings.yaml           # All configurable parameters
├── src/
│   ├── config.py               # Typed Pydantic settings
│   ├── bot.py                  # Main orchestrator + entry point
│   ├── data/
│   │   ├── kalshi_auth.py      # RSA-PSS authentication
│   │   ├── kalshi_client.py    # Async Kalshi REST client
│   │   ├── kalshi_ws.py        # Kalshi WebSocket client
│   │   ├── binance_feed.py     # BTC price stream
│   │   ├── coinglass_client.py # Funding rates, OI
│   │   ├── market_scanner.py   # Active market discovery
│   │   ├── data_hub.py         # Unified data aggregator
│   │   ├── database.py         # SQLite persistence
│   │   └── models.py           # All data models
│   ├── features/
│   │   ├── indicators.py       # Technical indicator functions
│   │   └── feature_engine.py   # Snapshot → feature vector
│   ├── model/
│   │   ├── predict.py          # Probability models (heuristic + LightGBM)
│   │   ├── calibrate.py        # Probability calibration
│   │   └── train.py            # Model training pipeline
│   ├── strategy/
│   │   ├── edge_detector.py    # Model vs market probability comparison
│   │   ├── market_maker.py     # Spread capture strategy
│   │   └── signal_combiner.py  # Signal prioritization
│   ├── risk/
│   │   ├── position_sizer.py   # Kelly Criterion sizing
│   │   ├── risk_manager.py     # Safety limits enforcement
│   │   └── volatility.py       # Volatility regime tracking
│   └── execution/
│       ├── order_manager.py    # Order lifecycle management
│       └── position_tracker.py # Position and P&L tracking
├── backtest/
│   ├── data_collector.py       # Historical data collection
│   ├── backtester.py           # Strategy backtesting engine
│   └── analysis.py             # Performance analysis
├── tests/                      # Test suite
├── logs/                       # Runtime logs
├── data/                       # SQLite database
└── pyproject.toml              # Dependencies and tool config
```

## Optional Extras

```bash
# Install ML dependencies (for LightGBM model training)
pip install -e ".[ml]"

# Install backtesting visualization tools
pip install -e ".[backtest]"

# Install dev tools (pytest, mypy, ruff)
pip install -e ".[dev]"
```

## Testing

```bash
pytest tests/ -v

# Using the venv Python explicitly
.venv/bin/python -m pytest tests/ -v

# With coverage
pytest tests/ --cov=src --cov-report=term-missing
```

## Data Collection & Backtesting

```bash
# Collect data for 24 hours
python -m backtest.data_collector 24

# Run backtest (requires collected data)
python -c "
from backtest.backtester import Backtester
from backtest.analysis import BacktestAnalyzer
from src.config import load_settings
import pandas as pd

settings = load_settings()
bt = Backtester(settings)
data = pd.read_csv('your_data.csv')
result = bt.run(data)
print(BacktestAnalyzer().summary(result))
"
```

## Key API Details

- **Kalshi REST API**: `https://api.elections.kalshi.com/trade-api/v2/`
- **Kalshi Demo API**: `https://demo-api.kalshi.co/trade-api/v2/`
- **Authentication**: RSA-PSS signatures (SHA256)
- **Prices**: Dollar-denominated (`yes_price_dollars`, `no_price_dollars`)
- **Contract series**: `KXBTC15M` (Bitcoin 15-minute price up/down)
- **API Docs**: [trading-api.readme.io/reference](https://trading-api.readme.io/reference)
