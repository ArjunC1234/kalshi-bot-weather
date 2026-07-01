from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from supabase_hourly_collector import (
    CITIES,
    Bracket,
    HttpRecorder,
    RawPayload,
    deterministic_id,
    gzip_json_bytes,
    mark_synced,
    market_feature_rows,
    read_json_gz,
    raw_payload_db_row,
    raw_storage_path,
    status,
    sync_spool,
    write_json_gz_immutable,
)


class SupabaseHourlyCollectorTests(unittest.TestCase):
    def test_deterministic_id_is_stable(self) -> None:
        first = deterministic_id("market", "den", "event", "hour", "ticker")
        second = deterministic_id("market", "den", "event", "hour", "ticker")
        other = deterministic_id("market", "den", "event", "hour", "other")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(len(first), 32)

    def test_gzip_json_bytes_is_deterministic(self) -> None:
        payload = {"b": 2, "a": [1, 2, 3]}
        first = gzip_json_bytes(payload)
        second = gzip_json_bytes(payload)
        self.assertEqual(first, second)
        self.assertEqual(json.loads(gzip.decompress(first)), {"a": [1, 2, 3], "b": 2})

    def test_raw_storage_path_includes_identity_parts(self) -> None:
        path = raw_storage_path(
            "nws",
            "2026-07-01",
            "den",
            "KXHIGHDEN-26JUL01",
            "2026-07-01T17:00:00Z",
            "abc123",
        )
        self.assertEqual(
            path,
            "raw/nws/2026-07-01/den/KXHIGHDEN-26JUL01/20260701T170000Z/abc123.json.gz",
        )

    def test_market_feature_rows_are_compact_and_normalized(self) -> None:
        city = CITIES[0]
        snapshot_hour = datetime(2026, 7, 1, 17, tzinfo=UTC)
        brackets = (
            Bracket("A", "80 or below", None, 80),
            Bracket("B", "81 to 82", 81, 82),
            Bracket("C", "83 or above", 83, None),
        )
        markets = [
            {
                "ticker": "A",
                "yes_bid_dollars": "0.10",
                "yes_ask_dollars": "0.20",
                "volume_fp": "12.5",
                "volume_24h_fp": "3.5",
                "yes_bid_size_fp": "11",
                "yes_ask_size_fp": "7",
            },
            {"ticker": "B", "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.40"},
            {"ticker": "C", "yes_bid_dollars": "0.20", "yes_ask_dollars": "0.30"},
        ]
        rows = market_feature_rows(
            "run",
            snapshot_hour,
            city,
            snapshot_hour.date(),
            "EVENT",
            brackets,
            markets,
            "raw",
        )
        self.assertEqual(len(rows), 3)
        self.assertAlmostEqual(
            sum(float(row["normalized_market_midpoint_probability"]) for row in rows),
            1.0,
        )
        self.assertEqual(rows[1]["market_top_ticker"], "B")
        self.assertAlmostEqual(rows[1]["yes_spread"], 0.10)
        self.assertAlmostEqual(rows[0]["volume"], 12.5)
        self.assertAlmostEqual(rows[0]["volume_24h"], 3.5)
        self.assertAlmostEqual(rows[0]["no_bid_size"], 7.0)
        self.assertAlmostEqual(rows[0]["no_ask_size"], 11.0)
        self.assertTrue(rows[0]["metadata"]["no_bid_size_inferred_from_yes_ask_size"])

    def test_retag_latest_payload_replaces_unknown_event_path(self) -> None:
        snapshot_hour = datetime(2026, 7, 1, 19, tzinfo=UTC)
        recorder = HttpRecorder("test@example.com", "run", snapshot_hour, "bucket")
        original = recorder.record_payload(
            "kalshi",
            "kalshi_open_markets",
            "https://example.com",
            {},
            snapshot_hour,
            snapshot_hour,
            200,
            {"markets": []},
            "den",
            None,
            None,
        )
        self.assertIn("unknown-date/den/unknown-event", original.storage_path)

        updated = recorder.retag_latest_payload(
            "kalshi", "kalshi_open_markets", "den", "KXHIGHDEN-26JUL01", "2026-07-01"
        )

        self.assertIsNotNone(updated)
        self.assertIn("2026-07-01/den/KXHIGHDEN-26JUL01", updated.storage_path)
        self.assertEqual(updated.event_ticker, "KXHIGHDEN-26JUL01")
        self.assertEqual(updated.target_date, "2026-07-01")
        self.assertNotEqual(updated.raw_payload_id, original.raw_payload_id)

    def test_raw_payload_db_row_excludes_payload_body(self) -> None:
        record = RawPayload(
            raw_payload_id="raw",
            collector_run_id="run",
            city="den",
            event_ticker="event",
            target_date="2026-07-01",
            snapshot_hour_utc="2026-07-01T17:00:00+00:00",
            provider="nws",
            endpoint_name="nws_points",
            method="GET",
            url="https://example.com",
            params={},
            requested_at_utc="2026-07-01T17:00:00+00:00",
            received_at_utc="2026-07-01T17:00:01+00:00",
            latency_ms=1000,
            status_code=200,
            success=True,
            error_type=None,
            error_message=None,
            content_sha256="hash",
            compressed_size_bytes=20,
            storage_bucket="bucket",
            storage_path="path",
            schema_version=1,
            payload={"large": "body"},
        )
        row = raw_payload_db_row(record)
        self.assertNotIn("payload", row)
        self.assertEqual(row["raw_payload_id"], "raw")

    def test_spool_status_counts_pending_and_synced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pending = root / "pending" / "one.json.gz"
            write_json_gz_immutable(pending, {"ok": True})
            self.assertEqual(read_json_gz(pending), {"ok": True})
            before = status(root)
            self.assertEqual(before["pending_spool_files"], 1)
            mark_synced(pending, root)
            after = status(root)
            self.assertEqual(after["pending_spool_files"], 0)
            self.assertEqual(after["synced_spool_files"], 1)

    def test_sync_replaces_existing_child_rows_for_retry(self) -> None:
        class FakeSupabaseClient:
            def __init__(self) -> None:
                self.actions: list[tuple[str, str, str | int]] = []

            def upload_raw_payload(self, record: dict) -> None:
                self.actions.append(("upload", record["raw_payload_id"], ""))

            def upsert(self, table: str, rows: list[dict]) -> None:
                if not rows:
                    return
                self.actions.append(("upsert", table, len(rows)))

            def delete_run_rows(self, table: str, collector_run_id: str) -> None:
                self.actions.append(("delete", table, collector_run_id))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spool = {
                "raw_payloads": [{"raw_payload_id": "raw-1"}],
                "tables": {
                    "collector_runs": [
                        {"collector_run_id": "run-1", "spool_status": "pending"}
                    ],
                    "raw_payloads": [{"raw_payload_id": "raw-1", "collector_run_id": "run-1"}],
                    "events": [{"event_id": "event-1", "collector_run_id": "run-1"}],
                    "market_snapshots": [],
                    "weather_snapshots": [],
                    "model_outputs": [],
                    "settlements": [],
                    "provider_errors": [],
                },
            }
            write_json_gz_immutable(root / "pending" / "retry.json.gz", spool)
            client = FakeSupabaseClient()

            synced = sync_spool(root, client)  # type: ignore[arg-type]

            self.assertEqual(synced, 1)
            self.assertEqual(client.actions[0], ("upload", "raw-1", ""))
            self.assertEqual(client.actions[1], ("upsert", "collector_runs", 1))
            self.assertIn(("delete", "provider_errors", "run-1"), client.actions)
            self.assertIn(("delete", "raw_payloads", "run-1"), client.actions)
            self.assertGreater(
                client.actions.index(("upsert", "raw_payloads", 1)),
                client.actions.index(("delete", "raw_payloads", "run-1")),
            )


if __name__ == "__main__":
    unittest.main()
