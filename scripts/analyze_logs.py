#!/usr/bin/env python3
"""
Analyze bot.log (structlog JSON format) for trading patterns.

Usage:
    python scripts/analyze_logs.py [path/to/bot.log]

Default log path: logs/bot.log
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev

LOG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/bot.log")

if not LOG_PATH.exists():
    print(f"ERROR: Log file not found at {LOG_PATH.resolve()}")
    print("The bot has not been run yet, or logs were cleared.")
    print(f"Run the bot first, then re-run this script.")
    sys.exit(1)

# -- Parse all JSON log lines --
events = []
parse_errors = 0
with open(LOG_PATH) as f:
    for lineno, line in enumerate(f, 1):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            parse_errors += 1

print(f"Parsed {len(events)} log lines ({parse_errors} parse errors)\n")

# -- Helper: extract events by type --
def get_events(event_name):
    return [e for e in events if e.get("event") == event_name]


# ================================================================
# 1. trade_filled events
# ================================================================
fills = get_events("trade_filled")
print(f"{'='*60}")
print(f"1. TRADE FILLS: {len(fills)} events")
print(f"{'='*60}")
if fills:
    for f_ in fills[:10]:
        print(f"  {f_.get('timestamp','')} | {f_.get('ticker','')} | "
              f"side={f_.get('side','')} price={f_.get('price','')} "
              f"edge={f_.get('edge','')} count={f_.get('count','')}")
    if len(fills) > 10:
        print(f"  ... and {len(fills)-10} more")
print()


# ================================================================
# 2. position_settled events (actual + estimated) & P&L
# ================================================================
settled_actual = get_events("position_settled_actual")
settled_estimated = get_events("position_settled_estimated")
settled_all = settled_actual + settled_estimated

print(f"{'='*60}")
print(f"2. POSITION SETTLEMENTS: {len(settled_all)} total")
print(f"   Actual: {len(settled_actual)}  |  Estimated (paper): {len(settled_estimated)}")
print(f"{'='*60}")
if settled_all:
    pnls = [s.get("pnl", 0) for s in settled_all]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    print(f"  Total P&L:    ${sum(pnls):.2f}")
    print(f"  Wins:         {len(wins)}  (avg ${mean(wins):.2f})" if wins else "  Wins: 0")
    print(f"  Losses:       {len(losses)}  (avg ${mean(losses):.2f})" if losses else "  Losses: 0")
    print(f"  Win rate:     {len(wins)/len(pnls)*100:.1f}%")
    print(f"  Mean P&L:     ${mean(pnls):.4f}")
    if len(pnls) > 1:
        print(f"  Median P&L:   ${median(pnls):.4f}")
        print(f"  Stdev P&L:    ${stdev(pnls):.4f}")
    for s in settled_all[:10]:
        won_str = "WON" if s.get("pnl", 0) > 0 else "LOST"
        print(f"  {s.get('timestamp','')} | {s.get('ticker','')} | "
              f"side={s.get('side','')} entry={s.get('entry_price','')} "
              f"pnl=${s.get('pnl',0):.4f} {won_str}")
    if len(settled_all) > 10:
        print(f"  ... and {len(settled_all)-10} more")
print()


# ================================================================
# 3. Exit types
# ================================================================
tp = get_events("take_profit_executed")
pe = get_events("pre_expiry_exit_executed")
tb = get_events("thesis_break_exit")

print(f"{'='*60}")
print(f"3. EXIT TYPES")
print(f"{'='*60}")
print(f"  take_profit_executed:      {len(tp)}")
print(f"  pre_expiry_exit_executed:  {len(pe)}")
print(f"  thesis_break_exit:         {len(tb)}")
for label, evts in [("Take Profit", tp), ("Pre-Expiry", pe), ("Thesis Break", tb)]:
    if evts:
        exit_pnls = [e.get("pnl", 0) for e in evts]
        print(f"  {label} total P&L: ${sum(exit_pnls):.2f}  avg: ${mean(exit_pnls):.4f}")
print()


# ================================================================
# 4. Trade type distribution (signal_type from trade context)
# ================================================================
print(f"{'='*60}")
print(f"4. TRADE TYPE DISTRIBUTION")
print(f"{'='*60}")

type_counter = Counter()
for e in events:
    if e.get("event") in ("trade_filled", "order_resting"):
        st = e.get("signal_type", "unknown")
        type_counter[st] += 1

if type_counter and not (len(type_counter) == 1 and "unknown" in type_counter):
    total = sum(type_counter.values())
    for stype, count in type_counter.most_common():
        print(f"  {stype}: {count} ({count/total*100:.1f}%)")
else:
    print("  (signal_type not logged in trade_filled events)")
    print("  TIP: Add signal_type= to the trade_filled logger.info() call")
print()


# ================================================================
# 5. Entry price distribution (extreme prices >0.60 or <0.40?)
# ================================================================
print(f"{'='*60}")
print(f"5. ENTRY PRICE ANALYSIS")
print(f"{'='*60}")
if fills:
    prices = []
    for f_ in fills:
        p = f_.get("price")
        if p is not None:
            try:
                prices.append(float(p))
            except (ValueError, TypeError):
                pass
    if prices:
        extreme_high = [p for p in prices if p > 0.60]
        extreme_low = [p for p in prices if p < 0.40]
        mid_range = [p for p in prices if 0.40 <= p <= 0.60]
        print(f"  Total fills with price: {len(prices)}")
        print(f"  Price > $0.60 (high):   {len(extreme_high)} ({len(extreme_high)/len(prices)*100:.1f}%)")
        print(f"  Price < $0.40 (low):    {len(extreme_low)} ({len(extreme_low)/len(prices)*100:.1f}%)")
        print(f"  $0.40-$0.60 (mid):      {len(mid_range)} ({len(mid_range)/len(prices)*100:.1f}%)")
        print(f"  Mean price:   ${mean(prices):.4f}")
        print(f"  Median price: ${median(prices):.4f}")
        print(f"  Min:  ${min(prices):.4f}   Max: ${max(prices):.4f}")
        buckets = Counter()
        for p in prices:
            buckets[round(p, 1)] += 1
        print("  Distribution by $0.10 bucket:")
        for b in sorted(buckets):
            bar = "#" * buckets[b]
            print(f"    ${b:.1f}: {buckets[b]:3d} {bar}")
else:
    print("  No trade_filled events found")
print()


# ================================================================
# 6. Edge value distribution at entry
# ================================================================
print(f"{'='*60}")
print(f"6. EDGE VALUE DISTRIBUTION")
print(f"{'='*60}")
if fills:
    edges = []
    for f_ in fills:
        e = f_.get("edge")
        if e is not None:
            try:
                edges.append(float(e))
            except (ValueError, TypeError):
                pass
    if edges:
        print(f"  Count:  {len(edges)}")
        print(f"  Mean:   {mean(edges):.4f}")
        print(f"  Median: {median(edges):.4f}")
        print(f"  Min:    {min(edges):.4f}   Max: {max(edges):.4f}")
        if len(edges) > 1:
            print(f"  Stdev:  {stdev(edges):.4f}")
        buckets = Counter()
        for e in edges:
            bucket = round(e * 20) / 20
            buckets[bucket] += 1
        print("  Distribution by 0.05 bucket:")
        for b in sorted(buckets):
            bar = "#" * buckets[b]
            print(f"    {b:+.2f}: {buckets[b]:3d} {bar}")
    else:
        print("  No edge values found in fills")
else:
    print("  No trade_filled events found")
print()


# ================================================================
# 7. Trade rejections and reasons
# ================================================================
rejections = get_events("trade_rejected")
cooldowns = get_events("entry_cooldown_active")
thesis_blocks = get_events("thesis_break_cooldown_blocked")

print(f"{'='*60}")
print(f"7. TRADE REJECTIONS")
print(f"{'='*60}")
print(f"  trade_rejected:                {len(rejections)}")
print(f"  entry_cooldown_active:         {len(cooldowns)}")
print(f"  thesis_break_cooldown_blocked: {len(thesis_blocks)}")

if rejections:
    reason_counter = Counter()
    for r in rejections:
        reason_counter[r.get("reason", "unknown")] += 1
    print("  Rejection reasons:")
    for reason, count in reason_counter.most_common():
        print(f"    {count:4d} | {reason}")

total_attempted = len(fills) + len(rejections)
if total_attempted > 0:
    print(f"\n  Fill rate: {len(fills)}/{total_attempted} = {len(fills)/total_attempted*100:.1f}%")
    print(f"  Rejection rate: {len(rejections)}/{total_attempted} = {len(rejections)/total_attempted*100:.1f}%")
print()


# ================================================================
# 8. Timestamp patterns: winning vs losing trades
# ================================================================
print(f"{'='*60}")
print(f"8. TIMESTAMP PATTERNS")
print(f"{'='*60}")

def parse_ts(ts_str):
    """Parse ISO timestamp from structlog."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None

