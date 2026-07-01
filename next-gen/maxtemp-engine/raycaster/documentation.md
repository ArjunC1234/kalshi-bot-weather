# Raycaster

Raycaster is the model family for predicting the final NWS-recorded daily maximum
temperature. Each model version lives in its own subfolder so experiments can be
compared without overwriting old assumptions.

## What Belongs Here

- Versioned max-temperature models, for example `v1/`.
- Model-family documentation and promotion notes.
- Shared Raycaster notes that apply across versions.

## What Does Not Belong Here

- Trading rules, position sizing, or PnL logic. Put those in `strategy-engine/`.
- Production deployment scripts. Put those in `production/`.
- Raw collectors or Supabase sync code. Put those in `libs/` or production-specific code.

## How To Use

Run version-specific commands from the `next-gen/` folder. For v1:

```powershell
python maxtemp-engine/raycaster/v1/cli.py evaluate --data data/export --output reports/raycaster_v1
```

