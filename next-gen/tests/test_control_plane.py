from __future__ import annotations

# ruff: noqa: E402, I001

import csv
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from control.artifacts.index import ArtifactIndex, inspect_artifact, scan_artifacts
from control.bot import bot_stub
from control.compatibility import check_model_dataset_compatibility
from control.dashboard import dashboard_summary
from control.exports.manager import compare_exports, inspect_export, reduce_export, validate_export
from control.jobs.runner import JobRunner
from control.jobs.store import JobStore
from control.providers.supabase_export import preview_export_profile
from control.registry.loader import load_registry, validate_registry
from control.server import _safe_path
from control.visualizations.query import execute_visualization_query


class ControlPlaneTests(unittest.TestCase):
    def test_builtin_registry_validates_and_exposes_profiles(self) -> None:
        self.assertEqual(validate_registry(), [])
        registry = load_registry()

        self.assertIsNotNone(registry.get("model", "raycaster_v1"))
        self.assertIsNotNone(registry.get("model", "neuralcaster_v2"))
        profile = registry.get("export_profile", "lightweight_model_eval")
        self.assertIsNotNone(profile)
        preview = preview_export_profile(profile)  # type: ignore[arg-type]
        weather = preview["tables"]["weather_snapshots"]
        self.assertIn("features", weather["required_columns"])
        self.assertIn("snapshot_time_utc", weather["effective_columns"])

    def test_artifact_scan_indexes_legacy_export_with_inferred_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            export = root / "data" / "export_a"
            _write_csv(
                export / "events.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "target_date": "2026-07-01",
                        "snapshot_time_utc": "2026-07-01T01:00:00+00:00",
                    }
                ],
            )
            _write_csv(
                export / "weather_snapshots.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "snapshot_time_utc": "2026-07-01T01:00:00+00:00",
                        "expected_high_f": "91.2",
                    }
                ],
            )

            records = scan_artifacts(
                root / "data",
                root / "model",
                root / "quality",
                root / "strategy",
            )
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].artifact_type, "local_export")
            inspected = inspect_artifact(export)
            role = inspected["schemas"]["events"]["columns"]["city"]["role"]
            self.assertEqual(role, "dimension.city")

            index = ArtifactIndex(root / "artifacts.sqlite")
            index.replace_all(records)
            self.assertEqual(index.list()[0]["id"], "export_a")

    def test_model_dataset_compatibility_reports_missing_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_csv(root / "events.csv", [{"city": "nyc"}])
            registry = load_registry()
            model = registry.get("model", "raycaster_v1")
            self.assertIsNotNone(model)

            result = check_model_dataset_compatibility(model, root)  # type: ignore[arg-type]

            self.assertFalse(result["compatible"])
            self.assertIn("Missing required table: weather_snapshots", result["blocking"])

    def test_command_job_runner_tracks_success_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = JobStore(root / "jobs.sqlite")
            runner = JobRunner(store, root)

            job = runner.create_command_job(
                "test",
                "smoke",
                "run",
                {},
                [sys.executable, "-c", "print('control job ok')"],
                cwd=root,
            )
            deadline = time.time() + 5
            current = store.get(job.id)
            while current and current.status in {"queued", "running"} and time.time() < deadline:
                time.sleep(0.05)
                current = store.get(job.id)

            self.assertIsNotNone(current)
            self.assertEqual(current.status, "succeeded")
            self.assertIn("control job ok", Path(current.log_path).read_text(encoding="utf-8"))

    def test_registered_job_rejects_unknown_params_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry_root = root / "registry"
            model_dir = registry_root / "models"
            model_dir.mkdir(parents=True)
            script = root / "write_report.py"
            script.write_text(
                "import pathlib, sys\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])\n"
                "out.mkdir(parents=True, exist_ok=True)\n"
                "(out / 'summary.json').write_text('{}')\n",
                encoding="utf-8",
            )
            (model_dir / "toy.json").write_text(
                """
                {
                  "id": "toy",
                  "kind": "model",
                  "label": "Toy",
                  "entrypoints": {
                    "evaluate": {
                      "command": [
                        "python",
                        "write_report.py",
                        "--data",
                        "{{ inputs.dataset.path }}",
                        "--output",
                        "{{ outputs.report_dir }}"
                      ],
                      "params_schema": {
                        "type": "object",
                        "properties": {"alpha": {"type": "number"}}
                      },
                      "produces": {"artifact_type": "model_report", "contract": "toy_report.v1"}
                    }
                  },
                  "inputs": {"dataset": {"type": "local_export", "required_tables": ["events"]}}
                }
                """,
                encoding="utf-8",
            )
            registry = load_registry(registry_root)
            store = JobStore(root / "jobs.sqlite")
            runner = JobRunner(store, root)

            with self.assertRaises(ValueError):
                runner.create_registered_job(
                    registry,
                    "model",
                    "toy",
                    "evaluate",
                    {"dataset_path": "data/export_a", "not_registered": 1},
                )

            job = runner.create_registered_job(
                registry,
                "model",
                "toy",
                "evaluate",
                {"dataset_path": "data/export_a", "alpha": 0.2, "timeout_seconds": 5},
            )
            current = _wait_for_job(store, job.id)
            self.assertEqual(current.status, "succeeded")
            manifest = Path(current.output_path) / "run_manifest.json"
            self.assertTrue(manifest.exists())
            self.assertIn("toy_report.v1", manifest.read_text(encoding="utf-8"))

    def test_export_manager_reduces_validates_and_compares_exports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "data" / "export_a"
            _write_export(source, city="nyc", value="90")
            destination = root / "data" / "export_reduced"

            reduced = reduce_export(
                source,
                destination,
                tables=["events", "weather_snapshots"],
                cities=["nyc"],
                start="2026-07-01",
                end="2026-07-01",
            )
            inspected = inspect_export(destination)
            validation = validate_export(destination)
            comparison = compare_exports(source, destination)

            self.assertEqual(reduced["artifact_type"], "local_export")
            self.assertEqual(inspected["coverage"]["cities"], ["nyc"])
            self.assertFalse(validation["valid"])
            self.assertIn("Missing required table: market_snapshots", validation["blocking"])
            self.assertLess(comparison["table_deltas"]["market_snapshots"], 0)

    def test_safe_path_rejects_repo_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            self.assertEqual(_safe_path(root, "data/export_a"), root / "data/export_a")
            with self.assertRaises(ValueError):
                _safe_path(root, "../outside")

    def test_dashboard_summary_and_bot_stub_are_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_export(root / "data" / "export_a", city="nyc", value="90")
            store = JobStore(root / "jobs.sqlite")
            summary = dashboard_summary(root, load_registry(), store)
            bot = bot_stub("status")

            self.assertEqual(summary["brand"], "Kalshi Bot Control Center")
            self.assertEqual(summary["exports_available"], 1)
            self.assertEqual(bot["status"], "not_configured")

    def test_visualization_query_filters_bins_aggregates_and_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_csv(
                root / "weather_snapshots.csv",
                [
                    {
                        "city": "nyc",
                        "target_date": "2026-07-01",
                        "snapshot_time_utc": "2026-07-01T00:00:00+00:00",
                        "snapshot_local_hour": "0",
                        "nws_anchor_high_f": "90",
                    },
                    {
                        "city": "nyc",
                        "target_date": "2026-07-01",
                        "snapshot_time_utc": "2026-07-01T03:00:00+00:00",
                        "snapshot_local_hour": "3",
                        "nws_anchor_high_f": "94",
                    },
                    {
                        "city": "mia",
                        "target_date": "2026-07-01",
                        "snapshot_time_utc": "2026-07-01T03:00:00+00:00",
                        "snapshot_local_hour": "3",
                        "nws_anchor_high_f": "88",
                    },
                    {
                        "city": "nyc",
                        "target_date": "2026-07-02",
                        "snapshot_time_utc": "2026-07-02T03:00:00+00:00",
                        "snapshot_local_hour": "3",
                        "nws_anchor_high_f": "91",
                    },
                ],
            )

            result = execute_visualization_query(
                root,
                {
                    "table": "weather_snapshots",
                    "x": "hour_block",
                    "y": "nws_anchor_high_f",
                    "group": ["city"],
                    "filters": {
                        "cities": ["nyc"],
                        "date_from": "2026-07-01",
                        "date_to": "2026-07-01",
                    },
                    "hour_blocks": 8,
                    "aggregation": {"op": "avg"},
                    "page": 1,
                    "page_size": 1,
                    "density": True,
                },
            )

            self.assertEqual(result["metadata"]["total_rows"], 4)
            self.assertEqual(result["metadata"]["filtered_rows"], 2)
            self.assertEqual(result["metadata"]["result_rows"], 2)
            self.assertTrue(result["metadata"]["pagination"]["has_next_page"])
            self.assertEqual(
                result["rows"],
                [
                    {
                        "hour_block": 0,
                        "city": "nyc",
                        "avg_nws_anchor_high_f": 90.0,
                        "row_count": 1,
                    }
                ],
            )

    def test_visualization_query_reports_sampling_decimation_and_density(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = [
                {
                    "city": "nyc",
                    "target_date": "2026-07-01",
                    "snapshot_time_utc": f"2026-07-01T{hour:02d}:00:00+00:00",
                    "snapshot_local_hour": str(hour),
                    "nws_anchor_high_f": str(80 + hour),
                }
                for hour in range(12)
            ]
            _write_csv(root / "weather_snapshots.csv", rows)

            result = execute_visualization_query(
                root,
                {
                    "table": "weather_snapshots",
                    "x": "snapshot_local_hour",
                    "y": "nws_anchor_high_f",
                    "sample": {"limit": 6},
                    "decimate_to": 3,
                    "density": {"bins": 4},
                },
            )

            self.assertEqual(result["metadata"]["sampling"]["method"], "deterministic_stride")
            self.assertEqual(result["metadata"]["decimation"]["target"], 3)
            self.assertTrue(result["metadata"]["density"]["available"])
            self.assertEqual(result["metadata"]["returned_rows"], 3)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_export(path: Path, *, city: str, value: str) -> None:
    _write_csv(
        path / "events.csv",
        [
            {
                "city": city,
                "event_ticker": "E1",
                "target_date": "2026-07-01",
                "snapshot_time_utc": "2026-07-01T01:00:00+00:00",
            }
        ],
    )
    _write_csv(
        path / "weather_snapshots.csv",
        [
            {
                "city": city,
                "event_ticker": "E1",
                "target_date": "2026-07-01",
                "snapshot_time_utc": "2026-07-01T01:00:00+00:00",
                "nws_anchor_high_f": value,
            }
        ],
    )
    _write_csv(
        path / "market_snapshots.csv",
        [
            {
                "city": city,
                "event_ticker": "E1",
                "market_ticker": "M1",
                "target_date": "2026-07-01",
                "snapshot_time_utc": "2026-07-01T01:00:00+00:00",
            }
        ],
    )


def _wait_for_job(store: JobStore, job_id: str):
    deadline = time.time() + 5
    current = store.get(job_id)
    while current and current.status in {"queued", "running"} and time.time() < deadline:
        time.sleep(0.05)
        current = store.get(job_id)
    assert current is not None
    return current


if __name__ == "__main__":
    unittest.main()
