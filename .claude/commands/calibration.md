Run the calibration analysis to check model probability accuracy:

1. Run `.venv/bin/python scripts/calibration_report.py data/bot.db`
2. Display the full calibration report
3. Highlight any signal types with ECE > 0.10 (poorly calibrated)
4. For `settlement_ride` and `trend_continuation` specifically, note whether the model is overconfident or underconfident
5. If Brier score > 0.220, suggest the model may need reweighting
6. If the model is worse than market (implied) calibration, note this and suggest investigating which strategies are dragging model accuracy

If the database has fewer than 30 settlement trades with calibration data, inform the user they need more data for reliable calibration.
