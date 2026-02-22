Scaffold a new trading strategy for the bot. The strategy name is provided as `$ARGUMENTS`.

Follow the existing patterns in the codebase:

1. **Create detector** at `src/strategy/<name>_detector.py`:
   - Class named `<Name>Detector` or `<Name>SignalDetector`
   - Constructor takes `StrategyConfig` (and optionally `VolatilityTracker`)
   - Main method `detect()` takes relevant inputs (prediction, snapshot, features) and returns `TradeSignal | None`
   - Use `structlog.get_logger()` for logging with snake_case event names
   - Use `Decimal` for all price calculations
   - Follow the pattern from `src/strategy/edge_detector.py` or `src/strategy/mc_detector.py`

2. **Add config fields** to `src/config.py` in `StrategyConfig`:
   - Enable toggle: `<name>_enabled: bool = False`
   - Key thresholds as needed

3. **Wire into SignalCombiner** at `src/strategy/signal_combiner.py`:
   - Import the detector
   - Instantiate in `__init__`
   - Add evaluation block in `evaluate()` following the priority order documented in CLAUDE.md
   - Include phase gating if appropriate

4. **Create test file** at `tests/test_<name>.py`:
   - Use fixtures from `tests/conftest.py` (sample_snapshot, sample_prediction, sample_feature_vector, bot_settings)
   - Test: signal generation, edge cases, config toggle, no signal when conditions aren't met
   - Follow pytest-asyncio patterns (asyncio_mode = "auto")

5. **Update CLAUDE.md** strategy priority table if needed

After scaffolding, explain what was created and what the user needs to customize (thresholds, detection logic, signal conditions).
