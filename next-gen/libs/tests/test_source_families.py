from __future__ import annotations

from datetime import date, datetime

from libs.models import Bracket, WeatherSnapshot
from libs.source_families import (
    family_at_or_above_count,
    family_support_count,
    source_family_features,
    weather_values,
)


def _bracket() -> Bracket:
    return Bracket("TEST-77", "77 degrees", 77, 77)


def test_nbm_and_hrrr_are_one_numerical_confirmation() -> None:
    values = {"nbm_projected_high_f": 77.0, "hrrr_projected_high_f": 77.0}
    assert family_support_count(_bracket(), values) == 1


def test_nws_daily_and_hourly_are_one_confirmation() -> None:
    values = {
        "nws_anchor_high_f": 77.0,
        "nws_daily_daytime_high_f": 77.0,
        "nws_hourly_window_max_f": 77.0,
    }
    assert family_support_count(_bracket(), values) == 1


def test_observed_high_is_a_constraint_not_a_confirmation() -> None:
    values = {"observed_high_so_far_f": 77.0}
    assert family_support_count(_bracket(), values) == 0
    assert family_at_or_above_count(77.0, values) == 0


def test_ensemble_is_not_independent_without_explicit_provenance() -> None:
    values = {
        "nws_anchor_high_f": 77.0,
        "nbm_projected_high_f": 77.0,
        "hrrr_projected_high_f": 77.0,
        "ensemble_raw_median_high_f": 77.0,
    }
    assert family_support_count(_bracket(), values) == 2
    assert source_family_features(values)["family_forecast_count"] == 2


def test_typed_weather_adapter_preserves_family_semantics() -> None:
    weather = WeatherSnapshot(
        city="la",
        event_ticker="TEST",
        target_date=date(2026, 7, 14),
        snapshot_hour_utc=datetime(2026, 7, 14),
        nws_anchor_high_f=77.0,
        hrrr_projected_high_f=77.0,
        nbm_projected_high_f=77.0,
        observed_high_so_far_f=75.0,
    )
    assert family_support_count(_bracket(), weather_values(weather)) == 2
