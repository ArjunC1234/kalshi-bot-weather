# Local Weather + Strategy Evidence Report

Generated: 2026-08-17  
Scope: local repository artifacts only, no live Supabase queries, no code or data mutations.

## Read This First

The local evidence is useful, but it is still small. The strongest local dataset covers **2026-07-02 through 2026-07-29** with **168 settled city-days** across six cities. That is enough to compare model families and find hypotheses. It is not enough to prove a durable trading edge.

**Best current evidence:**

| Signal | Strength | Why it matters |
|---|---:|---|
| Market-aware Neuralcaster v2 beats weather-only Neuralcaster v2 on contract probabilities | Strong local evidence | Same family, repeated serious-size runs, lower log loss and higher top-one accuracy |
| Simple/residual weather models generalize better than high-capacity GRU/MLP residual models on current data | Moderate evidence | V3 Huber/market-baseline outperform v3 GRU and MLP on the July 2-30 runs |
| Raw edge alone is not reliable | Strong local evidence | Higher raw edge did not monotonically improve PnL; very high edge often lost |
| YES-only policies were more stable than all-side or NO-only policies in the safer later-window strategy reports | Moderate evidence | Several non-test-calibrated July 23-30 reports selected YES-only and stayed positive |
| City effects are large, but not yet stable enough to hard-code forever | Weak-to-moderate evidence | Denver, Miami, NYC were better in aggregate strategy logs; LA and OKC were poor, but reports are overlapping and not independent |
| Late-day market/weather features look very predictive | Strong but dangerous evidence | HRRR/NBM/observed-high become highly accurate late; this can create timing-dependent backtest optimism |

## What I Inspected

### Data Exports

Primary local dataset used for quantitative analysis:

`next-gen/data/codex_latest_20260702_20260730_for_neuralcaster_edgecaster`

Key tables:

| Table | Rows |
|---|---:|
| weather_snapshots | 4,148 |
| market_snapshots | 24,888 |
| events | 4,148 |
| final_temperature_labels | 168 |
| settlements | 168 |
| collector_runs | 2,079 |
| provider_errors | 103 |
| raw_payloads | 36,974 |

Also inspected nearby/latest report-linked exports, including:

- `next-gen/data/codex_latest_20260702_20260730_settled_for_v2ev_v3_20260731`
- `next-gen/data/export_20260702_20260722T143730`
- `next-gen/data/export_20260622_20260722_20260723T140150Z`
- prior July smoke/control exports used by older reports

### Model Reports

Parsed **99 model report summaries** under `next-gen/reports/model`, including:

- Raycaster v1
- Cloudcaster v1
- Neuralcaster v1
- Neuralcaster v2 weather-only and market-aware GRU distribution models
- Neuralcaster v3 residual models: NWS baseline, source blend, market baseline, ridge, Huber, tree, MLP, GRU

### Strategy Reports

Parsed **111 strategy report summaries** under `next-gen/reports/strategy`, including:

- Cloudcaster v1 paper/diagnostic reports
- Edgecaster v1 sweeps
- Edgecaster v2 fixed-window and calibrated reports
- Neuralcaster EV strategy validation reports
- Neuralcaster v3 EV validation reports

Important caveat: many strategy folders are overlapping experiments against the same dates. Treat aggregate trade counts as repeated diagnostics, not independent observations.

### Code and Registries

Inspected model/strategy capabilities from:

- `next-gen/maxtemp-engine/raycaster/v1/documentation.md`
- `next-gen/maxtemp-engine/neuralcaster/v2/cli.py`
- `next-gen/maxtemp-engine/neuralcaster/v2/documentation.md`
- `next-gen/maxtemp-engine/neuralcaster/v3/residual_models.py`
- `next-gen/maxtemp-engine/neuralcaster/v3/residual_features.py`
- `next-gen/edgecaster/v2/dataset.py`
- `next-gen/edgecaster/v2/model.py`
- `next-gen/edgecaster/v2/documentation.md`
- `next-gen/control/registry/builtin/models/neuralcaster_v2.json`
- `next-gen/control/registry/builtin/strategies/edgecaster_v2.json`

