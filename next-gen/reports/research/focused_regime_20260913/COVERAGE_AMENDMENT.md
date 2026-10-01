# Coverage Amendment

Recorded after run_v1 scoring, before creating full-afternoon exports or refitting.

The original quote-repair workflow reconstructed source receipts and depth only
when some side had ask $0.50-$0.95 and spread <=$0.05. This historical verification
filter restricted the common snapshot universe to 61 of 84 evaluation city-days,
including only four LA and five Miami days. All 23 omitted city-days had collected
afternoon snapshots, but none fully verified in that derivative export.

Run_v1 remains a restricted-coverage result. It produced zero qualifying trades,
and both fitted weather coefficients were zero. It must not be presented as
covering all collected city-days.

Extend raw-payload receipt/depth reconstruction to every climate-hour 14-19
snapshot, without price or outcome selection, from August 4 through September 10.
Run_v2 retains the original fixed model specification, fit/calibration/evaluation
dates, entry rules, preferred-challenger selection rule and promotion criteria.
Only data verification coverage changes. Record hashes of the new derivative
exports and preserve run_v1. Both runs remain retrospective development evidence.
