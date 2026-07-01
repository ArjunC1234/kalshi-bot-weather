# Next-Gen Codebase

This folder contains all new implementation work.

## Subsystems

- `libs/`: shared clients, schemas, utilities, and reusable helpers.
- `maxtemp-engine/`: weather models that predict final NWS max temperature and convert distributions into Kalshi brackets.
- `backtest/`: offline replay, Supabase export loading, settlement ingestion, metrics, and charts.
- `strategy-engine/`: trade decision logic, EV filters, position sizing, fee modeling, and PnL simulation.
- `production/`: deployable server bot structure and upload scripts.
- `tests/`: cross-subsystem integration tests only.

## Rules

- Engine-specific tests belong in that engine's `tests/` folder.
- Engine scripts can live directly in the engine folder beside `documentation.md`.
- Cross-engine tests belong in `next-gen/tests/`.
- Production code must only upload `next-gen/production/deployable/`.
- Market prices belong in strategy code, not the max-temperature model.
