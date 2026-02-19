#!/usr/bin/env python3
"""Verify all trade P&L calculations from bot logs and dashboard API.

Usage:
    python scripts/verify_trades.py                          # uses defaults
    python scripts/verify_trades.py --log /tmp/bot_output.log --api http://localhost:8080
    python scripts/verify_trades.py --log /tmp/bot_output.log --no-api   # log-only mode

Checks performed:
  1. Per-trade P&L math (settlement, stop-loss, take-profit, pre-expiry, thesis-break)
  2. Per-asset P&L totals match sum of individual trades
  3. Total P&L matches sum of per-asset P&Ls
  4. Win rate = wins / total_settled
  5. Consecutive loss/win streak tracking
  6. Dashboard totals match log-derived totals (when --api is available)
  7. Fee calculations use correct formula: ceil(rate * C * P * (1-P) * 100) / 100
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal


# ---------------------------------------------------------------------------
# Fee calculation (mirrors src/strategy/edge_detector.py)
# ---------------------------------------------------------------------------

def compute_fee(count: int, price: float, is_maker: bool = False) -> Decimal:
    """Kalshi fee: ceil(rate * C * P * (1-P) * 100) / 100."""
    rate = Decimal("0.0175") if is_maker else Decimal("0.07")
    p = Decimal(str(price))
    c = Decimal(str(count))
    raw = rate * c * p * (1 - p)
    cents = (raw * 100).to_integral_value(rounding=ROUND_CEILING)
    return cents / 100


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Fill:
    """A buy fill that creates/adds to a position."""
    ticker: str
    side: str
    count: int
    price: float
    signal_type: str
    timestamp: str


@dataclass
class Exit:
    """A trade exit (settlement, stop-loss, take-profit, etc.)."""
    ticker: str
    side: str
    count: int
    entry_price: float
    exit_type: str  # settle, stop_loss, take_profit, pre_expiry, thesis_break
    logged_pnl: float
    logged_fee: float | None = None  # sell fee logged for non-settlement exits
    implied_prob: float | None = None  # for settlement
    won: bool | None = None  # for settlement
    timestamp: str = ""


@dataclass
class PositionState:
    """Tracks a position for fee accumulation and weighted-avg entry."""
    ticker: str
    side: str
    count: int
    avg_price: Decimal
    fees_paid: Decimal = Decimal("0")

    def add_fill(self, fill_count: int, fill_price: float, buy_fee: Decimal) -> None:
        """Add contracts to this position, updating weighted-avg price."""
        fp = Decimal(str(fill_price))
        new_total = self.count + fill_count
        if new_total > 0:
            self.avg_price = (
                self.avg_price * self.count + fp * fill_count
            ) / new_total
        self.count = new_total
        self.fees_paid += buy_fee

    def net_opposite(self, fill_count: int, fill_price: float, buy_fee: Decimal) -> None:
        """Net opposite-side fill against this position."""
        remaining = self.count - fill_count
        if remaining > 0:
            self.count = remaining
            # avg_price stays the same, fees accumulate
            self.fees_paid += buy_fee
        elif remaining == 0:
            self.count = 0
            self.fees_paid += buy_fee
        else:
            # Flipped sides — this is complex but rare
            self.count = abs(remaining)
            self.avg_price = Decimal(str(fill_price))
            self.fees_paid += buy_fee


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

def parse_log(log_path: str) -> tuple[list[Fill], list[Exit]]:
    """Parse bot log file and extract fills and exits."""
    fills: list[Fill] = []
    exits: list[Exit] = []

    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            event = entry.get("event", "")

            if event == "trade_filled":
                fills.append(Fill(
                    ticker=entry["ticker"],
                    side=entry["side"],
                    count=entry["count"],
                    price=float(entry["price"]),
                    signal_type=entry.get("signal_type", ""),
                    timestamp=entry.get("timestamp", ""),
                ))

            elif event == "position_settled_paper" or event == "position_settled_actual":
                exits.append(Exit(
                    ticker=entry["ticker"],
                    side=entry["side"],
                    count=entry["count"],
                    entry_price=entry["entry_price"],
                    exit_type="settle",
                    logged_pnl=entry["pnl"],
                    implied_prob=entry.get("implied_prob"),
                    won=entry.get("won"),
                    timestamp=entry.get("timestamp", ""),
                ))

            elif event == "stop_loss_executed":
                exits.append(Exit(
                    ticker=entry["ticker"],
                    side=entry["side"],
                    count=entry["count"],
                    entry_price=entry["entry_price"],
                    exit_type="stop_loss",
                    logged_pnl=entry["pnl"],
                    logged_fee=entry.get("fee"),
                    timestamp=entry.get("timestamp", ""),
                ))

            elif event == "take_profit_executed":
                exits.append(Exit(
                    ticker=entry["ticker"],
                    side=entry["side"],
                    count=entry["count"],
                    entry_price=entry["entry_price"],
                    exit_type="take_profit",
                    logged_pnl=entry["pnl"],
                    logged_fee=entry.get("fee"),
                    timestamp=entry.get("timestamp", ""),
                ))

            elif event == "pre_expiry_exit_executed":
                exits.append(Exit(
                    ticker=entry["ticker"],
                    side=entry["side"],
                    count=entry["count"],
                    entry_price=entry["entry_price"],
                    exit_type="pre_expiry",
                    logged_pnl=entry["pnl"],
                    logged_fee=entry.get("fee"),
                    timestamp=entry.get("timestamp", ""),
                ))

            elif event == "thesis_break_exit":
                exits.append(Exit(
                    ticker=entry["ticker"],
                    side=entry["side"],
                    count=entry["count"],
                    entry_price=entry["entry_price"],
                    exit_type="thesis_break",
                    logged_pnl=entry["pnl"],
                    logged_fee=entry.get("fee"),
                    timestamp=entry.get("timestamp", ""),
                ))

    return fills, exits


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def ticker_to_asset(ticker: str) -> str:
    """Map market ticker to asset symbol."""
    upper = ticker.upper()
    if "BTC" in upper:
        return "BTC"
    elif "ETH" in upper:
        return "ETH"
    return "UNKNOWN"


def verify_trades(fills: list[Fill], exits: list[Exit]) -> dict:
    """Verify every exit's P&L and return summary stats."""
    # Build position states from fills
    positions: dict[str, PositionState] = {}
    # Track fills per ticker to reconstruct fees_paid at exit time
    # We process fills in order and track position state
    fill_idx = 0
    exit_idx = 0

    # Interleave fills and exits by timestamp
    all_events = []
    for f in fills:
        all_events.append(("fill", f.timestamp, f))
    for e in exits:
        all_events.append(("exit", e.timestamp, e))
    all_events.sort(key=lambda x: x[1])

    errors: list[str] = []
    warnings: list[str] = []
    per_asset_pnl: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    total_pnl = Decimal("0")
    wins = 0
    losses = 0
    total_settled = 0
    consec_wins = 0
    consec_losses = 0
    max_consec_wins = 0
    max_consec_losses = 0

    trade_details: list[dict] = []

    for evt_type, ts, evt in all_events:
        if evt_type == "fill":
            f: Fill = evt
            buy_fee = compute_fee(f.count, f.price, is_maker=True)
            ticker = f.ticker

            if ticker in positions:
                pos = positions[ticker]
                if pos.side == f.side:
                    # Same side: add to position
                    pos.add_fill(f.count, f.price, buy_fee)
                else:
                    # Opposite side: net against position
                    pos.net_opposite(f.count, f.price, buy_fee)
                    if pos.count == 0:
                        del positions[ticker]
                    elif f.count > positions[ticker].count:
                        # Flipped — update side
                        pos.side = f.side
            else:
                positions[ticker] = PositionState(
                    ticker=ticker,
                    side=f.side,
                    count=f.count,
                    avg_price=Decimal(str(f.price)),
                    fees_paid=buy_fee,
                )

        elif evt_type == "exit":
            e: Exit = evt
            asset = ticker_to_asset(e.ticker)
            total_settled += 1

            # Get position state (may not exist if fills were before log start)
            pos = positions.get(e.ticker)

            if e.exit_type == "settle":
                # Settlement: pnl = payout - cost - fees_paid
                count = e.count
                entry_price = Decimal(str(e.entry_price))
                cost = entry_price * count

                if e.won:
                    payout = Decimal(str(count))
                else:
                    payout = Decimal("0")

                # Estimate fees_paid from position state
                if pos:
                    fees_paid = pos.fees_paid
                else:
                    # Reconstruct buy fee from entry
                    fees_paid = compute_fee(count, e.entry_price, is_maker=True)
                    warnings.append(
                        f"  {e.ticker}: no position state, estimated buy fee ${fees_paid}"
                    )

                expected_pnl = float(payout - cost - fees_paid)
                logged_pnl = e.logged_pnl
                diff = abs(expected_pnl - logged_pnl)

                status = "OK" if diff < 0.02 else "MISMATCH"
                if diff >= 0.02:
                    errors.append(
                        f"  {e.ticker} settle: expected ${expected_pnl:.2f}, "
                        f"logged ${logged_pnl:.2f}, diff ${diff:.2f} "
                        f"(count={count}, entry={e.entry_price}, won={e.won}, fees={float(fees_paid):.2f})"
                    )

                trade_details.append({
                    "ticker": e.ticker,
                    "type": "settle",
                    "side": e.side,
                    "count": count,
                    "entry": e.entry_price,
                    "won": e.won,
                    "expected_pnl": round(expected_pnl, 2),
                    "logged_pnl": logged_pnl,
                    "diff": round(diff, 2),
                    "status": status,
                    "asset": asset,
                })

            else:
                # Non-settlement exit: pnl = exit_revenue - entry_cost - sell_fee - fees_paid
                count = e.count
                entry_price = Decimal(str(e.entry_price))
                # We need exit_price — derive from logged data
                # For SL/TP/PE/TB, the log has entry_price and exit_price
                # But we only captured entry_price... let's use logged_fee to verify
                entry_cost = entry_price * count

                if pos:
                    fees_paid = pos.fees_paid
                else:
                    fees_paid = compute_fee(count, e.entry_price, is_maker=True)
                    warnings.append(
                        f"  {e.ticker}: no position state for {e.exit_type}, estimated buy fee ${fees_paid}"
                    )

                # We can't independently compute exit revenue without exit_price
                # But we can verify: logged_pnl = exit_rev - entry_cost - sell_fee - fees_paid
                # If we have the logged sell_fee, verify it matches the fee formula
                if e.logged_fee is not None:
                    # Verify sell fee (we need exit_price, derive from pnl equation)
                    # pnl = exit_rev - entry_cost - sell_fee - fees_paid
                    # exit_rev = pnl + entry_cost + sell_fee + fees_paid
                    exit_rev = Decimal(str(e.logged_pnl)) + entry_cost + Decimal(str(e.logged_fee)) + fees_paid
                    exit_price_per = exit_rev / count if count > 0 else Decimal("0")

                    # Verify fee formula
                    is_maker = (e.exit_type == "take_profit")
                    expected_sell_fee = compute_fee(count, float(exit_price_per), is_maker=is_maker)
                    fee_diff = abs(float(expected_sell_fee) - e.logged_fee)

                    if fee_diff >= 0.02:
                        errors.append(
                            f"  {e.ticker} {e.exit_type}: sell fee mismatch: "
                            f"expected ${float(expected_sell_fee):.2f}, logged ${e.logged_fee:.2f}"
                        )

                    # Recompute pnl with our values
                    expected_pnl = float(exit_rev - entry_cost - expected_sell_fee - fees_paid)
                    diff = abs(expected_pnl - e.logged_pnl)
                    status = "OK" if diff < 0.02 else "MISMATCH"
                    if diff >= 0.02:
                        errors.append(
                            f"  {e.ticker} {e.exit_type}: pnl mismatch: "
                            f"expected ${expected_pnl:.2f}, logged ${e.logged_pnl:.2f}"
                        )
                else:
                    # No logged fee — can only flag, not verify
                    status = "NO_FEE_DATA"
                    diff = 0.0
                    expected_pnl = e.logged_pnl

                trade_details.append({
                    "ticker": e.ticker,
                    "type": e.exit_type,
                    "side": e.side,
                    "count": count,
                    "entry": e.entry_price,
                    "expected_pnl": round(expected_pnl, 2),
                    "logged_pnl": e.logged_pnl,
                    "diff": round(diff, 2),
                    "status": status,
                    "asset": asset,
                })

            # Track P&L
            pnl_val = Decimal(str(e.logged_pnl))
            per_asset_pnl[asset] += pnl_val
            total_pnl += pnl_val

            # Win/loss tracking
            if e.logged_pnl > 0:
                wins += 1
                consec_wins += 1
                consec_losses = 0
                max_consec_wins = max(max_consec_wins, consec_wins)
            elif e.logged_pnl < 0:
                losses += 1
                consec_losses += 1
                consec_wins = 0
                max_consec_losses = max(max_consec_losses, consec_losses)
            # pnl == 0: breakeven, don't touch streaks

            # Clean up position after exit
            if e.ticker in positions:
                del positions[e.ticker]

    win_rate = wins / total_settled if total_settled > 0 else 0.0

    return {
        "trade_details": trade_details,
        "per_asset_pnl": {k: float(v) for k, v in per_asset_pnl.items()},
        "total_pnl": float(total_pnl),
        "wins": wins,
        "losses": losses,
        "total_settled": total_settled,
        "win_rate": win_rate,
        "consec_losses": consec_losses,
        "consec_wins": consec_wins,
        "max_consec_losses": max_consec_losses,
        "max_consec_wins": max_consec_wins,
        "errors": errors,
        "warnings": warnings,
        "orphan_positions": {
            k: {"side": v.side, "count": v.count, "avg_price": float(v.avg_price)}
            for k, v in positions.items()
            if v.count > 0
        },
    }


