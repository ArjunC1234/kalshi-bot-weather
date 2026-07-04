# Legacy Experimentation

This folder is an archive of the first chapter of the project. It is kept for evidence, recovery, and comparison, not as an active development area.

## What Belongs Here

- Prototype weather probability scripts.
- Old backtesting and strategy simulation scripts.
- Old local collector experiments.
- Old demo trading bot experiments.
- Historical `backtest_data/` and `output/` folders.
- Early reports, plots, notebooks, ad-hoc CSV files, and scripts.
- Old systemd files that were replaced by next-gen production files.

## What Does Not Belong Here

- New Raycaster code.
- New backtest engine code.
- New strategy-engine code.
- New production collector code.
- New Trends GUI work.

## Usage Rules

- Treat these files as read-mostly.
- Do not import legacy scripts directly from next-gen modules.
- If a legacy idea is useful, copy the idea into the correct next-gen subsystem and rewrite it around current shared models and exports.
- Do not deploy files from this folder to the droplet.

## Why This Archive Still Matters

Legacy work contains useful lessons:

- Weather accuracy alone was not enough to infer trading edge.
- Exact point-in-time data capture matters more than retrospective reconstruction.
- Market data, weather model outputs, and strategy outputs must stay separated.
- The active database should store facts, not experiments.
- Model reports should be generated locally from frozen exports.
