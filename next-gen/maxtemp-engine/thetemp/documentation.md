# TheTemp Model Template

`thetemp` is a copy-ready template for new max-temperature model families. It exists to make new model experiments consistent with Raycaster-style commands and report outputs.

## Clone Checklist

1. Copy `maxtemp-engine/thetemp/` to `maxtemp-engine/<model-name>/`.
2. Rename model constants and CLI labels.
3. Replace placeholder feature/training/prediction logic.
4. Keep market-price features out unless the model is intentionally moved to `strategy-engine/`.
5. Add or update tests under the new model's `v1/tests/`.
6. Add a discovery shim under `next-gen/tests/` if normal unittest discovery cannot import the model path.
7. Update the new model family's `documentation.md`.

## What Belongs Here

- Reusable model folder layout.
- Minimal CLI and artifact conventions.
- Baseline examples for feature extraction, prediction, evaluation, and tests.
- Stable report-output examples.

## What Does Not Belong Here

- Serious production model claims.
- Trading strategy logic.
- Kalshi market-price features.
- Collector code.

## When To Use

Use this template when starting a new weather-only max-temperature model. Do not mutate Raycaster directly for unrelated experiments; clone this template or create a new Raycaster version.
