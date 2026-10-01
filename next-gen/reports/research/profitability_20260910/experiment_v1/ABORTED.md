# Incomplete Experiment

The first run ended before selection because no candidate had 20 validation trades.
An input audit then found repeated identical event primary keys and missing event joins
in the unordered REST export. The export requests used pagination without a stable
order. No model was selected and no September performance was examined in this run.

Repeat the same predeclared experiment after exporting with stable primary-key order
and verifying join coverage. Do not interpret this sweep as a trustworthy model comparison.