## Dataset Reality

The local July 2-29 modeling set has **28 settled target dates per city**.

| City | Days | Mean high | Std dev | Min | Max |
|---|---:|---:|---:|---:|---:|
| aus | 28 | 95.61 | 5.26 | 80 | 104 |
| den | 28 | 94.32 | 3.61 | 88 | 103 |
| la | 28 | 77.75 | 3.36 | 73 | 86 |
| mia | 28 | 92.82 | 1.33 | 91 | 97 |
| nyc | 28 | 83.64 | 7.04 | 69 | 100 |
| okc | 28 | 94.96 | 5.51 | 86 | 104 |

Interpretation:

- **Miami is low-variance**, so a simple/weather or market baseline can look good there.
- **NYC, OKC, and Austin have wider ranges**, so they test whether the model handles movement.
- **LA has a narrow range but weak strategy PnL**, suggesting contract pricing/market structure may matter more than raw weather difficulty.
- **28 days per city is not enough** to trust city-specific rules as permanent.

## Weather Source Behavior

### Source Accuracy By Time Of Day

Mean absolute error against final high:

| Snapshot window | Rows | NWS anchor | HRRR | NBM | Ensemble median | Observed high so far |
|---|---:|---:|---:|---:|---:|---:|
| Early, hours 0-6 | 1,173 | 1.74 F | 2.15 F | 2.22 F | 1.91 F | 14.39 F |
| Mid, hours 7-14 | 1,344 | 1.63 F | 1.81 F | 1.77 F | 1.89 F | 7.13 F |
| Late, hours 15-23 | 1,511 | 5.71 F | 0.62 F | 0.61 F | 3.46 F | 0.57 F |
| Last 3 hours, 21-23 | 503 | 10.69 F | 0.51 F | 0.51 F | 4.27 F | 0.51 F |

Takeaway:

- Early in the day, NWS anchor and ensemble median are reasonable.
- Late in the day, HRRR/NBM and observed-high are extremely accurate.
- The NWS anchor becomes stale late in the day. It should be treated as a baseline anchor, not as the truth.

### Source Disagreement Closes In One Place And Widens In Another

| Snapshot window | All-source range | HRRR-NBM disagreement | Ensemble std dev |
|---|---:|---:|---:|
| Early, hours 0-6 | 3.53 F | 2.59 F | 2.87 F |
| Mid, hours 7-14 | 3.13 F | 2.05 F | 2.84 F |
| Late, hours 15-23 | 6.26 F | 0.17 F | 2.72 F |
| Last 3 hours, 21-23 | 10.67 F | 0.01 F | 2.70 F |

This looks contradictory, but it makes sense:

- **HRRR and NBM converge hard late**.
- **All-source range widens late** because stale NWS/ensemble values can sit far away from live observed/high-resolution updates.

Practical implication:

- `hrrr_nbm_disagreement_f` is useful for whether numerical models agree.
- `all_weather_sources_range_f` is more of a stale-source/stress indicator late in the day, not simply uncertainty.

## Market Behavior

The market itself is a strong weather signal.

| Snapshot window | Unique snapshots | Top market contract hit rate | Avg top probability | Avg top-two gap |
|---|---:|---:|---:|---:|
| Early, hours 0-6 | 1,173 | 52.3% | 0.511 | 0.215 |
| Mid, hours 7-14 | 1,344 | 61.7% | 0.619 | 0.358 |
| Late, hours 15-23 | 1,511 | 97.6% | 0.951 | 0.923 |
| Last 3 hours, 21-23 | 503 | 100.0% | 0.973 | 0.966 |

Market-implied expected high MAE:

| Snapshot window | MAE |
|---|---:|
| Early, hours 0-6 | 1.25 F |
| Mid, hours 7-14 | 1.01 F |
| Late, hours 15-23 | 0.62 F |
| Last 3 hours, 21-23 | 0.61 F |

