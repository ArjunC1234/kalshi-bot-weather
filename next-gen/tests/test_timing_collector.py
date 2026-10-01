from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests

COLLECTOR = Path(__file__).resolve().parents[1] / "production" / "deployable" / "collector"
sys.path.insert(0, str(COLLECTOR))

from config import CITY_BY_KEY  # noqa: E402
from spool import read_json_gz  # noqa: E402
from timing_collector import (  # noqa: E402
    TimingRecorder,
    capture_city,
    collect_timing,
    retry_after_seconds,
    sync_timing,
)

SNAPSHOT = datetime(2026, 9, 13, 17, 5, 30, tzinfo=UTC)


def response(body, status=200, headers=None):
    result = Mock(status_code=status, headers=headers or {"ETag": "unchanged", "Age": "12"})
    result.json.return_value = body
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(str(status))
    return result


def markets(city="nyc"):
    event = f"{CITY_BY_KEY[city].series_ticker}-26SEP13"
    labels = ("79 or below", "80 to 81", "82 to 83", "84 to 85", "86 to 87", "88 or above")
    return {
        "markets": [
            {
                "ticker": f"{event}-B{i}",
                "event_ticker": event,
                "yes_sub_title": label,
                "yes_bid": 20,
                "yes_ask": 22,
            }
            for i, label in enumerate(labels)
        ]
    }


def responses(city="nyc"):
    return [
        response(
            {
                "properties": {
                    "forecast": "https://api.weather.gov/daily",
                    "forecastHourly": "https://api.weather.gov/hourly",
                }
            }
        ),
        response({"properties": {"periods": [{"temperature": 85}]}}),
        response({"properties": {"periods": [{"temperature": 84}]}}),
        response(markets(city)),
    ]


class TimingCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def recorder(self, run_id="run"):
        return TimingRecorder(
            "test@example.com", run_id, SNAPSHOT, "bucket", 3, state_dir=self.root / "state"
        )

    def test_quote_is_requested_after_both_forecast_receipts(self):
        recorder = self.recorder()
        with patch.object(recorder.session, "get", side_effect=responses()) as get:
            result = capture_city(recorder, CITY_BY_KEY["nyc"])
        self.assertTrue(result["post_forecast_order_verified"])
        self.assertEqual(result["market_count"], 6)
        self.assertIn("/markets", get.call_args_list[-1].args[0])
        self.assertGreaterEqual(result["quote_requested_at_utc"], result["forecast_ready_at_utc"])
        self.assertEqual(len(result["raw_payload_ids"]), 4)
        for record in recorder.raw_payloads:
            self.assertEqual(record.event_ticker, "KXHIGHNY-26SEP13")
            self.assertEqual(recorder.response_headers[record.raw_payload_id]["Age"], "12")

    def test_subhour_runs_preserve_unique_receipts_without_training_rows(self):
        paths = []
        with patch("requests.Session.get", side_effect=responses() + responses()):
            for snapshot in (SNAPSHOT, SNAPSHOT + timedelta(minutes=5)):
                paths.append(
                    collect_timing(self.root, "test", "bucket", (CITY_BY_KEY["nyc"],), snapshot)
                )
        self.assertNotEqual(paths[0], paths[1])
        first, second = map(read_json_gz, paths)
        self.assertEqual(second["snapshot_time_utc"], "2026-09-13T17:10:30+00:00")
        ids = [
            {record["raw_payload_id"] for record in payload["raw_payloads"]}
            for payload in (first, second)
        ]
        self.assertFalse(ids[0] & ids[1])
        for payload in (first, second):
            for table in (
                "events",
                "weather_snapshots",
                "market_snapshots",
                "settlements",
                "final_temperature_labels",
            ):
                self.assertEqual(payload["tables"][table], [])
            self.assertEqual(payload["tables"]["collector_runs"][0]["city_count_completed"], 1)

    def test_repeated_explicit_snapshot_still_gets_unique_run(self):
        with patch("requests.Session.get", side_effect=responses() + responses()):
            first = collect_timing(self.root, "test", "bucket", (CITY_BY_KEY["nyc"],), SNAPSHOT)
            second = collect_timing(self.root, "test", "bucket", (CITY_BY_KEY["nyc"],), SNAPSHOT)
        self.assertNotEqual(first, second)

    def test_empty_forecast_is_not_counted_complete(self):
        recorder = self.recorder()
        replies = responses()
        replies[1] = response({"properties": {"periods": []}})
        with patch.object(recorder.session, "get", side_effect=replies):
            result = capture_city(recorder, CITY_BY_KEY["nyc"])
        self.assertFalse(result["complete"])
        self.assertFalse(result["post_forecast_order_verified"])
        self.assertTrue(recorder.errors)

    def test_missing_points_still_captures_quote_and_reports_failure(self):
        recorder = self.recorder()
        with patch.object(
            recorder.session, "get", side_effect=[response({}, 503), response(markets())]
        ):
            result = capture_city(recorder, CITY_BY_KEY["nyc"])
        self.assertFalse(result["complete"])
        self.assertEqual(len(recorder.raw_payloads), 2)
        self.assertEqual(recorder.raw_payloads[-1].provider, "kalshi")

    def test_failed_city_does_not_stop_other_cities(self):
        replies = responses()
        replies[-1] = response({}, 503)
        with patch("requests.Session.get", side_effect=replies + responses("mia")):
            path = collect_timing(
                self.root, "test", "bucket", (CITY_BY_KEY["nyc"], CITY_BY_KEY["mia"]), SNAPSHOT
            )
        run = read_json_gz(path)["tables"]["collector_runs"][0]
        self.assertEqual(run["city_count_completed"], 1)
        self.assertEqual(run["city_count_attempted"], 2)
        self.assertGreater(run["provider_error_count"], 0)

    def test_paginated_markets_fail_closed(self):
        recorder = self.recorder()
        replies = responses()
        replies[-1] = response(dict(markets(), cursor="next-page"))
        with patch.object(recorder.session, "get", side_effect=replies):
            with self.assertRaisesRegex(RuntimeError, "paginated"):
                capture_city(recorder, CITY_BY_KEY["nyc"])

    def test_rate_limit_cooldown_persists_and_is_provider_specific(self):
        recorder = self.recorder()
        with patch.object(
            recorder.session, "get", return_value=response({}, 429, {"Retry-After": "900"})
        ) as get:
            self.assertIsNone(recorder.get_json("nws", "daily", "https://api.weather.gov/daily"))
            self.assertEqual(get.call_count, 1)
        other = self.recorder("next-run")
        with patch.object(other.session, "get", return_value=response({})) as get:
            self.assertIsNone(other.get_json("nws", "daily", "https://api.weather.gov/daily"))
            get.assert_not_called()
            self.assertEqual(other.get_json("kalshi", "markets", "https://example.com/markets"), {})
            get.assert_called_once()
        self.assertGreaterEqual(len(recorder.response_headers), 1)

    def test_retry_after_numeric_http_date_and_invalid(self):
        self.assertEqual(retry_after_seconds("900", SNAPSHOT), 900)
        self.assertEqual(retry_after_seconds("Sun, 13 Sep 2026 18:05:30 GMT", SNAPSHOT), 3600)
        self.assertEqual(retry_after_seconds("invalid", SNAPSHOT), 300)
        self.assertEqual(retry_after_seconds("-1", SNAPSHOT), 300)
        self.assertEqual(retry_after_seconds("inf", SNAPSHOT), 300)
        self.assertEqual(retry_after_seconds("nan", SNAPSHOT), 300)

    def test_failed_required_request_still_has_unique_receipt_and_headers(self):
        recorder = self.recorder()
        with patch.object(recorder.session, "get", return_value=response({}, 503)):
            with self.assertRaises(RuntimeError):
                recorder.get_json("kalshi", "markets", "https://example.com", required=True)
        self.assertIn(recorder.raw_payloads[0].raw_payload_id, recorder.response_headers)
        self.assertFalse(recorder.raw_payloads[0].success)

    def test_sync_failure_leaves_retryable_spool_and_archive(self):
        with patch("requests.Session.get", side_effect=responses()):
            path = collect_timing(self.root, "test", "bucket", (CITY_BY_KEY["nyc"],), SNAPSHOT)
        storage, postgres = Mock(), Mock()
        postgres.insert_facts.side_effect = RuntimeError("database unavailable")
        with self.assertRaises(RuntimeError):
            sync_timing(self.root, storage, postgres)
        self.assertTrue(path.exists())
        self.assertEqual(len(list((self.root / "archive").glob("*/*.json.gz"))), 1)
        postgres.insert_facts.side_effect = None
        self.assertEqual(sync_timing(self.root, storage, postgres), 1)
        self.assertFalse(path.exists())

    def test_sync_batch_limit_preserves_backlog(self):
        with patch("requests.Session.get", side_effect=responses() * 4):
            for index in range(4):
                collect_timing(
                    self.root,
                    "test",
                    "bucket",
                    (CITY_BY_KEY["nyc"],),
                    SNAPSHOT + timedelta(minutes=index),
                )
        self.assertEqual(sync_timing(self.root, Mock(), Mock()), 3)
        self.assertEqual(len(list((self.root / "pending").glob("*.json.gz"))), 1)


if __name__ == "__main__":
    unittest.main()
