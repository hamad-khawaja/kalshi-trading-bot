# Delta Analysis: Feb 20 (Win Spike) → Feb 23 (Current)

Comparing the state at commit `d314c34` (Feb 20) vs `0543ae6` (Feb 23).
Goal: identify what changes hurt performance and should be reverted.

---

## Recommend Reverting

### 1. Market Anchor Weights (Model → Market Follower)

**Feb 20**: `MARKET_ANCHOR_WEIGHT = 0.30`, only applied when model agreed with market direction AND consistency >= 0.5
**Now**: `MARKET_ANCHOR_WEIGHT = 0.45` (agree), `MARKET_ANCHOR_DISAGREE_WEIGHT = 0.70` (disagree), always applied

**Why revert**: At 0.70 disagree weight, the model capitulates to the market — there's no independent edge left. The whole point of the model is to find edge *before* the market prices it in. On Feb 20 it only anchored when the model already agreed, preserving contrarian signals.

**File**: `src/model/predict.py` lines ~81-82, ~377-393

---

### 2. MAX_ADJUSTMENT (Model Can't Have Strong Opinions)

**Feb 20**: `MAX_ADJUSTMENT = 0.30`
**Now**: `MAX_ADJUSTMENT = 0.22`

**Why revert**: Combined with the stronger market anchor, predictions cluster near 0.50 with no conviction. The model range is compressed from [0.20, 0.80] to [0.28, 0.72], reducing signal strength.

**File**: `src/model/predict.py` line ~69

---

### 3. Phase Observation Window (Blocks First Half of Window)

**Feb 20**: `phase_observation_end = 180.0` (3 min)
**Now**: `phase_observation_end = 420.0` (7 min)

**Why revert**: 7 min observation blocks almost half the 15-min window. The winning trades on Feb 20 entered at 3 min+. Consider compromise at 300s (5 min) if 3 min feels too early. Also `phase_confirmation_end` went from 480s → 540s.

**File**: `config/settings.yaml`

---

### 4. Six New Model Signals (Untested Dilution)

**Feb 20**: 11 signals (momentum, technical, flow, mean reversion, time decay, cross exchange, taker, settlement, cross asset, chainlink, btc beta)
**Now**: 17 signals — added hour-of-day, funding rate, predicted funding, liquidation, funding divergence, liquidation ratio

**Current weights**:
- `HOUR_SIGNAL_WEIGHT = 0.01`
- `FUNDING_RATE_WEIGHT = 0.02`
- `PREDICTED_FUNDING_WEIGHT = 0.01`
- `LIQUIDATION_WEIGHT = 0.01`
- `FUNDING_DIVERGENCE_WEIGHT = 0.02`
- `LIQUIDATION_RATIO_WEIGHT = 0.01`

**Why revert**: These are all untested, added post-winning-streak. They dilute the core momentum signal (38%) that was driving profitability. Zero them out until validated separately via backtest.

**File**: `src/model/predict.py` signal weight constants and `predict()` method

---

### 5. Order Flow Signal Rewrite (Untested)

**Feb 20**: `flow_signal = features.order_flow_imbalance * 0.5`
**Now**: `flow_signal = imbalance * 0.15 + top_concentration * 0.15 + support_resistance * 0.70`

**Why revert**: The support/resistance blend (70% weight) was added on Feb 23, completely untested. Revert to simple `imbalance * 0.5`.

**File**: `src/model/predict.py` lines ~138-142

---

### 6. Trend Guard (Too Aggressive Blocking)

**Feb 20**: Blocked only when ALL non-zero momentum timeframes (15s, 60s, 180s, 600s) unanimously agreed against trade direction
**Now**: Blocks when 2 of 3 timeframes (60s, 180s, 600s) have magnitude > 0.0001 against trade direction (majority vote)

**Why revert**: Majority vote blocks significantly more trades than unanimous agreement. The Feb 20 version had a higher bar to block, letting more marginal-but-profitable trades through.

**File**: `src/strategy/signal_combiner.py` trend guard section (~lines 170-220)

---

### 7. Extreme Vol Regime Softening

**Feb 20**: `extreme: edge_mult=2.5, kelly=0.25`
**Now**: `extreme: edge_mult=1.5, kelly=0.50`

**Why revert**: Softening extreme vol protection is risky. The original conservative settings (2.5x edge bar, 0.25 Kelly) protected capital during volatile periods. The softened values allow double the position size with much less edge required.

**File**: `src/risk/volatility.py` lines ~75, ~88

---

