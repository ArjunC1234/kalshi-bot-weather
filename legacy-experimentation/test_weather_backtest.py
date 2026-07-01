from __future__ import annotations

import math
import json
import gzip
import tempfile
import unittest
from unittest import mock
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from weather_backtest import (
    ReplayHttpClient,
    SNAPSHOT_SCHEMA_VERSION,
    challenger_distributions,
    checkpoint_schedule,
    collect_due,
    date_cluster_bootstrap,
    derive_source_metadata,
    ensure_manifest,
    fetch_hrrr_guidance,
    hrrr_top3_rerank_distribution,
    missing_path,
    paired_checkpoint_deltas,
    paired_model_deltas,
    read_json_gz,
    score_probabilities,
    source_metadata,
    validate_settlement,
    validate_trace_observation_times,
    write_json_gz_immutable,
)
from weather_probabilities import CITIES, ENSEMBLE_MODELS, DataError


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


class HrrrGuidanceTests(unittest.TestCase):
    class FakeHttp:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload
            self.calls: list[tuple[str, dict[str, object] | None]] = []

        def get_json(self, url: str, params: dict[str, object] | None = None) -> dict[str, object]:
            self.calls.append((url, params))
            return self.payload

    def test_hrrr_guidance_uses_remaining_forecast_and_observed_floor(self) -> None:
        payload = {
            "hourly": {
                "time": [
                    "2026-06-21T05:00",
                    "2026-06-21T06:00",
                    "2026-06-21T07:00",
                ],
                "temperature_2m": [80.0, 83.0, 82.0],
            }
        }
        guidance = fetch_hrrr_guidance(
            self.FakeHttp(payload),  # type: ignore[arg-type]
            CITIES[0],
            datetime(2026, 6, 21, 5, tzinfo=UTC),
            datetime(2026, 6, 21, 8, tzinfo=UTC),
            observed_high_f=84.0,
            observed_at=datetime(2026, 6, 21, 6, tzinfo=UTC),
            as_of=datetime(2026, 6, 21, 6, tzinfo=UTC),
        )
        self.assertEqual(guidance["full_window_high_f"], 83.0)
        self.assertEqual(guidance["remaining_forecast_high_f"], 82.0)
        self.assertEqual(guidance["projected_high_f"], 84.0)

    def test_hrrr_top3_rerank_moves_mass_toward_hrrr_bracket(self) -> None:
        snapshot = {
            "distribution": {
                "probabilities": [0.05, 0.30, 0.34, 0.31, 0.00],
                "bandwidth_f": 1.0,
                "member_highs_f": [79.0, 80.0],
                "brackets": [
                    {"ticker": "L", "label": "77 or below", "lower": None, "upper": 77},
                    {"ticker": "M1", "label": "78 to 79", "lower": 78, "upper": 79},
                    {"ticker": "M2", "label": "80 to 81", "lower": 80, "upper": 81},
                    {"ticker": "M3", "label": "82 to 83", "lower": 82, "upper": 83},
                    {"ticker": "H", "label": "84 or above", "lower": 84, "upper": None},
                ],
            },
            "auxiliary_inputs": {"open_meteo_hrrr": {"projected_high_f": 79.0}},
        }
        variant = hrrr_top3_rerank_distribution(snapshot)
        self.assertIsNotNone(variant)
        probabilities = variant["probabilities"]  # type: ignore[index]
        self.assertTrue(math.isclose(sum(probabilities), 1.0))  # type: ignore[arg-type]
        self.assertGreater(probabilities[1], probabilities[2])  # type: ignore[index]
        self.assertEqual(variant["hrrr_bracket_index"], 1)  # type: ignore[index]
        self.assertEqual(variant["hrrr_centered_top3_indexes"], [0, 1, 2])  # type: ignore[index]
        self.assertEqual(
            variant["hrrr_centered_original_probabilities"],  # type: ignore[index]
            [0.05, 0.30, 0.34],
        )

    def test_hrrr_top3_uses_neighbors_not_baseline_top_three(self) -> None:
        snapshot = {
            "distribution": {
                "probabilities": [0.01, 0.02, 0.03, 0.45, 0.49],
                "bandwidth_f": 1.0,
                "member_highs_f": [79.0, 80.0],
                "brackets": [
                    {"ticker": "L", "label": "77 or below", "lower": None, "upper": 77},
                    {"ticker": "M1", "label": "78 to 79", "lower": 78, "upper": 79},
                    {"ticker": "M2", "label": "80 to 81", "lower": 80, "upper": 81},
                    {"ticker": "M3", "label": "82 to 83", "lower": 82, "upper": 83},
                    {"ticker": "H", "label": "84 or above", "lower": 84, "upper": None},
                ],
            },
            "auxiliary_inputs": {"open_meteo_hrrr": {"projected_high_f": 79.0}},
        }
        variant = hrrr_top3_rerank_distribution(snapshot)
        self.assertIsNotNone(variant)
        self.assertEqual(variant["hrrr_centered_top3_indexes"], [0, 1, 2])  # type: ignore[index]


