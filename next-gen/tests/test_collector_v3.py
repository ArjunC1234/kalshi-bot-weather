from __future__ import annotations

import json
import sys
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "production" / "deployable" / "collector"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(COLLECTOR))

from clients import HttpRecorder, raw_storage_path  # noqa: E402
from config import CITY_BY_KEY  # noqa: E402
from normalizers import (  # noqa: E402
    Bracket,
    final_high_validation_warnings,
    final_temperature_label_row,
    parse_nws_cli_final_high,
    select_nws_cli_final_high_product,
    settlement_row,
    validate_brackets,
    weather_row,
)
from schema import FACT_TABLES, INIT_SQL  # noqa: E402
from time_utils import city_clock, target_date_for_snapshot  # noqa: E402

from backtest.load_dataset import load_dataset  # noqa: E402


class StaticSource:
    def __init__(self, tables: dict[str, list[dict]]) -> None:
        self.tables = tables

    def load_table(self, table: str) -> list[dict]:
        return self.tables.get(table, [])


class CollectorV3Tests(unittest.TestCase):
    def test_schema_excludes_model_outputs(self) -> None:
        self.assertNotIn("model_outputs", FACT_TABLES)
        self.assertNotIn("create table if not exists model_outputs", INIT_SQL.lower())
        self.assertIn("final_temperature_labels", FACT_TABLES)
        self.assertIn("create table if not exists final_temperature_labels", INIT_SQL.lower())
        self.assertIn("v_events_missing_final_high", INIT_SQL)
        self.assertIn("final_nws_high_f", INIT_SQL)

    def test_city_clock_uses_city_local_fields(self) -> None:
        city = CITY_BY_KEY["la"]
        clock = city_clock(city, datetime(2026, 7, 1, 13, tzinfo=UTC), date(2026, 7, 1))
        self.assertEqual(clock.snapshot_local_date.isoformat(), "2026-07-01")
        self.assertEqual(clock.snapshot_local_hour, 6)
        self.assertEqual(clock.climate_day_start_utc.isoformat(), "2026-07-01T08:00:00+00:00")
        self.assertEqual(clock.climate_day_end_utc.isoformat(), "2026-07-02T08:00:00+00:00")

    def test_target_date_uses_fixed_standard_climate_day_not_dst_wall_date(self) -> None:
        city = CITY_BY_KEY["nyc"]
        self.assertEqual(
            target_date_for_snapshot(city, datetime(2026, 7, 2, 4, tzinfo=UTC)),
            date(2026, 7, 1),
        )
        self.assertEqual(
            target_date_for_snapshot(city, datetime(2026, 7, 2, 5, tzinfo=UTC)),
            date(2026, 7, 2),
        )

    def test_raw_payload_retag_removes_unknown_event_for_normal_payload(self) -> None:
        recorder = HttpRecorder(
            "test@example.com",
            "run-1",
            datetime(2026, 7, 1, 19, tzinfo=UTC),
            "bucket",
            3,
        )
        recorder.raw_payloads.append(
            recorder_payload(
                recorder,
                city="den",
                event_ticker=None,
                target_date=None,
                endpoint_name="kalshi_open_markets",
            )
        )
        raw_id = recorder.retag_latest_payload(
            "kalshi",
            "kalshi_open_markets",
            "den",
            "KXHIGHDEN-26JUL01",
            "2026-07-01",
            "2026-07-01T13:00:00-06:00",
        )
        self.assertIsNotNone(raw_id)
        record = recorder.raw_payloads[-1]
        self.assertNotIn("unknown-event", record.storage_path)
        self.assertIn("KXHIGHDEN-26JUL01", record.storage_path)
        self.assertEqual(record.target_date, "2026-07-01")

    def test_raw_storage_path_contains_provider_date_city_event_hour(self) -> None:
        path = raw_storage_path(
            "nws",
            "2026-07-01",
            "nyc",
            "KXHIGHNY-26JUL01",
            datetime(2026, 7, 1, 19, tzinfo=UTC),
            "abc123",
        )
        self.assertEqual(
            path, "raw/nws/2026-07-01/nyc/KXHIGHNY-26JUL01/20260701T190000Z/abc123.json.gz"
        )

    def test_weather_row_keys_are_schema_compatible_and_preserve_sources(self) -> None:
        city = CITY_BY_KEY["nyc"]
        clock = city_clock(city, datetime(2026, 7, 1, 16, tzinfo=UTC), date(2026, 7, 1))
        row = weather_row(
            "run-1",
            city,
            date(2026, 7, 1),
            "KXHIGHNY-26JUL01",
            clock,
            {"properties": {"periods": []}},
            {"properties": {"periods": []}},
            {"features": []},
            {},
            {
                "hourly": {
                    "time": ["2026-07-01T16:00", "2026-07-01T18:00"],
                    "temperature_2m": [80.0, 83.0],
                }
            },
            {
                "hourly": {
                    "time": ["2026-07-01T16:00", "2026-07-01T18:00"],
                    "temperature_2m": [79.0, 82.0],
                }
            },
            {"nws_points": "raw-1", "open_meteo_hrrr": "raw-2"},
        )
        forbidden = {"hrrr_full_window_high_f", "hrrr_remaining_forecast_high_f"}
        self.assertTrue(forbidden.isdisjoint(row))
        self.assertEqual(row["hrrr_projected_high_f"], 83.0)
        self.assertEqual(row["nbm_projected_high_f"], 82.0)
        self.assertEqual(row["source_payload_ids"]["open_meteo_hrrr"], "raw-2")
        self.assertIn("hrrr_full_window_high_f", row["features"])

    def test_settlement_requires_exactly_one_winner(self) -> None:
        city = CITY_BY_KEY["aus"]
        brackets = validate_brackets(
            [
                Bracket("LOW", "80 or below", None, 80),
                Bracket("MID", "81 to 85", 81, 85),
                Bracket("HIGH", "86 or above", 86, None),
            ]
        )
        with self.assertRaises(ValueError):
            settlement_row(
                city,
                date(2026, 7, 1),
                "KXHIGHAUS-26JUL01",
                [{"ticker": "LOW", "result": "no"}, {"ticker": "MID", "result": "no"}],
                brackets,
                "raw-1",
                datetime(2026, 7, 2, tzinfo=UTC),
            )
        row = settlement_row(
            city,
            date(2026, 7, 1),
            "KXHIGHAUS-26JUL01",
            [
                {"ticker": "LOW", "result": "no"},
                {
                    "ticker": "MID",
                    "result": "yes",
                    "yes_sub_title": "81 to 85",
                    "expiration_value": "84",
                },
                {"ticker": "HIGH", "result": "no"},
            ],
            brackets,
            "raw-1",
            datetime(2026, 7, 2, tzinfo=UTC),
        )
        self.assertEqual(row["winner_ticker"], "MID")
        self.assertEqual(row["settlement_bracket_index"], 1)

    def test_nws_cli_final_high_parser_accepts_final_report_only(self) -> None:
        final_product = {
            "id": "product-final",
            "issuanceTime": "2026-07-02T06:20:00+00:00",
            "productText": """
...THE CENTRAL PARK NY CLIMATE SUMMARY FOR JULY 1 2026...

TEMPERATURE (F)
 TODAY
  MAXIMUM         93    127 PM 100    1901  84      9       89
  MINIMUM         75    649 AM  52    1943  69      6       72

PRECIPITATION (IN)
""",
        }
        interim_product = {
            **final_product,
            "id": "product-interim",
            "issuanceTime": "2026-07-01T20:20:00+00:00",
            "productText": final_product["productText"].replace(
                "TEMPERATURE (F)", "VALID TODAY AS OF 0400 PM LOCAL TIME.\n\nTEMPERATURE (F)"
            ),
        }
        valid_as_of_product = {
            **final_product,
            "id": "product-valid-as-of",
            "issuanceTime": "2026-07-01T12:29:00+00:00",
            "productText": final_product["productText"].replace(
                "TEMPERATURE (F)", "VALID AS OF 0600 AM LOCAL TIME.\n\nTEMPERATURE (F)"
            ),
        }
        self.assertEqual(parse_nws_cli_final_high(final_product, date(2026, 7, 1)), 93.0)
        self.assertIsNone(parse_nws_cli_final_high(interim_product, date(2026, 7, 1)))
        self.assertIsNone(parse_nws_cli_final_high(valid_as_of_product, date(2026, 7, 1)))
        self.assertIsNone(parse_nws_cli_final_high(final_product, date(2026, 7, 2)))

    def test_select_nws_cli_final_high_product_chooses_latest_parseable(self) -> None:
        older = {
            "id": "older",
            "issuanceTime": "2026-07-02T06:00:00+00:00",
            "productText": """
...THE MIAMI FL CLIMATE SUMMARY FOR JULY 1 2026...
TEMPERATURE (F)
 TODAY
  MAXIMUM         91    200 PM
""",
        }
        newer = {
            "id": "newer",
            "issuanceTime": "2026-07-02T07:00:00+00:00",
            "productText": older["productText"].replace("91", "92", 1),
        }
        selected = select_nws_cli_final_high_product([older, newer], date(2026, 7, 1))
        self.assertIsNotNone(selected)
        product, high = selected or ({}, 0.0)
        self.assertEqual(product["id"], "newer")
        self.assertEqual(high, 92.0)

    def test_final_temperature_label_row_and_bracket_warning(self) -> None:
        city = CITY_BY_KEY["den"]
        metadata = {
            "brackets": [
                {"ticker": "LOW", "label": "89 or below", "lower_f": None, "upper_f": 89},
                {"ticker": "HIGH", "label": "90 or above", "lower_f": 90, "upper_f": None},
            ]
        }
        self.assertEqual(final_high_validation_warnings(91.0, metadata, "HIGH"), [])
        warnings = final_high_validation_warnings(88.0, metadata, "HIGH")
        self.assertTrue(warnings)
        row = final_temperature_label_row(
            city,
            date(2026, 7, 1),
            "KXHIGHDEN-26JUL01",
            "KDEN",
            91.0,
            {"id": "product-1", "issuanceTime": "2026-07-02T06:00:00+00:00"},
            "raw-1",
            datetime(2026, 7, 2, 7, tzinfo=UTC),
            [],
        )
        self.assertEqual(row["final_high_f"], 91.0)
        self.assertEqual(row["source_provider"], "nws_cli")

    def test_backtest_loader_accepts_v3_export_aliases(self) -> None:
        source = StaticSource(
            {
                "collector_runs": [
                    {
                        "collector_run_id": "run-1",
                        "snapshot_time_utc": "2026-07-01T19:00:00+00:00",
                        "schema_version": 3,
                    }
                ],
                "events": [
                    {
                        "city": "nyc",
                        "event_ticker": "KXHIGHNY-26JUL01",
                        "target_date": "2026-07-01",
                        "snapshot_time_utc": "2026-07-01T19:00:00+00:00",
                        "climate_day_start_utc": "2026-07-01T05:00:00+00:00",
                        "climate_day_end_utc": "2026-07-02T05:00:00+00:00",
                        "station_id": "KNYC",
                    }
                ],
                "market_snapshots": [
                    {
                        "city": "nyc",
                        "event_ticker": "KXHIGHNY-26JUL01",
                        "market_ticker": "M1",
                        "target_date": "2026-07-01",
                        "snapshot_time_utc": "2026-07-01T19:00:00+00:00",
                        "bracket_label": "85 or below",
                        "bracket_index": 0,
                        "yes_ask_dollars": "0.42",
                    }
                ],
                "weather_snapshots": [
                    {
                        "city": "nyc",
                        "event_ticker": "KXHIGHNY-26JUL01",
                        "target_date": "2026-07-01",
                        "snapshot_time_utc": "2026-07-01T19:00:00+00:00",
                        "nws_anchor_high_f": "86",
                        "features": json.dumps({"source": "v3"}),
                    }
                ],
                "settlements": [
                    {
                        "city": "nyc",
                        "event_ticker": "KXHIGHNY-26JUL01",
                        "target_date": "2026-07-01",
                        "settled_at_utc": "2026-07-02T06:00:00+00:00",
                        "winner_ticker": "M1",
                        "settlement_bracket_index": "0",
                    }
                ],
                "final_temperature_labels": [
                    {
                        "city": "nyc",
                        "event_ticker": "KXHIGHNY-26JUL01",
                        "target_date": "2026-07-01",
                        "station_id": "KNYC",
                        "final_high_f": "87",
                        "source_provider": "nws_cli",
                        "product_id": "product-1",
                        "issued_at_utc": "2026-07-02T06:20:00+00:00",
                        "validation_status": "valid",
                        "warnings": "[]",
                    }
                ],
            }
        )
        dataset = load_dataset(source)
        self.assertEqual(len(dataset.collector_runs), 1)
        self.assertEqual(len(dataset.events), 1)
        self.assertEqual(dataset.weather[0].features["source"], "v3")
        self.assertEqual(len(dataset.final_temperature_labels), 1)
        self.assertEqual(dataset.final_temperature_labels[0].final_high_f, 87.0)


def recorder_payload(
    recorder: HttpRecorder,
    city: str,
    event_ticker: str | None,
    target_date: str | None,
    endpoint_name: str,
):
    from clients import RawPayload

    payload = {"ok": True}
    return RawPayload(
        raw_payload_id="raw-unknown",
        collector_run_id=recorder.collector_run_id,
        city=city,
        event_ticker=event_ticker,
        target_date=target_date,
        snapshot_time_utc=recorder.snapshot_time_utc.isoformat(),
        snapshot_time_local=None,
        provider="kalshi",
        endpoint_name=endpoint_name,
        method="GET",
        url="https://example.test",
        params={},
        requested_at_utc=recorder.snapshot_time_utc.isoformat(),
        received_at_utc=recorder.snapshot_time_utc.isoformat(),
        latency_ms=0,
        status_code=200,
        success=True,
        error_type=None,
        error_message=None,
        content_sha256="unknown",
        compressed_size_bytes=1,
        storage_bucket="bucket",
        storage_path="raw/kalshi/2026-07-01/den/unknown-event/20260701T190000Z/unknown.json.gz",
        schema_version=3,
        payload=payload,
    )


if __name__ == "__main__":
    unittest.main()
