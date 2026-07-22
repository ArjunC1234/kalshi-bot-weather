# Neuralcaster v1

`neuralcaster_v1` is an offline experiment for checking whether a small neural network can learn residuals over the existing weather source-blend baseline.

It is intentionally not wired into production, replay, or deployment. The current dataset is still thin, so the output should be treated as exploratory model diagnostics rather than evidence for promotion.

Run from `next-gen`:

```powershell
python maxtemp-engine/neuralcaster/v1/cli.py rolling-eval --data data/export_20260701_20260715_20260715T175455Z --output reports/model/neuralcaster_v1_experiment --train-days 5 --test-days 1
python maxtemp-engine/neuralcaster/v1/cli.py report --run reports/model/neuralcaster_v1_experiment
```

