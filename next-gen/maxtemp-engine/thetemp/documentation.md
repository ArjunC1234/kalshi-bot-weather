# TheTemp Model Template

`thetemp` is a copy-ready template for new max-temperature model families. Clone
this folder when starting a new model, then rename the folder, model constants,
CLI labels, and tests.

## Clone Checklist

1. Copy `maxtemp-engine/thetemp/` to `maxtemp-engine/<model-name>/`.
2. Rename `MODEL_NAME` in `v1/config.py`.
3. Replace placeholder feature/training logic with the new model's real logic.
4. Add or update tests under the new model's `v1/tests/`.
5. Add a discovery shim under `next-gen/tests/` if the model path is not importable
   by normal unittest discovery.

## What Belongs Here

- Reusable model folder layout.
- Minimal CLI and artifact conventions.
- Baseline examples for feature extraction, prediction, evaluation, and tests.

## What Does Not Belong Here

- Real production model claims.
- Trading strategy logic.
- Kalshi market-price features.