if settled_all:
    win_hours = Counter()
    loss_hours = Counter()
    for s in settled_all:
        ts = parse_ts(s.get("timestamp"))
        if ts:
            hour = ts.hour
            if s.get("pnl", 0) > 0:
                win_hours[hour] += 1
            else:
                loss_hours[hour] += 1

    if win_hours or loss_hours:
        all_hours = sorted(set(list(win_hours.keys()) + list(loss_hours.keys())))
        print("  Hour (UTC) | Wins | Losses | Win Rate")
        print("  " + "-"*45)
        for h in all_hours:
            w = win_hours.get(h, 0)
            l = loss_hours.get(h, 0)
            total_h = w + l
            wr = w / total_h * 100 if total_h > 0 else 0
            print(f"  {h:02d}:00       | {w:4d} | {l:5d}  | {wr:5.1f}%")
    else:
        print("  No timestamps parsed from settlement events")

if fills:
    fill_hours = Counter()
    for f_ in fills:
        ts = parse_ts(f_.get("timestamp"))
        if ts:
            fill_hours[ts.hour] += 1
    if fill_hours:
        print("\n  Fill activity by hour (UTC):")
        for h in sorted(fill_hours):
            bar = "#" * fill_hours[h]
            print(f"    {h:02d}:00  {fill_hours[h]:3d} {bar}")
print()


# ================================================================
# SUMMARY
# ================================================================
print(f"{'='*60}")
print(f"SUMMARY")
print(f"{'='*60}")
print(f"  Total log lines:       {len(events)}")
print(f"  Trades filled:         {len(fills)}")
print(f"  Orders resting:        {len(get_events('order_resting'))}")
print(f"  Positions settled:     {len(settled_all)}")
print(f"  Take profits:          {len(tp)}")
print(f"  Pre-expiry exits:      {len(pe)}")
print(f"  Thesis break exits:    {len(tb)}")
print(f"  Rejections:            {len(rejections)}")
print(f"  Cooldowns:             {len(cooldowns)}")
print(f"  Thesis-break blocks:   {len(thesis_blocks)}")

all_pnl_events = settled_all + tp + pe + tb
if all_pnl_events:
    total_pnl = sum(e.get("pnl", 0) for e in all_pnl_events)
    print(f"\n  TOTAL P&L (all exits): ${total_pnl:.2f}")
    print(f"  Avg P&L per exit:     ${total_pnl/len(all_pnl_events):.4f}")
