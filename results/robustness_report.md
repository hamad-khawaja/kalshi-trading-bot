# Parameter Robustness Report

**Generated:** 2026-02-22 19:00 UTC
**Database:** `data/bot.db`
**Total exit trades:** 877

## Summary

| Parameter | Baseline | Sensitivity ($/unit) | Fragile? |
|-----------|----------|---------------------|----------|
| `min_edge_threshold` | 0.03 | -1165.46 | no |
| `confidence_min` | 0.55 | -3.26 | no |
| `yes_side_edge_multiplier` | 1.4 | -29.53 | no |
| `min_entry_price` | 0.3 | +638.77 | YES |
| `stop_loss_pct` | 0.35 | -1350.31 | YES |
| `take_profit_min_profit_cents` | 0.1 | -0.00 | no |
| `min_quality_score` | 0.8 | -207.44 | no |
| `kelly_fraction` | 0.15 | -928.04 | no |

## min_edge_threshold
*Minimum net edge to enter a trade* (baseline: 0.03)

| Value | Trades | Win% | Total P&L | Avg P&L | Sharpe | Max DD | PF |
|------:|-------:|-----:|----------:|--------:|------:|-------:|---:|
| 0.01 | 741 | 44.8% | $-105.59 | $-0.1425 | -0.018 | $-234.15 | 0.94 |
| 0.02 | 726 | 44.6% | $-110.43 | $-0.1521 | -0.019 | $-234.15 | 0.93 |
|  **0.03** | 714 | 44.1% | $-115.06 | $-0.1612 | -0.020 | $-234.15 | 0.93 |
| 0.04 | 693 | 43.6% | $-147.85 | $-0.2133 | -0.026 | $-237.72 | 0.91 |
| 0.05 | 681 | 43.2% | $-160.27 | $-0.2353 | -0.029 | $-250.42 | 0.90 |
| 0.06 | 644 | 42.7% | $-150.71 | $-0.2340 | -0.029 | $-271.43 | 0.90 |

## confidence_min
*Minimum model confidence (proxy: |prob - 0.50| * 2)* (baseline: 0.55)

| Value | Trades | Win% | Total P&L | Avg P&L | Sharpe | Max DD | PF |
|------:|-------:|-----:|----------:|--------:|------:|-------:|---:|
| 0.45 | 110 | 50.0% | $+10.00 | $+0.0909 | 0.009 | $-155.82 | 1.03 |
| 0.50 | 109 | 49.5% | $+8.86 | $+0.0813 | 0.008 | $-155.82 | 1.03 |
|  **0.55** | 109 | 49.5% | $+8.86 | $+0.0813 | 0.008 | $-155.82 | 1.03 |
| 0.60 | 109 | 49.5% | $+8.86 | $+0.0813 | 0.008 | $-155.82 | 1.03 |
| 0.65 | 109 | 49.5% | $+8.86 | $+0.0813 | 0.008 | $-155.82 | 1.03 |
| 0.70 | 109 | 49.5% | $+8.86 | $+0.0813 | 0.008 | $-155.82 | 1.03 |

## yes_side_edge_multiplier
*Extra edge required for YES-side trades* (baseline: 1.4)

| Value | Trades | Win% | Total P&L | Avg P&L | Sharpe | Max DD | PF |
|------:|-------:|-----:|----------:|--------:|------:|-------:|---:|
| 1.00 | 714 | 44.1% | $-115.06 | $-0.1612 | -0.020 | $-234.15 | 0.93 |
| 1.20 | 714 | 44.1% | $-115.06 | $-0.1612 | -0.020 | $-234.15 | 0.93 |
|  **1.40** | 714 | 44.1% | $-115.06 | $-0.1612 | -0.020 | $-234.15 | 0.93 |
| 1.60 | 711 | 44.2% | $-109.15 | $-0.1535 | -0.019 | $-234.15 | 0.93 |
| 1.80 | 707 | 43.9% | $-133.88 | $-0.1894 | -0.023 | $-237.72 | 0.92 |
| 2.00 | 700 | 43.7% | $-146.29 | $-0.2090 | -0.026 | $-237.72 | 0.91 |

## min_entry_price
*Minimum contract price to enter* (baseline: 0.3)

| Value | Trades | Win% | Total P&L | Avg P&L | Sharpe | Max DD | PF |
|------:|-------:|-----:|----------:|--------:|------:|-------:|---:|
| 0.15 | 847 | 48.6% | $-109.61 | $-0.1294 | -0.017 | $-243.11 | 0.94 |
| 0.20 | 825 | 49.2% | $-99.89 | $-0.1211 | -0.016 | $-243.11 | 0.94 |
| 0.25 | 800 | 50.0% | $-71.64 | $-0.0895 | -0.012 | $-243.11 | 0.96 |
|  **0.30** | 666 | 53.8% | $+200.09 | $+0.3004 | 0.043 | $-109.20 | 1.18 |
| 0.35 | 514 | 57.4% | $+11.48 | $+0.0223 | 0.004 | $-64.50 | 1.02 |
| 0.40 | 369 | 59.9% | $-7.21 | $-0.0195 | -0.004 | $-52.09 | 0.98 |

