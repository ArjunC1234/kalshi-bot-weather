from __future__ import annotations

# ruff: noqa: E402
import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

RAYCASTER_V1 = Path(__file__).resolve().parents[1]
NEXT_GEN = Path(__file__).resolve().parents[4]
for path in (NEXT_GEN, RAYCASTER_V1):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from features import feature_row_from_snapshot, source_blend_prediction

from libs.models import EventSnapshot, Settlement, WeatherSnapshot


class FeatureTests(unittest.TestCase):
    def test_extracts_weather_features_and_disagreements(self) -> None:
        event = EventSnapshot(
            city="den",
            event_ticker="KXHIGHDEN-TEST",
            target_date=date(2026, 7, 1),
            snapshot_hour_utc=datetime(2026, 7, 1, 18, tzinfo=UTC),
            climate_window_start_utc=datetime(2026, 7, 1, 7, tzinfo=UTC),
            climate_window_end_utc=datetime(2026, 7, 2, 7, tzinfo=UTC),
            station_id="KDEN",
        )
        weather = WeatherSnapshot(
            city="den",
            event_ticker="KXHIGHDEN-TEST",
            target_date=date(2026, 7, 1),
            snapshot_hour_utc=event.snapshot_hour_utc,
            nws_anchor_high_f=90,
            observed_high_so_far_f=88,
            hrrr_projected_high_f=92,
            nbm_projected_high_f=91,
            ensemble_raw_median_high_f=89,
            features={"hrrr_next_3h_max_f": 93, "observation_age_hours": 1.5},
        )
        settlement = Settlement(
            city="den",
            event_ticker="KXHIGHDEN-TEST",
            target_date=date(2026, 7, 1),
            settled_at_utc=event.climate_window_end_utc + timedelta(hours=2),
            winner_ticker="KXHIGHDEN-TEST-B90",
            settlement_temperature_f=91,
        )
        row = feature_row_from_snapshot(weather, event, settlement)
        self.assertEqual(row.features["checkpoint"], "t_plus_11h")
        self.assertEqual(row.features["hrrr_minus_nws"], 2)
        self.assertEqual(row.features["observed_minus_nws"], -2)
        self.assertEqual(row.features["hrrr_next_3h_max_f"], 93)
        self.assertEqual(row.settlement_temperature_f, 91)

    def test_source_blend_respects_observed_high(self) -> None:
        weather = WeatherSnapshot(
            city="nyc",
            event_ticker="TEST",
            target_date=date(2026, 7, 1),
            snapshot_hour_utc=datetime(2026, 7, 1, 18, tzinfo=UTC),
            nws_anchor_high_f=80,
            observed_high_so_far_f=85,
            hrrr_projected_high_f=81,
        )
        row = feature_row_from_snapshot(weather)
        self.assertEqual(source_blend_prediction(row), 85)


if __name__ == "__main__":
    unittest.main()
