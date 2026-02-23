# Trend Continuation Strategy Flow

Enters early in the window when recent settlements show a persistent directional streak.

**Priority**: 2nd (after directional, before FOMO)
**Only fires when**: no directional signal exists

---

## Core Thesis

When BTC grinds persistently in one direction across multiple 15-min windows, early prices (phase 1-2) are still near 50/50 -- a perfect entry point. Settlement history (last N windows all settled same direction) provides the signal. Enters on the continuation side before the market moves to extremes.

---

## Signal Flow

### Gate 1: Strategy Enabled
```
IF NOT trend_continuation_enabled: RETURN None
```

### Gate 2: Phase Gate (Early Window Only)
```
IF window_phase > trend_continuation_max_phase (2):
    RETURN None  -- only fire in phases 1-2 (first ~9 min)
    Clean up entered_markets tracking
```

### Gate 3: Single Entry Per Market
```
IF current_position != 0 OR ticker in entered_markets:
    RETURN None  -- no accumulation into a losing position
```

### Gate 4: Settlement Streak Check
```
Extract asset symbol from ticker (e.g. "KXBTC15M-..." -> "BTC")
history = settlement_history[asset]  (shared from DashboardState)

IF len(history) < min_streak (2):
    RETURN None  -- not enough data

recent = last N settlements
IF NOT all same result (all YES or all NO):
    RETURN None  -- mixed results, no clear trend

streak_direction = "yes" or "no"
```

### Gate 5: Momentum Confirmation
```
mom_60s = features.momentum_60s
threshold = trend_continuation_momentum_threshold (0.001)

IF streak == "no" AND mom_60s > +threshold:
    RETURN None  -- current window price rising, fights NO streak
IF streak == "yes" AND mom_60s < -threshold:
    RETURN None  -- current window price falling, fights YES streak
```

### Gate 6: Implied Probability Range
```
implied = orderbook implied YES probability

IF implied < min_implied_prob (0.35):
    RETURN None  -- already too extreme toward NO
IF implied > max_implied_prob (0.65):
    RETURN None  -- already too extreme toward YES
```

### Edge Calculation
```
side = streak_direction  (continuation: same as recent settlements)
streak_prob = 0.65  (our assumed probability for continuation)

IF side == "yes":
    raw_edge = streak_prob - implied
    trade_price = implied
ELSE:
    raw_edge = streak_prob - (1 - implied)
    trade_price = 1 - implied

fee_drag = ceil(0.0175 * 1 * trade_price * (1 - trade_price))
net_edge = raw_edge - fee_drag
```

### Gate 7: Minimum Edge
```
IF net_edge < trend_continuation_min_edge (0.04):
    RETURN None
```

### Price Calculation
```
target = implied + raw_edge * 0.3  (capture 30% of edge, conservative)
price = max(best_bid + 0.01, target)
price = min(best_ask - 0.01, price)

IF no ask available: RETURN None
```

### Output
```
Mark ticker as entered (prevent re-entry this window)
RETURN TradeSignal(signal_type="trend_continuation", ...)
```

---

## Decision Tree

```
trend_continuation_enabled?
    |-- N: stop
    |-- Y
        |
phase <= max_phase (2)?
    |-- N: stop (only early window)
    |-- Y
        |
already entered this market?
    |-- Y: stop (single entry per window)
    |-- N
        |
settlement history has >= 2 entries?
    |-- N: stop
    |-- Y
        |
last N settlements all same direction?
    |-- N: stop (mixed results)
    |-- Y (streak_direction = "yes" or "no")
        |
60s momentum agrees with streak?
    |-- N: stop (current window reverting)
    |-- Y
        |
implied prob in [0.35, 0.65]?
    |-- N: stop (already too extreme)
    |-- Y
        |
net_edge >= 0.04?
    |-- N: stop
    |-- Y
        |
ask available for price cap?
    |-- N: stop
    |-- Y
        |
TREND CONTINUATION SIGNAL EMITTED
```

---

## Config Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `trend_continuation_enabled` | true | Master toggle |
| `trend_continuation_min_streak` | 2 | Minimum consecutive same-direction settlements |
| `trend_continuation_max_phase` | 2 | Only enter in phases 1-2 |
| `trend_continuation_min_implied_prob` | 0.35 | Don't enter if implied already extreme toward NO |
| `trend_continuation_max_implied_prob` | 0.65 | Don't enter if implied already extreme toward YES |
| `trend_continuation_streak_prob` | 0.65 | Assumed P(continuation) for edge calculation |
| `trend_continuation_min_edge` | 0.04 | Minimum net edge after fees |
| `trend_continuation_kelly_fraction` | 1.50 | Position sizing multiplier (10x for paper) |
| `trend_continuation_momentum_threshold` | 0.001 | Skip if 60s momentum fights streak by > 0.1% |

---

## Blocking Gates Summary

| # | Gate | Reason |
|---|------|--------|
| 1 | Disabled | Config toggle off |
| 2 | Phase too late | Only phases 1-2 (first ~9 min) |
| 3 | Already entered | One entry per market per window |
| 4 | Not enough history | Need >= 2 settlements |
| 5 | Mixed settlements | No clear streak |
| 6 | Momentum fights streak | Current window reverting |
| 7 | Implied too extreme | Market already priced the trend |
| 8 | Edge too small | Not enough edge after fees |
| 9 | No ask level | Can't safely cap price |
