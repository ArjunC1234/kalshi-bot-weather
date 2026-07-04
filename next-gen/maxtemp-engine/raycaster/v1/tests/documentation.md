# Raycaster v1 Tests

These tests validate the Raycaster v1 contract.

## Should Cover

- Feature extraction from `WeatherSnapshot` and related event rows.
- Missing source fallback behavior.
- Source-blend fallback before enough final-high labels exist.
- Point and quantile prediction shape.
- Quantile monotonicization.
- Observed-high floor behavior.
- Temperature-to-bracket probability conversion.
- Expanding-window and rolling-window leakage prevention.
- Output CSV/JSON generation.

## Discovery Note

Because `maxtemp-engine` contains a hyphen, normal Python package discovery cannot import it as a package name. Top-level unittest discovery uses a shim under `next-gen/tests/` to load these tests.

Run from repository root:

```powershell
python -m unittest discover -s next-gen
```