Interpretation:

- Market-aware models should beat weather-only models because the market is already aggregating weather information.
- Late-day market accuracy is extremely high, but profitability is not automatic because prices also become expensive and spreads/fees/timing matter.
- A profitable bot cannot just "know the winner"; it must know when the price is still wrong enough.

## Model Evidence

### Serious-Size Model Families

This table keeps larger local runs only, roughly 1,000+ predictions or 120+ city-days.

| Model family / kind | Runs | Median MAE | Best MAE | Median log loss | Best log loss | Median top-1 |
|---|---:|---:|---:|---:|---:|---:|
| Neuralcaster v2 market GRU | 15 | 0.992 | 0.855 | 0.673 | 0.588 | 0.685 |
| V3 market baseline | 2 | 0.853 | 0.843 | 0.741 | 0.731 | 0.716 |
| V3 Huber residual | 4 | 0.829 | 0.819 | 0.774 | 0.764 | 0.708 |
| V3 ridge residual | 3 | 0.862 | 0.843 | 0.815 | 0.807 | 0.686 |
| V3 tree residual | 3 | 0.913 | 0.894 | 0.833 | 0.824 | 0.661 |
| Neuralcaster v2 weather-only GRU | 4 | 1.218 | 1.060 | 1.037 | 0.918 | 0.562 |
| V3 source blend | 2 | 1.198 | 1.184 | 1.049 | 1.042 | 0.531 |
| V3 GRU residual | 2 | 1.247 | 1.244 | 1.410 | 1.379 | 0.366 |
| V3 NWS baseline | 2 | 1.232 | 1.212 | 1.460 | 1.421 | 0.332 |

Interpretation:

- **Best contract probability model:** Neuralcaster v2 market-aware GRU with 14-day rolling/expanding windows and heavy market probability blend.
- **Best temperature residual model:** V3 Huber residual / market baseline.
- **Weakest current family for this data size:** V3 GRU residual. It has too much sequence capacity for 168-174 independent city-days.

### Market-Aware Beats Weather-Only

The most direct comparison is Neuralcaster v2:

| Run | Mode | Predictions | City-days | MAE | Log loss | Top-1 accuracy |
|---|---|---:|---:|---:|---:|---:|
| v2 market rolling14 blend95 | market | 2,012 | 168 | 0.865 | 0.588 | 0.751 |
| v2 market expanding14 blend95 | market | 2,119 | 174 | 0.855 | 0.597 | 0.741 |
| v2 market rolling14 blend95 alt | market | 2,119 | 174 | 0.888 | 0.599 | 0.741 |
| v2 weather fixed-window | weather | 827 | 126 | 1.060 | 0.918 | 0.590 |
| v2 weather rolling10 | weather | 1,547 | 126 | 1.172 | 0.998 | 0.564 |
| v2 weather rolling5 | weather | 2,267 | 126 | 1.316 | 1.128 | 0.516 |

Conclusion: **market-aware Neuralcaster v2 is not just slightly better; it is materially better for contract probabilities.**

### City-Level Model Performance

MAE by city for selected serious-size runs:

| City | v2 market expanding14 | v2 market rolling14 | v3 Huber market-delta | v3 market baseline | v3 source blend | v3 NWS baseline |
|---|---:|---:|---:|---:|---:|---:|
| aus | 0.505 | 0.469 | 0.489 | 0.476 | 0.807 | 0.764 |
| den | 0.935 | 1.015 | 0.732 | 0.721 | 1.348 | 1.187 |
| la | 1.025 | 1.141 | 1.086 | 1.110 | 1.507 | 1.990 |
| mia | 0.648 | 0.643 | 0.701 | 0.674 | 0.957 | 0.697 |
| nyc | 1.014 | 1.086 | 0.941 | 1.011 | 1.390 | 1.477 |
| okc | 1.004 | 0.979 | 0.964 | 1.066 | 1.097 | 1.156 |

