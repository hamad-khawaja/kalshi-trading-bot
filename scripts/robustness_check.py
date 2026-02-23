#!/usr/bin/env python3
"""Parameter robustness analysis for the Kalshi trading bot.

Replays historical trades under perturbed parameter values to identify
fragile parameters whose small changes flip P&L sign.

Usage:
    .venv/bin/python scripts/robustness_check.py
    .venv/bin/python scripts/robustness_check.py --db data/bot.db
    .venv/bin/python scripts/robustness_check.py --output results/robustness_report.md
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Fee calculation (mirrors src/strategy/edge_detector.py)
# ---------------------------------------------------------------------------

def compute_fee(count: int, price: float, is_maker: bool = True) -> float:
    """Kalshi fee: ceil(rate * C * P * (1-P) * 100) / 100."""
    rate = Decimal("0.0175") if is_maker else Decimal("0.07")
    p = Decimal(str(price))
    c = Decimal(str(count))
    raw = rate * c * p * (1 - p)
    cents = (raw * 100).to_integral_value(rounding=ROUND_CEILING)
    return float(cents / 100)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ParamSweep:
    name: str
    baseline: float
    values: list[float]
    param_type: str  # "entry_gate", "exit", "sizing"
    description: str


@dataclass
class Metrics:
    total_pnl: float
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float
    avg_pnl: float
    sharpe: float
    max_drawdown: float
    profit_factor: float


SWEEPS = [
    ParamSweep(
        "min_edge_threshold", 0.03,
        [0.01, 0.02, 0.03, 0.04, 0.05, 0.06],
        "entry_gate", "Minimum net edge to enter a trade",
    ),
    ParamSweep(
        "confidence_min", 0.55,
        [0.45, 0.50, 0.55, 0.60, 0.65, 0.70],
        "entry_gate", "Minimum model confidence (proxy: |prob - 0.50| * 2)",
    ),
    ParamSweep(
        "yes_side_edge_multiplier", 1.4,
        [1.0, 1.2, 1.4, 1.6, 1.8, 2.0],
        "entry_gate", "Extra edge required for YES-side trades",
    ),
    ParamSweep(
        "min_entry_price", 0.30,
        [0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
        "entry_gate", "Minimum contract price to enter",
    ),
    ParamSweep(
        "stop_loss_pct", 0.35,
        [0.20, 0.25, 0.30, 0.35, 0.40, 0.50],
        "exit", "Stop-loss threshold as % of entry price",
    ),
    ParamSweep(
        "take_profit_min_profit_cents", 0.10,
        [0.05, 0.08, 0.10, 0.12, 0.15, 0.20],
        "exit", "Minimum net profit per contract for take-profit",
    ),
    ParamSweep(
        "min_quality_score", 0.80,
        [0.60, 0.70, 0.80, 0.90, 0.95],
        "entry_gate", "Minimum combined edge + confidence quality gate",
    ),
    ParamSweep(
        "kelly_fraction", 0.15,
        [0.05, 0.10, 0.15, 0.20, 0.25],
        "sizing", "Fractional Kelly sizing multiplier",
    ),
]

# Baseline config values used across replay functions
BASELINE_EDGE = 0.03
BASELINE_YES_MULT = 1.4
BASELINE_KELLY = 0.15


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_trades(db_path: str) -> pd.DataFrame:
    """Load and pair entry/exit trades from the database.

    Returns a DataFrame with one row per exit, enriched with entry context.
    """
    conn = sqlite3.connect(db_path)

    entries = pd.read_sql_query(
        """
        SELECT market_ticker, side, action, count, price_dollars,
               fees_dollars, model_probability, implied_probability,
               strategy_tag, entry_time
        FROM trades WHERE action = 'buy'
        """,
        conn,
    )

    exits = pd.read_sql_query(
        """
        SELECT market_ticker, action AS exit_action, count AS exit_count,
               price_dollars AS exit_price, fees_dollars AS exit_fees,
               pnl_dollars, exit_time
        FROM trades WHERE action != 'buy'
        """,
        conn,
    )
    conn.close()

    if entries.empty or exits.empty:
        return pd.DataFrame()

    # Aggregate entries by market_ticker (handles multi-buy positions)
    def weighted_avg(group: pd.DataFrame) -> pd.Series:
        total_count = group["count"].sum()
        w_price = (group["price_dollars"] * group["count"]).sum() / total_count
        return pd.Series({
            "side": group["side"].iloc[0],
            "entry_count": total_count,
            "entry_price": w_price,
            "entry_fees": group["fees_dollars"].sum(),
            "model_probability": group["model_probability"].iloc[-1],
            "implied_probability": group["implied_probability"].iloc[-1],
            "strategy_tag": group["strategy_tag"].iloc[0],
        })

    entry_agg = entries.groupby("market_ticker").apply(
        weighted_avg, include_groups=False,
    ).reset_index()

    # Join exits to aggregated entries (side comes from entries only)
    trades = exits.merge(entry_agg, on="market_ticker", how="inner")

    # Compute derived fields for replay
    trades["raw_edge"] = (
        trades["model_probability"] - trades["implied_probability"]
    ).abs()
    trades["fee_drag"] = trades["entry_price"].apply(
        lambda p: compute_fee(1, p, is_maker=True)
    )
    trades["net_edge"] = trades["raw_edge"] - trades["fee_drag"]
    trades["confidence_proxy"] = (
        trades["model_probability"] - 0.50
    ).abs() * 2

    return trades


# ---------------------------------------------------------------------------
# Replay functions
# ---------------------------------------------------------------------------

def replay_min_edge(trades: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Filter trades by minimum net edge threshold."""
    effective = np.where(
        trades["side"] == "yes",
        threshold * BASELINE_YES_MULT,
        threshold,
    )
    mask = trades["net_edge"].values >= effective
    return trades[mask]


