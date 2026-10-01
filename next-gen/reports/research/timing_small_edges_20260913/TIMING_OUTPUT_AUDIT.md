# Timing Output Audit

The initial `timing` run completed on the predeclared model and data specification.
An output review found a misleading metadata field: the diagnostic probe export
inherited `pair_valid=false` from its pre-scoring transition record even when the
probe's separate `scored` field was true. Scoring used `scored` and `net_markout`,
not the inherited field, so the field did not determine any reported performance.

The `timing_v2` run removes that inherited field from probes. It also clarifies
the model-lock note: transition labels and diagnostic markouts are materialized
before fitting, while only pre-cutoff price-change labels enter the model fits.
No evaluation-dependent model or parameter selection occurs. Computing a label
table before fitting is not itself leakage; label use and decision invariance
are the checks that matter here.

The randomization output now counts days containing both revision signs so its
limited support is visible. The models, inputs, protocol, cohorts, forecasts,
entry choices and all performance definitions are unchanged. Preserve the first
run and verify numerical equality between its models/results and timing_v2.
