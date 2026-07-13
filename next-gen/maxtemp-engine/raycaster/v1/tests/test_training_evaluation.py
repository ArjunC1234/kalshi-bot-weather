from __future__ import annotations

# ruff: noqa: E402
import csv
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

RAYCASTER_V1 = Path(__file__).resolve().parents[1]
NEXT_GEN = Path(__file__).resolve().parents[4]
for path in (NEXT_GEN, RAYCASTER_V1):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from benchmark import benchmark_expanding_window
from evaluate import evaluate_expanding_window
from features import build_feature_rows
from train import (
    ACTIVE_CATEGORICAL_FEATURES,
    ACTIVE_NUMERIC_FEATURES,
    PREDICTION_BLEND_WEIGHT,
    SAMPLE_WEIGHTING,
    TARGET_MODE,
    load_model,
    save_model,
    train_raycaster_model,
)

from libs.models import (
    BacktestDataset,
    Bracket,
    EventSnapshot,
    MarketSnapshot,
    Settlement,
    WeatherSnapshot,
)


class TrainingEvaluationTests(unittest.TestCase):
    def test_fallback_before_minimum_training_events(self) -> None:
        rows = build_feature_rows(_dataset())
        model = train_raycaster_model(rows, min_training_events=99)
        self.assertEqual(model.mode, "fallback_source_blend")

    def test_expanding_evaluation_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = evaluate_expanding_window(
                _dataset(),
                Path(tmp),
                min_training_events=1,
                probability_floor=0.0,
            )
            self.assertEqual(summary["mode"], "expanding_window")
            self.assertTrue((Path(tmp) / "summary.json").exists())
            self.assertTrue((Path(tmp) / "daily_metrics.csv").exists())
            self.assertTrue((Path(tmp) / "city_day_metrics.csv").exists())
            self.assertTrue((Path(tmp) / "calibration_bins.csv").exists())
            self.assertGreaterEqual(summary["temperature_rows"], 1)
            self.assertGreaterEqual(summary["bracket_rows"], 1)

    def test_benchmark_writes_leakage_safe_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = benchmark_expanding_window(
                _dataset(),
                Path(tmp),
                estimator_names=[
                    "source_blend",
                    "raycaster",
                    "raycaster_source_blend_hybrid",
                ],
                min_training_events=1,
                probability_floor=0.0,
            )
            self.assertEqual(summary["mode"], "benchmark_expanding_window")
            self.assertTrue((Path(tmp) / "model_comparison.csv").exists())
            self.assertTrue((Path(tmp) / "weighted_model_comparison.csv").exists())
            self.assertTrue((Path(tmp) / "bootstrap_confidence_intervals.csv").exists())
            self.assertTrue((Path(tmp) / "calibration_summary.csv").exists())
            self.assertTrue((Path(tmp) / "model_decision.md").exists())
            self.assertTrue((Path(tmp) / "daily_metrics.csv").exists())
            self.assertEqual(summary["headline_weighting"], "city_day_weighted")
            weighted_path = Path(tmp) / "weighted_model_comparison.csv"
            with weighted_path.open(newline="", encoding="utf-8") as handle:
                weighted_rows = list(csv.DictReader(handle))
            self.assertIn(
                "city_day_weighted",
                {row["weighting"] for row in weighted_rows},
            )
            city_rows = {
                row["model_name"]: row
                for row in weighted_rows
                if row["weighting"] == "city_day_weighted"
            }
            self.assertIn("raycaster_source_blend_hybrid", city_rows)
            self.assertEqual(
                city_rows["source_blend"]["log_loss"],
                city_rows["raycaster_source_blend_hybrid"]["log_loss"],
            )
            diagnostics_path = Path(tmp) / "training_diagnostics.csv"
            with diagnostics_path.open(newline="", encoding="utf-8") as handle:
                diagnostics = list(csv.DictReader(handle))
            self.assertEqual(
                {
                    row["training_rows"]
                    for row in diagnostics
                    if row["target_date"] == "2026-07-01"
                },
                {"0"},
            )
            self.assertEqual(
                {
                    row["training_rows"]
                    for row in diagnostics
                    if row["target_date"] == "2026-07-03"
                },
                {"2"},
            )

    def test_training_schema_excludes_city_and_day_of_year_baselines(self) -> None:
        self.assertNotIn("city", ACTIVE_CATEGORICAL_FEATURES)
        self.assertNotIn("target_day_of_year_sin", ACTIVE_NUMERIC_FEATURES)
        self.assertNotIn("target_day_of_year_cos", ACTIVE_NUMERIC_FEATURES)

    def test_trained_model_records_residual_training_contract(self) -> None:
        rows = build_feature_rows(_dataset())
        model = train_raycaster_model(rows, min_training_events=1)
        self.assertEqual(model.target_mode, TARGET_MODE)
        self.assertEqual(model.prediction_blend_weight, PREDICTION_BLEND_WEIGHT)
        self.assertEqual(model.sample_weighting, SAMPLE_WEIGHTING)

    def test_saved_model_reloads_residual_training_contract(self) -> None:
        rows = build_feature_rows(_dataset())
        model = train_raycaster_model(rows, min_training_events=1)
        with tempfile.TemporaryDirectory() as tmp:
            save_model(model, Path(tmp), manifest={"data_path": "test"})
            loaded = load_model(Path(tmp))
            self.assertEqual(loaded.target_mode, TARGET_MODE)
            self.assertEqual(loaded.prediction_blend_weight, PREDICTION_BLEND_WEIGHT)
            self.assertEqual(loaded.sample_weighting, SAMPLE_WEIGHTING)


