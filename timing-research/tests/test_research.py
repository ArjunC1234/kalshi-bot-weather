from __future__ import annotations

import copy
import gzip
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from timing_research.__main__ import ROOT, check_lock, freeze, main, write_json
from timing_research.contract import (
    CITY_BY_KEY,
    city_clock,
    market_float,
    parse_bracket,
    validate_brackets,
)
from timing_research.experiment import (
    attach_outcomes,
    decide,
    exit_quote,
    fee,
    hit_lower_bound,
    interval,
    revision,
    summarize,
)
from timing_research.reader import (
    DAILY,
    HOURLY,
    MARKET,
    audit,
    canonical,
    digest,
    observation,
    read_captures,
    stamp,
)

BASE = datetime(2026, 9, 15, 12, 0, 31, tzinfo=UTC)


def protocol():
    return json.loads((ROOT / "experiments/timing_forward_v1/PROTOCOL.json").read_text())


def capture(start=BASE, high=84, version=None, depth=2):
    run_id = start.isoformat()
    raw = []
    for city, spec in CITY_BY_KEY.items():
        event = f"{spec.series_ticker}-26SEP{start.day:02}"
        labels = ["79 or below", "80 to 81", "82 to 83", "84 to 85", "86 to 87", "88 or above"]
        markets = []
        for index, label in enumerate(labels):
            market = {
                "ticker": f"{event}-B{index}",
                "event_ticker": event,
                "yes_sub_title": label,
                "status": "active",
                "close_time": (start + timedelta(hours=20)).isoformat(),
                "yes_bid": 24,
                "yes_ask": 26,
                "no_bid": 74,
                "no_ask": 76,
            }
            if depth is not None:
                market.update(
                    {
                        f"{side}_{kind}_size_fp": str(depth)
                        for side in ("yes", "no")
                        for kind in ("bid", "ask")
                    }
                )
            markets.append(market)
        forecast = {
            "properties": {
                "updateTime": (version or start - timedelta(minutes=10)).isoformat(),
                "generatedAt": (version or start - timedelta(minutes=9)).isoformat(),
                "periods": [
                    {
                        "startTime": start.replace(hour=14).isoformat(),
                        "isDaytime": True,
                        "temperature": high,
                        "temperatureUnit": "F",
                    }
                ],
            }
        }
        for index, (endpoint, body, provider) in enumerate(
            [
                ("timing_nws_points", {"properties": {}}, "nws"),
                (DAILY, copy.deepcopy(forecast), "nws"),
                (HOURLY, copy.deepcopy(forecast), "nws"),
                (MARKET, {"markets": markets}, "kalshi"),
            ]
        ):
            raw.append(
                {
                    "raw_payload_id": f"{run_id}-{city}-{index}",
                    "collector_run_id": run_id,
                    "city": city,
                    "event_ticker": event,
                    "target_date": start.date().isoformat(),
                    "endpoint_name": endpoint,
                    "provider": provider,
                    "status_code": 200,
                    "success": True,
                    "requested_at_utc": (start + timedelta(seconds=index + 0.1)).isoformat(),
                    "received_at_utc": (start + timedelta(seconds=index + 0.8)).isoformat(),
                    "payload": body,
                }
            )
    run = {
        "collector_run_id": run_id,
        "collector_version": "timing-v1",
        "raw_payload_count": 24,
        "started_at_utc": start.isoformat(),
        "completed_at_utc": (start + timedelta(seconds=5)).isoformat(),
        "snapshot_time_utc": start.isoformat(),
    }
    payload = {"tables": {"collector_runs": [run]}, "raw_payloads": raw}
    return sync_raw(payload)


def sync_raw(payload):
    payload["tables"]["raw_payloads"] = [
        {key: value for key, value in row.items() if key != "payload"}
        for row in payload["raw_payloads"]
    ]
    return payload