## Recommend Keeping

| Change | Reason to Keep |
|---|---|
| `no_side_edge_multiplier: 1.5` | 16.7% WR on NO side is real data — NO trades lose money |
| `yes_side_edge_multiplier: 1.4` | Robustness baseline validated |
| `min_entry_price: 0.30` + bypass fix | Bug fix + robustness baseline |
| `min_edge_threshold: 0.03` | Robustness baseline |
| `confidence_min: 0.55` | Robustness baseline, allows more trades |
| `stop_loss_pct: 0.20` | Robustness data: +$295 vs -$16 at 0.35 |
| Quiet hours → EST | Timezone fix, not a strategy change |
| Per-market EMA smoothing | Bug fix (was blending across different markets) |
| Trend continuation strategy | New strategy with proper gates, doesn't interfere with directional |
| Settlement ride `min_edge: 0.06` | Higher bar is safer |
| All logging additions | Pure observability, no behavior change |
| Dashboard Settings tab | Observability only |
| Min price bypass fix in `edge_detector.py` | Critical bug fix |
| `min_position_size: 5` | Correct (10x scaling applies to maximums, not floors) |
| `asset_mm_min_spread.ETH: 0.08` | Safer MM for noisier asset |
| `asset_settlement_ride_disabled: [ETH]` | ETH settlement ride was unprofitable |
| `btc_beta_min_signal: 0.20` | Allows ETH to follow BTC more easily |
| `asset_edge_multipliers.ETH: 1.4` | Better calibrated than 2.0 |

---

## Summary

The Feb 20 model was **simple and opinionated** — strong momentum signal, independent from market, entered early in the window. Since then:

1. **Model became a market follower** (0.70 disagree anchor)
2. **Signal diluted** with 6 untested inputs (funding, liquidations, hour-of-day)
3. **Gated out of the first 7 minutes** (was 3 min)
4. **Trend guard blocks more trades** (majority vs unanimous)
5. **Order flow rewritten** with untested support/resistance detection
6. **Extreme vol protection weakened**

The core profitable pattern — model detects edge independently, enters before market catches up — got systematically dismantled by well-intentioned but unvalidated changes.

---

## Commits Between Feb 20 and Now

```
0543ae6 Feb 23 - Add strategy flow documentation for all 6 trading strategies
924a762 Feb 23 - Add robust logging, dashboard Settings tab, fix min-price bypass, revert to robustness baselines
8307db6 Feb 23 - Add orderbook support/resistance detection for positional wall awareness
5afd42b Feb 23 - Harden trend continuation: momentum guard, single-entry, phase toggle
c767677 Feb 23 - Fix settlement history labels to match Kalshi website (use open_time)
bb672eb Feb 23 - Add trend continuation strategy, remove Monte Carlo, fix early-window guards
21742a1 Feb 23 - Merge PR #14 (session PnL + BTC toggle)
0041760 Feb 23 - Add predicted funding rate signal and enhanced orderbook concentration
a60110e Feb 23 - Fix total PNL to track session instead of daily, add BTC dashboard toggle
90d66cb Feb 22 - Improve model calibration: reduce overconfidence and NO-side losses
b4c9a7c Feb 22 - Remove standalone MC strategy, keep as model signal #17 only
9a1914c Feb 22 - Rewire MC as model signal #17, add guard toggles and cycle block logging
0c288c1 Feb 22 - Upgrade Monte Carlo: bootstrap resampling, edge cap, settlement discount
389cd30 Feb 22 - Add parameter robustness analysis script and /robustness-check skill
58376ec Feb 22 - Reduce trading fees: maker exits, hold-to-settle, and /test skill
be92b70 Feb 22 - Upgrade market-making strategy and add CLAUDE.md
c46eb37 Feb 22 - Add settlement bias override to trend guard
1aad3af Feb 22 - Tighten trend guard to majority vote with magnitude threshold
9d0aa5b Feb 22 - Re-enable ETH trading with guardrails and cross-asset divergence signals
07efb4b Feb 22 - Extend quiet hours to 6 PM-5 AM EST and log market volume on all trades
40dbce1 Feb 21 - Add performance optimizations and block settlement rides after take-profit
6fddc37 Feb 21 - Add Bybit futures feed (funding rate + liquidations) and convert all timing to EST
1077ad1 Feb 20 - Add comprehensive pre-live audit tests and dollar loss cap for position sizing
d314c34 Feb 20 - Add Monte Carlo strategy, strategy-aware cooldowns, and stop loss improvements
```