def replay_confidence(trades: pd.DataFrame, min_conf: float) -> pd.DataFrame:
    """Filter trades by confidence proxy."""
    return trades[trades["confidence_proxy"] >= min_conf]


def replay_yes_multiplier(
    trades: pd.DataFrame, multiplier: float
) -> pd.DataFrame:
    """Filter trades by YES-side edge multiplier."""
    effective = np.where(
        trades["side"] == "yes",
        BASELINE_EDGE * multiplier,
        BASELINE_EDGE,
    )
    mask = trades["net_edge"].values >= effective
    return trades[mask]


def replay_min_price(
    trades: pd.DataFrame, min_price: float
) -> pd.DataFrame:
    """Filter trades by minimum entry price."""
    return trades[trades["entry_price"] >= min_price]


def replay_quality_score(
    trades: pd.DataFrame, min_quality: float
) -> pd.DataFrame:
    """Filter trades by quality score proxy."""
    effective_threshold = np.where(
        trades["side"] == "yes",
        BASELINE_EDGE * BASELINE_YES_MULT,
        BASELINE_EDGE,
    )
    edge_ratio = trades["net_edge"].values / effective_threshold
    quality = edge_ratio * 0.5 + trades["confidence_proxy"].values * 0.5
    return trades[quality >= min_quality]


def replay_stop_loss(
    trades: pd.DataFrame, pct: float
) -> pd.DataFrame:
    """Adjust P&L for perturbed stop-loss threshold."""
    result = trades.copy()
    sl_mask = result["exit_action"] == "stop_loss"
    sl_rows = result[sl_mask]

    if sl_rows.empty:
        return result

    actual_loss_pct = (
        (sl_rows["entry_price"] - sl_rows["exit_price"]) / sl_rows["entry_price"]
    )

    # Tighter stop: would exit earlier at smaller loss
    tighter = actual_loss_pct > pct
    if tighter.any():
        new_exit_price = sl_rows.loc[tighter, "entry_price"] * (1 - pct)
        new_exit_fee = new_exit_price.apply(
            lambda p: compute_fee(1, p, is_maker=True)
        ) * sl_rows.loc[tighter, "exit_count"]
        new_pnl = (
            (new_exit_price - sl_rows.loc[tighter, "entry_price"])
            * sl_rows.loc[tighter, "exit_count"]
            - sl_rows.loc[tighter, "entry_fees"]
            - new_exit_fee
        )
        result.loc[tighter[tighter].index, "pnl_dollars"] = new_pnl

    return result


def replay_take_profit(
    trades: pd.DataFrame, min_profit: float
) -> pd.DataFrame:
    """Adjust P&L for perturbed take-profit threshold.

    If the TP threshold is raised above the actual profit, the TP wouldn't
    have fired. Conservative: keep actual P&L (trade continues to whatever
    exit happened next, which we can't know).
    """
    # No adjustment needed — higher threshold means fewer TP exits fire,
    # but we can't simulate what happens instead without tick data.
    # Lower threshold means more TP exits fire earlier, but we don't have
    # the intra-trade path to know when threshold was first crossed.
    # Return unchanged for honest reporting.
    return trades.copy()


def replay_kelly(
    trades: pd.DataFrame, kelly: float
) -> pd.DataFrame:
    """Scale P&L proportionally to Kelly fraction change."""
    result = trades.copy()
    scale = kelly / BASELINE_KELLY
    result["pnl_dollars"] = result["pnl_dollars"] * scale
    return result