def obs(payload, city="nyc"):
    return observation(
        payload["tables"]["collector_runs"][0],
        city,
        [row for row in payload["raw_payloads"] if row["city"] == city],
    )


class ResearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.protocol = protocol()

    def store(self, payload, name="capture.json.gz"):
        path = self.root / name
        path.write_bytes(gzip.compress(canonical(payload), mtime=0))
        return path

    def observations(self):
        old = obs(capture())
        new = obs(capture(BASE + timedelta(minutes=5), high=86))
        future = obs(capture(BASE + timedelta(minutes=20), high=86))
        return [old, new, future]

    def test_price_and_quantity_units(self):
        self.assertEqual(market_float({"yes_bid": 1}, "yes_bid"), 0.01)
        self.assertEqual(market_float({"no_ask_size_fp": "200.50"}, "no_ask_size_fp"), 200.5)
        self.assertIsNone(
            market_float({"no_ask_dollars": "nan", "no_ask": 70}, "no_ask_dollars", "no_ask")
        )

    def test_bracket_gap_rejected(self):
        markets = capture()["raw_payloads"][3]["payload"]["markets"]
        markets[2]["yes_sub_title"] = "83 to 83"
        with self.assertRaisesRegex(ValueError, "noncontiguous"):
            validate_brackets([parse_bracket(row) for row in markets])

    def test_standard_climate_day_does_not_shift_for_dst(self):
        clock = city_clock(CITY_BY_KEY["nyc"], BASE, BASE.date())
        self.assertEqual(clock.climate_day_start_utc.hour, 5)
        with self.assertRaisesRegex(ValueError, "naive"):
            stamp("2026-09-15T12:00:00")

    def test_read_real_contract_and_deduplicate_copies(self):
        payload = capture()
        self.store(payload)
        self.store(payload, "copy.json.gz")
        data = read_captures(self.root, BASE + timedelta(hours=1))
        self.assertFalse(data["integrity_errors"])
        self.assertEqual(data["duplicate_copies_ignored"], 1)
        self.assertEqual(len(data["observations"]), 6)
        self.assertTrue(all(row["capture_valid"] for row in data["observations"]))

    def test_conflicting_duplicate_and_corrupt_spool_fail_closed(self):
        first = capture()
        second = copy.deepcopy(first)
        second["raw_payloads"][1]["payload"]["properties"]["periods"][0]["temperature"] = 99
        self.store(first)
        self.store(second, "conflict.json.gz")
        (self.root / "broken.json.gz").write_bytes(b"bad")
        data = read_captures(self.root, BASE + timedelta(hours=1))
        self.assertEqual(len(data["integrity_errors"]), 2)

    def test_duplicate_receipt_and_wrong_normalized_metadata_rejected(self):
        payload = capture()
        payload["raw_payloads"][1]["raw_payload_id"] = payload["raw_payloads"][0]["raw_payload_id"]
        self.store(sync_raw(payload))
        self.assertTrue(read_captures(self.root, BASE + timedelta(hours=1))["integrity_errors"])
        payload = capture()
        payload["tables"]["raw_payloads"][0]["success"] = False
        self.store(payload, "mismatch.json.gz")
        self.assertEqual(
            len(read_captures(self.root, BASE + timedelta(hours=1))["integrity_errors"]), 2
        )

    def test_future_availability_cutoff_excludes_unfinished_run(self):
        self.store(capture())
        self.assertEqual(read_captures(self.root, BASE + timedelta(seconds=4))["runs"], [])

    def test_inflight_quote_rejected_even_when_response_is_later(self):
        payload = capture()
        payload["raw_payloads"][3]["requested_at_utc"] = (BASE + timedelta(seconds=2)).isoformat()
        row = obs(payload)
        self.assertTrue(row["signal_valid"])
        self.assertIsNone(row["quote"])
        self.assertFalse(row["capture_valid"])

    def test_future_version_rejected(self):
        row = obs(capture(version=BASE + timedelta(hours=1)))
        self.assertFalse(row["signal_valid"])
        self.assertIn("forecast:future_forecast_version", row["issues"])

    def test_missing_depth_is_not_inferred(self):
        old, new = (
            obs(capture(depth=None)),
            obs(capture(BASE + timedelta(minutes=5), high=86, depth=None)),
        )
        decisions = decide([old, new], self.protocol)
        self.assertEqual(len(decisions["intentions"]), 1)
        candidate = decisions["intentions"][0]["probe"]
        self.assertIsNotNone(candidate)
        self.assertFalse(candidate["depth_verified"])

    def test_leading_trailing_missing_slots_are_counted(self):
        self.store(capture())
        data = read_captures(self.root, BASE + timedelta(hours=1))
        summary = audit(
            data,
            BASE.replace(second=30) - timedelta(minutes=5),
            BASE.replace(second=30) + timedelta(minutes=10),
        )
        self.assertEqual(summary["expected_cycles"], 3)
        self.assertEqual(summary["missing_cycles"], 2)

    def test_receipt_timestamp_reversal_rejected(self):
        payload = capture()
        payload["raw_payloads"][0]["requested_at_utc"] = (BASE - timedelta(seconds=1)).isoformat()
        self.store(sync_raw(payload))
        self.assertTrue(read_captures(self.root, BASE + timedelta(hours=1))["integrity_errors"])

    def test_changed_without_new_version_and_cross_event_rejected(self):
        old = obs(capture())
        new = obs(
            capture(BASE + timedelta(minutes=5), high=86, version=BASE - timedelta(minutes=10))
        )
        new["generated"] = old["generated"]
        self.assertEqual(revision(old, new, self.protocol)[1], "changed_without_new_version")
        new["event"] += "-other"
        self.assertEqual(revision(old, new, self.protocol)[1], "different_event")

    def test_nonadjacent_or_stale_forecasts_rejected(self):
        old, new, _ = self.observations()
        new["ready"] += timedelta(minutes=15)
        self.assertEqual(revision(old, new, self.protocol)[1], "nonadjacent_receipts")
        new["ready"] -= timedelta(minutes=15)
        old["source_age_minutes"] = 91
        self.assertEqual(revision(old, new, self.protocol)[1], "stale_forecast")

    def test_first_revision_consumed_even_without_entry(self):
        old, new, later = self.observations()
        new["quote"] = None
        later = obs(capture(BASE + timedelta(minutes=10), high=88))
        decisions = decide([old, new, later], self.protocol)
        self.assertEqual(len(decisions["intentions"]), 1)
        self.assertEqual(decisions["intentions"][0]["probe_reason"], "missing_entry")

    def test_future_quotes_and_outcomes_do_not_change_decisions(self):
        observations = self.observations()
        before = digest(decide(observations[:2], self.protocol))
        future = copy.deepcopy(observations[2])
        future["quote"]["centroid"] = 5
        after = decide(observations[:2] + [future], self.protocol)
        self.assertEqual(
            before,
            digest(
                {
                    **after,
                    "transitions": after["transitions"][
                        : len(decide(observations[:2], self.protocol)["transitions"])
                    ],
                }
            ),
        )

    def test_exit_horizons_use_timestamps_not_row_offsets(self):
        _, new, later = self.observations()
        self.assertIsNotNone(exit_quote(new["quote"], [later["quote"]], 15, 90))
        self.assertIsNone(exit_quote(new["quote"], [later["quote"]], 5, 90))
        later["quote"]["rows"][0]["ticker"] = "changed"
        self.assertIsNone(exit_quote(new["quote"], [later["quote"]], 15, 90))

    def test_controls_use_prior_days_with_already_available_outcomes(self):
        observations = []
        for day in range(3):
            for minutes in (0, 5, 20):
                observations.append(obs(capture(BASE + timedelta(days=day, minutes=minutes))))
        observations += [
            obs(capture(BASE + timedelta(days=3))),
            obs(capture(BASE + timedelta(days=3, minutes=5), high=86)),
        ]
        before = decide(observations, self.protocol)["intentions"]
        self.assertEqual(len(before), 1)
        self.assertEqual(len(before[0]["baseline_controls"]), 3)
        self.assertIsNotNone(before[0]["baseline_change"])
        self.assertTrue(
            all(
                row["available_at"] < before[0]["signal_time"]
                for row in before[0]["baseline_controls"]
            )
        )
        later = obs(capture(BASE + timedelta(days=3, minutes=20), high=86))
        later["quote"]["centroid"] = 5
        self.assertEqual(
            digest(before), digest(decide(observations + [later], self.protocol)["intentions"])
        )

    def test_cli_decision_and_scoring_artifacts_round_trip(self):
        write_json(self.root / "PROTOCOL.json", self.protocol)
        (self.root / "PROTOCOL.md").write_text("Fixed test protocol")
        freeze(self.root, BASE - timedelta(days=2))
        for minutes, high in ((0, 84), (5, 86), (20, 86)):
            self.store(capture(BASE + timedelta(minutes=minutes), high), f"{minutes}.json.gz")
        with patch("timing_research.__main__.datetime") as clock:
            clock.now.return_value = stamp(self.protocol["score_not_before"])
            self.assertEqual(
                main(
                    [
                        "decide",
                        "--experiment",
                        str(self.root),
                        "--data",
                        str(self.root),
                        "--as-of",
                        self.protocol["end"],
                        "--output",
                        str(self.root / "sealed"),
                    ]
                ),
                0,
            )
            sealed = (self.root / "sealed/DECISIONS.json").read_bytes()
            self.assertEqual(
                main(
                    [
                        "score",
                        "--experiment",
                        str(self.root),
                        "--data",
                        str(self.root),
                        "--decisions",
                        str(self.root / "sealed"),
                        "--output",
                        str(self.root / "results"),
                    ]
                ),
                0,
            )
            self.assertEqual((self.root / "sealed/DECISIONS.json").read_bytes(), sealed)
        result = json.loads((self.root / "results/SUMMARY.json").read_text())
        self.assertEqual(result["status"], "not_promoted")
        self.assertEqual(len(result["daily"]), 14)

    def test_costs_and_unresolved_labels(self):
        observations = self.observations()
        decisions = decide(observations, self.protocol)
        scored = attach_outcomes(decisions, observations, [], self.protocol)
        row = next(row for row in scored if row["kind"] == "revision")
        self.assertIsNone(row["settlement"])
        result = row["horizons"]["15"]
        self.assertAlmostEqual(result["net_markout"], 0.73 - 0.77 - fee(0.73, 1) - fee(0.77, 1))
        self.assertEqual(fee(0.5, 1), 0.02)
        self.assertLessEqual(result["fee_stress_net_markout"], result["net_markout"])

    def test_valid_settlement_is_scored_without_mutating_decision(self):
        rows = self.observations()
        decisions = decide(rows, self.protocol)
        before = digest(decisions)
        probe = decisions["intentions"][0]["probe"]
        label = {
            "event_ticker": rows[1]["event"],
            "winner_ticker": probe["ticker"],
            "source_provider": "kalshi",
            "validation_status": "valid",
            "raw_payload_id": "verified-label",
            "settled_at_utc": "2026-09-16T12:00:00Z",
        }
        outcome = attach_outcomes(decisions, rows, [label], self.protocol)[0]["settlement"]
        self.assertEqual(outcome["hit"], 0)
        self.assertAlmostEqual(outcome["net_pnl"], -outcome["debit"])
        self.assertEqual(digest(decisions), before)

    def test_hit_bound_does_not_certify_all_wins_in_tiny_sample(self):
        self.assertAlmostEqual(hit_lower_bound(10, 10), 0.05**0.1)
        self.assertLess(hit_lower_bound(10, 10), 0.8)
        self.assertIsNone(hit_lower_bound(0, 0))
        self.assertEqual(hit_lower_bound(0, 10), 0)

    def test_failed_audit_returns_nonzero(self):
        result = main(
            [
                "audit",
                "--data",
                str(self.root),
                "--start",
                "2026-09-14T02:20:30Z",
                "--end",
                "2026-09-14T02:25:30Z",
                "--as-of",
                "2026-09-14T03:00:00Z",
                "--output",
                str(self.root / "audit"),
            ]
        )
        self.assertEqual(result, 1)

    def test_missing_exit_depth_cannot_score_profit(self):
        rows = self.observations()
        for market in rows[2]["quote"]["rows"]:
            market["no_bid_size"] = None
        result = attach_outcomes(decide(rows, self.protocol), rows, [], self.protocol)[0]
        self.assertIsNotNone(result["horizons"]["15"]["price_change"])
        self.assertIsNone(result["horizons"]["15"]["net_markout"])

    def test_conflicting_settlement_labels_rejected(self):
        rows = self.observations()
        event = rows[1]["event"]
        labels = [
            {
                "event_ticker": event,
                "source_provider": "kalshi",
                "validation_status": "valid",
                "settled_at_utc": "2026-09-16T12:00:00Z",
                "winner_ticker": f"{event}-B{i}",
            }
            for i in (1, 2)
        ]
        with self.assertRaisesRegex(ValueError, "conflicting_settlement"):
            attach_outcomes(decide(rows, self.protocol), rows, labels, self.protocol)

    def test_empty_report_has_fourteen_days_and_fails_promotion(self):
        quality = {"integrity_errors": [], "cycle_coverage": 1, "valid_city_cycle_coverage": 1}
        summary = summarize(
            {"intentions": [], "controls": [], "transitions": []}, [], quality, self.protocol
        )
        self.assertEqual(len(summary["daily"]), 14)
        self.assertEqual(summary["status"], "not_promoted")
        self.assertIsNone(summary["hit_rate"])

    def test_day_bootstrap_is_reproducible(self):
        rows = [{"target_date": "2026-09-15", "x": 1}, {"target_date": "2026-09-16", "x": -1}]
        result = interval(rows, "x", self.protocol)
        self.assertEqual(result, interval(rows, "x", self.protocol))
        self.assertEqual(result["days"], 2)
        self.assertEqual(result["mean"], 0)

    def test_lock_is_exclusive_and_detects_protocol_tampering(self):
        write_json(self.root / "PROTOCOL.json", self.protocol)
        (self.root / "PROTOCOL.md").write_text("Fixed test protocol")
        freeze(self.root, BASE - timedelta(days=2))
        check_lock(self.root)
        with self.assertRaises(FileExistsError):
            freeze(self.root, BASE - timedelta(days=2))
        changed = dict(self.protocol, material_revision_f=2)
        (self.root / "PROTOCOL.json").write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "altered"):
            check_lock(self.root)

    def test_late_freeze_and_early_scoring_refused(self):
        write_json(self.root / "PROTOCOL.json", self.protocol)
        (self.root / "PROTOCOL.md").write_text("Fixed test protocol")
        with self.assertRaisesRegex(ValueError, "after"):
            freeze(self.root, BASE)
        freeze(self.root, BASE - timedelta(days=2))
        with patch("timing_research.__main__.datetime") as clock:
            clock.now.return_value = BASE
            with self.assertRaisesRegex(ValueError, "embargoed"):
                main(
                    [
                        "score",
                        "--experiment",
                        str(self.root),
                        "--data",
                        str(self.root),
                        "--output",
                        str(self.root / "out"),
                        "--decisions",
                        str(self.root / "decisions"),
                    ]
                )


if __name__ == "__main__":
    unittest.main()
