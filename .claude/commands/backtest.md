Run a backtest using historical Binance candle data:

1. Parse the user's request for parameters:
   - `--days N` (default: 7)
   - `--bankroll N` (default: 1000)
   - `--asset BTC|ETH|all` (default: BTC)
   - `--label name` (optional — saves results for later comparison)
   - `--plot` (optional — generate equity curve and edge distribution PNGs)
   - `--config path` (default: config/settings.yaml)
   - `--compare baseline.json candidate.json` (compare two saved runs)

   The user may say things like "backtest 30 days ETH" or "backtest BTC vs ETH for 2 weeks" — map to the right flags.

2. Run the backtest:
   ```
   .venv/bin/python -m backtest <flags>
   ```

3. Present the results in a clean summary:
   - Total trades, win rate, total P&L, max drawdown, Sharpe ratio, profit factor
   - Signal type breakdown (directional, FOMO, certainty_scalp, settlement_ride, market_making)
   - Exit type breakdown (settlement win/loss, stop-loss, take-profit)
   - Calibration report (predicted prob vs actual win rate)

4. If `--label` was used, confirm the results file was saved to `results/<label>_<asset>.json`

5. If `--compare` was used, show the side-by-side comparison report

6. If `--plot` was used, confirm plot files were saved and show their paths

7. If the backtest shows negative P&L, briefly note which signal types or exit types are dragging performance and suggest which parameters to investigate with `/robustness-check`

Arguments are passed through from the user's message after `/backtest`. For example:
- `/backtest` → 7 days BTC $1000
- `/backtest 30 days all` → `--days 30 --asset all --bankroll 1000`
- `/backtest ETH 14 days labeled eth-baseline with plots` → `--days 14 --asset ETH --label eth-baseline --plot --bankroll 1000`
- `/backtest compare baseline candidate` → `--compare results/baseline_BTC.json results/candidate_BTC.json`
