# ruff: noqa: E402, I001

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "production" / "deployable" / "collector"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(COLLECTOR))

from backtest.load_dataset import load_dataset
from clients import HttpRecorder, _export_bounds, raw_storage_path
from collector import weather_company_label_from_kalshi_settlement
from config import CITY_BY_KEY
from normalizers import (
    Bracket,
    event_row,
    final_high_validation_warnings,
    final_temperature_label_row,
    market_rows,
    market_float,
    parse_nws_cli_final_high,
    parse_weather_company_final_high,
    select_nws_cli_final_high_product,
    settlement_row,
    validate_brackets,
    weather_row,
)
from schema import FACT_TABLES, INIT_SQL
from time_utils import city_clock, target_date_for_snapshot


class StaticSource:
    def __init__(self, tables: dict[str, list[dict]]) -> None:
        self.tables = tables

    def load_table(self, table: str) -> list[dict]:
        return self.tables.get(table, [])


class CollectorV3Tests(unittest.TestCase):
    def test_market_counts_preserve_contract_units(self) -> None:
        self.assertEqual(market_float({"yes_bid_size_fp": "13.00"}, "yes_bid_size_fp"), 13.)
        self.assertEqual(market_float({"yes_ask_size": 200}, "yes_ask_size"), 200.)
        self.assertEqual(market_float({"volume_fp": "2000.50"}, "volume_fp"), 2000.5)
        self.assertEqual(market_float({"open_interest": 150}, "open_interest"), 150.)

    def test_market_prices_use_field_units_even_at_one_cent(self) -> None:
        self.assertEqual(market_float({"yes_bid": 1}, "yes_bid"), .01)
        self.assertEqual(market_float({"yes_bid": 50}, "yes_bid"), .5)
        self.assertEqual(market_float({"yes_bid_dollars": "0.5000"}, "yes_bid_dollars"), .5)
        self.assertEqual(market_float({"yes_bid_dollars": "1.0000"}, "yes_bid_dollars"), 1.)

    def test_deployable_collector_imports_without_repo_libs(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "collector.py", "init-db", "--print-sql"],
            cwd=COLLECTOR,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("create table if not exists collector_runs", result.stdout.lower())

    def test_schema_excludes_model_outputs(self) -> None:
        self.assertNotIn("model_outputs", FACT_TABLES)
        self.assertNotIn("create table if not exists model_outputs", INIT_SQL.lower())
        self.assertIn("final_temperature_labels", FACT_TABLES)
        self.assertIn("create table if not exists final_temperature_labels", INIT_SQL.lower())
        self.assertIn("v_events_missing_final_high", INIT_SQL)
        self.assertIn("v_events_missing_weather_company_final_high", INIT_SQL)
        self.assertIn("final_nws_high_f", INIT_SQL)
        self.assertIn("final_weather_company_high_f", INIT_SQL)
        self.assertIn("family_baseline_high_f", INIT_SQL)
        self.assertIn("add column if not exists family_baseline_high_f", INIT_SQL)

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
            {
                "features": [
                    {
                        "properties": {
                            "timestamp": "2026-07-01T15:45:00+00:00",
                            "temperature": {"value": 29.4, "unitCode": "wmoUnit:degC"},
                        }
                    }
                ]
            },
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
            {
                "iem_asos_metar": {
                    "observations": [
                        {"valid": "2026-07-01T15:50:00+00:00", "tmpf": 86.0}
                    ]
                }
            },
        )
        forbidden = {"hrrr_full_window_high_f", "hrrr_remaining_forecast_high_f"}
        self.assertTrue(forbidden.isdisjoint(row))
        self.assertEqual(row["hrrr_projected_high_f"], 86.0)
        self.assertEqual(row["nbm_projected_high_f"], 86.0)
        self.assertEqual(row["settlement_observed_high_so_far_f"], 86.0)
        self.assertEqual(row["settlement_observed_source_count"], 2)
        self.assertEqual(row["settlement_observed_age_seconds"], 600.0)
        self.assertAlmostEqual(row["settlement_observed_source_range_f"], 1.08, places=2)
        self.assertAlmostEqual(row["settlement_observed_nws_delta_f"], 1.08, places=2)
        self.assertEqual(row["family_baseline_high_f"], 86.0)
        self.assertEqual(row["family_numerical_anchor_high_f"], 86.0)
        self.assertEqual(row["source_payload_ids"]["open_meteo_hrrr"], "raw-2")
        self.assertIn("hrrr_full_window_high_f", row["features"])
        weather_schema = re.search(
            r"create table if not exists weather_snapshots \((.*?)\n\);",
            INIT_SQL,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(weather_schema)
        weather_columns = set(
            re.findall(
                r"^\s+([a-z_][a-z0-9_]*)\s+"
                r"(?:text|integer|double precision|date|timestamptz|jsonb)",
                weather_schema.group(1) if weather_schema else "",
                flags=re.MULTILINE,
            )
        )
        self.assertTrue(set(row).issubset(weather_columns), sorted(set(row) - weather_columns))

    def test_market_metadata_preserves_settlement_rules_and_sources(self) -> None:
        city = CITY_BY_KEY["nyc"]
        clock = city_clock(city, datetime(2026, 8, 28, 20, tzinfo=UTC), date(2026, 8, 28))
        brackets = (
            Bracket("LOW", "83 or below", None, 83),
            Bracket("HIGH", "84 or above", 84, None),
        )
        markets = [
            {
                "ticker": "LOW",
                "event_ticker": "KXHIGHNY-26AUG28",
                "yes_sub_title": "83 or below",
                "settlement_sources": [{"name": "weather.com", "station": "KNYC"}],
                "rules_primary": "Settles to Weather.com daily high at Central Park.",
                "rules_secondary": "Fallback to NWS climate report.",
            },
            {
                "ticker": "HIGH",
                "event_ticker": "KXHIGHNY-26AUG28",
                "yes_sub_title": "84 or above",
                "settlement_sources": [{"name": "weather.com", "station": "KNYC"}],
                "rules_primary": "Settles to Weather.com daily high at Central Park.",
                "rules_secondary": "Fallback to NWS climate report.",
            },
        ]

        event = event_row(
            "run-1",
            city,
            date(2026, 8, 28),
            "KXHIGHNY-26AUG28",
            clock,
            "raw-1",
            markets,
            brackets,
        )
        market = market_rows(
            "run-1",
            city,
            date(2026, 8, 28),
            "KXHIGHNY-26AUG28",
            clock,
            brackets,
            markets,
            "raw-1",
        )[0]

        self.assertEqual(event["rules_primary"], markets[0]["rules_primary"])
        self.assertEqual(event["rules_secondary"], markets[0]["rules_secondary"])
        self.assertEqual(event["settlement_source_provider"], "weather_company_daily")
        self.assertEqual(event["settlement_station_id"], "KNYC")
        self.assertEqual(market["rules_primary"], markets[0]["rules_primary"])
        self.assertEqual(market["settlement_sources"]["raw"][0][0]["name"], "weather.com")

    def test_export_bounds_use_dates_for_final_temperature_labels(self) -> None:
        self.assertEqual(
            _export_bounds("final_temperature_labels", "2026-07-01", "2026-07-02"),
            ("2026-07-01", "2026-07-02"),
        )

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
        self.assertEqual(row["market_settlement_source"], "unknown")
        self.assertIsNone(row["rules_primary"])

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

    def test_nws_cli_final_high_parser_accepts_record_suffix(self) -> None:
        product = {
            "id": "product-record",
            "issuanceTime": "2026-07-03T06:19:00+00:00",
            "productText": """
...THE CENTRAL PARK NY CLIMATE SUMMARY FOR JULY 2 2026...

TEMPERATURE (F)
 TODAY
  MAXIMUM        100R   247 PM 100    1901  84     16       84
  MINIMUM         78    525 AM  52    1943  69      9       73

PRECIPITATION (IN)
""",
        }
        self.assertEqual(parse_nws_cli_final_high(product, date(2026, 7, 2)), 100.0)

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

    def test_weather_company_final_high_parser_accepts_explicit_daily_payloads(self) -> None:
        self.assertEqual(
            parse_weather_company_final_high(
                {
                    "days": [
                        {"date": "2026-07-01", "temperatureMaxF": 91},
                        {"date": "2026-07-02", "temperatureMaxF": 88},
                    ]
                },
                date(2026, 7, 1),
            ),
            91.0,
        )
        self.assertEqual(
            parse_weather_company_final_high(
                {"target_date": "2026-07-01", "temperatureMax": {"value": 32, "unit": "C"}},
                date(2026, 7, 1),
            ),
            89.6,
        )
        self.assertIsNone(
            parse_weather_company_final_high(
                {"days": [{"date": "2026-07-02", "temperatureMaxF": 88}]},
                date(2026, 7, 1),
            )
        )

    def test_weather_company_label_fallback_uses_kalshi_settlement(self) -> None:
        selected = weather_company_label_from_kalshi_settlement(
            {
                "event_ticker": "KXHIGHNY-26AUG27",
                "target_date": "2026-08-27",
                "kalshi_settlement_temperature_f": "83",
            }
        )
        self.assertIsNotNone(selected)
        product, final_high_f, raw_payload_id = selected or ({}, 0.0, "unexpected")
        self.assertEqual(final_high_f, 83.0)
        self.assertIsNone(raw_payload_id)
        self.assertEqual(product["source"], "kalshi_settlement_expiration_value")
        self.assertEqual(product["metadata"]["source_provider"], "weather_company_daily")

        self.assertIsNone(
            weather_company_label_from_kalshi_settlement(
                {
                    "event_ticker": "KXHIGHNY-26AUG28",
                    "target_date": "2026-08-28",
                    "kalshi_settlement_temperature_f": None,
                }
            )
        )

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
        weather_company = final_temperature_label_row(
            city,
            date(2026, 7, 1),
            "KXHIGHDEN-26JUL01",
            "KDEN",
            92.0,
            {"id": "twc-1", "validTimeLocal": "2026-07-01T23:59:00-06:00"},
            "raw-2",
            datetime(2026, 7, 2, 7, tzinfo=UTC),
            [],
            source_provider="weather_company_daily",
        )
        self.assertEqual(weather_company["source_provider"], "weather_company_daily")
        self.assertNotEqual(
            row["final_temperature_label_id"],
            weather_company["final_temperature_label_id"],
        )

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
                        "settlement_sources": json.dumps({"raw": ["weather.com"]}),
                        "rules_primary": "Primary settlement rule",
                        "rules_secondary": "Secondary settlement rule",
                        "settlement_source_provider": "weather_company_daily",
                        "settlement_station_id": "KNYC",
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
                        "settlement_sources": json.dumps({"raw": ["weather.com"]}),
                        "rules_primary": "Primary settlement rule",
                        "rules_secondary": "Secondary settlement rule",
                    }
                ],
                "weather_snapshots": [
                    {
                        "city": "nyc",
                        "event_ticker": "KXHIGHNY-26JUL01",
                        "target_date": "2026-07-01",
                        "snapshot_time_utc": "2026-07-01T19:00:00+00:00",
                        "nws_anchor_high_f": "86",
                        "settlement_observed_high_so_far_f": "87",
                        "settlement_observed_age_seconds": "900",
                        "settlement_observed_source_count": "2",
                        "settlement_observed_source_range_f": "1",
                        "settlement_observed_source_stddev_f": "0.5",
                        "settlement_observed_nws_delta_f": "1",
                        "settlement_observed_sources": json.dumps(
                            {"weather_company": {"high_f": 87}}
                        ),
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
                        "market_settlement_source": "weather_company_daily",
                        "rules_primary": "Primary settlement rule",
                        "rules_secondary": "Secondary settlement rule",
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
        self.assertEqual(dataset.events[0].settlement_source_provider, "weather_company_daily")
        self.assertEqual(dataset.events[0].settlement_station_id, "KNYC")
        self.assertEqual(dataset.events[0].rules_primary, "Primary settlement rule")
        self.assertEqual(dataset.events[0].settlement_sources["raw"], ["weather.com"])
        self.assertEqual(dataset.markets[0].rules_secondary, "Secondary settlement rule")
        self.assertEqual(dataset.markets[0].settlement_sources["raw"], ["weather.com"])
        self.assertEqual(dataset.weather[0].features["source"], "v3")
        self.assertEqual(dataset.weather[0].features["settlement_observed_high_so_far_f"], 87.0)
        self.assertEqual(dataset.weather[0].features["settlement_observed_age_hours"], 0.25)
        self.assertEqual(dataset.settlements[0].market_settlement_source, "weather_company_daily")
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
