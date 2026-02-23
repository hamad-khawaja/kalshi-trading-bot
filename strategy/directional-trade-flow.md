# Directional Trade Signal Flow

End-to-end trace of how the bot determines a directional strategy trade.

---

## 1. Data Assembly

Price feeds (Coinbase, Kraken, Bybit, Chainlink) -> `DataHub` builds a `MarketSnapshot` with 30+ fields:

- **Core**: BTC/ETH price, price history arrays (1min, 5min, 30min)
- **Kalshi**: orderbook (levels, implied prob, spread, depth), strike price, time-to-expiry, volume
- **Cross-exchange**: Binance price, spread vs Coinbase, momentum lead/lag
- **Taker flow**: buy/sell volume from Binance futures
- **On-chain**: Chainlink oracle price, divergence from spot
- **Derivatives**: funding rates, predicted funding, liquidation stats
- **Derived**: statistical fair value (Black-Scholes), window phase (1-5), time elapsed

If `btc_price` is None, snapshot returns None and no signal is possible.

---

## 2. Feature Extraction (`FeatureEngine.compute`)

`FeatureEngine` computes **27 normalized features** from the snapshot:

| Category | Features |
|----------|----------|
| **Momentum** (5) | `momentum_15s`, `momentum_60s`, `momentum_180s`, `momentum_600s`, `momentum_1800s` — percentage change over each window |
| **Technical** (6) | `realized_vol_5min`, `rsi_14`, `bollinger_position`, `macd_histogram`, `roc_acceleration`, `volume_weighted_momentum` |
| **Orderbook** (4) | `order_flow_imbalance`, `orderbook_depth_imbalance`, `orderbook_top_concentration`, `orderbook_support_resistance` |
| **Spread** (2) | `spread`, `spread_ratio` |
| **Time** (1) | `time_to_expiry_normalized` (tte / 900) |
| **Cross-exchange** (2) | `cross_exchange_spread`, `cross_exchange_lead` |
| **Taker flow** (1) | `taker_buy_sell_ratio` |
| **Settlement** (1) | `settlement_bias` (exponential-decay-weighted recent YES/NO outcomes) |
| **BTC beta** (1) | `btc_beta_signal` (BTC momentum leading, only non-zero for ETH) |
| **Funding** (2) | `funding_rate_signal`, `predicted_funding_signal` |
| **Liquidation** (2) | `liquidation_imbalance`, `liquidation_ratio_divergence` |
| **Chainlink** (1) | `chainlink_divergence` |

Missing data defaults to 0.0 for that feature.

---

## 3. Probability Estimation (`HeuristicModel.predict`)

Combines **16 weighted signals** to produce P(YES):

```
BASE PROBABILITY = 0.50

Signals (weight):
  momentum_signal        (38%) - weighted avg of 5 timeframes, consistency multiplier
  technical_signal       (18%) - avg of BB, MACD, ROC, VWM
  mean_reversion_signal  (10%) - RSI contrarian, suppressed during confirmed trends
  cross_exchange_signal   (7%) - 70% lead + 30% spread
  btc_beta_signal         (6%) - BTC momentum leading for ETH
  cross_asset_divergence  (3%) - lagging asset catches up to leader
  order_flow_signal       (2%) - 15% flow + 15% concentration + 70% walls
  chainlink_oracle        (2%) - amplified 1.5x if round just updated
  funding_rate            (2%) - crowded longs = bearish
  funding_divergence      (2%) - cross-asset funding divergence
  hour_of_day             (1%) - US session +0.2, overnight -0.5
  taker_flow              (1%) - buy/sell ratio
  predicted_funding       (1%) - next epoch prediction
  liquidation_imbalance   (1%) - long liqs = bearish
  liquidation_divergence  (1%) - cross-asset long concentration
  settlement_bias         (0%) - disabled in v2
```

### Key Processing Steps

1. **Consensus gate**: if < 3 signals have |value| > 0.05, output is zeroed. If majority agreement < 60%, dampened to 30%.
2. **Momentum consistency bonus**: if all 5 timeframes agree and |signal| > 0.3, allow more extreme probabilities (0.22 -> 0.45 cap).
3. **Dead zone**: if |probability - 0.50| < 0.03, snap to 0.50 (suppress marginal signals).
4. **EMA smoothing**: blend 75/25 with previous prediction unless delta > 0.08 (snap on big moves).
5. **Market anchor**: blend model probability toward orderbook implied probability:
   - Model agrees with market: 55% model / 45% implied
   - Model disagrees: 30% model / 70% implied (market is right 83% of the time)
