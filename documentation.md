# Repository Documentation

This repo is organized around a clean split between archived experiments and the new modular implementation.

## Folders

- `legacy-experimentation/` contains all prior prototype work, historical reports, old collectors, old backtests, and generated outputs.
- `next-gen/` contains all new implementation work going forward.

## Rules

- Do not add new model, strategy, backtest, or production code at the repository root.
- Do not commit real `.env` files or credentials.
- Add shared reusable code under `next-gen/libs/`.
- Add engine-specific code and tests inside the relevant engine folder.

## Common Commands

```powershell
python -m compileall next-gen
python -m unittest discover -s next-gen
python -m ruff check next-gen
```