The v3 Huber/market-baseline style is especially attractive for **temperature point accuracy**. But v2 market-aware still wins the **contract probability** task, which is closer to trading.

## Strategy Evidence

### Safer Later-Window Strategy Reports

I separated later-window reports with train/test dates around **2026-07-16 through 2026-07-30** and excluded obvious test-calibrated/latest-test-only names where possible.

Best non-test-calibrated later-window reports with 5+ trades:

| Report family | Test window | Trades | PnL | ROI | Hit rate | Notes |
|---|---|---:|---:|---:|---:|---|
| Neuralcaster EV validation | 2026-07-23 to 2026-07-29 | 28 | +29.06 | 36.8% | 50.0% | YES-only selected from validation |
| V3 Huber market-delta EV validation | 2026-07-23 to 2026-07-29 | 37 | +18.73 | 18.1% | 51.4% | YES-only, stronger model edge but modest hit rate |
| V2 EV expanding14 calibrated | 2026-07-24 to 2026-07-30 | 29 | +9.12 | 11.1% | 44.8% | Validation looked much better than test |
| V2 EV train/test | 2026-07-24 to 2026-07-30 | 42 | +8.70 | 7.5% | 52.4% | Allowed YES and NO |
| V3 source-blend EV train/test | 2026-07-24 to 2026-07-30 | 37 | +7.90 | 7.4% | 37.8% | Low hit rate, profit from payoff asymmetry |
| Cloudcaster market EV calibrated | 2026-07-24 to 2026-07-30 | 33 | +4.56 | 4.9% | 36.4% | Small edge after validation |

Main interpretation:

- Positive later-window reports exist.
- The profitable reports are not all the same model, which is encouraging.
- But the sample is still tiny: roughly 28-42 trades per report.
- Validation-to-test decay is real. Example: one v2 calibrated report had validation PnL +66.79 but test PnL +9.12.

### Reports To Treat Carefully

Some older Cloudcaster reports show very large PnL, for example:

| Older report | Trades | PnL | ROI | Hit rate |
|---|---:|---:|---:|---:|
| cloudcaster_v1_ablation_weather_only_20260713T202852 | 42 | +245.19 | 102.2% | 40.5% |
| cloudcaster_v1_budget10_cheap_tier | 40 | +70.06 | 140.3% | 47.5% |

These are useful historically, but I would not use them as primary evidence for a final generalized bot. They predate the safer later-window protocol and may contain more selection bias.

## Trade-Level Diagnostics

### Side Behavior

Across all parsed trade logs, side-level aggregate PnL was negative because old and overlapping experiments dominate:

| Side | Trade rows | Reports | Aggregate PnL | Mean PnL | Hit rate |
|---|---:|---:|---:|---:|---:|
| NO | 1,597 | 30 | -115.79 | -0.073 | 58.9% |
| YES | 1,460 | 49 | -154.03 | -0.106 | 35.2% |

For the safer later-window reports, YES-only was the most common selected profitable policy, but aggregate duplicated trades were still negative:

| Side | Trade rows | Reports | Aggregate PnL | Mean PnL | Hit rate |
|---|---:|---:|---:|---:|---:|
| NO | 101 | 3 | -15.89 | -0.157 | 40.6% |
| YES | 710 | 26 | -142.80 | -0.201 | 34.1% |

This means the report-level evidence and aggregate trade-row evidence are not perfectly aligned. The reason is overlap: the same losing setup can produce many duplicated rows across repeated experimental variants. Use report-level validation/test summaries more than pooled row totals.

### City Behavior

Across all strategy trade logs:

| City | Trade rows | Reports | Aggregate PnL | Mean PnL | Hit rate |
|---|---:|---:|---:|---:|---:|
| mia | 746 | 80 | +308.37 | +0.413 | 42.0% |
| den | 791 | 83 | +209.72 | +0.265 | 44.8% |
| nyc | 701 | 80 | +120.82 | +0.172 | 40.4% |
| okc | 751 | 77 | +8.72 | +0.012 | 44.1% |
| aus | 727 | 79 | -53.56 | -0.074 | 47.0% |
| la | 721 | 80 | -254.56 | -0.353 | 38.6% |

