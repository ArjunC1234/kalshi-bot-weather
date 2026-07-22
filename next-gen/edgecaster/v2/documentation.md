# Edgecaster v2

`edgecaster/v2` is a research-only PyTorch candidate-set ranker for deciding
whether Neuralcaster-implied weather contract edges are tradeable.

It differs from `edgecaster/model.py` in two important ways:

- It scores all YES/NO candidates for an event snapshot together using a
  DeepSets-style context, because the real decision is to pick the best
  candidate or abstain.
- It does not hard-overwrite bounded bracket probabilities from observed highs.
  Observed-high distance to bracket bounds is a feature, not forced certainty.

## Commands

Fixed-window research run:

```powershell
python -m edgecaster.v2.cli fixed-window `
  --data data/export_20260701_20260717_20260717T184509Z `
  --model-report reports/model/neuralcaster_v2_fixed_train_20260702_20260714_score_20260702_20260716_20260717T `
  --output reports/strategy/edgecaster_v2_fixed_20260717T `
  --train-start 2026-07-02 `
  --train-end 2026-07-14 `
  --test-start 2026-07-15 `
  --test-end 2026-07-16
```

Walk-forward run:

```powershell
python -m edgecaster.v2.cli rolling-eval `
  --data data/export_20260701_20260717_20260717T184509Z `
  --model-report reports/model/neuralcaster_v2_gru_market_opt11_blend95_7d_20260717T `
  --output reports/strategy/edgecaster_v2_rolling_20260717T `
  --train-days 7
```

## Outputs

- `summary.json`: model config, training counts, and PnL summary.
- `predictions.csv`: every candidate with predicted reward, rank score, trade
  probability, realized reward, and features.
- `trades.csv`: selected paper trades.
- `daily_pnl.csv`, `city_metrics.csv`, `side_metrics.csv`: strategy summaries.
- `ranking_diagnostics.csv`: whether the rank/trade heads selected the best
  realized candidate in each event snapshot.
- `loss_history.csv` and `charts/training_loss.png`: training diagnostics.

This module is not production wiring. It does not place orders and does not
select a production model.
