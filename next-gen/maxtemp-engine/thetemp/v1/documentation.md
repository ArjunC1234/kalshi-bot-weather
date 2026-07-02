# TheTemp v1 Template

This is a runnable template for a new final-NWS-high model version. It is not
intended to be a serious forecast model. Its purpose is to show the required
shape of a max-temperature model folder.

## Commands

Run from `next-gen/`:

```powershell
python maxtemp-engine/thetemp/v1/cli.py train --data data/export --output models/thetemp/v1/run_001
python maxtemp-engine/thetemp/v1/cli.py predict --data data/export --model models/thetemp/v1/run_001 --output reports/thetemp_predictions
python maxtemp-engine/thetemp/v1/cli.py evaluate --data data/export --output reports/thetemp_eval
python maxtemp-engine/thetemp/v1/cli.py report --run reports/thetemp_eval
```

## Template Behavior

- Uses a deterministic source-blend baseline.
- Uses no market prices.
- Converts expected high into a simple quantile distribution.
- Converts quantiles into Kalshi bracket probabilities.
- Writes the same core output names expected from max-temp models.

Replace the internals before treating a cloned model as a real candidate.

