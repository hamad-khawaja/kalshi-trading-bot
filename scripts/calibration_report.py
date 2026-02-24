#!/usr/bin/env python3
"""Calibration report: predicted probability vs actual settlement outcome.

Usage:
    python scripts/calibration_report.py [--db path/to/bot.db] [--output path/to/report.md]

Produces a markdown report with:
- Calibration table (binned predicted prob vs actual win rate)
- Brier score and Expected Calibration Error (ECE)
- Breakdown by signal_type (especially settlement_ride, trend_continuation)
- Model vs market (implied) calibration comparison
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict

MIN_BIN_SIZE = 3       # Skip bins with fewer trades
MIN_STRATEGY_SIZE = 10  # Skip strategy segments with fewer trades
MIN_TOTAL_TRADES = 30   # Warn if not enough data
BIN_WIDTH = 0.05        # 5% bins


def fetch_calibration_data(db_path: str) -> list[dict]:
    """Fetch settlement trades with model probabilities from the database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Check if 'won' column exists (migration may not have run yet)
    cursor = conn.execute("PRAGMA table_info(trades)")
    columns = {row["name"] for row in cursor.fetchall()}
    has_won = "won" in columns

    if has_won:
        cursor = conn.execute("""
            SELECT model_probability, implied_probability, won, strategy_tag, side
            FROM trades
            WHERE action = 'settle'
              AND won IS NOT NULL
              AND model_probability IS NOT NULL
        """)
    else:
        # Fallback: derive won from pnl_dollars (won = pnl > 0 for settlements)
        cursor = conn.execute("""
            SELECT model_probability, implied_probability,
                   CASE WHEN pnl_dollars > 0 THEN 1 ELSE 0 END AS won,
                   strategy_tag, side
            FROM trades
            WHERE action = 'settle'
              AND model_probability IS NOT NULL
              AND pnl_dollars IS NOT NULL
        """)

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def adjusted_prob(model_prob: float, side: str) -> float:
    """Convert model P(YES) to P(our side wins).

    For YES trades, the relevant probability is model_prob directly.
    For NO trades, the relevant probability is 1 - model_prob.
    """
    if side == "no":
        return 1.0 - model_prob
    return model_prob


def bin_index(prob: float) -> int:
    """Map a probability to its bin index (0-based)."""
    idx = int(prob / BIN_WIDTH)
    return min(idx, int(1.0 / BIN_WIDTH) - 1)


def bin_label(idx: int) -> str:
    """Human-readable bin label."""
    low = idx * BIN_WIDTH
    high = low + BIN_WIDTH
    return f"{low:.2f}-{high:.2f}"


def compute_calibration(trades: list[dict], prob_key: str = "model") -> dict:
    """Compute calibration metrics for a set of trades.

    Args:
        trades: List of trade dicts with model_probability, implied_probability,
                won, side fields.
        prob_key: "model" to use model_probability, "implied" to use implied_probability.

    Returns:
        Dict with brier_score, ece, bins (list of bin dicts), n_trades.
    """
    bins: dict[int, list[tuple[float, int]]] = defaultdict(list)
    brier_sum = 0.0

    for t in trades:
        raw_prob = t["model_probability"] if prob_key == "model" else t["implied_probability"]
        if raw_prob is None:
            continue
        pred = adjusted_prob(raw_prob, t["side"])
        outcome = 1 if t["won"] else 0
        brier_sum += (pred - outcome) ** 2
        bins[bin_index(pred)].append((pred, outcome))

    n = len(trades)
    if n == 0:
        return {"brier_score": None, "ece": None, "bins": [], "n_trades": 0}

    brier = brier_sum / n

    # ECE: weighted average of |accuracy - confidence| per bin
    ece = 0.0
    bin_results = []
    num_bins = int(1.0 / BIN_WIDTH)
    for idx in range(num_bins):
        entries = bins.get(idx, [])
        if len(entries) < MIN_BIN_SIZE:
            if entries:
                bin_results.append({
                    "label": bin_label(idx),
                    "count": len(entries),
                    "win_rate": None,
                    "mean_pred": None,
                    "deviation": None,
                    "too_few": True,
                })
            continue
        mean_pred = sum(p for p, _ in entries) / len(entries)
        win_rate = sum(o for _, o in entries) / len(entries)
        deviation = win_rate - mean_pred
        ece += (len(entries) / n) * abs(deviation)
        bin_results.append({
            "label": bin_label(idx),
            "count": len(entries),
            "win_rate": win_rate,
            "mean_pred": mean_pred,
            "deviation": deviation,
            "too_few": False,
        })

    return {
        "brier_score": brier,
        "ece": ece,
        "bins": bin_results,
        "n_trades": n,
    }


def format_calibration_table(cal: dict) -> str:
    """Format calibration results as a markdown table."""
    lines = []
    lines.append(
        "| Predicted Prob | Count | Actual Win% | Mean Pred | Deviation |"
    )
    lines.append(
        "|----------------|------:|------------:|----------:|----------:|"
    )
    for b in cal["bins"]:
        if b["too_few"]:
            lines.append(
                f"| {b['label']:14s} | {b['count']:5d} | {'(too few)':>11s} | "
                f"{'':>9s} | {'':>9s} |"
            )
        else:
            sign = "+" if b["deviation"] >= 0 else ""
            lines.append(
                f"| {b['label']:14s} | {b['count']:5d} | "
                f"{b['win_rate']:10.1%} | "
                f"{b['mean_pred']:9.3f} | "
                f"{sign}{b['deviation']:8.3f} |"
            )
    return "\n".join(lines)