6. **Confidence estimation**: based on signal agreement, spread width, volatility, time remaining, volume, depth alignment.

### Output
`PredictionResult`: `probability_yes` [0.05, 0.95], `confidence` [0.0, 1.0]

---

## 4. Edge Detection (`EdgeDetector.detect`)

### Step 1: Orderbook Liquidity Check

```
IF spread > max_spread OR total_depth < min_depth:
    orderbook_is_thin = TRUE
    IF statistical_fair_value available:
        implied = fair_value (Black-Scholes estimate)
        using_fair_value = TRUE  (triggers 1.5x edge multiplier later)
    ELSE:
        RETURN None  -- cannot compute edge
ELSE:
    implied = orderbook midpoint implied probability
```

### Step 2: Direction & Raw Edge

```
IF model_prob > implied:
    side = "yes",  raw_edge = model_prob - implied,  trade_price = implied
ELSE:
    side = "no",   raw_edge = implied - model_prob,  trade_price = 1 - implied
```

### Step 3: Fee Drag

```
fee = ceil(0.0175 * count * price * (1 - price))   (maker rate, post_only)
net_edge = raw_edge - fee_drag
```

Fee is maximized at price=0.50 (~$0.0175/contract), approaches zero at extremes.

### Step 4: Pre-Threshold Filters

| Filter | Condition | Action |
|--------|-----------|--------|
| Min entry price | `trade_price < 0.30` | BLOCK |
| Vol regime | `realized_vol_5min > max` | BLOCK |
| Zone filter | `trade_price > 0.60` (zone 4-5) | BLOCK |

### Step 5: Adaptive Threshold Calculation

Eight multipliers stack on top of `min_edge_threshold` (0.03):

```
min_threshold = 0.03  (base)

1. Vol adjustment     -- low=0.8x, normal=1.0x, high=1.5x, extreme=1.5x
2. Session multiplier -- time-of-day profiler
3. Thin-book mult     -- 1.5x if using fair value
4. Zone multiplier    -- [0.6, 0.8, 1.0] for zones 1, 2, 3
5. Time-decay mult    -- 1.0x at 7.5min -> 1.8x at 1min remaining
6. YES-side penalty   -- 1.4x (YES empirically worse win rate)
7. NO-side penalty    -- 1.5x (model overconfident on contrarian NO)
8. Per-asset mult     -- ETH=1.4x (noisier than BTC)
```

### Step 6: Threshold Checks

```
IF net_edge < min_threshold:          BLOCK (edge too small)
IF net_edge > max_threshold:          BLOCK (suspicious mispricing)
    UNLESS zone <= 2 AND net_edge <= 2x max_threshold (cheap zone exception)
```

### Step 7: Confidence & Quality Gates

```
IF confidence < 0.55:                 BLOCK
quality_score = (net_edge/min_threshold)*0.5 + (confidence/1.0)*0.5
IF quality_score < 0.80:              BLOCK
```

### Step 8: Price Calculation

```
YES side:
    target = implied + raw_edge * 0.6  (capture 60% of edge)
    price = max(best_bid + 0.01, target)
    price = min(best_ask - 0.01, price)  (avoid post_only rejection)

    Thin book: price = bid + 0.01, capped at ask - 0.02

NO side: similar using (1 - implied)
```

### Step 9: Final Order Price Check

```
IF float(suggested_price) < 0.30:     BLOCK (order price too cheap)
```

This catches thin-book scenarios where fair value passes (~$0.35) but actual order price is anchored to a $0.02 bid.

### Output
`TradeSignal(signal_type="directional", side, net_edge, suggested_price, confidence, entry_zone)`

---

## 5. Signal Combiner Gates (`SignalCombiner.evaluate`)

Even after edge detection succeeds, 6 additional gates in the combiner:

### Gate 1: Time-to-Expiry
```
IF tte < 60 seconds: BLOCK (no new positions in final minute)
```

### Gate 2: Quiet Hours
```
IF current EST hour in [18-23, 0-4]: BLOCK directional (MM still allowed)
```

