# FOMO Exploitation Strategy Flow

Detects retail panic and generates contrarian signals to buy the underpriced side.

**Priority**: 3rd (after directional and trend continuation)
**Only fires when**: no directional or trend continuation signal exists
**Currently**: DISABLED in config (`fomo_enabled: false`)

---

## Core Thesis

- BTC direction predicts 15-min binary resolution 96.6% of the time (n=29)
- When retail panics and pushes prices to extremes, the OPPOSITE side becomes underpriced
- Fee advantage at extremes: ~0.2% at $0.20 vs 1.56% at $0.50

The strategy is contrarian: when BTC moves up and retail aggressively buys YES, it buys NO (and vice versa).

---

## Signal Flow

### Gate 1: Strategy Enabled
```
IF NOT fomo_enabled: RETURN None
```

### Gate 2: Implied Probability Available
```
IF no implied_yes_prob from orderbook: RETURN None
```

### Gate 3: Momentum Analysis
```
Analyze 4 timeframes: momentum_15s, 60s, 180s, 600s

direction = weighted consensus (+1=up, -1=down, 0=mixed)
    weights: 10% 15s, 20% 60s, 30% 180s, 40% 600s
magnitude = |momentum_600s|
consistent = all nonzero timeframes agree on direction

IF direction == 0: RETURN None  (no clear momentum)
IF consistency required AND NOT consistent: RETURN None
IF magnitude < fomo_momentum_min_magnitude (0.003): RETURN None
```

### Gate 4: Divergence Detection (Contrarian Logic)
```
BTC UP (direction > 0):
    Retail buys YES aggressively -> implied YES inflated
    divergence = implied - model_prob  (how much retail overpaid)
    underpriced_side = "no"
    trade_price = 1 - implied

BTC DOWN (direction < 0):
    Retail buys NO aggressively -> implied YES deflated
    divergence = model_prob - implied  (how much NO is overpaid)
    underpriced_side = "yes"
    trade_price = implied
```

### Gate 5: Min Entry Price
```
IF trade_price < min_entry_price (0.30): RETURN None
```

### Gate 6: Minimum Divergence
```
IF divergence < fomo_min_divergence (0.20): RETURN None
```

### Gate 7: Implied Range Check
```
IF implied > fomo_max_implied_prob (0.85): RETURN None
IF implied < fomo_min_implied_prob (0.15): RETURN None
```

### Gate 8: Confidence Gate
```
IF confidence < fomo_min_confidence (0.80): RETURN None
```

### Gate 9: FOMO Score
```
Composite score in [0, 1]:
    40% divergence_score = tanh(divergence / 0.20)
    30% momentum_score   = tanh(magnitude / 0.008)
    15% fee_score         = 1 - (price*(1-price) / 0.25)  (extreme prices = tiny fees)
    15% time_score        = min(1, tte / 600)  (more time = more reversion chance)

IF fomo_score < fomo_min_score (0.65): RETURN None
```

### Gate 10: Edge Threshold
```
raw_edge = divergence
fee_drag = ceil(0.0175 * 1 * trade_price * (1 - trade_price))
net_edge = raw_edge - fee_drag

IF net_edge < fomo_edge_threshold (0.08): RETURN None
```

### Price Calculation
```
target = implied + raw_edge * 0.5  (capture 50% of edge)
price = max(best_bid + 0.01, target)
price = min(best_ask - 0.01, price)

IF no ask available: RETURN None
```

### Output
```
RETURN TradeSignal(signal_type="fomo", side=underpriced_side, ...)
```

---

## Decision Tree

```
fomo_enabled?
    |-- N: stop
    |-- Y
        |
implied probability available?
    |-- N: stop
    |-- Y
        |
clear momentum direction?
    |-- N: stop (mixed/weak)
    |-- Y
        |
momentum consistent across timeframes?
    |-- N: stop (required)
    |-- Y
        |
momentum magnitude >= 0.003?
    |-- N: stop (too weak)
    |-- Y
        |
trade_price >= $0.30?
    |-- N: stop (too cheap)
    |-- Y
        |
divergence >= 0.20?
    |-- N: stop (retail not panicking enough)
    |-- Y
        |
implied in [0.15, 0.85]?
    |-- N: stop (too extreme)
    |-- Y
        |
confidence >= 0.80?
    |-- N: stop (uncertain)
    |-- Y
        |
fomo_score >= 0.65?
    |-- N: stop
    |-- Y
        |
net_edge >= 0.08?
    |-- N: stop
    |-- Y
        |
ask available for price cap?
    |-- N: stop
    |-- Y
        |
FOMO SIGNAL EMITTED (contrarian: buy opposite of retail panic)
```

---

## Config Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `fomo_enabled` | false | Master toggle (currently disabled) |
| `fomo_min_divergence` | 0.20 | Minimum gap between implied and model |
| `fomo_edge_threshold` | 0.08 | Minimum net edge after fees |
| `fomo_min_score` | 0.65 | Minimum composite FOMO score |
| `fomo_min_confidence` | 0.80 | Minimum model confidence |
| `fomo_momentum_min_magnitude` | 0.003 | Minimum |momentum_600s| |
| `fomo_momentum_consistency_required` | true | All timeframes must agree |
| `fomo_max_implied_prob` | 0.85 | Upper implied prob bound |
| `fomo_min_implied_prob` | 0.15 | Lower implied prob bound |

---

## Blocking Gates Summary

| # | Gate | Reason |
|---|------|--------|
| 1 | Disabled | Config toggle off (currently disabled) |
| 2 | No implied prob | Orderbook empty |
| 3 | No clear momentum | Mixed/weak price movement |
| 4 | Inconsistent momentum | Timeframes disagree |
| 5 | Weak momentum | Magnitude < 0.3% |
| 6 | Trade price too cheap | Below min_entry_price |
| 7 | Divergence too small | Retail not panicking enough |
| 8 | Implied out of range | Too extreme already |
| 9 | Low confidence | Model uncertain |
| 10 | Low FOMO score | Composite score weak |
| 11 | Low edge | Not enough edge after fees |
| 12 | No ask level | Can't cap price safely |
