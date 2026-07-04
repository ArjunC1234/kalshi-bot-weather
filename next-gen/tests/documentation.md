# Cross-System Tests

Use this folder for integration tests that cross subsystem boundaries.

## What Belongs Here

- Tests that load a frozen export and exercise multiple subsystems.
- Tests that verify Trends can read model and quality reports.
- Tests that verify model report output shape is compatible with Trends.
- Discovery shims for model folders that cannot be imported directly because of path names such as `maxtemp-engine`.

## What Does Not Belong Here

- Pure Raycaster tests.
- Pure backtest tests.
- Pure strategy tests.
- Production collector unit tests.

Run from repository root:

```powershell
python -m unittest discover -s next-gen
```
