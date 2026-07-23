# Kalshi Bot Control Center Implementation Contract

This UI is the standalone Kalshi Bot Control Center, separate from the legacy Trends site. It uses Trends only as a reference for chart behavior and existing artifact semantics.

## Core Product Contract

- Dashboard: read-only operational summary for latest exports, open jobs, registry coverage, data gaps, system status, and largest tables.
- Export Dataset: create export jobs from registry profiles, preview profile/table scope, select cities/tables, validate existing exports, compare exports, clone, reduce, extend, and archive local datasets.
- Data Explorer: inspect selected export schemas, choose city/table/fields/chart mode, query visualization data through backend aggregation/sampling/decimation/pagination contracts, and review raw rows without chart tooltip overload.
- Model Lab: browse registry-discovered models, inspect purpose/inputs/outputs/version, choose datasets and entrypoints, render parameter forms from schemas, run train/evaluate/backtest/predict jobs, and open model reports.
- Strategy Lab: browse registry-discovered strategies, choose dataset/model report, tune gates and risk controls, run backtests/evaluations, show compact gate sweeps with inspector-based details instead of massive hover text.
- Reports: browse all model, strategy, quality, and generic artifacts with specialized renderers plus a fallback renderer for non-static schemas.
- Jobs: inspect all jobs, status, params, command, output path, return code, errors, logs, and cancellation controls.
- Bot Monitor: reserved standalone page for deployed bot telemetry, status, alert, resource, latency, fill, and runtime streams once backend monitoring is implemented.
- Settings: expose API base URL, mock mode, current connection state, registry root, data/report roots, and visualization defaults.

## Design Contract

- Brand is `Kalshi Bot Control Center`; the UI must not present itself as the old Trends website.
- Visual language follows the approved mocks: dark tactical control-room base, teal primary signal, blue compute accent, amber warning, coral critical, white foreground.
- Primary palette: `#071012`, `#111819`, `#1DD6B7`, `#4F7DFF`, `#FFB547`, `#FF6B5B`, `#EAF3F0`.
- Layout uses a persistent sidebar, operational topbar, bordered panels, compact cards, right inspectors, and dense data-first surfaces.
- Animations are restrained: hover lift, status spinner, drawer transition; reduced-motion disables motion.
- Tooltips and hover details must stay compact/body-mounted through chart components; dense details belong in inspectors or pinned selections.
- No nested scrollbars except contained data tables; page-level scrolling is handled by the active content/inspector regions.
- Responsive contract:
  - Desktop and 16:9/16:10: sidebar + content + optional inspector.
  - Ultrawide/21:9+: wider inspector and denser cards/charts.
  - Square/tablet: icon rail and stacked inspector.
  - Mobile: drawer navigation, single-column panels, no horizontal page overflow, long IDs wrap.

## Backend/API Contract

- `VITE_CONTROL_API_BASE_URL` points at the control API, default `/control/api`.
- `VITE_CONTROL_MOCK_MODE` supports `never`, `on-error`, and `always`; local UI defaults to `always` until a control API is running.
- `VITE_CONTROL_REQUEST_TIMEOUT_MS` controls fetch timeout.
- API client exposes typed contracts for dashboard, registry, export profiles, exports, artifacts, reports, datasets, city coverage, jobs/logs/cancel, export preview/create/validate/compare/clone/reduce/extend/archive, model-run compatibility, bot status, and visualization query.
- Registry entries are the source of truth for discoverable models, strategies, export profiles, visualization contracts, and bot runtimes.
- Non-static schemas are represented through artifact schemas and JSON-schema parameter contracts; renderers must gracefully fall back when a schema is unknown.

## Verification Contract

- `npm.cmd run build` must pass before handoff.
- Browser QA must cover dashboard plus navigation through exports, data explorer, model lab, strategy lab, reports, jobs, bot monitor, and settings.
- Viewport QA must include desktop, ultrawide, and mobile with no horizontal page overflow.
- Any remaining warning must be explicitly reported; current expected warning is Vite bundle size from ECharts until chart code-splitting is implemented.