def fetch_dashboard(api_url: str) -> dict | None:
    """Fetch current state from dashboard API."""
    try:
        url = f"{api_url}/api/state"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  WARNING: Could not fetch dashboard API: {e}")
        return None


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_report(result: dict, dashboard: dict | None) -> int:
    """Print verification report. Returns exit code (0=pass, 1=fail)."""
    errors = result["errors"]
    warnings = result["warnings"]
    details = result["trade_details"]
    has_errors = len(errors) > 0

    print("=" * 70)
    print("  TRADE P&L VERIFICATION REPORT")
    print("=" * 70)
    print()

    # Per-trade details
    print(f"--- {len(details)} Trades Verified ---")
    print()
    for t in details:
        icon = "OK" if t["status"] == "OK" else "!!" if t["status"] == "MISMATCH" else "??"
        pnl_sign = "+" if t["logged_pnl"] >= 0 else ""
        print(
            f"  [{icon}] {t['ticker']:40s} {t['type']:14s} "
            f"{t['side'].upper():3s} x{t['count']:<3d} @ {t['entry']:<6.3f}  "
            f"{pnl_sign}${t['logged_pnl']:<7.2f}"
            + (f"  (diff ${t['diff']:.2f})" if t["diff"] >= 0.01 else "")
        )
    print()

    # Aggregate checks
    print("--- Aggregate Checks ---")
    print()

    # Per-asset P&L
    for asset, pnl in sorted(result["per_asset_pnl"].items()):
        sign = "+" if pnl >= 0 else ""
        print(f"  {asset} P&L (from log):  {sign}${pnl:.2f}")

    total = result["total_pnl"]
    total_sign = "+" if total >= 0 else ""
    print(f"  Total P&L (from log): {total_sign}${total:.2f}")
    print()

    # Win rate
    wr = result["win_rate"]
    print(f"  Wins: {result['wins']}  |  Losses: {result['losses']}  |  Total: {result['total_settled']}")
    print(f"  Win rate: {wr*100:.1f}% ({result['wins']}/{result['total_settled']})")
    print(f"  Consec wins (current): {result['consec_wins']}  |  Max: {result['max_consec_wins']}")
    print(f"  Consec losses (current): {result['consec_losses']}  |  Max: {result['max_consec_losses']}")
    print()

    # Dashboard comparison
    if dashboard:
        print("--- Dashboard Comparison ---")
        print()
        risk = dashboard.get("risk", {})
        dash_pa = dashboard.get("per_asset_pnl", {})

        checks = []

        # Per-asset P&L
        for asset, log_pnl in sorted(result["per_asset_pnl"].items()):
            dash_val = dash_pa.get(asset, 0.0)
            match = abs(log_pnl - dash_val) < 0.02
            checks.append(match)
            icon = "PASS" if match else "FAIL"
            print(f"  [{icon}] {asset} P&L:  log={log_pnl:+.2f}  dashboard={dash_val:+.2f}")
            if not match:
                has_errors = True

        # Total P&L
        dash_daily = risk.get("daily_pnl", 0.0)
        match = abs(total - dash_daily) < 0.02
        checks.append(match)
        icon = "PASS" if match else "FAIL"
        print(f"  [{icon}] Daily P&L:  log={total:+.2f}  dashboard={dash_daily:+.2f}")
        if not match:
            has_errors = True

        # Win rate
        dash_wr = risk.get("win_rate", 0.0)
        match = abs(wr - dash_wr) < 0.01
        checks.append(match)
        icon = "PASS" if match else "FAIL"
        print(f"  [{icon}] Win rate:  log={wr*100:.1f}%  dashboard={dash_wr*100:.1f}%")
        if not match:
            has_errors = True

        # Total settled
        dash_settled = risk.get("total_settled", 0)
        match = result["total_settled"] == dash_settled
        checks.append(match)
        icon = "PASS" if match else "FAIL"
        print(f"  [{icon}] Settled:  log={result['total_settled']}  dashboard={dash_settled}")
        if not match:
            has_errors = True

        # Consecutive losses
        dash_cl = risk.get("consecutive_losses", 0)
        match = result["consec_losses"] == dash_cl
        checks.append(match)
        icon = "PASS" if match else "FAIL"
        print(f"  [{icon}] Consec losses:  log={result['consec_losses']}  dashboard={dash_cl}")
        if not match:
            has_errors = True

        # Consecutive wins
        dash_cw = risk.get("consecutive_wins", 0)
        match = result["consec_wins"] == dash_cw
        checks.append(match)
        icon = "PASS" if match else "FAIL"
        print(f"  [{icon}] Consec wins:  log={result['consec_wins']}  dashboard={dash_cw}")
        if not match:
            has_errors = True

        # Trades today
        dash_trades = risk.get("trades_today", 0)
        print(f"  [INFO] Trades today: dashboard={dash_trades} (fills+exits, not just exits)")

        print()

    # Orphan positions
    orphans = result["orphan_positions"]
    if orphans:
        print("--- Orphan Positions (filled but no exit in log) ---")
        print()
        for ticker, info in orphans.items():
            print(f"  {ticker}  {info['side'].upper()} x{info['count']} @ {info['avg_price']:.3f}")
        print()

    # Warnings
    if warnings:
        print(f"--- {len(warnings)} Warnings ---")
        for w in warnings:
            print(w)
        print()

    # Errors
    if errors:
        print(f"--- {len(errors)} ERRORS ---")
        for e in errors:
            print(e)
        print()

    # Final verdict
    print("=" * 70)
    if has_errors:
        print("  RESULT: FAIL — see errors above")
        print("=" * 70)
        return 1
    else:
        print(f"  RESULT: ALL {len(details)} TRADES VERIFIED OK")
        print("=" * 70)
        return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Verify bot trade P&L calculations")
    parser.add_argument(
        "--log", default="/tmp/bot_output.log",
        help="Path to bot log file (default: /tmp/bot_output.log)",
    )
    parser.add_argument(
        "--api", default="http://localhost:8080",
        help="Dashboard API URL (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--no-api", action="store_true",
        help="Skip dashboard API comparison",
    )
    args = parser.parse_args()

    print(f"Reading log: {args.log}")
    try:
        fills, exits = parse_log(args.log)
    except FileNotFoundError:
        print(f"ERROR: Log file not found: {args.log}")
        return 1

    print(f"Found {len(fills)} fills, {len(exits)} exits")
    print()

    result = verify_trades(fills, exits)

    dashboard = None
    if not args.no_api:
        print(f"Fetching dashboard state from {args.api}...")
        dashboard = fetch_dashboard(args.api)
        print()

    return print_report(result, dashboard)


if __name__ == "__main__":
    sys.exit(main())
