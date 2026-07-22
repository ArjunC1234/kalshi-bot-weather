# Neuralcaster Opportunity Analysis
Fixed-window test: Neuralcaster and Edgecaster trained on 2026-07-02..2026-07-14; opportunity review covers 2026-07-15..2026-07-16.
## Key Counts
- Test candidate side/contracts: 2591
- Candidates passing execution gates: 1220
- Edgecaster positive predicted rewards: 0
- Edgecaster max predicted reward: -0.009251
- Edgecaster candidates passing default reward gate: 0

## Neuralcaster Edge Thresholds
| Min raw edge | Candidates | Winners | Hit rate | 1-contract PnL | Avg edge | Avg realized reward |
|---:|---:|---:|---:|---:|---:|---:|
| 0.00 | 281 | 105 | 0.374 | -21.04 | 0.053 | -0.075 |
| 0.01 | 157 | 48 | 0.306 | -18.13 | 0.092 | -0.115 |
| 0.02 | 74 | 18 | 0.243 | -5.24 | 0.179 | -0.071 |
| 0.03 | 32 | 7 | 0.219 | -1.88 | 0.381 | -0.059 |
| 0.05 | 19 | 4 | 0.211 | -1.42 | 0.616 | -0.075 |
| 0.08 | 16 | 4 | 0.250 | -0.45 | 0.722 | -0.028 |
| 0.10 | 16 | 4 | 0.250 | -0.45 | 0.722 | -0.028 |
| 0.20 | 16 | 4 | 0.250 | -0.45 | 0.722 | -0.028 |
| 0.40 | 14 | 2 | 0.143 | -0.96 | 0.789 | -0.069 |

## Same Selection/Sizing, Raw Neuralcaster Edge
| Min raw edge | Trades | Contracts | Risk | PnL | ROI | Hit rate | Max drawdown |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.00 | 12 | 62 | 33.19 | -8.19 | -0.247 | 0.417 | -10.24 |
| 0.01 | 12 | 67 | 32.98 | -12.98 | -0.394 | 0.333 | -14.50 |
| 0.02 | 12 | 72 | 33.66 | -12.66 | -0.376 | 0.333 | -14.86 |
| 0.03 | 9 | 58 | 22.59 | -3.59 | -0.159 | 0.444 | -8.19 |
| 0.05 | 4 | 21 | 10.65 | -2.65 | -0.249 | 0.500 | -4.85 |
| 0.08 | 3 | 15 | 7.83 | 0.17 | 0.022 | 0.667 | -2.66 |
| 0.10 | 3 | 15 | 7.83 | 0.17 | 0.022 | 0.667 | -2.66 |
| 0.20 | 3 | 15 | 7.83 | 0.17 | 0.022 | 0.667 | -2.66 |
| 0.40 | 3 | 22 | 6.16 | 8.84 | 1.435 | 0.667 | -2.66 |

## Diagnosis
- Edgecaster did not miss trades because execution gates were too strict; it missed them because the trained reward model compressed every test prediction below zero.
- Neuralcaster raw edge had profitable pockets, especially at higher edge thresholds, but it also had severe false positives. The trade selector needs to preserve high-conviction raw-edge signals instead of letting Ridge/HGB collapse all rewards negative.
- The immediate fix to test is a hybrid policy: allow raw Neuralcaster edge trades when edge is very high, and use Edgecaster only as a veto/calibrator once it has more stable target labels.

## Train vs Test Raw Edge

| Period | Min raw edge | Candidates | Winners | Hit rate | 1-contract PnL | Avg realized reward |
|---|---:|---:|---:|---:|---:|---:|
| train | 0.03 | 249 | 83 | 0.333 | 15.08 | 0.061 |
| train | 0.08 | 42 | 12 | 0.286 | 2.02 | 0.048 |
| train | 0.40 | 34 | 7 | 0.206 | 1.30 | 0.038 |
| test | 0.03 | 32 | 7 | 0.219 | -1.88 | -0.059 |
| test | 0.08 | 16 | 4 | 0.250 | -0.45 | -0.028 |
| test | 0.40 | 14 | 2 | 0.143 | -0.96 | -0.069 |

## Concrete Misses And False Positives

Top missed realized winners with raw Neuralcaster edge >= 0.03:

| Date | City | Side | Contract | Ask | Raw edge | Realized reward | Note |
|---|---|---|---|---:|---:|---:|---|
| 2026-07-15 | mia | no | KXHIGHMIA-26JUL15-B92.5 | 0.07 | 0.93 | 0.93 | Raw-edge selector at 0.40 catches this later snapshot. |
| 2026-07-16 | aus | no | KXHIGHAUS-26JUL16-B88.5 | 0.56 | 0.44 | 0.44 | Raw-edge selector at 0.40 catches this. |
| 2026-07-16 | mia | no | KXHIGHMIA-26JUL16-B94.5 | 0.44 | 0.037 | 0.56 | Positive but below high-edge filter. |
| 2026-07-16 | la | no | KXHIGHLAX-26JUL16-B81.5 | 0.52 | 0.032 | 0.48 | Positive but below high-edge filter. |

Largest false-positive cluster:

| Date | City | Side | Contract | Ask | Raw edge | Realized reward | Note |
|---|---|---|---|---:|---:|---:|---|
| 2026-07-15 | okc | no | KXHIGHTOKC-26JUL15-B86.5 | 0.06 | 0.94 | -0.06 | Edgecaster feature layer forced YES probability to 0 after observed high exceeded bracket upper, but settlement still selected that bracket. |

## Updated Diagnosis

Edgecaster is missing out in two separate ways:

- The trained reward model collapses all 2,591 test predictions into a tiny negative range, so even obviously high raw-edge candidates cannot pass the default 0.03 reward gate.
- The candidate features include an observed-floor adjustment that can force bounded bracket YES probability to 0. That creates huge NO edges. Sometimes this correctly identifies a dead bracket, but the OKC example shows it can also create false certainty when observed-high-so-far and final settlement disagree.

The best diagnostic policy from this run is not broad positive edge. Broad positive edge loses. The only profitable selector found here is a very high raw-edge filter with existing event/budget caps: raw edge >= 0.40 produced 3 trades, +8.84 PnL, 66.7% hit rate. This is too few trades to trust, but it shows Edgecaster did miss profitable high-conviction trades.