For later-window reports only:

| City | Trade rows | Reports | Aggregate PnL | Mean PnL | Hit rate |
|---|---:|---:|---:|---:|---:|
| den | 161 | 26 | +185.35 | +1.151 | 47.2% |
| mia | 144 | 26 | +33.83 | +0.235 | 38.2% |
| nyc | 135 | 26 | +30.54 | +0.226 | 40.0% |
| aus | 116 | 26 | -67.45 | -0.582 | 40.5% |
| okc | 135 | 26 | -150.32 | -1.114 | 23.0% |
| la | 120 | 22 | -190.64 | -1.589 | 16.7% |

Evidence level: **weak-to-moderate**. Denver looks promising; LA/OKC look dangerous. But do not hard-code these as permanent city rules yet.

### Edge Is Not Monotonic

Later-window trade rows by raw edge:

| Raw edge bucket | Trade rows | Reports | Aggregate PnL | Mean PnL | Hit rate |
|---|---:|---:|---:|---:|---:|
| 0.00 to 0.01 | 56 | 5 | -24.28 | -0.434 | 30.4% |
| 0.01 to 0.03 | 95 | 11 | +61.48 | +0.647 | 53.7% |
| 0.03 to 0.05 | 40 | 13 | +5.50 | +0.138 | 50.0% |
| 0.05 to 0.08 | 29 | 11 | -35.61 | -1.228 | 24.1% |
| 0.08 to 0.12 | 64 | 15 | +0.12 | +0.002 | 35.9% |
| 0.12 to 0.20 | 195 | 21 | -100.40 | -0.515 | 29.7% |
| 0.20 to 0.40 | 294 | 21 | +15.18 | +0.052 | 36.1% |
| 0.40 to 1.00 | 38 | 15 | -80.68 | -2.123 | 2.6% |

Conclusion: **raw edge is not calibrated confidence**. Very high edge can mean the model is disagreeing with the market for the wrong reason.

### Price Also Matters

Later-window trade rows by entry price:

| Entry price bucket | Trade rows | Reports | Aggregate PnL | Mean PnL | Hit rate |
|---|---:|---:|---:|---:|---:|
| 0.00 to 0.15 | 37 | 5 | -31.70 | -0.857 | 2.7% |
| 0.15 to 0.25 | 79 | 22 | -73.16 | -0.926 | 15.2% |
| 0.25 to 0.35 | 270 | 26 | -51.19 | -0.190 | 27.4% |
| 0.35 to 0.50 | 305 | 26 | -9.97 | -0.033 | 41.0% |
| 0.50 to 0.65 | 112 | 23 | +1.93 | +0.017 | 57.1% |
| 0.65 to 0.80 | 8 | 5 | +5.40 | +0.675 | 87.5% |

This does not mean high-price contracts are always best. The high-price bins have small counts and can be dominated by late obvious winners. It does mean the bot has often been getting trapped by cheap, unlikely contracts.

## Model Structure: What Seems To Work

### Raycaster v1

Shape:

- Weather-only feature model.
- Deterministic source-blend fallback.
- HistGradientBoosting point model and quantile uncertainty.
- No Kalshi market prices.

Evidence:

- Useful as a baseline and diagnostic.
- Some early fixed-artifact results are suspiciously good and should not be treated as primary evidence unless the training cutoff is verified.
- Weather-only models are consistently weaker than market-aware v2 for contract probabilities.

Use going forward:

- Keep as a leakage-safe baseline.
- Use it to measure whether market-aware models are actually adding value.

### Neuralcaster v2

Shape:

- PyTorch temporal GRU.
- Predicts final high as residual over a baseline.
- Outputs Gaussian uncertainty and bracket probabilities.
- `weather` mode uses weather sequence features.
- `market` mode adds market expected high, top probability, entropy, spread, probability mass.
- Can blend market probabilities into output.
- Supports rolling and fixed-window evaluation.

