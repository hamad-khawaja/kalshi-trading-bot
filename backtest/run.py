"""CLI entry point for the backtester.

Usage:
    python -m backtest --days 30 --bankroll 100
    python -m backtest --days 30 --bankroll 100 --label baseline
    python -m backtest --days 30 --bankroll 100 --label candidate
    python -m backtest --compare results/baseline.json results/candidate.json
    python -m backtest --days 30 --bankroll 100 --plot
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from backtest.analysis import BacktestAnalyzer
from backtest.backtester import BacktestResult, Backtester
from backtest.fetch_data import fetch_candles
from src.config import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BTC 15-minute binary options backtester"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days of history to test (default: 7)",
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=100.0,
        help="Starting bankroll in dollars (default: 100)",
    )
    parser.add_argument(
        "--label",
        type=str,
        default="",
        help="Label for this run; saves results to results/<label>.json",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BASELINE", "CANDIDATE"),
        help="Compare two saved result files",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Generate equity curve and edge distribution plots",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/settings.yaml",
        help="Path to settings.yaml (default: config/settings.yaml)",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="BTCUSDT",
        help="Binance symbol (default: BTCUSDT)",
    )

    args = parser.parse_args()
    analyzer = BacktestAnalyzer()

    # --- Comparison mode ---
    if args.compare:
        baseline_path, candidate_path = args.compare
        try:
            baseline = BacktestResult.from_json(baseline_path)
            candidate = BacktestResult.from_json(candidate_path)
        except FileNotFoundError as e:
            print(f"Error: {e}")
            sys.exit(1)

        report = analyzer.comparison_report(baseline, candidate)
        print(report)
        return

    # --- Normal backtest mode ---
    print(f"BTC 15-Minute Backtester")
    print(f"{'=' * 60}")
    print(f"Days: {args.days} | Bankroll: ${args.bankroll:.2f} | Symbol: {args.symbol}")
    print()

    # Step 1: Fetch candles
    print("Fetching historical candles...")
    t0 = time.time()
    candles = asyncio.run(fetch_candles(args.days, symbol=args.symbol))
    fetch_time = time.time() - t0
    print(f"  Fetched in {fetch_time:.1f}s")
    print()

    if candles.empty:
        print("Error: No candle data fetched. Check your network connection.")
        sys.exit(1)

    # Step 2: Load settings and run backtest
    print("Running backtest...")
    settings = load_settings(args.config)
    backtester = Backtester(settings)

    t0 = time.time()
    result = backtester.run(candles, initial_bankroll=args.bankroll)
    run_time = time.time() - t0
    print(f"  Simulated {result.total_windows} windows in {run_time:.1f}s")
    print()

    # Step 3: Print results
    if args.label:
        result.label = args.label
    print(analyzer.summary(result))

    # Step 4: Signal type breakdown
    print()
    print(analyzer.signal_type_breakdown(result))

    # Step 5: Calibration report
    print()
    print(analyzer.calibration_report(result))

    # Step 6: Save results if labeled
    if args.label:
        save_path = f"results/{args.label}.json"
        result.to_json(save_path)
        print(f"\nResults saved to {save_path}")

    # Step 7: Generate plots if requested
    if args.plot:
        plot_prefix = f"results/{args.label}_" if args.label else "results/backtest_"
        analyzer.plot_equity_curve(result, f"{plot_prefix}equity.png")
        analyzer.plot_edge_distribution(result, f"{plot_prefix}edges.png")


if __name__ == "__main__":
    main()
