# Certainty Scalp Strategy Flow

Exploits vol-mispricing on near-certain outcomes in the last 4 minutes before settlement.

**Priority**: 4th (after directional, trend continuation, FOMO)
**Only fires when**: no higher-priority signal exists

---

## Core Thesis

When BTC is sitting safely above/below strike with low volatility and 1-4 minutes left, the market often overprices the probability of a reversal. A $0.92 YES contract might have a mathematical (Black-Scholes) probability of 97-99%, creating a mispriced volatility edge. Buy the near-certain side, hold to settlement (free exit), profit from the vol gap. Fees are minimal at extreme prices since fee is proportional to p*(1-p).

---

## Signal Flow

### Gate 1: Strategy Enabled
```
IF NOT certainty_scalp_enabled: RETURN None
```

### Gate 2: Time Window
```
IF tte > certainty_scalp_max_ttx (240s / 4 min): RETURN None
IF tte <= certainty_scalp_min_ttx (60s / 1 min): RETURN None

Only fires in the 60-240 second window before settlement.
```

### Gate 3: Determine Side (Two Paths)

#### Path 1: Vol-Based (Preferred)
```
Requires: low/normal vol regime AND strike AND spot price AND 5min price history

fair_value_prob = Black-Scholes P(BTC > strike) from:
    - current spot price
    - strike price
    - realized vol (from 5-min price history)
    - time to expiry

IF fair_value_prob >= 0.95 AND implied >= 0.85:
    side = "yes", trigger = "vol_based"
ELIF fair_value_prob <= 0.05 AND implied <= 0.15:
    side = "no", trigger = "vol_based"
```

#### Path 2: Legacy Model-Based (Fallback)
```
IF implied >= 0.85 AND model_prob >= 0.80:
    side = "yes", trigger = "legacy"
ELIF implied <= 0.15 AND model_prob <= 0.20:
    side = "no", trigger = "legacy"
ELSE:
    RETURN None
```

### Gate 4: Spot Price Confirmation
```
IF strike_price AND spot_price available:
    distance_pct = (spot - strike) / strike

    IF side == "yes" AND distance_pct < 0.002 (0.2%):
        RETURN None  -- spot not convincingly above strike
    IF side == "no" AND distance_pct > -0.002:
        RETURN None  -- spot not convincingly below strike
```

### Gate 5: Edge Calculation

#### Vol-Based Path
```
IF side == "yes":
    raw_edge = fair_value_prob - implied
ELSE:
    raw_edge = (1 - fair_value_prob) - (1 - implied)

fee_drag = taker_fee(trade_price)
net_edge = raw_edge - fee_drag

IF net_edge < 0.02: RETURN None
Price from best available ask level.
```

#### Legacy Path
```
Run full EdgeDetector.detect() for price/fee calculation
IF returns None: RETURN None
IF directional.side != side: RETURN None
IF directional.net_edge < 0.02: RETURN None
```

### Output
```
RETURN TradeSignal(
    signal_type="certainty_scalp",
    post_only=False,  -- TAKER order (need guaranteed fill in final minutes)
    ...
)
```

---

## Decision Tree

```
certainty_scalp_enabled?
    |-- N: stop
    |-- Y
        |
60s < tte <= 240s?
    |-- N: stop (outside time window)
    |-- Y
        |
vol regime low/normal AND price history available?
    |-- Y: compute fair_value_prob (Black-Scholes)
    |       |
    |   fair_value >= 95% AND implied >= 85%?
    |       |-- Y: side determined (vol_based trigger)
    |       |-- N: fall through to legacy
    |
    |-- N: skip to legacy
        |
implied >= 85% AND model >= 80%? (or inverse for NO)
    |-- N: stop (not near-certain)
    |-- Y: side determined (legacy trigger)
        |
spot price well past strike? (>= 0.2%)
    |-- N: stop (not convincing)
    |-- Y
        |
net_edge >= 0.02?
    |-- N: stop
    |-- Y
        |
CERTAINTY SCALP SIGNAL EMITTED (taker order)
```

---

## Config Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `certainty_scalp_enabled` | true | Master toggle |
| `certainty_scalp_max_ttx` | 240.0 | Only when TTX <= 4 min |
| `certainty_scalp_min_ttx` | 60.0 | At least 60s to get filled |
| `certainty_scalp_min_implied_prob` | 0.85 | Market must be 85%+ one direction |
| `certainty_scalp_min_model_prob` | 0.80 | Model must agree at 80%+ (legacy path) |
| `certainty_scalp_min_fair_value_prob` | 0.95 | Vol-based: require 95%+ Black-Scholes prob |
| `certainty_scalp_min_edge` | 0.02 | Low bar (fees tiny at extremes) |
| `certainty_scalp_kelly_fraction` | 0.30 | Aggressive sizing |
| `certainty_scalp_min_spot_distance_pct` | 0.002 | 0.2% spot past strike |

---

## Key Characteristics

- **Two trigger paths**: vol-based (preferred, mathematically grounded) and legacy (model-based fallback)
- **Vol-based path**: uses Black-Scholes fair value from realized vol — the edge is market overpricing reversal risk
- **Only in low/normal vol**: vol-based path requires calm markets (the math assumes log-normal moves)
- **Taker orders** (`post_only=False`): needs guaranteed fill in final minutes
- **Aggressive sizing**: kelly_fraction = 0.30 (vs 0.15 for directional)
- **Low edge bar**: 0.02 (vs 0.03 for directional) — fees are tiny at extreme prices
- **Hold to settlement**: no sell needed, free exit at expiry

---

## Blocking Gates Summary

| # | Gate | Reason |
|---|------|--------|
| 1 | Disabled | Config toggle off |
| 2 | Outside time window | Only 60-240s before settlement |
| 3 | High/extreme vol | Vol-based path requires low/normal vol regime |
| 4 | Not near-certain | Neither vol-based (95%) nor model-based (80%) confirms |
| 5 | Spot not past strike | Price hasn't cleared strike convincingly |
| 6 | Edge too small | Net edge < 0.02 |
| 7 | No ask level | Can't price the order |
| 8 | Edge detector fails | Legacy path: any of the 16+ edge detector gates |
| 9 | Side disagreement | Legacy path: edge detector picked opposite direction |
