from __future__ import annotations

import math
import tempfile
import unittest
from unittest import mock
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from weather_backtest import (
    ReplayHttpClient,
    checkpoint_schedule,
    collect_due,
    date_cluster_bootstrap,
    ensure_manifest,
    missing_path,
    paired_checkpoint_deltas,
    read_json_gz,
    score_probabilities,
    validate_settlement,
    validate_trace_observation_times,
    write_json_gz_immutable,
)
from weather_probabilities import CITIES, DataError


class ScheduleTests(unittest.TestCase):
    def test_fixed_standard_time_schedule_does_not_move_for_dst(self) -> None:
        expected_hours = {"nyc": 5, "mia": 5, "la": 8, "den": 7, "aus": 6, "okc": 6}
        for city in CITIES:
            winter = checkpoint_schedule(city, date(2026, 1, 15))
            summer = checkpoint_schedule(city, date(2026, 7, 15))
            self.assertEqual(winter[0].scheduled_at.hour, (expected_hours[city.key] - 6) % 24)
            self.assertEqual(summer[0].scheduled_at.hour, (expected_hours[city.key] - 6) % 24)
            self.assertEqual(winter[1].scheduled_at.hour, (expected_hours[city.key] + 6) % 24)
            self.assertEqual(summer[1].scheduled_at.hour, (expected_hours[city.key] + 6) % 24)

    def test_missed_checkpoint_is_immutable_and_not_backfilled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkpoint = checkpoint_schedule(CITIES[0], date(2026, 6, 21))[1]
            ensure_manifest(root, "test", checkpoint.scheduled_at)
            collect_due(
                root,
                "test",
                "unit-test@example.com",
                now=checkpoint.scheduled_at + timedelta(minutes=6),
            )
            path = missing_path(root / "cohorts" / "test", checkpoint)
            self.assertTrue(path.exists())
            first = read_json_gz(path)
            collect_due(
                root,
                "test",
                "unit-test@example.com",
                now=checkpoint.scheduled_at + timedelta(minutes=20),
            )
            self.assertEqual(read_json_gz(path), first)

    def test_first_run_includes_checkpoint_inside_grace_window(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = checkpoint_schedule(CITIES[0], date(2026, 6, 21))[1]
            with mock.patch("weather_backtest.capture_checkpoint") as capture:
                capture.return_value = Path("snapshot.json.gz")
                result = collect_due(
                    Path(temporary),
                    "test",
                    "unit-test@example.com",
                    now=checkpoint.scheduled_at + timedelta(minutes=2),
                )
            self.assertEqual(result, 0)
            self.assertGreaterEqual(capture.call_count, 1)
            captured_city_keys = {call.args[2].city.key for call in capture.call_args_list}
            self.assertIn("nyc", captured_city_keys)


class StorageTests(unittest.TestCase):
    def test_immutable_gzip_json_rejects_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "artifact.json.gz"
            write_json_gz_immutable(path, {"value": 1})
            self.assertEqual(read_json_gz(path), {"value": 1})
            with self.assertRaises(FileExistsError):
                write_json_gz_immutable(path, {"value": 2})


class ReplayTests(unittest.TestCase):
    def test_replay_requires_exact_url_params_and_full_consumption(self) -> None:
        records = [
            {"url": "one", "params": {"x": 1}, "payload": {"value": 1}},
            {"url": "two", "params": None, "payload": {"value": 2}},
        ]
        replay = ReplayHttpClient(records)
        self.assertEqual(replay.get_json("one", {"x": 1}), {"value": 1})
        with self.assertRaises(DataError):
            replay.assert_consumed()
        self.assertEqual(replay.get_json("two"), {"value": 2})
        replay.assert_consumed()

        mismatch = ReplayHttpClient(records)
        with self.assertRaises(DataError):
            mismatch.get_json("wrong", {"x": 1})

    def test_rejects_observations_after_as_of(self) -> None:
        records = [
            {
                "url": "https://api.weather.gov/stations/KNYC/observations",
                "payload": {
                    "features": [
                        {"properties": {"timestamp": "2026-06-21T12:01:00Z"}}
                    ]
                },
            }
        ]
        with self.assertRaises(DataError):
            validate_trace_observation_times(
                records, datetime(2026, 6, 21, 12, tzinfo=UTC)
            )


class SettlementTests(unittest.TestCase):
    @staticmethod
    def snapshot() -> dict[str, object]:
        return {
            "distribution": {
                "brackets": [
                    {"ticker": "LOW"},
                    {"ticker": "MID"},
                    {"ticker": "HIGH"},
                ]
            }
        }

    def test_requires_exactly_one_yes_and_all_expected_contracts(self) -> None:
        result = validate_settlement(
            self.snapshot(),
            [
                {"ticker": "LOW", "result": "no"},
                {"ticker": "MID", "result": "yes"},
                {"ticker": "HIGH", "result": "no"},
            ],
        )
        self.assertEqual(result["winner_ticker"], "MID")

        with self.assertRaises(DataError):
            validate_settlement(
                self.snapshot(),
                [
                    {"ticker": "LOW", "result": "yes"},
                    {"ticker": "MID", "result": "yes"},
                    {"ticker": "HIGH", "result": "no"},
                ],
            )
        with self.assertRaises(DataError):
            validate_settlement(
                self.snapshot(),
                [
                    {"ticker": "LOW", "result": "no"},
                    {"ticker": "MID", "result": "yes"},
                ],
            )


class ScoringTests(unittest.TestCase):
    def test_probabilistic_scores_and_top_one(self) -> None:
        score = score_probabilities(["A", "B", "C"], [0.7, 0.2, 0.1], "A")
        self.assertTrue(math.isclose(score["log_loss"], -math.log(0.7)))
        self.assertTrue(math.isclose(score["brier"], 0.14))
        self.assertTrue(math.isclose(score["ranked_probability_score"], 0.05))
        self.assertTrue(score["top_one_covered"])
        self.assertTrue(score["top_one_correct"])

    def test_tie_has_no_top_one_coverage_and_zero_probability_is_explicit(self) -> None:
        tied = score_probabilities(["A", "B"], [0.5, 0.5], "A")
        self.assertFalse(tied["top_one_covered"])
        self.assertIsNone(tied["top_one_correct"])
        zero = score_probabilities(["A", "B"], [1.0, 0.0], "B")
        self.assertTrue(zero["zero_probability"])
        self.assertIsNone(zero["log_loss"])

    def test_paired_deltas_match_same_event_only(self) -> None:
        rows = [
            {
                "model": "full",
                "city": "nyc",
                "target_date": "2026-06-21",
                "checkpoint": "t_minus_6h",
                "log_loss": 1.0,
                "brier": 0.5,
                "ranked_probability_score": 0.3,
            },
            {
                "model": "full",
                "city": "nyc",
                "target_date": "2026-06-21",
                "checkpoint": "t_plus_6h",
                "log_loss": 0.8,
                "brier": 0.4,
                "ranked_probability_score": 0.2,
            },
        ]
        result = paired_checkpoint_deltas(rows)[0]
        self.assertEqual(result["pair_count"], 1)
        self.assertTrue(math.isclose(result["log_loss_delta"], -0.2))
        self.assertTrue(math.isclose(result["ranked_probability_score_delta"], -0.1))

    def test_date_cluster_bootstrap_is_deterministic(self) -> None:
        rows = [
            {"target_date": "2026-06-20", "metric": 0.1},
            {"target_date": "2026-06-20", "metric": 0.2},
            {"target_date": "2026-06-21", "metric": 0.7},
            {"target_date": "2026-06-21", "metric": 0.8},
        ]
        first = date_cluster_bootstrap(rows, "metric", 200)
        second = date_cluster_bootstrap(rows, "metric", 200)
        self.assertEqual(first, second)
        self.assertIsNotNone(first)


if __name__ == "__main__":
    unittest.main()
