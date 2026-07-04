# Model Reports

Model evaluation and prediction reports belong here.

## Examples

- `raycaster_v1_eval`
- `raycaster_v1_rolling`
- `raycaster_v1_predictions`
- `thetemp_eval`

## Common Files

Model report folders may contain:

- `summary.json`
- `predictions.csv`
- `bracket_distributions.csv`
- `training_diagnostics.csv`
- `temperature_metrics.csv`
- `bracket_metrics.csv`
- `errors.csv`
- `by_checkpoint.csv`
- `by_city.csv`
- `charts/`

## Trends Compatibility

The Trends GUI discovers model reports from this folder. Prefer exact timestamped report folders so the GUI can load a specific run without ambiguity.

## Rule

Do not write model reports back to Supabase. Supabase stores source facts and final labels only.