def generate_report(db_path: str) -> str:
    """Generate the full calibration report as markdown."""
    trades = fetch_calibration_data(db_path)
    lines = []

    lines.append("# Calibration Report")
    lines.append("")
    lines.append(f"**Database:** `{db_path}`")
    lines.append(f"**Settlement trades with calibration data:** {len(trades)}")
    lines.append("")

    if len(trades) < MIN_TOTAL_TRADES:
        lines.append(
            f"> **Warning:** Only {len(trades)} settlement trades found "
            f"(minimum {MIN_TOTAL_TRADES} recommended). "
            "Results may not be statistically meaningful."
        )
        lines.append("")

    if not trades:
        lines.append("No settlement trades with model probability data found.")
        lines.append("")
        lines.append(
            "Calibration data is recorded automatically at settlement. "
            "Run the bot and accumulate trades to populate this report."
        )
        return "\n".join(lines)

    # Overall model calibration
    model_cal = compute_calibration(trades, "model")
    lines.append("## Model Calibration (Overall)")
    lines.append("")
    lines.append(f"- **Brier Score:** {model_cal['brier_score']:.4f} (random baseline: 0.2500)")
    lines.append(f"- **ECE:** {model_cal['ece']:.4f}")
    lines.append(f"- **N:** {model_cal['n_trades']}")
    lines.append("")
    lines.append(format_calibration_table(model_cal))
    lines.append("")

    # Overall implied (market) calibration for comparison
    implied_trades = [t for t in trades if t.get("implied_probability") is not None]
    if implied_trades:
        implied_cal = compute_calibration(implied_trades, "implied")
        lines.append("## Market (Implied) Calibration")
        lines.append("")
        lines.append(
            f"- **Brier Score:** {implied_cal['brier_score']:.4f}"
        )
        lines.append(f"- **ECE:** {implied_cal['ece']:.4f}")
        lines.append(f"- **N:** {implied_cal['n_trades']}")
        lines.append("")
        lines.append(format_calibration_table(implied_cal))
        lines.append("")

        # Model vs market comparison
        if model_cal["brier_score"] is not None and implied_cal["brier_score"] is not None:
            diff = model_cal["brier_score"] - implied_cal["brier_score"]
            better = "model" if diff < 0 else "market"
            lines.append(
                f"> **Model vs Market:** {better} is better calibrated "
                f"(Brier diff: {abs(diff):.4f})"
            )
            lines.append("")

    # Per-strategy breakdown
    strategies: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        strategies[t["strategy_tag"]].append(t)

    if strategies:
        lines.append("## Calibration by Strategy")
        lines.append("")

        # Sort by count descending
        for strat, strat_trades in sorted(
            strategies.items(), key=lambda x: -len(x[1])
        ):
            if len(strat_trades) < MIN_STRATEGY_SIZE:
                lines.append(
                    f"### {strat} (N={len(strat_trades)}) — *too few trades, skipped*"
                )
                lines.append("")
                continue

            strat_cal = compute_calibration(strat_trades, "model")
            confidence_label = ""
            if strat_cal["ece"] is not None:
                if strat_cal["ece"] > 0.10:
                    confidence_label = " — POORLY CALIBRATED"
                # Check over/under confidence
                total_deviation = sum(
                    b["deviation"] * b["count"]
                    for b in strat_cal["bins"]
                    if not b["too_few"] and b["deviation"] is not None
                )
                total_counted = sum(
                    b["count"] for b in strat_cal["bins"] if not b["too_few"]
                )
                if total_counted > 0:
                    avg_dev = total_deviation / total_counted
                    if avg_dev < -0.03:
                        confidence_label += " (overconfident)"
                    elif avg_dev > 0.03:
                        confidence_label += " (underconfident)"

            lines.append(
                f"### {strat} (N={len(strat_trades)}){confidence_label}"
            )
            lines.append("")
            lines.append(
                f"- **Brier Score:** {strat_cal['brier_score']:.4f}"
            )
            lines.append(f"- **ECE:** {strat_cal['ece']:.4f}")
            win_count = sum(1 for t in strat_trades if t["won"])
            lines.append(
                f"- **Win Rate:** {win_count}/{len(strat_trades)} "
                f"({win_count / len(strat_trades):.1%})"
            )
            lines.append("")
            lines.append(format_calibration_table(strat_cal))
            lines.append("")

    # Interpretation guide
    lines.append("---")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("- **Brier Score:** 0 = perfect, 0.25 = random. Lower is better.")
    lines.append(
        "- **ECE (Expected Calibration Error):** 0 = perfectly calibrated. "
        "< 0.05 is good, > 0.10 is concerning."
    )
    lines.append(
        "- **Deviation:** positive = underconfident (wins more than predicted), "
        "negative = overconfident (wins less than predicted)."
    )
    lines.append(
        "- **Overconfident model:** predicts higher win rate than reality → "
        "reduce edge/probability estimates."
    )
    lines.append(
        "- **Underconfident model:** wins more often than predicted → "
        "model is conservative, consider sizing up."
    )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Calibration report")
    parser.add_argument(
        "db", nargs="?", default="data/bot.db",
        help="Path to bot database (default: data/bot.db)",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Write report to file instead of stdout",
    )
    args = parser.parse_args()

    report = generate_report(args.db)
    if args.output:
        with open(args.output, "w") as f:
            f.write(report)
        print(f"Report written to {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
