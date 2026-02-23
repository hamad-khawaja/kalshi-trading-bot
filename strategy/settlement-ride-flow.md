# Settlement Ride Strategy Flow

Late-window entry that holds to settlement, betting on a decisive outcome.

**Priority**: 5th (after directional, trend continuation, FOMO, certainty scalp)
**Only fires when**: no higher-priority signal exists

---

## Core Thesis

After 10 minutes in a 15-minute window, the market has enough information to form a strong opinion. If the implied probability is far from 50/50 (not a coin flip) and the edge detector finds a directional edge, enter and hold to settlement. The late entry means less time for the trade to go wrong, and settlement exit is free (no sell fees).

---

## Signal Flow

### Gate 1: Strategy Enabled
```
IF NOT settlement_ride_enabled: RETURN None
```

### Gate 2: Per-Asset Disable
```
IF asset in asset_settlement_ride_disabled (e.g. [ETH]):
    RETURN None
```

### Gate 3: Time Elapsed
```
IF time_elapsed < settlement_ride_min_elapsed_seconds (600s / 10 min):
    RETURN None  -- too early in the window
```

### Gate 4: Time to Expiry
```
IF tte <= 60s (MIN_TIME_TO_TRADE_SECONDS):
    RETURN None  -- too close to settlement
```

### Gate 5: Not a Coin Flip
```
implied = orderbook implied YES probability
distance_from_half = |implied - 0.50|

Per-asset overrides:
    BTC: min_implied_distance = 0.12
    ETH: min_implied_distance = 0.22

IF distance_from_half < min_implied_distance:
    RETURN None  -- market is too undecided, coin-flip risk
```

### Gate 6: Edge Detector Confirmation
```
Run full EdgeDetector.detect(prediction, snapshot)
    (bypasses phase gating and streak confirmation)
IF returns None: RETURN None  (failed edge detector gates)
```

### Gate 7: Minimum Edge
```
Per-asset overrides:
    BTC: min_edge = 0.06
    ETH: min_edge = 0.08

IF directional.net_edge < min_edge: RETURN None
```

### Output
```
RETURN TradeSignal(
    signal_type="settlement_ride",
    ...fields copied from edge detector's directional signal
)
```

---

## Decision Tree

```
settlement_ride_enabled?
    |-- N: stop
    |-- Y
        |
asset not disabled? (ETH disabled)
    |-- disabled: stop
    |-- allowed
        |
time_elapsed >= 600s (10 min)?
    |-- N: stop (too early)
    |-- Y
        |
tte > 60s?
    |-- N: stop (too close to settlement)
    |-- Y
        |
|implied - 0.50| >= min_distance (0.12 BTC / 0.22 ETH)?
    |-- N: stop (coin flip)
    |-- Y
        |
edge detector passes all gates?
    |-- N: stop
    |-- Y
        |
net_edge >= min_edge (0.06 BTC / 0.08 ETH)?
    |-- N: stop
    |-- Y
        |
SETTLEMENT RIDE SIGNAL EMITTED
```

---

## Config Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `settlement_ride_enabled` | true | Master toggle |
| `settlement_ride_min_elapsed_seconds` | 600.0 | Only after 10 min elapsed |
| `settlement_ride_min_edge` | 0.06 | Minimum net edge (BTC default) |
| `settlement_ride_min_implied_distance` | 0.12 | Min distance from 0.50 (BTC default) |
| `settlement_ride_kelly_fraction` | 0.10 | Conservative sizing |
| `asset_settlement_ride_disabled` | [ETH] | Disabled for ETH |
| `asset_settlement_ride_min_edge.ETH` | 0.08 | ETH needs higher edge |
| `asset_settlement_ride_min_implied_distance.ETH` | 0.22 | ETH needs more decisive market |

---

## Key Characteristics

- **BTC only** in current config (ETH disabled)
- **Late window**: only fires after 10 min of 15 min window
- **Reuses edge detector**: inherits all 16+ gates from directional detection
- **Hold to settlement**: no sell needed, free exit
- **Conservative sizing**: kelly_fraction = 0.10 (vs 0.15 directional)
- **Higher thresholds for ETH**: 0.08 edge / 0.22 distance (if re-enabled)

---

## Blocking Gates Summary

| # | Gate | Reason |
|---|------|--------|
| 1 | Disabled | Config toggle off |
| 2 | Asset disabled | ETH currently disabled |
| 3 | Too early | Less than 10 min elapsed |
| 4 | Too late | Less than 60s to expiry |
| 5 | Coin flip | Implied prob too close to 0.50 |
| 6 | Edge detector fails | Any of the 16+ edge detector gates |
| 7 | Edge too small | Below 0.06 (BTC) or 0.08 (ETH) |
