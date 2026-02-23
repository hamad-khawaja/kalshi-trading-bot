Run all tests and verification scripts for the trading bot:

1. **Pytest**: Run the full test suite with `.venv/bin/pytest tests/ -x --tb=short` and report:
   - Total tests collected
   - Pass / fail / error counts
   - Any failing test names with short tracebacks
   - Total runtime

2. **Ruff lint**: Run `.venv/bin/ruff check src/ tests/` and report:
   - Total error count
   - Breakdown by rule (E501, F401, etc.) with counts
   - Any NEW errors in recently modified files (check `git diff --name-only` to identify changed files, then filter lint output to those files)

3. **Type check** (if mypy is available): Run `.venv/bin/mypy src/ --ignore-missing-imports` or skip with a note if mypy is not installed

4. **Import health**: Verify the bot can import cleanly with `.venv/bin/python3 -c "from src.bot import TradingBot; print('Import OK')"` — catches circular imports or missing dependencies

5. **Summary**: Present a single pass/fail verdict for each check in a table:

   | Check       | Result | Details |
   |-------------|--------|---------|
   | Pytest      | ...    | ...     |
   | Ruff        | ...    | ...     |
   | Type check  | ...    | ...     |
   | Import      | ...    | ...     |

Flag any regressions clearly. If all checks pass, confirm the codebase is ready.