class ChallengerTests(unittest.TestCase):
    @staticmethod
    def synthetic_snapshot() -> dict[str, object]:
        hourly: dict[str, object] = {
            "time": ["2026-06-21T05:00", "2026-06-21T06:00"]
        }
        family_bases = {
            "GEFS": 70.0,
            "ECMWF IFS": 75.0,
            "ICON EPS": 80.0,
            "GEM": 85.0,
        }
        for family, (_, suffix) in ENSEMBLE_MODELS.items():
            for index in range(10):
                member = "" if index == 0 else f"_member{index:02d}"
                high = family_bases[family] + index * 0.2
                hourly[f"temperature_2m{member}_{suffix}"] = [high - 2, high]
        return {
            "schema_version": 1,
            "checkpoint": {"as_of": "2026-06-20T23:00:00Z"},
            "event": {
                "window_start": "2026-06-21T05:00:00Z",
                "window_end": "2026-06-22T05:00:00Z",
            },
            "distribution": {
                "nws_high_f": 80.0,
                "observed_high_f": None,
                "observed_at": None,
                "brackets": [
                    {"ticker": "L", "label": "77 or below", "lower": None, "upper": 77},
                    {"ticker": "M1", "label": "78 to 79", "lower": 78, "upper": 79},
                    {"ticker": "M2", "label": "80 to 81", "lower": 80, "upper": 81},
                    {"ticker": "H", "label": "82 or above", "lower": 82, "upper": None},
                ],
            },
            "http_trace": [
                {
                    "url": "https://ensemble-api.open-meteo.com/v1/ensemble",
                    "payload": {"hourly": hourly},
                }
            ],
        }

    def test_family_centering_preserves_spread_and_equal_family_weights(self) -> None:
        variants, diagnostics = challenger_distributions(self.synthetic_snapshot())
        self.assertEqual(set(variants), {
            "family_centered",
            "gefs_centered",
            "ecmwf_ifs_centered",
            "icon_eps_centered",
            "gem_centered",
        })
        for row in diagnostics:
            self.assertTrue(math.isclose(row["centered_median_f"], 80.0))
            original_spread = row["maximum_high_f"] - row["minimum_high_f"]
            centered_spread = (
                row["centered_maximum_high_f"] - row["centered_minimum_high_f"]
            )
            self.assertTrue(math.isclose(original_spread, centered_spread))
        self.assertEqual(
            variants["family_centered"]["family_total_weights"],
            {"GEFS": 0.25, "ECMWF IFS": 0.25, "ICON EPS": 0.25, "GEM": 0.25},
        )
        self.assertTrue(
            math.isclose(sum(variants["family_centered"]["probabilities"]), 1.0)
        )

    def test_schema_v1_metadata_derivation_and_schema_v2_round_trip(self) -> None:
        snapshot = {
            "schema_version": 1,
            "checkpoint": {"as_of": "2026-06-21T12:00:00Z"},
            "http_trace": [
                {
                    "url": "https://api.weather.gov/gridpoints/BOU/1,1/forecast",
                    "requested_at": "2026-06-21T12:00:01Z",
                    "received_at": "2026-06-21T12:00:02Z",
                    "payload": {
                        "properties": {
                            "updateTime": "2026-06-21T10:00:00Z",
                            "generatedAt": "2026-06-21T11:59:00Z",
                        }
                    },
                },
                {
                    "url": "https://api.weather.gov/stations/KDEN/observations",
                    "requested_at": "2026-06-21T12:00:02Z",
                    "received_at": "2026-06-21T12:00:03Z",
                    "payload": {
                        "features": [
                            {"properties": {"timestamp": "2026-06-21T11:40:00Z"}}
                        ]
                    },
                },
            ],
        }
        metadata = derive_source_metadata(snapshot)
        self.assertEqual(metadata["nws_daily"]["update_age_seconds_at_receipt"], 7202.0)
        self.assertEqual(
            metadata["nws_observations"]["observation_age_seconds_at_checkpoint"],
            1200.0,
        )
        snapshot["schema_version"] = SNAPSHOT_SCHEMA_VERSION
        snapshot["source_metadata"] = metadata
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json.gz"
            write_json_gz_immutable(path, snapshot)
            loaded = read_json_gz(path)
        self.assertEqual(source_metadata(loaded), metadata)

    def test_paired_model_delta_uses_identical_event_checkpoint(self) -> None:
        rows = [
            {
                "model": "full",
                "city": "den",
                "target_date": "2026-06-20",
                "checkpoint": "t_plus_10h",
                "log_loss": 1.0,
                "brier": 0.5,
                "ranked_probability_score": 0.3,
            },
            {
                "model": "family_centered",
                "city": "den",
                "target_date": "2026-06-20",
                "checkpoint": "t_plus_10h",
                "log_loss": 0.8,
                "brier": 0.4,
                "ranked_probability_score": 0.2,
            },
        ]
        result = paired_model_deltas(rows, 100)[0]
        self.assertEqual(result["challenger"], "family_centered")
        self.assertEqual(result["pair_count"], 1)
        self.assertTrue(math.isclose(result["log_loss_delta"], -0.2))
        self.assertFalse(result["automatic_promotion"])


DENVER_SNAPSHOT = Path(
    "backtest_data/cohorts/pilot-v1/snapshots/2026-06-20/den/t_plus_10h.json.gz"
)


@unittest.skipUnless(DENVER_SNAPSHOT.exists(), "live Denver v1 snapshot is unavailable")
class DenverSnapshotRegressionTests(unittest.TestCase):
    def test_v1_metadata_and_family_centered_tail_regression(self) -> None:
        with gzip.open(DENVER_SNAPSHOT, "rt", encoding="utf-8") as handle:
            snapshot = json.load(handle)
        self.assertEqual(snapshot["schema_version"], 1)
        metadata = source_metadata(snapshot)
        self.assertEqual(metadata["nws_daily"]["update_time"], "2026-06-20T06:46:27+00:00")
        variants, _ = challenger_distributions(snapshot)
        probabilities = variants["family_centered"]["probabilities"]
        outer_tails = probabilities[0] + probabilities[-2] + probabilities[-1]
        self.assertTrue(math.isclose(outer_tails, 0.2561, abs_tol=5e-5))


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
