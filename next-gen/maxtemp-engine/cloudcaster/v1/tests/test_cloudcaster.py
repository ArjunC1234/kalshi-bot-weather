from __future__ import annotations

# ruff: noqa: E402
import csv
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

CLOUDCASTER_V1 = Path(__file__).resolve().parents[1]
NEXT_GEN = Path(__file__).resolve().parents[4]
RAYCASTER_V1 = NEXT_GEN / "maxtemp-engine" / "raycaster" / "v1"
for module_name in (
    "baselines",
    "dataset",
    "distribution",
    "evaluate",
    "features",
    "train",
):
    sys.modules.pop(module_name, None)
for path in (CLOUDCASTER_V1, RAYCASTER_V1, NEXT_GEN):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from cloud_evaluate import evaluate_expanding_window
from cloud_features import build_cloudcaster_rows
from cloud_temperature import apply_temperature
from cloud_train import predict_distributions, train_cloudcaster_model
from dataset import markets_by_snapshot
from features import build_feature_rows, source_blend_prediction

from libs.models import (
    BacktestDataset,
    Bracket,
    EventSnapshot,
    MarketSnapshot,
    Settlement,
    WeatherSnapshot,
)


class CloudcasterTests(unittest.TestCase):
    def test_temperature_scaling_sharpens_when_temperature_below_one(self) -> None:
        scores = {"low": 0.2, "mid": 0.6, "high": 0.2}
        scaled = apply_temperature(scores, temperature=0.5)
        self.assertGreater(scaled["mid"], scores["mid"])
        self.assertAlmostEqual(sum(scaled.values()), 1.0, places=7)

    def test_feature_rows_have_one_winner_per_snapshot(self) -> None:
        dataset = _dataset()
        rows = build_feature_rows(dataset)
        expected = [source_blend_prediction(row) for row in rows]
        quantiles = [_simple_quantiles(value) for value in expected]
        cloud_rows = build_cloudcaster_rows(rows, markets_by_snapshot(dataset), expected, quantiles)
        by_snapshot = {}
        for row in cloud_rows:
            by_snapshot.setdefault(row.snapshot_key, []).append(row)
        self.assertTrue(by_snapshot)
        self.assertTrue(
            all(sum(row.target or 0 for row in rows) == 1 for rows in by_snapshot.values())
        )

    def test_probability_predictions_sum_to_one(self) -> None:
        dataset = _dataset()
        rows = build_feature_rows(dataset)
        expected = [source_blend_prediction(row) for row in rows]
        quantiles = [_simple_quantiles(value) for value in expected]
        cloud_rows = build_cloudcaster_rows(rows, markets_by_snapshot(dataset), expected, quantiles)
        model = train_cloudcaster_model(cloud_rows, min_training_rows=1)
        fallback = {
            key: {row.market_ticker: 1 / len(group) for row in group}
            for key, group in _group_by_snapshot(cloud_rows).items()
        }
        distributions = predict_distributions(model, cloud_rows, fallback, probability_floor=0.0)
        self.assertTrue(distributions)
        for distribution in distributions:
            self.assertAlmostEqual(sum(distribution.probabilities.values()), 1.0, places=7)

    def test_expanding_evaluation_writes_outputs_without_future_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = evaluate_expanding_window(
                _dataset(),
                Path(tmp),
                min_raycaster_training_events=1,
                min_cloudcaster_training_rows=1,
                probability_floor=0.0,
            )
            self.assertEqual(summary["mode"], "cloudcaster_expanding_window")
            self.assertTrue((Path(tmp) / "summary.json").exists())
            self.assertTrue((Path(tmp) / "bracket_metrics.csv").exists())
            self.assertTrue((Path(tmp) / "calibration_bins.csv").exists())
            self.assertGreaterEqual(summary["bracket_rows"], 1)
            diagnostics_path = Path(tmp) / "training_diagnostics.csv"
            with diagnostics_path.open(newline="", encoding="utf-8") as handle:
                diagnostics = list(csv.DictReader(handle))
            self.assertEqual(
                {
                    row["cloudcaster_training_rows"]
                    for row in diagnostics
                    if row["target_date"] == "2026-07-01"
                },
                {"0"},
            )
            self.assertEqual(
                {
                    row["raycaster_training_rows"]
                    for row in diagnostics
                    if row["target_date"] == "2026-07-03"
                },
                {"2"},
            )
            self.assertTrue(all("cloudcaster_temperature" in row for row in diagnostics))


def _group_by_snapshot(rows):
    output = {}
    for row in rows:
        output.setdefault(row.snapshot_key, []).append(row)
    return output


def _simple_quantiles(expected: float) -> dict[float, float]:
    return {
        0.05: expected - 3.0,
        0.10: expected - 2.0,
        0.25: expected - 1.0,
        0.50: expected,
        0.75: expected + 1.0,
        0.90: expected + 2.0,
        0.95: expected + 3.0,
    }


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