Evidence:

- Best serious-size contract probability performance.
- Market mode materially beats weather mode.
- 14-day rolling/expanding windows are stronger than 5-day weather-only windows.

Use going forward:

- Primary contract-probability engine should be Neuralcaster v2 market-aware.
- Use expanding or rolling 14-day walk-forward.
- Keep `market_probability_blend` high when the goal is contract trading, but validate it out of sample.

### Neuralcaster v3

Shape:

- Residual over NWS anchor.
- Includes source offsets, source range/std, warming rates, market expected offset, market entropy/probability/spread, and 1-hour deltas.
- Supports baseline, source blend, market baseline, ridge, Huber, tree, MLP, GRU, and robust ensemble styles.

Evidence:

- Huber and market-baseline are strong for temperature MAE.
- V3 GRU/MLP residuals underperform on current data.
- Strategy PnL for v3 is mixed: Huber market-delta produced one good later-window run, but many v3 variants lost.

Use going forward:

- Use V3 Huber/market-baseline as a sanity check and ensemble input.
- Do not make v3 GRU/MLP the main trading model yet.

### Edgecaster v2

Shape:

- Candidate-set ranker.
- Scores all YES/NO candidates in an event snapshot together.
- Uses candidate features plus DeepSets-style context.
- Has win, reward, rank, and trade heads.
- Calibrated mode can shrink sparse segments back toward global reliability.

Evidence:

- Better abstraction than raw edge.
- Still unstable on current data.
- Calibrated/validation-selected reports are more trustworthy than pure sweeps.
- The default `min_predicted_reward=0.03` / `min_trade_probability=0.50` can produce zero trades if predictions are too conservative or if model predictions do not cover the chosen train/test window.

Use going forward:

- Keep it, but make it more conservative and explicitly validation-driven.
- Raw edge should be one feature, not the gate.

## Most Generalizable Signal Hypotheses

### Hypothesis 1: Market-aware probability modeling is necessary

Evidence strength: **moderate-to-strong**.

Why:

- The market top contract itself is already informative.
- Neuralcaster v2 market-aware runs beat weather-only runs materially.

Pipeline implication:

- The main weather engine should include market features.
- Weather-only should remain a baseline, not the trading model.

### Hypothesis 2: The best edge is model-vs-market disagreement only when calibrated by context

Evidence strength: **moderate**.

Why:

- Raw edge alone fails.
- Mid-small edge buckets sometimes beat high edge buckets.
- Very high edge often means model error, stale features, or market structure weirdness.

Pipeline implication:

- Edgecaster should use:
  - model probability
  - market price
  - spread
  - side
  - city
  - checkpoint bucket
  - source disagreement
  - market confidence
  - calibration lower bound

Do not use `model_probability - ask_price` as a direct trade rule.

### Hypothesis 3: YES-only is currently safer than NO-heavy trading

Evidence strength: **weak-to-moderate**.

Why:

- Several safer validation/test reports selected YES-only.
- NO-only latest-test experiments were poor.
- But pooled trade-row evidence is noisy and overlapping.

Pipeline implication:

- Start with YES-only or YES-dominant mode.
- Add NO contracts back only after separate side-specific validation.

### Hypothesis 4: Avoid cheap longshots

Evidence strength: **moderate**.

Why:

- 0.00-0.35 entry price buckets were poor in later-window aggregate diagnostics.
- Cheap contracts can have attractive-looking edge but poor realized hit rates.

Pipeline implication:

- Favor entry prices roughly **0.35 to 0.65** for initial conservative testing.
- Treat 0.15 to 0.35 as experimental.
- Avoid sub-0.15 unless a separate validation report proves it.

### Hypothesis 5: City-specific filters may help, but need more days

Evidence strength: **weak-to-moderate**.

Why:

- Denver repeatedly looks strong.
- LA and OKC repeatedly look weak in strategy logs.
- But there are only 28 settled city-days in the main local dataset.

