from __future__ import annotations

# ruff: noqa: E402
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

THETEMP_V1 = Path(__file__).resolve().parents[1]
NEXT_GEN = Path(__file__).resolve().parents[4]
for path in (NEXT_GEN, THETEMP_V1):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evaluate import evaluate_dataset
from features import build_feature_rows
from train import train_model

from libs.models import (
    BacktestDataset,
    Bracket,
    EventSnapshot,
    MarketSnapshot,
    Settlement,
    WeatherSnapshot,
)


class TheTempTemplateTests(unittest.TestCase):
    def test_template_builds_rows_and_model(self) -> None:
        rows = build_feature_rows(_dataset())
        model = train_model(rows)
        self.assertEqual(model.model_name, "thetemp_v1")
        self.assertEqual(model.training_rows, 1)

    def test_template_evaluates_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = evaluate_dataset(_dataset(), tmp)
            self.assertEqual(summary["model_name"], "thetemp_v1")
            self.assertEqual(summary["temperature_prediction_count"], 1)
            self.assertEqual(summary["bracket_prediction_count"], 1)


def _dataset() -> BacktestDataset:
    target = date(2026, 7, 1)
    snapshot = datetime(2026, 7, 1, 18, tzinfo=UTC)
    start = datetime(2026, 7, 1, 5, tzinfo=UTC)
    event_ticker = "TEST-TEMPLATE"
    brackets = [
        Bracket("LOW", "Low", None, 79, 0),
        Bracket("WIN", "Win", 80, 80, 1),
        Bracket("HIGH", "High", 81, None, 2),
    ]
    return BacktestDataset(
        events=[
            EventSnapshot(
                city="nyc",
                event_ticker=event_ticker,
                target_date=target,
                snapshot_hour_utc=snapshot,
                climate_window_start_utc=start,
                climate_window_end_utc=start + timedelta(hours=24),
                station_id="KNYC",
            )
        ],
        weather=[
            WeatherSnapshot(
                city="nyc",
                event_ticker=event_ticker,
                target_date=target,
                snapshot_hour_utc=snapshot,
                nws_anchor_high_f=80,
                observed_high_so_far_f=79,
                hrrr_projected_high_f=81,
                nbm_projected_high_f=80,
                ensemble_raw_median_high_f=80,
            )
        ],
        markets=[
            MarketSnapshot(
                city="nyc",
                event_ticker=event_ticker,
                market_ticker=bracket.ticker,
                target_date=target,
                snapshot_hour_utc=snapshot,
                bracket=bracket,
                yes_bid=0.1,
                yes_ask=0.2,
            )
            for bracket in brackets
        ],
        settlements=[
            Settlement(
                city="nyc",
                event_ticker=event_ticker,
                target_date=target,
                settled_at_utc=start + timedelta(hours=26),
                winner_ticker="WIN",
                settlement_temperature_f=80,
                settlement_bracket_index=1,
            )
        ],
    )


if __name__ == "__main__":
    unittest.main()
