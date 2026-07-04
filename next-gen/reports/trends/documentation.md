# Trends Reports

Optional Trends-only diagnostics belong here.

## Current Behavior

The Trends GUI no longer writes or requires large generated `trends_data.json` files. It discovers local exports and report folders, then serves split API endpoints for the selected sources.

## What Belongs Here

- Future Trends-specific diagnostics.
- Saved UI/debug artifacts if they are intentionally generated.
- Small snapshots useful for debugging the GUI.

## What Does Not Belong Here

- Model reports. Put those in `reports/model/`.
- Data quality reports. Put those in `reports/quality/`.
- Frozen Supabase exports. Put those in `data/`.