Pipeline implication:

- Use city as a feature and calibration segment.
- Prefer soft penalties/position sizing over hard permanent exclusions.
- If forced to trade now, use a conservative city allowlist for research only: Denver, Miami, NYC first; LA/OKC downweighted or blocked until proven otherwise.

## Risks And Failure Modes

### 1. Sample Size

The strongest dataset has only **168 independent city-days**. Snapshot rows are numerous, but many rows come from the same city-day and are highly correlated.

Meaning:

- A strategy with 30-40 trades can look great by chance.
- A city rule can be wrong after one unusual week.
- Neural nets can overfit quickly.

### 2. Overlapping Reports

Many strategy reports reuse the same dates, same model outputs, and similar policy settings.

Meaning:

- Aggregating every trade row overcounts evidence.
- Report-level train/test summaries are more trustworthy than pooled row totals.

### 3. Test Calibration / Selection Bias

Some reports are explicitly named `calibrated_on_test` or `latest_test`. These can be useful diagnostics, but they are not proof of generalization.

Meaning:

- Use them to generate hypotheses.
- Do not use them to choose final thresholds unless re-tested on a future unseen window.

### 4. Late-Day Leakage-Like Optimism

Late-day observed high, HRRR, NBM, and market top probability are extremely predictive.

This is not automatically leakage if the snapshot truly existed before order time. But it can become leakage-like if:

- the trade would not have filled at the recorded ask,
- the market was effectively resolved,
- the settlement happened before/near snapshot time,
- the data row includes stale or post-close values,
- timing granularity is too coarse.

### 5. Market-Aware Models Can Learn The Market, Not The Edge

Market-aware models predict contracts well because the market predicts contracts well. Profit requires finding where the market is wrong, not merely copying it.

Meaning:

- Market-aware Neuralcaster is good for probability accuracy.
- Edgecaster/calibration must prove it can identify mispricing.

## Recommended Current Direction

This is not a final production claim. It is the most defensible direction from current local evidence.

### Weather Engine

Use a market-aware Neuralcaster v2 primary model:

- mode: `market`
- training policy: expanding or rolling
- train days: 14
- test days: 1 for evaluation
- market probability blend: high, around 0.95 for contract probability quality

Pair it with a V3 Huber/market-baseline residual model as a guardrail:

- If v2 market and v3 Huber/market-baseline strongly disagree, downweight the trade.
- If v2 edge exists but v3 residual says the temperature direction is implausible, require stronger price discount.

### Strategy Engine

Use a validation-selected, calibrated EV strategy rather than raw edge:

- Start YES-only or YES-dominant.
- Require validation support before enabling NO contracts.
- Avoid low-price longshots initially:
  - conservative entry price band: 0.35 to 0.65
  - experimental extension: 0.25 to 0.80 only after validation
- Do not gate only on raw edge.
- Prefer calibrated EV lower bound or reliability-shrunk EV.
- Add city/checkpoint/side calibration shrinkage.
- Use soft city penalties before hard city bans.

Candidate initial policy for the next validation run:

| Setting | Conservative value |
|---|---:|
| model | Neuralcaster v2 market expanding/rolling 14d |
| strategy side | YES only |
| min EV | 0.00 to 0.03 |
| max spread | 0.10 |
| min entry price | 0.35 |
| max entry price | 0.65 |
| daily budget | 40 |
| max positions per event | 1 |
| city treatment | downweight LA/OKC, do not hard-ban until more data |
| selection | validation-calibrated, not test-calibrated |

## Bottom Line

The most defensible local conclusion is:

> The project has a real modeling signal, especially from market-aware Neuralcaster v2 and simple residual weather models, but the trading signal is not yet proven. The most promising strategy direction is a validation-calibrated, YES-dominant policy that avoids cheap longshots and treats raw edge as one input rather than the final decision.

The most important next data need is not more snapshot rows. It is more **settled independent city-days** and repeated forward validation windows.

