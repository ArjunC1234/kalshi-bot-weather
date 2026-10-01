from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backtest.data_sources import LocalExportSource, SupabaseSource
from backtest.export_supabase import export_supabase
from backtest.label_import import import_final_temperature_labels
from backtest.settlement_source_report import write_settlement_source_report
from libs.io_utils import write_json_gz
from libs.settlement_policy import POST_SETTLEMENT_SYSTEM_START


class FakeSupabaseClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, str]]] = []

    def select(self, table: str, params: dict[str, str] | None = None) -> list[dict]:
        self.calls.append((table, params or {}))
        return self.rows


class DataSourceTests(unittest.TestCase):
    def test_local_and_supabase_sources_return_same_shape(self) -> None:
        rows = [{"city": "den", "value": 1}]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json_gz(root / "events.json.gz", rows)
            local = LocalExportSource(root)
            supabase = SupabaseSource(FakeSupabaseClient(rows))  # type: ignore[arg-type]

            self.assertEqual(local.load_table("events"), rows)
            self.assertEqual(supabase.load_table("events"), rows)

    def test_export_defaults_to_post_settlement_system_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = FakeSupabaseClient([])

            export_supabase(
                "2026-07-01",
                "2026-08-31",
                root,
                client=client,  # type: ignore[arg-type]
            )

            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["requested_start"], "2026-07-01")
            self.assertEqual(manifest["start"], POST_SETTLEMENT_SYSTEM_START)
            self.assertTrue(manifest["post_settlement_system_only"])
            self.assertTrue(
                any(
                    f"snapshot_time_utc.gte.{POST_SETTLEMENT_SYSTEM_START}T00:00:00+00:00"
                    in call[1].get("and", "")
                    for call in client.calls
                )
            )

    def test_export_can_include_pre_settlement_system_data_for_research(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = FakeSupabaseClient([])

            export_supabase(
                "2026-07-01",
                "2026-08-31",
                root,
                client=client,  # type: ignore[arg-type]
                post_settlement_system_only=False,
            )

            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["start"], "2026-07-01")
            self.assertFalse(manifest["post_settlement_system_only"])

    def test_settlement_source_report_warns_when_weather_company_labels_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            output = root / "report"
            write_json_gz(
                export / "final_temperature_labels.json.gz",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "target_date": "2026-07-01",
                        "station_id": "KNYC",
                        "final_high_f": 90,
                        "source_provider": "nws_cli",
                    }
                ],
            )

            summary = write_settlement_source_report(
                LocalExportSource(export),
                output,
                source_export_id="export",
            )

            self.assertEqual(summary["settlement_sources"]["label_source"], "nws_cli_daily")
            self.assertEqual(summary["nws_weather_company_pairs"], 0)
            self.assertTrue((output / "source_coverage.csv").exists())
            self.assertTrue((output / "label_source_comparison.csv").exists())
            self.assertIn("No Weather Company daily labels", " ".join(summary["warnings"]))

    def test_import_labels_adds_weather_company_comparison_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            export = root / "export"
            labels = root / "weather_company.csv"
            write_json_gz(
                export / "final_temperature_labels.json.gz",
                [
                    {
                        "city": "nyc",
                        "event_ticker": "E1",
                        "target_date": "2026-07-01",
                        "station_id": "KNYC",
                        "final_high_f": 90,
                        "source_provider": "nws_cli",
                    }
                ],
            )
            labels.write_text(
                "city,event_ticker,target_date,station_id,final_high_f\n"
                "nyc,E1,2026-07-01,KNYC,91\n",
                encoding="utf-8",
            )

            result = import_final_temperature_labels(
                export,
                labels,
                source_provider="weather_company_daily",
            )
            summary = write_settlement_source_report(
                LocalExportSource(export),
                root / "report",
                source_export_id="export",
            )

            self.assertEqual(result["total_label_rows"], 2)
            self.assertEqual(summary["nws_weather_company_pairs"], 1)


if __name__ == "__main__":
    unittest.main()