## stop_loss_pct
*Stop-loss threshold as % of entry price* (baseline: 0.35)

| Value | Trades | Win% | Total P&L | Avg P&L | Sharpe | Max DD | PF |
|------:|-------:|-----:|----------:|--------:|------:|-------:|---:|
| 0.20 | 877 | 47.4% | $+311.27 | $+0.3549 | 0.054 | $-157.52 | 1.24 |
| 0.25 | 877 | 47.4% | $+198.78 | $+0.2267 | 0.034 | $-177.46 | 1.14 |
| 0.30 | 877 | 47.4% | $+119.56 | $+0.1363 | 0.020 | $-187.93 | 1.08 |
|  **0.35** | 877 | 47.4% | $+53.54 | $+0.0611 | 0.009 | $-197.78 | 1.03 |
| 0.40 | 877 | 47.4% | $+1.31 | $+0.0015 | 0.000 | $-207.54 | 1.00 |
| 0.50 | 877 | 47.4% | $-106.17 | $-0.1211 | -0.017 | $-224.75 | 0.94 |

## take_profit_min_profit_cents
*Minimum net profit per contract for take-profit* (baseline: 0.1)

| Value | Trades | Win% | Total P&L | Avg P&L | Sharpe | Max DD | PF |
|------:|-------:|-----:|----------:|--------:|------:|-------:|---:|
| 0.05 | 877 | 47.4% | $-139.21 | $-0.1587 | -0.022 | $-243.11 | 0.92 |
| 0.08 | 877 | 47.4% | $-139.21 | $-0.1587 | -0.022 | $-243.11 | 0.92 |
|  **0.10** | 877 | 47.4% | $-139.21 | $-0.1587 | -0.022 | $-243.11 | 0.92 |
| 0.12 | 877 | 47.4% | $-139.21 | $-0.1587 | -0.022 | $-243.11 | 0.92 |
| 0.15 | 877 | 47.4% | $-139.21 | $-0.1587 | -0.022 | $-243.11 | 0.92 |
| 0.20 | 877 | 47.4% | $-139.21 | $-0.1587 | -0.022 | $-243.11 | 0.92 |

## min_quality_score
*Minimum combined edge + confidence quality gate* (baseline: 0.8)

| Value | Trades | Win% | Total P&L | Avg P&L | Sharpe | Max DD | PF |
|------:|-------:|-----:|----------:|--------:|------:|-------:|---:|
| 0.60 | 712 | 44.0% | $-117.48 | $-0.1650 | -0.020 | $-234.15 | 0.93 |
| 0.70 | 698 | 43.7% | $-139.38 | $-0.1997 | -0.025 | $-237.72 | 0.91 |
|  **0.80** | 687 | 43.2% | $-169.55 | $-0.2468 | -0.030 | $-256.07 | 0.90 |
| 0.90 | 681 | 43.3% | $-160.12 | $-0.2351 | -0.029 | $-256.07 | 0.90 |
| 0.95 | 671 | 43.2% | $-203.54 | $-0.3033 | -0.038 | $-303.97 | 0.87 |

## kelly_fraction
*Fractional Kelly sizing multiplier* (baseline: 0.15)

| Value | Trades | Win% | Total P&L | Avg P&L | Sharpe | Max DD | PF |
|------:|-------:|-----:|----------:|--------:|------:|-------:|---:|
| 0.05 | 877 | 47.4% | $-46.40 | $-0.0529 | -0.022 | $-81.04 | 0.92 |
| 0.10 | 877 | 47.4% | $-92.81 | $-0.1058 | -0.022 | $-162.07 | 0.92 |
|  **0.15** | 877 | 47.4% | $-139.21 | $-0.1587 | -0.022 | $-243.11 | 0.92 |
| 0.20 | 877 | 47.4% | $-185.61 | $-0.2116 | -0.022 | $-324.15 | 0.92 |
| 0.25 | 877 | 47.4% | $-232.01 | $-0.2646 | -0.022 | $-405.18 | 0.92 |

## Recommendations

**Fragile parameters** (P&L sign changes across range):
- `min_entry_price` — consider widening or removing this gate
- `stop_loss_pct` — consider widening or removing this gate

Fragile parameters are most likely to be overfit. Small market regime changes will flip these from profitable to losing. Consider: (1) widening the parameter range that stays profitable, (2) reducing the parameter's influence, or (3) using an adaptive version that adjusts to market conditions.

---
*Limitations: Confidence proxy uses |model_prob - 0.50| * 2 (actual confidence includes spread/vol/depth). Exit parameter replay is conservative — without intra-trade tick data, we keep actual P&L for trades whose exit type doesn't match. Entry-gate replay ignores dynamic adjustments (vol regime, session multipliers, time-decay scaling).*