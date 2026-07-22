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
from control.compatibility import check_model_dataset_compatibility
from control.jobs.runner import JobRunner
from control.jobs.store import JobStore
from control.providers.supabase_export import preview_export_profile
from control.registry.loader import load_registry, validate_registry


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


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
