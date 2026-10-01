# Implementation Verification

Completed September 14, 2026 UTC (September 13 EDT).

## Scope

Moved the unfinished reader and draft protocol out of `next-gen` into the sibling
`timing-research` directory. Moved the existing downloaded immutable archive into
this project's ignored `data` directory. Kept earlier experiments, production
collector files, systemd scheduling, credentials and live strategy untouched.
This is a separate research package in the same repository, not a new Git repo.

The reader now uses a narrow local JSON contract rather than importing production
modules. Python 3.12 and standard-library modules suffice. There is no dependency
installation, credentials access, network client, order submission or deployment
step in the research package.

## Verification

- 29 isolated research tests passed, including synthetic end-to-end sealed decision
  and outcome attachment, future-data invariance, prior-only controls, input
  integrity, sub-hour timestamp alignment, missing depth, fee handling, settlement
  validation, lock tampering, and refusal to score early.
- 31 existing collector tests passed.
- 16 existing backtest tests passed.
- Ruff formatting and lint checks passed for the new package and tests.
- Frozen code/protocol lock created at `2026-09-14T03:40:30.772924+00:00`.
- Lock SHA-256:
  `4e5f66ebee026444f2b226a1fac2e134a38918087a656fd73b6653f174682af1`.
- Real-data audit and preflight decision CLI runs completed successfully.
- Direct premature scoring was refused with the October 2 embargo message; no
  performance output was created by that command.

These tests provide evidence for the implemented cases, not proof against every
possible defect. Source-file changes after freeze will intentionally invalidate
v1 until handled as a documented new experiment.

## Real Preflight Sample

Verified interval: September 14 02:20:30 through 03:00:00 UTC.

- Eight expected scheduled cycles, eight present; 48/48 complete city captures.
- Zero provider failures, 429s, conflicting receipt records, or missing cycles.
- 288 market-bracket observations, with **zero displayed NO ask sizes**.
- All 48 city observations lacked a current-target-day daytime high in the NWS
  daily response. This was evening data, outside the experiment's morning window;
  successful collection is different from an available research signal.
- Median daily forecast version age was about 379 minutes. This describes the
  evening sample and is not an estimate of morning update latency.

Full outputs are in `verified_preflight_audit/`: AUDIT.json, OBSERVATIONS.json,
INPUTS.json and PROVENANCE.json. These validate local archive contents, not ongoing
remote service status or current Supabase sync. The initial 24-hour interval is not
yet complete; auditing it requires a later archive refresh. No future audit job was
scheduled by this implementation.

## Experiment State

The 14-day signal window is September 15-28 UTC, ending September 29 00:00 UTC.
Primary horizon: 15 minutes; 5/60-minute results remain secondary. Scoring unlocks
October 2 00:00 UTC. The current sealed artifact in `preflight_decisions/` contains
zero evaluation intentions and explicitly says `awaiting_evaluation_window`.

The missing-size finding blocks depth-checked hypothetical economics for the
current stream. Price-movement research remains possible. Adding order-book size
capture would be a separately authorized production change; it was not silently
implemented or deployed here. No profitable model or minimum hit rate is claimed.
