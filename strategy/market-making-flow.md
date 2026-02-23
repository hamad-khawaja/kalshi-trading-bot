# Market Making Strategy Flow

Captures bid-ask spread by placing resting limit orders on both sides of the book.

**Priority**: 6th (lowest) -- always runs alongside or standalone
**Runs when**: spread is wide enough and conditions allow; can run alongside directional (opposite side only)

---

## Core Thesis

When the Kalshi orderbook spread is wide, place resting limit orders on both sides to capture the bid-ask spread. Anchored to model's fair value with vol-aware spread offsets and non-linear inventory skew. Uses post_only orders for maker fees (1.75% vs 7% taker).

---

## Signal Flow

### Gate 1: Extreme Volatility Filter
```
IF mm_vol_filter_enabled AND vol_regime == "extreme":
    RETURN []  -- sit out entirely in extreme vol
```

### Gate 2: Spread Width Check
```
effective_min_spread = mm_min_spread (0.05)
Per-asset override: ETH = 0.08

IF spread < effective_min_spread:
    RETURN []  -- spread too tight, not profitable after fees
IF spread > mm_max_spread (0.30):
    RETURN []  -- spread too wide, dead/illiquid market
```

### Gate 3: Confidence
```
IF confidence < 0.30:
    RETURN []  -- model too uncertain for fair value anchor
```

### Gate 4: Time to Expiry
```
IF tte < 120s:
    RETURN []  -- too close to expiry, risk of settlement
```

### Gate 5: Inventory Cap
```
IF |current_position| >= mm_max_inventory (20):
    RETURN []  -- already holding too much, risk accumulation
```

### Gate 6: Orderbook Levels
```
IF best_yes_bid is None OR best_no_bid is None:
    RETURN []  -- need both sides of the book
```

### Quote Generation

#### Fair Value Anchor
```
fair_value = round(model.probability_yes, 2)
```

#### Vol-Aware Spread Offset
```
offsets by regime:
    low:     $0.01  (tighter quotes, more fills)
    normal:  $0.02
    high:    $0.04  (wider quotes, less adverse selection)
    extreme: blocked (Gate 1)
```

#### Non-Linear Inventory Skew
```
normalized = current_position / max_inventory  (in [-1, 1])
skew = sign(normalized) * normalized^2 * 0.08

Effect: quadratic scaling pushes quotes away from the side
where inventory is building, encouraging mean reversion.
    +10 contracts (50% of 20 max) -> skew = +0.02
    +20 contracts (100% of max)   -> skew = +0.08
```

#### YES Bid Calculation
```
yes_bid = fair_value - spread_offset - max(0, inventory_skew)
yes_bid = max(best_yes_bid + 0.01, yes_bid)   (at least 1c above current bid)
yes_bid = clamp(0.01, 0.99)

Prevent post_only cross:
    yes_ask = 1 - best_no_bid
    IF yes_bid >= yes_ask: yes_bid = yes_ask - 0.01

Profitability check:
    potential_profit = yes_ask - yes_bid
    maker_fee = ceil(0.0175 * 1 * yes_bid * (1 - yes_bid))
    IF potential_profit <= maker_fee: SKIP yes side
```

#### NO Bid Calculation
```
no_fair = 1 - fair_value
no_bid = no_fair - spread_offset + min(0, inventory_skew)
no_bid = max(best_no_bid + 0.01, no_bid)
no_bid = clamp(0.01, 0.99)

Prevent post_only cross:
    no_ask = 1 - best_yes_bid
    IF no_bid >= no_ask: no_bid = no_ask - 0.01

Profitability check:
    potential_profit = no_ask - no_bid
    maker_fee = ceil(0.0175 * 1 * no_bid * (1 - no_bid))
    IF potential_profit <= maker_fee: SKIP no side
```

#### Directional Side Filter
```
IF directional_side == "yes": SKIP YES bid (only quote NO)
IF directional_side == "no":  SKIP NO bid (only quote YES)
```

### Output
```
RETURN [0-2 TradeSignals with signal_type="market_making"]
    - YES bid (if profitable and not filtered)
    - NO bid (if profitable and not filtered)
```

---

## Decision Tree

```
mm_vol_filter AND extreme vol?
    |-- Y: stop
    |-- N
        |
spread in [min_spread, max_spread]?
    |-- N: stop (too tight or too wide)
    |-- Y
        |
confidence >= 0.30?
    |-- N: stop
    |-- Y
        |
tte >= 120s?
    |-- N: stop
    |-- Y
        |
|inventory| < max_inventory (20)?
    |-- N: stop
    |-- Y
        |
both bid levels available?
    |-- N: stop
    |-- Y
        |
Calculate fair_value, spread_offset, inventory_skew
        |
For each side (YES, NO):
    |-- directional filter blocks this side? -> skip
    |-- potential_profit > maker_fee? -> emit quote
    |-- else -> skip
        |
RETURN [0-2 market_making signals]
```

---

## Config Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `use_market_maker` | true | Master toggle |
| `mm_min_spread` | 0.05 | Minimum spread to quote |
| `mm_max_spread` | 0.30 | Maximum spread (wider = dead market) |
| `mm_max_inventory` | 20 | Max contracts before stopping |
| `mm_vol_filter_enabled` | true | Block in extreme vol |
| `asset_mm_min_spread.ETH` | 0.08 | Wider min spread for ETH |
| `asset_market_maker_disabled` | [] | Per-asset MM disable |

---

## Key Characteristics

- **Always runs**: alongside directional (opposite side only) or standalone
- **Maker only**: `post_only=True` for 1.75% fees (vs 7% taker)
- **Both sides**: can emit 0, 1, or 2 quotes per cycle
- **Inventory management**: non-linear quadratic skew pushes quotes toward reducing exposure
- **Vol-aware**: tighter in low vol (more fills), wider in high vol (less adverse selection)
- **Profitability gate**: each quote checked individually against maker fees
- **Directional filter**: when running alongside directional, only quotes opposite side

---

## Blocking Gates Summary

| # | Gate | Reason |
|---|------|--------|
| 1 | Extreme vol | Vol regime filter |
| 2 | Spread too tight | Below min_spread (not profitable) |
| 3 | Spread too wide | Above max_spread (dead market) |
| 4 | Low confidence | Model too uncertain for fair value |
| 5 | Near expiry | Less than 120s remaining |
| 6 | Inventory full | At max_inventory cap |
| 7 | No bid levels | Orderbook missing sides |
| 8 | Not profitable | Spread capture < maker fee |
| 9 | Directional filter | Same side as active directional |
| 10 | Per-asset disabled | Asset-level MM toggle |
| 11 | Session blocked | Time profiler disallows MM |
