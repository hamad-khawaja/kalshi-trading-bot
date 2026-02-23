# Certainty Scalp Strategy Flow

Bets large on near-certain outcomes in the last 3 minutes before settlement.

**Priority**: 4th (after directional, trend continuation, FOMO)
**Only fires when**: no higher-priority signal exists

---

## Core Thesis

When both the model and market agree an outcome is near-certain (85%+) with only 1-3 minutes left, buy the likely winner at a high price (e.g. $0.90) for small per-contract profit but very high win rate. Holds to settlement (free exit, no sell fees). Fees are minimal at extreme prices since fee is proportional to p*(1-p).

---

## Signal Flow

### Gate 1: Strategy Enabled
```
IF NOT certainty_scalp_enabled: RETURN None
```

### Gate 2: Time Window
```
IF tte > certainty_scalp_max_ttx (180s / 3 min): RETURN None
IF tte <= certainty_scalp_min_ttx (60s / 1 min): RETURN None

Only fires in the 60-180 second window before settlement.
```

### Gate 3: Market + Model Agreement on Near-Certainty
```
implied = orderbook implied YES probability
model_prob = prediction.probability_yes
min_prob = 0.85

IF implied >= 0.85 AND model_prob >= 0.80:
    side = "yes"  -- both agree YES is near-certain
ELIF implied <= 0.15 AND model_prob <= 0.20:
    side = "no"   -- both agree NO is near-certain
ELSE:
    RETURN None   -- no consensus on near-certain outcome
```

### Gate 4: Spot Price Confirmation
```
IF strike_price AND btc_price available:
    distance_pct = (spot - strike) / strike

    IF side == "yes" AND distance_pct < 0.002 (0.2%):
        RETURN None  -- spot not convincingly above strike
    IF side == "no" AND distance_pct > -0.002:
        RETURN None  -- spot not convincingly below strike
```

### Gate 5: Edge Detector Confirmation
```
Run full EdgeDetector.detect() for price/fee calculation
IF returns None: RETURN None  (failed edge detector gates)
IF directional.side != side: RETURN None  (edge detector disagrees on direction)
IF directional.net_edge < certainty_scalp_min_edge (0.02): RETURN None
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
60s < tte <= 180s?
    |-- N: stop (outside time window)
    |-- Y
        |
implied >= 85% AND model >= 80%?  (or inverse for NO)
    |-- N: stop (not near-certain)
    |-- Y (side determined)
        |
spot price well past strike? (>= 0.2%)
    |-- N: stop (not convincing)
    |-- Y
        |
edge detector passes all gates?
    |-- N: stop
    |-- Y
        |
edge detector agrees on same side?
    |-- N: stop
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
| `certainty_scalp_max_ttx` | 180.0 | Only when TTX <= 3 min |
| `certainty_scalp_min_ttx` | 60.0 | At least 60s to get filled |
| `certainty_scalp_min_implied_prob` | 0.85 | Market must be 85%+ one direction |
| `certainty_scalp_min_model_prob` | 0.80 | Model must agree at 80%+ |
| `certainty_scalp_min_edge` | 0.02 | Low bar (fees tiny at extremes) |
| `certainty_scalp_kelly_fraction` | 0.30 | Aggressive sizing |
| `certainty_scalp_min_spot_distance_pct` | 0.002 | 0.2% spot past strike |

---

## Key Characteristics

- **Taker orders** (`post_only=False`): needs guaranteed fill in final minutes
- **Aggressive sizing**: kelly_fraction = 0.30 (vs 0.15 for directional)
- **Low edge bar**: 0.02 (vs 0.03 for directional) -- fees are tiny at extreme prices
- **Triple confirmation**: market (85%), model (80%), and spot price all must agree
- **Hold to settlement**: no sell needed, free exit at expiry

---

## Blocking Gates Summary

| # | Gate | Reason |
|---|------|--------|
| 1 | Disabled | Config toggle off |
| 2 | Outside time window | Only 60-180s before settlement |
| 3 | Not near-certain | Market or model not extreme enough |
| 4 | Spot not past strike | Price hasn't cleared strike convincingly |
| 5 | Edge detector fails | Any of the 16+ edge detector gates |
| 6 | Side disagreement | Edge detector picked opposite direction |
| 7 | Edge too small | Net edge < 0.02 |