### Gate 3: Asset Disabled
```
IF asset in directional_disabled list: BLOCK
    UNLESS btc_beta_signal >= 0.20 (BTC leading allows ETH directional)
```

### Gate 4: Phase Gating
```
Phase 1 (0-7min):   BLOCK (observation, track direction for bounce-back)
Phase 2 (7-9min):   REQUIRE momentum reversal from Phase 1 direction
Phase 3 (9-12min):  ALLOW (active trading phase)
Phase 4 (12-14min): TIGHTEN thresholds (1.3x edge, +0.05 confidence)
Phase 5 (14-15min): BLOCK (lottery tickets near settlement)
```

### Gate 5: Trend Guard
```
Count momentum timeframes (60s, 180s, 600s) above/below threshold:
IF 2+ timeframes oppose trade side:
    BLOCK (don't fight the trend)
    UNLESS settlement_bias > 0.3 overrides (cross-window trend trumps)
```

### Gate 6: Edge Streak Confirmation
```
Track consecutive cycles with same-side edge:
IF streak < 2 (edge_confirmation_cycles): BLOCK (edge not yet confirmed)
```

### On Success
```
signals.append(directional)
Market making can still run alongside (opposite side only)
RETURN [directional_signal, ...optional_mm_signals]
```

---

## Complete Decision Tree

```
Price Feeds (Coinbase, Kraken, Bybit, Chainlink)
    |
    v
MarketSnapshot (30+ fields)
    |
    v
FeatureVector (27 features)
    |
    v
HeuristicModel (16 signals -> P(YES))
    |-- consensus gate (3+ signals required)
    |-- momentum consistency bonus
    |-- market anchor (blend toward implied)
    |-- confidence estimation
    |
    v
EdgeDetector.detect()
    |-- orderbook liquidity check
    |-- direction & edge calculation: net_edge = raw_edge - fee_drag
    |-- min entry price filter ($0.30)
    |-- vol regime filter
    |-- zone filter ($0.60 max)
    |-- 8 adaptive threshold multipliers
    |-- net_edge vs [min_threshold, max_threshold]
    |-- confidence >= 0.55
    |-- quality score >= 0.80
    |-- price calculation (capture 60% of edge)
    |-- final order price >= $0.30
    |
    v
SignalCombiner.evaluate()
    |-- TTX >= 60s
    |-- not quiet hours
    |-- asset directional enabled (or btc_beta override)
    |-- phase gating (phase 1/5 blocked, phase 2 needs bounce-back)
    |-- trend guard (2+ momentum bars must not oppose)
    |-- edge streak >= 2 consecutive cycles
    |
    v
DIRECTIONAL TRADE SIGNAL EMITTED
```

---

## All Blocking Gates Summary

| # | Gate | Location | Level |
|---|------|----------|-------|
| 1 | No price data | DataHub | data |
| 2 | Consensus < 3 signals | HeuristicModel | model |
| 3 | Thin book, no fair value | EdgeDetector | data |
| 4 | Min entry price (implied) | EdgeDetector | filter |
| 5 | Vol regime extreme | EdgeDetector | filter |
| 6 | Zone too expensive | EdgeDetector | filter |
| 7 | Net edge < threshold | EdgeDetector | threshold |
| 8 | Net edge > max threshold | EdgeDetector | threshold |
| 9 | Confidence too low | EdgeDetector | threshold |
| 10 | Quality score too low | EdgeDetector | threshold |
| 11 | Thin book no levels | EdgeDetector | data |
| 12 | Thin book tight spread | EdgeDetector | data |
| 13 | Min entry price (order) | EdgeDetector | filter |
| 14 | TTX < 60s | SignalCombiner | timing |
| 15 | Quiet hours | SignalCombiner | timing |
| 16 | Asset directional disabled | SignalCombiner | config |
| 17 | Phase 1 observation | SignalCombiner | timing |
| 18 | Phase 2 no bounce-back | SignalCombiner | timing |
| 19 | Phase 4 tightened | SignalCombiner | timing |
| 20 | Phase 5 final | SignalCombiner | timing |
| 21 | Trend guard | SignalCombiner | momentum |
| 22 | Edge streak < required | SignalCombiner | persistence |
