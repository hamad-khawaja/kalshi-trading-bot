Run the parameter robustness analysis to check for overfitting:

1. Run `.venv/bin/python scripts/robustness_check.py --db data/bot.db --output results/robustness_report.md`
2. Display the summary table showing each parameter's sensitivity and fragility
3. For any **fragile** parameters (P&L sign changes across perturbation range), highlight them and explain the risk
4. Show the per-parameter detail tables for fragile parameters only (skip stable ones unless the user asks)
5. Report the output file path: `results/robustness_report.md`

If the database is empty or missing, inform the user they need trade history first.