REPLAY_FUNCTIONS = {
    "min_edge_threshold": replay_min_edge,
    "confidence_min": replay_confidence,
    "yes_side_edge_multiplier": replay_yes_multiplier,
    "min_entry_price": replay_min_price,
    "min_quality_score": replay_quality_score,
    "stop_loss_pct": replay_stop_loss,
    "take_profit_min_profit_cents": replay_take_profit,
    "kelly_fraction": replay_kelly,
}


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(trades: pd.DataFrame) -> Metrics:
    """Compute performance metrics from a trade DataFrame."""
    if trades.empty or "pnl_dollars" not in trades.columns:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0)

    pnl = trades["pnl_dollars"].dropna()
    n = len(pnl)
    if n == 0:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0)

    total = pnl.sum()
    wins = (pnl > 0).sum()
    losses = (pnl <= 0).sum()
    win_rate = wins / n if n > 0 else 0
    avg = total / n

    std = pnl.std()
    sharpe = avg / std if std > 0 else 0

    cumulative = pnl.cumsum()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max).min()

    win_sum = pnl[pnl > 0].sum()
    loss_sum = abs(pnl[pnl <= 0].sum())
    pf = win_sum / loss_sum if loss_sum > 0 else float("inf")

    return Metrics(
        total_pnl=round(total, 2),
        trade_count=n,
        win_count=int(wins),
        loss_count=int(losses),
        win_rate=round(win_rate, 4),
        avg_pnl=round(avg, 4),
        sharpe=round(sharpe, 4),
        max_drawdown=round(drawdown, 2),
        profit_factor=round(pf, 4),
    )


def compute_sensitivity(
    values: list[float], pnl_values: list[float]
) -> float:
    """Compute sensitivity: slope of total_pnl vs parameter value."""
    if len(values) < 2:
        return 0.0
    coeffs = np.polyfit(values, pnl_values, 1)
    return round(float(coeffs[0]), 2)


