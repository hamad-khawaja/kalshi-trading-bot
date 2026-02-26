"""Backtest analysis, reporting, and comparison."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from backtest.backtester import BacktestResult


class BacktestAnalyzer:
    """Generates reports, visualizations, and comparisons from backtest results."""

    def summary(self, result: BacktestResult) -> str:
        """Generate a text summary of backtest performance."""
        lines = [
            "=" * 60,
            "BACKTEST RESULTS",
            f"  Label: {result.label}" if result.label else "",
            f"  Asset: {result.asset}" if result.asset else "",
            "=" * 60,
            f"Windows Evaluated: {result.total_windows}",
            f"Total Trades:      {result.total_trades}",
            f"  Directional:     {result.directional_trades}",
            f"  Sett. Ride:      {result.settlement_ride_trades}",
            f"  Cert. Scalp:     {result.certainty_scalp_trades}",
            f"Trade Rate:        {result.trade_rate:.1%} of windows",
            f"Winning Trades:    {result.winning_trades}",
            f"Losing Trades:     {result.losing_trades}",
            f"Win Rate:          {result.win_rate:.1%}",
            "",
            f"Initial Bankroll:  ${result.initial_bankroll:.2f}",
            f"Final Bankroll:    ${result.final_bankroll:.2f}",
            f"Total P&L:         ${result.total_pnl:+.2f}",
            f"Total Fees:        ${result.total_fees:.2f}",
            f"Max Drawdown:      ${result.max_drawdown:.2f}",
            f"Sharpe Ratio:      {result.sharpe_ratio:.2f}",
            f"Profit Factor:     {result.profit_factor:.2f}",
            f"Avg Edge:          {result.avg_edge:.4f}",
        ]

        if result.stop_loss_exits > 0:
            lines.append(f"Stop-Loss Exits:   {result.stop_loss_exits}")
        if result.drawdown_blocks > 0:
            lines.append(f"Drawdown Blocks:   {result.drawdown_blocks}")

        # Remove empty label/asset lines
        lines = [l for l in lines if l != ""]

        if result.trades:
            pnls = [t.pnl for t in result.trades]
            lines.extend([
                "",
                "--- Trade Statistics ---",
                f"Avg P&L per trade: ${np.mean(pnls):+.4f}",
                f"Median P&L:        ${np.median(pnls):+.4f}",
                f"Std Dev P&L:       ${np.std(pnls):.4f}",
                f"Best Trade:        ${max(pnls):+.4f}",
                f"Worst Trade:       ${min(pnls):+.4f}",
                f"Avg Fees:          ${np.mean([t.fees for t in result.trades]):.4f}",
            ])

            # Side breakdown
            yes_trades = [t for t in result.trades if t.side == "yes"]
            no_trades = [t for t in result.trades if t.side == "no"]
            if yes_trades:
                yes_wr = sum(1 for t in yes_trades if t.pnl > 0) / len(yes_trades)
                yes_pnl = sum(t.pnl for t in yes_trades)
                lines.append(
                    f"YES trades: {len(yes_trades)} ({yes_wr:.1%} WR, ${yes_pnl:+.2f})"
                )
            if no_trades:
                no_wr = sum(1 for t in no_trades if t.pnl > 0) / len(no_trades)
                no_pnl = sum(t.pnl for t in no_trades)
                lines.append(
                    f"NO  trades: {len(no_trades)} ({no_wr:.1%} WR, ${no_pnl:+.2f})"
                )

        if result.risk_blocks > 0:
            lines.append(f"\nRisk blocks: {result.risk_blocks}")

        lines.append("=" * 60)
        return "\n".join(lines)

    def signal_type_breakdown(self, result: BacktestResult) -> str:
        """Performance breakdown by signal type."""
        if not result.trades:
            return "No trades to analyze."

        lines = [
            "--- Signal Type Breakdown ---",
            f"{'Type':<16} {'Count':<7} {'Win Rate':<10} {'PnL':<12} {'Avg Edge':<10}",
            "-" * 55,
        ]

        for sig_type in ["directional", "settlement_ride", "certainty_scalp"]:
            trades = [t for t in result.trades if t.signal_type == sig_type]
            if not trades:
                continue
            count = len(trades)
            wins = sum(1 for t in trades if t.pnl > 0)
            wr = wins / count if count > 0 else 0.0
            pnl = sum(t.pnl for t in trades)
            avg_edge = np.mean([t.edge for t in trades])
            lines.append(
                f"{sig_type:<16} {count:<7} {wr:<10.1%} ${pnl:<11.2f} {avg_edge:<10.4f}"
            )

        # Total row
        count = len(result.trades)
        wins = result.winning_trades
        wr = result.win_rate
        pnl = result.total_pnl
        avg_edge = result.avg_edge
        lines.append("-" * 55)
        lines.append(
            f"{'TOTAL':<16} {count:<7} {wr:<10.1%} ${pnl:<11.2f} {avg_edge:<10.4f}"
        )

        return "\n".join(lines)

    def exit_type_breakdown(self, result: BacktestResult) -> str:
        """Performance breakdown by exit type (settlement vs stop-loss)."""
        if not result.trades:
            return "No trades to analyze."

        lines = [
            "--- Exit Type Breakdown ---",
            f"{'Type':<14} {'Count':<7} {'Win Rate':<10} {'PnL':<12} {'Avg PnL':<10}",
            "-" * 53,
        ]

        for exit_type in ["settlement", "stop_loss"]:
            trades = [t for t in result.trades if t.exit_type == exit_type]
            if not trades:
                continue
            count = len(trades)
            wins = sum(1 for t in trades if t.pnl > 0)
            wr = wins / count if count > 0 else 0.0
            pnl = sum(t.pnl for t in trades)
            avg_pnl = np.mean([t.pnl for t in trades])
            lines.append(
                f"{exit_type:<14} {count:<7} {wr:<10.1%} ${pnl:<11.2f} ${avg_pnl:<9.4f}"
            )

        return "\n".join(lines)

    def comparison_report(
        self, baseline: BacktestResult, candidate: BacktestResult
    ) -> str:
        """Side-by-side comparison of two backtest results."""
        b_label = baseline.label or "Baseline"
        c_label = candidate.label or "Candidate"

        def _delta(b: float, c: float, fmt: str = "+.4f", pct: bool = False) -> str:
            diff = c - b
            if pct and b != 0:
                pct_change = (diff / abs(b)) * 100
                return f"{diff:{fmt}} ({pct_change:+.1f}%)"
            return f"{diff:{fmt}}"

        lines = [
            "=" * 70,
            f"COMPARISON: {b_label} vs {c_label}",
            "=" * 70,
            f"{'Metric':<22} {b_label:>14} {c_label:>14} {'Delta':>16}",
            "-" * 70,
            f"{'Total Trades':<22} {baseline.total_trades:>14} {candidate.total_trades:>14} {_delta(baseline.total_trades, candidate.total_trades, '+d'):>16}",
            f"{'  Directional':<22} {baseline.directional_trades:>14} {candidate.directional_trades:>14} {_delta(baseline.directional_trades, candidate.directional_trades, '+d'):>16}",
            f"{'  Sett. Ride':<22} {baseline.settlement_ride_trades:>14} {candidate.settlement_ride_trades:>14} {_delta(baseline.settlement_ride_trades, candidate.settlement_ride_trades, '+d'):>16}",
            f"{'  Cert. Scalp':<22} {baseline.certainty_scalp_trades:>14} {candidate.certainty_scalp_trades:>14} {_delta(baseline.certainty_scalp_trades, candidate.certainty_scalp_trades, '+d'):>16}",
            f"{'Win Rate':<22} {baseline.win_rate:>13.1%} {candidate.win_rate:>13.1%} {_delta(baseline.win_rate * 100, candidate.win_rate * 100, '+.1f'):>15}pp",
            f"{'Total PnL':<22} {'$' + f'{baseline.total_pnl:.2f}':>13} {'$' + f'{candidate.total_pnl:.2f}':>13} {_delta(baseline.total_pnl, candidate.total_pnl, '+.2f'):>16}",
            f"{'Max Drawdown':<22} {'$' + f'{baseline.max_drawdown:.2f}':>13} {'$' + f'{candidate.max_drawdown:.2f}':>13} {_delta(baseline.max_drawdown, candidate.max_drawdown, '+.2f'):>16}",
            f"{'Sharpe Ratio':<22} {baseline.sharpe_ratio:>14.2f} {candidate.sharpe_ratio:>14.2f} {_delta(baseline.sharpe_ratio, candidate.sharpe_ratio, '+.2f'):>16}",
            f"{'Profit Factor':<22} {baseline.profit_factor:>14.2f} {candidate.profit_factor:>14.2f} {_delta(baseline.profit_factor, candidate.profit_factor, '+.2f'):>16}",
            f"{'Avg Edge':<22} {baseline.avg_edge:>14.4f} {candidate.avg_edge:>14.4f} {_delta(baseline.avg_edge, candidate.avg_edge):>16}",
            f"{'Total Fees':<22} {'$' + f'{baseline.total_fees:.2f}':>13} {'$' + f'{candidate.total_fees:.2f}':>13} {_delta(baseline.total_fees, candidate.total_fees, '+.2f'):>16}",
            f"{'Trade Rate':<22} {baseline.trade_rate:>13.1%} {candidate.trade_rate:>13.1%} {_delta(baseline.trade_rate * 100, candidate.trade_rate * 100, '+.1f'):>15}pp",
            f"{'Stop-Loss Exits':<22} {baseline.stop_loss_exits:>14} {candidate.stop_loss_exits:>14} {_delta(baseline.stop_loss_exits, candidate.stop_loss_exits, '+d'):>16}",
            "=" * 70,
        ]

        # Verdict
        pnl_better = candidate.total_pnl > baseline.total_pnl
        wr_better = candidate.win_rate > baseline.win_rate
        sharpe_better = candidate.sharpe_ratio > baseline.sharpe_ratio
        score = sum([pnl_better, wr_better, sharpe_better])
        if score >= 2:
            verdict = f"{c_label} WINS on {score}/3 key metrics"
        elif score == 1:
            verdict = "Mixed results — review carefully"
        else:
            verdict = f"{b_label} WINS on {3 - score}/3 key metrics"
        lines.append(f"Verdict: {verdict}")

        return "\n".join(lines)

    def plot_equity_curve(
        self, result: BacktestResult, save_path: str
    ) -> None:
        """Plot equity curve with drawdown overlay."""
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            print(
                "matplotlib required for plotting. "
                "Install with: pip install matplotlib"
            )
            return

        if not result.equity_curve:
            return

        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        times = list(range(len(result.equity_curve)))
        values = [v for _, v in result.equity_curve]

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(14, 8), height_ratios=[3, 1]
        )

        # Equity curve
        ax1.plot(times, values, linewidth=1, color="steelblue")
        title = "Equity Curve"
        if result.label:
            title += f" — {result.label}"
        if result.asset:
            title += f" ({result.asset})"
        ax1.set_title(title)
        ax1.set_ylabel("Portfolio Value ($)")
        ax1.grid(True, alpha=0.3)
        ax1.axhline(y=values[0], color="gray", linestyle="--", alpha=0.5)

        # Mark trades
        trade_indices = []
        trade_values = []
        trade_colors = []
        for i, (ts, val) in enumerate(result.equity_curve):
            # Check if this point corresponds to a trade
            if any(t.timestamp.isoformat() == ts and t.pnl != 0 for t in result.trades):
                trade_indices.append(i)
                trade_values.append(val)
                matching = [t for t in result.trades if t.timestamp.isoformat() == ts]
                if matching and matching[0].pnl > 0:
                    trade_colors.append("green")
                else:
                    trade_colors.append("red")
        if trade_indices:
            ax1.scatter(
                trade_indices, trade_values, c=trade_colors, s=15, alpha=0.6, zorder=5
            )

        # Drawdown
        values_arr = np.array(values)
        peak = np.maximum.accumulate(values_arr)
        drawdown = peak - values_arr
        ax2.fill_between(times, drawdown, color="salmon", alpha=0.5)
        ax2.set_title("Drawdown ($)")
        ax2.set_ylabel("Drawdown")
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Equity curve saved to {save_path}")

    def plot_edge_distribution(
        self, result: BacktestResult, save_path: str
    ) -> None:
        """Plot distribution of predicted edges vs actual outcomes."""
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            return

        if not result.trades:
            return

        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        edges = [t.edge for t in result.trades]
        colors = ["green" if t.pnl > 0 else "red" for t in result.trades]

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.bar(range(len(edges)), edges, color=colors, alpha=0.6, width=1)
        title = "Edge Distribution (Green=Win, Red=Loss)"
        if result.label:
            title += f" — {result.label}"
        if result.asset:
            title += f" ({result.asset})"
        ax.set_title(title)
        ax.set_xlabel("Trade #")
        ax.set_ylabel("Net Edge")
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Edge distribution saved to {save_path}")

    def calibration_report(self, result: BacktestResult) -> str:
        """Compute model calibration: predicted probability vs actual win rate."""
        if not result.trades:
            return "No trades to analyze."

        # Bucket predictions into deciles
        buckets: dict[str, list[bool]] = {}
        for trade in result.trades:
            prob = trade.model_prob
            bucket = f"{int(prob * 10) / 10:.1f}-{int(prob * 10) / 10 + 0.1:.1f}"
            if bucket not in buckets:
                buckets[bucket] = []

            if trade.side == "yes":
                buckets[bucket].append(trade.settled_yes)
            else:
                buckets[bucket].append(not trade.settled_yes)

        lines = [
            "--- Model Calibration ---",
            f"{'Bucket':<12} {'Count':<8} {'Win Rate':<10} {'Predicted':<10}",
            "-" * 40,
        ]

        for bucket_name in sorted(buckets.keys()):
            outcomes = buckets[bucket_name]
            win_rate = sum(outcomes) / len(outcomes) if outcomes else 0
            mid_pred = float(bucket_name.split("-")[0]) + 0.05
            lines.append(
                f"{bucket_name:<12} {len(outcomes):<8} {win_rate:<10.1%} {mid_pred:<10.1%}"
            )

        return "\n".join(lines)
