"""Backtest analysis and reporting."""

from __future__ import annotations

import numpy as np

from backtest.backtester import BacktestResult


class BacktestAnalyzer:
    """Generates reports and visualizations from backtest results."""

    def summary(self, result: BacktestResult) -> str:
        """Generate a text summary of backtest performance."""
        lines = [
            "=" * 60,
            "BACKTEST RESULTS",
            "=" * 60,
            f"Total Trades:      {result.total_trades}",
            f"Winning Trades:    {result.winning_trades}",
            f"Losing Trades:     {result.losing_trades}",
            f"Win Rate:          {result.win_rate:.1%}",
            "",
            f"Total P&L:         ${result.total_pnl:.2f}",
            f"Max Drawdown:      ${result.max_drawdown:.2f}",
            f"Sharpe Ratio:      {result.sharpe_ratio:.2f}",
            f"Profit Factor:     {result.profit_factor:.2f}",
            f"Avg Edge:          {result.avg_edge:.4f}",
        ]

        if result.trades:
            pnls = [t.pnl for t in result.trades]
            lines.extend([
                "",
                "--- Trade Statistics ---",
                f"Avg P&L per trade: ${np.mean(pnls):.2f}",
                f"Median P&L:        ${np.median(pnls):.2f}",
                f"Std Dev P&L:       ${np.std(pnls):.2f}",
                f"Best Trade:        ${max(pnls):.2f}",
                f"Worst Trade:       ${min(pnls):.2f}",
                f"Avg Fees:          ${np.mean([t.fees for t in result.trades]):.2f}",
            ])

            # Side breakdown
            yes_trades = [t for t in result.trades if t.side == "yes"]
            no_trades = [t for t in result.trades if t.side == "no"]
            if yes_trades:
                yes_wr = sum(1 for t in yes_trades if t.pnl > 0) / len(yes_trades)
                lines.append(
                    f"YES trades: {len(yes_trades)} ({yes_wr:.1%} win rate)"
                )
            if no_trades:
                no_wr = sum(1 for t in no_trades if t.pnl > 0) / len(no_trades)
                lines.append(
                    f"NO  trades: {len(no_trades)} ({no_wr:.1%} win rate)"
                )

        lines.append("=" * 60)
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
                "Install with: pip install kalshi-btc-bot[backtest]"
            )
            return

        if not result.equity_curve:
            return

        times, values = zip(*result.equity_curve)

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), height_ratios=[3, 1])

        # Equity curve
        ax1.plot(times, values, linewidth=1, color="steelblue")
        ax1.set_title("Equity Curve")
        ax1.set_ylabel("Portfolio Value ($)")
        ax1.grid(True, alpha=0.3)
        ax1.axhline(y=values[0], color="gray", linestyle="--", alpha=0.5)

        # Drawdown
        values_arr = np.array(values)
        peak = np.maximum.accumulate(values_arr)
        drawdown = (peak - values_arr)
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

        edges = [t.edge for t in result.trades]
        colors = ["green" if t.pnl > 0 else "red" for t in result.trades]

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.bar(range(len(edges)), edges, color=colors, alpha=0.6, width=1)
        ax.set_title("Edge Distribution (Green=Win, Red=Loss)")
        ax.set_xlabel("Trade #")
        ax.set_ylabel("Net Edge")
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.close()

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
                buckets[bucket].append(trade.actual_up)
            else:
                buckets[bucket].append(not trade.actual_up)

        lines = [
            "Model Calibration Report",
            "-" * 40,
            f"{'Bucket':<12} {'Count':<8} {'Win Rate':<10} {'Predicted':<10}",
        ]

        for bucket_name in sorted(buckets.keys()):
            outcomes = buckets[bucket_name]
            win_rate = sum(outcomes) / len(outcomes) if outcomes else 0
            mid_pred = float(bucket_name.split("-")[0]) + 0.05
            lines.append(
                f"{bucket_name:<12} {len(outcomes):<8} {win_rate:<10.1%} {mid_pred:<10.1%}"
            )

        return "\n".join(lines)