def is_fragile(pnl_values: list[float]) -> bool:
    """Check if P&L changes sign across perturbation range."""
    has_pos = any(p > 0 for p in pnl_values)
    has_neg = any(p < 0 for p in pnl_values)
    return has_pos and has_neg


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_terminal_table(
    sweep: ParamSweep, results: list[tuple[float, Metrics]]
) -> str:
    """Format a parameter sweep as a terminal-friendly table."""
    lines = []
    lines.append(f"\n{'='*78}")
    lines.append(f"  {sweep.name} (baseline: {sweep.baseline})")
    lines.append(f"  {sweep.description}")
    lines.append(f"{'='*78}")
    header = (
        f"{'Value':>7} | {'Trades':>6} | {'Win%':>6} | "
        f"{'Total P&L':>10} | {'Avg P&L':>8} | "
        f"{'Sharpe':>6} | {'Max DD':>8} | {'PF':>5}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    pnl_values = []
    for val, m in results:
        marker = "*" if val == sweep.baseline else " "
        wr = f"{m.win_rate * 100:.1f}%"
        pnl_str = f"${m.total_pnl:+.2f}"
        avg_str = f"${m.avg_pnl:+.4f}"
        dd_str = f"${m.max_drawdown:.2f}"
        pf_str = f"{m.profit_factor:.2f}" if m.profit_factor < 100 else "inf"
        lines.append(
            f"{val:>6.2f}{marker} | {m.trade_count:>6} | {wr:>6} | "
            f"{pnl_str:>10} | {avg_str:>8} | "
            f"{m.sharpe:>6.3f} | {dd_str:>8} | {pf_str:>5}"
        )
        pnl_values.append(m.total_pnl)

    sens = compute_sensitivity([v for v, _ in results], pnl_values)
    fragile = is_fragile(pnl_values)
    lines.append(f"  Sensitivity: {sens:+.2f} $/unit" + (
        "  | *** FRAGILE: P&L sign changes ***" if fragile else ""
    ))

    return "\n".join(lines)


def format_markdown_report(
    all_results: list[tuple[ParamSweep, list[tuple[float, Metrics]]]],
    db_path: str,
    total_trades: int,
) -> str:
    """Generate a full markdown report."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Parameter Robustness Report",
        "",
        f"**Generated:** {now}",
        f"**Database:** `{db_path}`",
        f"**Total exit trades:** {total_trades}",
        "",
        "## Summary",
        "",
        "| Parameter | Baseline | Sensitivity ($/unit) | Fragile? |",
        "|-----------|----------|---------------------|----------|",
    ]

    fragile_params = []
    for sweep, results in all_results:
        pnl_values = [m.total_pnl for _, m in results]
        sens = compute_sensitivity([v for v, _ in results], pnl_values)
        frag = is_fragile(pnl_values)
        frag_str = "YES" if frag else "no"
        lines.append(
            f"| `{sweep.name}` | {sweep.baseline} | {sens:+.2f} | {frag_str} |"
        )
        if frag:
            fragile_params.append(sweep.name)

    lines.append("")

    # Per-parameter detail tables
    for sweep, results in all_results:
        lines.append(f"## {sweep.name}")
        lines.append(f"*{sweep.description}* (baseline: {sweep.baseline})")
        lines.append("")
        lines.append(
            "| Value | Trades | Win% | Total P&L | Avg P&L | "
            "Sharpe | Max DD | PF |"
        )
        lines.append(
            "|------:|-------:|-----:|----------:|--------:|"
            "------:|-------:|---:|"
        )
        for val, m in results:
            marker = " **" if val == sweep.baseline else ""
            end_marker = "**" if val == sweep.baseline else ""
            wr = f"{m.win_rate * 100:.1f}%"
            pf_str = (
                f"{m.profit_factor:.2f}" if m.profit_factor < 100 else "inf"
            )
            lines.append(
                f"| {marker}{val:.2f}{end_marker} | {m.trade_count} | "
                f"{wr} | ${m.total_pnl:+.2f} | "
                f"${m.avg_pnl:+.4f} | {m.sharpe:.3f} | "
                f"${m.max_drawdown:.2f} | {pf_str} |"
            )
        lines.append("")

    # Recommendations
    lines.append("## Recommendations")
    lines.append("")
    if fragile_params:
        lines.append("**Fragile parameters** (P&L sign changes across range):")
        for p in fragile_params:
            lines.append(f"- `{p}` — consider widening or removing this gate")
        lines.append("")
        lines.append(
            "Fragile parameters are most likely to be overfit. Small market "
            "regime changes will flip these from profitable to losing. "
            "Consider: (1) widening the parameter range that stays profitable, "
            "(2) reducing the parameter's influence, or (3) using an adaptive "
            "version that adjusts to market conditions."
        )
    else:
        lines.append(
            "No parameters are fragile across the tested range. "
            "This suggests the strategy is reasonably robust to parameter "
            "perturbations."
        )

    lines.append("")
    lines.append("---")
    lines.append(
        "*Limitations: Confidence proxy uses |model_prob - 0.50| * 2 "
        "(actual confidence includes spread/vol/depth). "
        "Exit parameter replay is conservative — without intra-trade tick "
        "data, we keep actual P&L for trades whose exit type doesn't match. "
        "Entry-gate replay ignores dynamic adjustments (vol regime, session "
        "multipliers, time-decay scaling).*"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parameter robustness analysis for the Kalshi trading bot"
    )
    parser.add_argument(
        "--db", default="data/bot.db", help="Path to trade database"
    )
    parser.add_argument(
        "--output",
        default="results/robustness_report.md",
        help="Output markdown report path",
    )
    args = parser.parse_args()

    # Load trades
    print(f"Loading trades from {args.db}...")
    trades = load_trades(args.db)
    if trades.empty:
        print("No trades found in database. Exiting.")
        sys.exit(1)

    total_trades = len(trades)
    print(f"Loaded {total_trades} exit trades across "
          f"{trades['market_ticker'].nunique()} markets")
    print()

    # Run all parameter sweeps
    all_results: list[tuple[ParamSweep, list[tuple[float, Metrics]]]] = []

    for sweep in SWEEPS:
        replay_fn = REPLAY_FUNCTIONS[sweep.name]
        results: list[tuple[float, Metrics]] = []

        for val in sweep.values:
            adjusted = replay_fn(trades, val)
            metrics = compute_metrics(adjusted)
            results.append((val, metrics))

        all_results.append((sweep, results))
        print(format_terminal_table(sweep, results))

    # Print overall summary
    print(f"\n{'='*78}")
    print("  SUMMARY")
    print(f"{'='*78}")
    print(f"{'Parameter':<32} | {'Sensitivity':>14} | {'Fragile?':>8}")
    print("-" * 62)

    fragile_count = 0
    for sweep, results in all_results:
        pnl_values = [m.total_pnl for _, m in results]
        sens = compute_sensitivity([v for v, _ in results], pnl_values)
        frag = is_fragile(pnl_values)
        frag_str = "*** YES ***" if frag else "no"
        if frag:
            fragile_count += 1
        print(f"{sweep.name:<32} | {sens:>+10.2f} $/u | {frag_str:>8}")

    print(f"\n  {fragile_count}/{len(SWEEPS)} parameters are fragile")

    # Write markdown report
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = format_markdown_report(all_results, args.db, total_trades)
    output_path.write_text(report)
    print(f"\n  Report written to {args.output}")


if __name__ == "__main__":
    main()
