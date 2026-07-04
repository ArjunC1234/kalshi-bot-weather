# TheTemp v1 Template

This is a runnable template for a final-NWS-high model version. It is not intended to be a serious forecast model. Its purpose is to define the expected shape of a max-temperature model folder.

## Commands

Run from `next-gen/`:

```powershell
python maxtemp-engine/thetemp/v1/cli.py train --data data/export_YYYY --output models/thetemp/v1/run_001
python maxtemp-engine/thetemp/v1/cli.py predict --data data/export_YYYY --model models/thetemp/v1/run_001 --output reports/model/thetemp_predictions
python maxtemp-engine/thetemp/v1/cli.py evaluate --data data/export_YYYY --output reports/model/thetemp_eval
python maxtemp-engine/thetemp/v1/cli.py report --run reports/model/thetemp_eval
```

## Template Behavior

- Uses a deterministic source-blend baseline.
- Uses no market prices.
- Converts expected high into a simple quantile distribution.
- Converts quantiles into Kalshi bracket probabilities.
- Writes the same core output names expected from max-temperature models.

## Replacement Checklist

- Replace placeholder feature extraction.
- Replace placeholder model training.
- Keep final NWS high as the temperature label.
- Preserve output file compatibility where possible.
- Add tests before treating the cloned model as a candidate.

Do not treat this template as a real model without replacing the internals.
