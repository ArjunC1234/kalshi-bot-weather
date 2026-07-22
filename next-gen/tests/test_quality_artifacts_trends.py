from __future__ import annotations

# ruff: noqa: E402, I001

import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.data_sources import LocalExportSource
from backtest.health import build_daily_health_report
from backtest.quality import build_quality_report
from libs.artifacts import (
    create_artifact_manifest,
    promote_artifact,
    read_artifact_manifest,
    write_artifact_manifest,
)
from trends.datasets import build_workbench_payload
from trends.sources import SourceRoots, discover_sources, resolve_source


class QualityArtifactTrendTests(unittest.TestCase):
    def test_daily_health_pending_counts_ignore_previous_target_date_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_csv(
                root / "events.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E_PREV",
                        "target_date": "2026-07-19",
                        "snapshot_local_date": "2026-07-20",
                        "snapshot_local_hour": "1",
                        "snapshot_time_utc": "2026-07-20T01:00:00+00:00",
                    },
                    {
                        "city": "nyc",
                        "event_ticker": "E_CUR",
                        "target_date": "2026-07-20",
                        "snapshot_local_date": "2026-07-20",
                        "snapshot_local_hour": "2",
                        "snapshot_time_utc": "2026-07-20T02:00:00+00:00",
                    },
                ],
            )
            _write_csv(
                root / "market_snapshots.csv",
                [
                    {"city": "nyc", "snapshot_local_date": "2026-07-20"},
                    {"city": "nyc", "snapshot_local_date": "2026-07-20"},
                ],
            )
            _write_csv(
                root / "weather_snapshots.csv",
                [
                    {"city": "nyc", "snapshot_local_date": "2026-07-20"},
                    {"city": "nyc", "snapshot_local_date": "2026-07-20"},
                ],
            )
            _write_csv(
                root / "raw_payloads.csv",
                [
                    {
                        "snapshot_local_date": "2026-07-20",
                        "storage_path": "raw/20260720/item.json.gz",
                    }
                ],
            )
            _write_csv(
                root / "settlements.csv",
                [{"city": "nyc", "event_ticker": "E_CUR", "target_date": "2026-07-20"}],
            )
            _write_csv(
                root / "final_temperature_labels.csv",
                [{"city": "nyc", "event_ticker": "E_CUR", "target_date": "2026-07-20"}],
            )
            _write_csv(root / "provider_errors.csv", [])
            report = build_daily_health_report(
                LocalExportSource(root),
                "2026-07-20",
                expected_cities=["nyc"],
            )
            self.assertEqual(report.event_count, 1)
            self.assertEqual(report.pending_settlements, 0)
            self.assertEqual(report.pending_final_highs, 0)
            self.assertEqual(report.actual_city_hours, 2)
            self.assertEqual(report.expected_city_hours, 2)
            self.assertEqual(report.cities_seen, ["nyc"])

    def test_quality_report_counts_missing_hours_and_pending_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_csv(
                root / "events.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "snapshot_time_utc": "2026-07-01T01:00:00+00:00",
                        "climate_day_end_utc": "2026-07-01T02:00:00+00:00",
                    },
                    {
                        "city": "den",
                        "event_ticker": "E2",
                        "snapshot_time_utc": "2026-07-01T01:00:00+00:00",
                        "climate_day_end_utc": "2026-07-01T02:00:00+00:00",
                    },
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "snapshot_time_utc": "2026-07-01T02:00:00+00:00",
                        "climate_day_end_utc": "2026-07-01T02:00:00+00:00",
                    },
                ],
            )
            _write_csv(root / "settlements.csv", [{"city": "nyc", "event_ticker": "E1"}])
            _write_csv(root / "final_temperature_labels.csv", [])
            _write_csv(root / "raw_payloads.csv", [{"compressed_size_bytes": "10"}])
            _write_csv(root / "provider_errors.csv", [{"provider": "nws"}])
            report = build_quality_report(LocalExportSource(root))
            self.assertEqual(report.snapshot_hours, 2)
            self.assertEqual(report.actual_city_hours, 3)
            self.assertEqual(len(report.missing_city_hours), 1)
            self.assertEqual(report.pending_settlements, 1)
            self.assertEqual(report.pending_final_highs, 2)
            self.assertEqual(report.provider_errors["nws"], 1)

    def test_artifact_manifest_and_promotion_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / "artifact"
            registry = root / "registry"
            manifest = create_artifact_manifest(
                "raycaster",
                "v1",
                "2026-07-01",
                "2026-07-08",
                training_rows=72,
                settled_city_days=6,
                metrics={"mae": 1.2},
            )
            write_artifact_manifest(artifact, manifest)
            loaded = read_artifact_manifest(artifact)
            self.assertEqual(loaded.model_family, "raycaster")
            pointer = promote_artifact(artifact, registry)
            self.assertTrue(pointer.exists())
            self.assertTrue(read_artifact_manifest(artifact).promoted)

    def test_workbench_series_includes_settled_bracket_ask(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_csv(
                root / "market_snapshots.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "market_ticker": "WIN",
                        "snapshot_time_utc": "2026-07-01T01:00:00+00:00",
                        "yes_ask_dollars": "0.42",
                        "market_top_probability": "0.50",
                    },
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "market_ticker": "LOSE",
                        "snapshot_time_utc": "2026-07-01T01:00:00+00:00",
                        "yes_ask_dollars": "0.20",
                        "market_top_probability": "0.50",
                    },
                ],
            )
            _write_csv(
                root / "settlements.csv",
                [{"city": "nyc", "event_ticker": "E1", "winner_ticker": "WIN"}],
            )
            _write_csv(
                root / "final_temperature_labels.csv",
                [{"city": "nyc", "target_date": "2026-07-01", "final_high_f": "93"}],
            )
            _write_csv(root / "events.csv", [{"city": "nyc", "event_ticker": "E1"}])
            _write_csv(
                root / "weather_snapshots.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "snapshot_time_utc": "2026-07-01T01:00:00+00:00",
                    }
                ],
            )
            payload = build_workbench_payload(root)
            asks = payload["series"]["settled_bracket_ask_by_snapshot"]
            self.assertEqual(len(asks), 1)
            self.assertAlmostEqual(asks[0]["value"], 0.42)

    def test_source_discovery_and_safe_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_root = root / "data"
            report_root = root / "reports"
            quality_root = root / "quality"
            export = data_root / "export_a"
            report = report_root / "raycaster_run"
            quality = quality_root / "quality_run"
            _write_csv(export / "events.csv", [{"city": "nyc"}])
            _write_csv(export / "weather_snapshots.csv", [{"city": "nyc"}])
            _write_csv(report / "predictions.csv", [{"city": "nyc"}])
            _write_csv(quality / "table_counts.csv", [{"table": "events", "count": "1"}])
            roots = SourceRoots(data_root, report_root, quality_root)

            sources = discover_sources(roots)

            self.assertEqual(sources["exports"][0]["id"], "export_a")
            self.assertEqual(sources["reports"][0]["id"], "raycaster_run")
            self.assertEqual(sources["quality_reports"][0]["id"], "quality_run")
            self.assertEqual(resolve_source(roots, "export", "export_a"), export.resolve())
            with self.assertRaises(ValueError):
                resolve_source(roots, "export", "../reports/raycaster_run")

    def test_workbench_joins_reports_and_export_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data"
            report = root / "report"
            _write_csv(
                data / "events.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "target_date": "2026-07-01",
                        "snapshot_time_utc": "2026-07-01T10:00:00+00:00",
                    }
                ],
            )
            _write_csv(
                data / "weather_snapshots.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "snapshot_time_utc": "2026-07-01T10:00:00+00:00",
                        "checkpoint_label": "t_plus_5h",
                        "nws_anchor_high_f": "90",
                        "observed_high_so_far_f": "88",
                        "hrrr_projected_high_f": "91",
                        "nbm_projected_high_f": "89",
                        "ensemble_raw_median_high_f": "90.5",
                    }
                ],
            )
            _write_csv(
                data / "market_snapshots.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "market_ticker": "WIN",
                        "snapshot_time_utc": "2026-07-01T10:00:00+00:00",
                        "bracket_label": "90 to 91",
                        "bracket_index": "1",
                        "yes_ask_dollars": "0.42",
                        "normalized_market_midpoint_probability": "0.40",
                    },
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "market_ticker": "LOSE",
                        "snapshot_time_utc": "2026-07-01T10:00:00+00:00",
                        "bracket_label": "92 or above",
                        "bracket_index": "2",
                        "yes_ask_dollars": "0.25",
                        "normalized_market_midpoint_probability": "0.20",
                    },
                ],
            )
            _write_csv(
                data / "settlements.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "winner_ticker": "WIN",
                        "winner_label": "90 to 91",
                    }
                ],
            )
            _write_csv(
                data / "final_temperature_labels.csv",
                [{"city": "nyc", "event_ticker": "E1", "final_high_f": "90"}],
            )
            _write_csv(
                report / "predictions.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "snapshot_hour_utc": "2026-07-01T10:00:00+00:00",
                        "model_name": "raycaster_v1",
                        "expected_high_f": "90.4",
                    }
                ],
            )
            _write_csv(
                report / "bracket_distributions.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "snapshot_hour_utc": "2026-07-01T10:00:00+00:00",
                        "model_name": "raycaster_v1",
                        "probabilities": "{'WIN': 0.7, 'LOSE': 0.3}",
                    }
                ],
            )
            _write_csv(
                report / "errors.csv",
                [
                    {
                        "metric_type": "temperature",
                        "city": "nyc",
                        "event_ticker": "E1",
                        "snapshot_hour_utc": "2026-07-01T10:00:00+00:00",
                        "checkpoint": "t_plus_5h",
                        "actual_high_f": "90",
                        "predicted_high_f": "90.4",
                        "error_f": "0.4",
                        "absolute_error_f": "0.4",
                    }
                ],
            )
            payload = build_workbench_payload(data, [report])
            self.assertTrue(payload["metadata"]["has_model_reports"])
            self.assertEqual(len(payload["analysis"]["event_replays"]), 1)
            self.assertEqual(len(payload["analysis"]["market_model_points"]), 2)
            self.assertEqual(len(payload["analysis"]["calibration_bins"]), 2)
            replay = payload["analysis"]["event_replays"][0]
            self.assertEqual(replay["final_high_f"], 90)
            self.assertEqual(replay["timeline"][0]["raycaster_v1_expected_high_f"], 90.4)

    def test_workbench_degrades_when_model_report_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_csv(
                root / "events.csv",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "target_date": "2026-07-01",
                        "snapshot_time_utc": "2026-07-01T10:00:00+00:00",
                    }
                ],
            )
            payload = build_workbench_payload(root)
            self.assertFalse(payload["metadata"]["has_model_reports"])
            self.assertEqual(payload["analysis"]["market_model_points"], [])
            report_modes = [
                mode["key"] for mode in payload["catalog"]["modes"] if mode["requires_report"]
            ]
            self.assertIn("performance", report_modes)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
