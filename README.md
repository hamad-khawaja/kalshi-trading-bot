# Kalshi Bitcoin 15-Minute Trading Bot

Automated trading bot for [Kalshi](https://kalshi.com) prediction markets, targeting **Bitcoin 15-minute price movement contracts** (`KXBTC15M` series). The bot continuously monitors BTC price data and Kalshi order books, estimates the probability of BTC going up/down in each 15-minute window, identifies mispriced contracts, and executes trades with strict risk management.

## Architecture

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

## Setup

### Prerequisites

- Python 3.11+
- Kalshi API key (RSA key pair) — see [Kalshi API docs](https://trading-api.readme.io/reference)
- Binance WebSocket access (no auth needed for public streams)
- Optional: Coinglass API key for funding rate data

### Installation

```bash
# Clone and install
git clone <repo-url>
cd kalshi-btc-bot
pip install -e ".[dev]"

# For ML model training
pip install -e ".[ml]"

# For backtesting visualizations
pip install -e ".[backtest]"
```

### Configuration

1. Generate an RSA key pair for Kalshi API authentication:
```bash
openssl genrsa -out kalshi_key.pem 4096
openssl rsa -in kalshi_key.pem -pubout -out kalshi_key_pub.pem
```

2. Upload the public key to your Kalshi account and note the API Key ID.

3. Set environment variables:
```bash
export KALSHI_API_KEY_ID="your-api-key-id"
export KALSHI_PRIVATE_KEY_PATH="/path/to/kalshi_key.pem"
export COINGLASS_API_KEY="your-coinglass-key"  # Optional
```

4. Edit `config/settings.yaml` to adjust strategy parameters, risk limits, and other settings.

### Running

```bash
# Paper trading (default, no real orders)
python -m src.bot

# With custom config
python -m src.bot config/settings.yaml
```

To switch to live trading, change `mode: live` and `environment: prod` in `config/settings.yaml`.

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
├── tests/                      # Comprehensive test suite (119 tests)
├── logs/                       # Runtime logs
├── data/                       # SQLite database
└── pyproject.toml              # Dependencies and tool config
```

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

## Testing

```bash
# Run all tests
pytest tests/ -v

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
data = pd.read_csv('your_data.csv')  # Prepare historical data
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