def _dataset() -> BacktestDataset:
    events = []
    weather = []
    markets = []
    settlements = []
    for offset, high in enumerate((80, 82, 84), start=1):
        target = date(2026, 7, offset)
        event_ticker = f"TEST-{offset}"
        snapshot = datetime(2026, 7, offset, 18, tzinfo=UTC)
        start = datetime(2026, 7, offset, 5, tzinfo=UTC)
        events.append(
            EventSnapshot(
                city="nyc",
                event_ticker=event_ticker,
                target_date=target,
                snapshot_hour_utc=snapshot,
                climate_window_start_utc=start,
                climate_window_end_utc=start + timedelta(hours=24),
                station_id="KNYC",
            )
        )
        weather.append(
            WeatherSnapshot(
                city="nyc",
                event_ticker=event_ticker,
                target_date=target,
                snapshot_hour_utc=snapshot,
                nws_anchor_high_f=high,
                observed_high_so_far_f=high - 1,
                hrrr_projected_high_f=high + 0.5,
                nbm_projected_high_f=high - 0.5,
                ensemble_raw_median_high_f=high,
            )
        )
        brackets = [
            Bracket(f"{event_ticker}-LOW", "Low", None, high - 1, 0),
            Bracket(f"{event_ticker}-WIN", "Win", high, high, 1),
            Bracket(f"{event_ticker}-HIGH", "High", high + 1, None, 2),
        ]
        markets.extend(
            MarketSnapshot(
                city="nyc",
                event_ticker=event_ticker,
                market_ticker=bracket.ticker,
                target_date=target,
                snapshot_hour_utc=snapshot,
                bracket=bracket,
                yes_bid=0.2,
                yes_ask=0.4,
            )
            for bracket in brackets
        )
        settlements.append(
            Settlement(
                city="nyc",
                event_ticker=event_ticker,
                target_date=target,
                settled_at_utc=start + timedelta(hours=26),
                winner_ticker=f"{event_ticker}-WIN",
                settlement_temperature_f=high,
                settlement_bracket_index=1,
            )
        )
    return BacktestDataset(events=events, weather=weather, markets=markets, settlements=settlements)


if __name__ == "__main__":
    unittest.main()
