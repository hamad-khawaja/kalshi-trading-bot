"""CLI entry point for the backtester.

Usage:
    python -m backtest --days 30 --bankroll 1000
    python -m backtest --days 30 --bankroll 1000 --asset BTC
    python -m backtest --days 30 --bankroll 1000 --asset ETH
    python -m backtest --days 30 --bankroll 1000 --asset all
    python -m backtest --days 30 --bankroll 1000 --label baseline
    python -m backtest --days 30 --bankroll 1000 --label candidate
    python -m backtest --compare results/baseline.json results/candidate.json
    python -m backtest --days 30 --bankroll 1000 --plot
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

ASSET_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}


def _run_single(
    asset: str,
    symbol: str,
    days: int,
    bankroll: float,
    config_path: str,
    label: str,
    plot: bool,
    analyzer: BacktestAnalyzer,
) -> BacktestResult:
    """Run backtest for a single asset and print results."""
    print(f"{asset} 15-Minute Backtester")
    print(f"{'=' * 60}")
    print(f"Days: {days} | Bankroll: ${bankroll:.2f} | Symbol: {symbol}")
    print()

    # Step 1: Fetch candles
    print("Fetching historical candles...")
    t0 = time.time()
    candles = asyncio.run(fetch_candles(days, symbol=symbol))
    fetch_time = time.time() - t0
    print(f"  Fetched in {fetch_time:.1f}s")
    print()

    if candles.empty:
        print(f"Error: No candle data fetched for {symbol}.")
        return BacktestResult()

    # Step 2: Load settings and run backtest
    print("Running backtest...")
    settings = load_settings(config_path)
    backtester = Backtester(settings, asset=asset)

    t0 = time.time()
    result = backtester.run(candles, initial_bankroll=bankroll)
    run_time = time.time() - t0
    print(f"  Simulated {result.total_windows} windows in {run_time:.1f}s")
    print()

    # Step 3: Print results
    if label:
        result.label = label
    result.asset = asset
    print(analyzer.summary(result))

    # Step 4: Signal type breakdown
    print()
    print(analyzer.signal_type_breakdown(result))

    # Step 5: Exit type breakdown
    print()
    print(analyzer.exit_type_breakdown(result))

    # Step 6: Calibration report
    print()
    print(analyzer.calibration_report(result))

    # Step 7: Save results if labeled
    if label:
        save_path = f"results/{label}_{asset}.json" if asset else f"results/{label}.json"
        result.to_json(save_path)
        print(f"\nResults saved to {save_path}")

    # Step 8: Generate plots if requested
    if plot:
        prefix = f"results/{label}_{asset}_" if label else f"results/backtest_{asset}_"
        analyzer.plot_equity_curve(result, f"{prefix}equity.png")
        analyzer.plot_edge_distribution(result, f"{prefix}edges.png")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BTC/ETH 15-minute binary options backtester"
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
        help="Label for this run; saves results to results/<label>_<asset>.json",
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
    parser.add_argument(
        "--asset",
        type=str,
        choices=["BTC", "ETH", "all"],
        default="BTC",
        help="Asset to backtest: BTC, ETH, or all (default: BTC)",
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

    # --- Determine assets to run ---
    if args.asset == "all":
        assets = [("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")]
    else:
        symbol = ASSET_SYMBOLS.get(args.asset, args.symbol)
        assets = [(args.asset, symbol)]

    # --- Run backtests ---
    results: list[BacktestResult] = []
    for asset_name, symbol in assets:
        if len(assets) > 1:
            print(f"\n{'#' * 60}")
            print(f"# {asset_name} Backtest")
            print(f"{'#' * 60}\n")

        result = _run_single(
            asset=asset_name,
            symbol=symbol,
            days=args.days,
            bankroll=args.bankroll,
            config_path=args.config,
            label=args.label,
            plot=args.plot,
            analyzer=analyzer,
        )
        results.append(result)

    # --- Combined summary for multi-asset ---
    if len(results) > 1:
        print(f"\n{'=' * 60}")
        print("COMBINED SUMMARY")
        print(f"{'=' * 60}")
        total_trades = sum(r.total_trades for r in results)
        total_pnl = sum(r.total_pnl for r in results)
        total_fees = sum(r.total_fees for r in results)
        for r in results:
            asset_label = r.asset or "?"
            print(
                f"  {asset_label}: {r.total_trades} trades, "
                f"${r.total_pnl:+.2f} PnL, "
                f"{r.win_rate:.1%} WR"
            )
        print(f"  {'---':>4}")
        print(
            f"  Total: {total_trades} trades, "
            f"${total_pnl:+.2f} PnL, "
            f"${total_fees:.2f} fees"
        )
        print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
