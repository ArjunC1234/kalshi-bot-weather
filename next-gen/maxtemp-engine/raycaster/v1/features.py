"""Feature extraction for Raycaster v1."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from libs.models import BacktestDataset, EventSnapshot, Settlement, WeatherSnapshot
from libs.time_utils import checkpoint_label

MODEL_NAME = "raycaster_v1"

NUMERIC_FEATURES = [
    "snapshot_hour_utc",
    "target_day_of_year_sin",
    "target_day_of_year_cos",
    "hours_elapsed",
    "hours_remaining",
    "nws_anchor_high_f",
    "observed_high_so_far_f",
    "hrrr_projected_high_f",
    "nbm_projected_high_f",
    "ensemble_raw_median_high_f",
    "observation_age_hours",
    "nws_hourly_window_max_f",
    "hrrr_next_3h_max_f",
    "hrrr_next_6h_max_f",
    "hrrr_next_8h_max_f",
    "hrrr_slope_3h_f_per_hour",
    "hrrr_slope_6h_f_per_hour",
    "hrrr_slope_8h_f_per_hour",
    "nbm_next_3h_max_f",
    "nbm_next_6h_max_f",
    "nbm_next_8h_max_f",
    "nbm_slope_3h_f_per_hour",
    "nbm_slope_6h_f_per_hour",
    "nbm_slope_8h_f_per_hour",
    "ensemble_spread_f",
    "ensemble_family_count",
    "ensemble_member_count",
    "source_std_f",
    "source_range_f",
    "hrrr_minus_nws",
    "nbm_minus_nws",
    "ensemble_minus_nws",
    "observed_minus_nws",
    "hrrr_minus_observed",
    "nbm_minus_observed",
]

CATEGORICAL_FEATURES = ["city", "checkpoint"]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES


@dataclass(frozen=True)
class FeatureRow:
    city: str
    event_ticker: str
    target_date: date
    snapshot_hour_utc: datetime
    features: dict[str, float | str | None]
    settlement_temperature_f: float | None = None
    winner_ticker: str | None = None
    settlement_bracket_index: int | None = None


def build_feature_rows(dataset: BacktestDataset) -> list[FeatureRow]:
    events = _events_by_snapshot(dataset.events)
    settlements = {settlement.event_ticker: settlement for settlement in dataset.settlements}
    rows: list[FeatureRow] = []
    for weather in dataset.weather:
        settlement = settlements.get(weather.event_ticker)
        event = events.get(
            _snapshot_key(weather.city, weather.event_ticker, weather.snapshot_hour_utc)
        )
        rows.append(feature_row_from_snapshot(weather, event, settlement))
    return sorted(rows, key=lambda row: (row.target_date, row.city, row.snapshot_hour_utc))


def feature_row_from_snapshot(
    weather: WeatherSnapshot,
    event: EventSnapshot | None = None,
    settlement: Settlement | None = None,
) -> FeatureRow:
    raw_features = weather.features or {}
    features: dict[str, float | str | None] = {
        "city": weather.city,
        "checkpoint": _checkpoint(weather, event),
        "snapshot_hour_utc": float(weather.snapshot_hour_utc.hour),
        "target_day_of_year_sin": _day_sin(weather.target_date),
        "target_day_of_year_cos": _day_cos(weather.target_date),
        "hours_elapsed": _hours_elapsed(weather, event),
        "hours_remaining": _hours_remaining(weather, event),
        "nws_anchor_high_f": weather.nws_anchor_high_f,
        "observed_high_so_far_f": weather.observed_high_so_far_f,
        "hrrr_projected_high_f": weather.hrrr_projected_high_f,
        "nbm_projected_high_f": weather.nbm_projected_high_f,
        "ensemble_raw_median_high_f": weather.ensemble_raw_median_high_f,
        "observation_age_hours": _first_number(
            raw_features,
            "observation_age_hours",
            "latest_observation_age_hours",
            "observed_high_age_hours",
        ),
        "nws_hourly_window_max_f": _first_number(
            raw_features,
            "nws_hourly_window_max_f",
            "nws_remaining_window_max_f",
            "nws_hourly_max_f",
        ),
        "hrrr_next_3h_max_f": _first_number(raw_features, "hrrr_next_3h_max_f"),
        "hrrr_next_6h_max_f": _first_number(raw_features, "hrrr_next_6h_max_f"),
        "hrrr_next_8h_max_f": _first_number(raw_features, "hrrr_next_8h_max_f"),
        "hrrr_slope_3h_f_per_hour": _first_number(raw_features, "hrrr_slope_3h_f_per_hour"),
        "hrrr_slope_6h_f_per_hour": _first_number(raw_features, "hrrr_slope_6h_f_per_hour"),
        "hrrr_slope_8h_f_per_hour": _first_number(raw_features, "hrrr_slope_8h_f_per_hour"),
        "nbm_next_3h_max_f": _first_number(raw_features, "nbm_next_3h_max_f"),
        "nbm_next_6h_max_f": _first_number(raw_features, "nbm_next_6h_max_f"),
        "nbm_next_8h_max_f": _first_number(raw_features, "nbm_next_8h_max_f"),
        "nbm_slope_3h_f_per_hour": _first_number(raw_features, "nbm_slope_3h_f_per_hour"),
        "nbm_slope_6h_f_per_hour": _first_number(raw_features, "nbm_slope_6h_f_per_hour"),
        "nbm_slope_8h_f_per_hour": _first_number(raw_features, "nbm_slope_8h_f_per_hour"),
        "ensemble_spread_f": _first_number(
            raw_features,
            "ensemble_spread_f",
            "ensemble_iqr_f",
            "ensemble_std_f",
        ),
        "ensemble_family_count": _first_number(raw_features, "ensemble_family_count"),
        "ensemble_member_count": _first_number(raw_features, "ensemble_member_count"),
    }
    _add_source_disagreement(features)
    return FeatureRow(
        city=weather.city,
        event_ticker=weather.event_ticker,
        target_date=weather.target_date,
        snapshot_hour_utc=weather.snapshot_hour_utc,
        features=features,
        settlement_temperature_f=(
            settlement.settlement_temperature_f if settlement is not None else None
        ),
        winner_ticker=settlement.winner_ticker if settlement is not None else None,
        settlement_bracket_index=(
            settlement.settlement_bracket_index if settlement is not None else None
        ),
    )


def source_blend_prediction(row: FeatureRow) -> float:
    values = [
        _as_float(row.features.get("nws_anchor_high_f")),
        _as_float(row.features.get("nws_hourly_window_max_f")),
        _as_float(row.features.get("hrrr_projected_high_f")),
        _as_float(row.features.get("nbm_projected_high_f")),
        _as_float(row.features.get("ensemble_raw_median_high_f")),
    ]
    sources = sorted(value for value in values if value is not None)
    observed = _as_float(row.features.get("observed_high_so_far_f"))
    if sources:
        midpoint = len(sources) // 2
        if len(sources) % 2:
            prediction = sources[midpoint]
        else:
            prediction = (sources[midpoint - 1] + sources[midpoint]) / 2.0
    elif observed is not None:
        prediction = observed
    else:
        prediction = 75.0
    return max(prediction, observed) if observed is not None else prediction


def feature_dicts(rows: list[FeatureRow]) -> list[dict[str, float | str | None]]:
    return [{column: row.features.get(column) for column in FEATURE_COLUMNS} for row in rows]


def target_values(rows: list[FeatureRow]) -> list[float]:
    return [
        float(row.settlement_temperature_f)
        for row in rows
        if row.settlement_temperature_f is not None
    ]


def rows_with_temperature(rows: list[FeatureRow]) -> list[FeatureRow]:
    return [row for row in rows if row.settlement_temperature_f is not None]


def _events_by_snapshot(
    events: list[EventSnapshot],
) -> dict[tuple[str, str, datetime], EventSnapshot]:
    return {
        _snapshot_key(event.city, event.event_ticker, event.snapshot_hour_utc): event
        for event in events
    }


def _snapshot_key(
    city: str,
    event_ticker: str,
    snapshot_hour_utc: datetime,
) -> tuple[str, str, datetime]:
    return city, event_ticker, snapshot_hour_utc


def _checkpoint(weather: WeatherSnapshot, event: EventSnapshot | None) -> str:
    if event is None:
        return f"utc_{weather.snapshot_hour_utc.hour:02d}"
    return checkpoint_label(weather.snapshot_hour_utc, event.climate_window_start_utc)


def _hours_elapsed(weather: WeatherSnapshot, event: EventSnapshot | None) -> float | None:
    if event is None:
        return None
    return (weather.snapshot_hour_utc - event.climate_window_start_utc).total_seconds() / 3600.0


def _hours_remaining(weather: WeatherSnapshot, event: EventSnapshot | None) -> float | None:
    if event is None:
        return None
    return (event.climate_window_end_utc - weather.snapshot_hour_utc).total_seconds() / 3600.0


def _day_sin(value: date) -> float:
    return math.sin(2.0 * math.pi * value.timetuple().tm_yday / 366.0)


def _day_cos(value: date) -> float:
    return math.cos(2.0 * math.pi * value.timetuple().tm_yday / 366.0)


def _first_number(values: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        parsed = _as_float(values.get(key))
        if parsed is not None:
            return parsed
    return None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _add_source_disagreement(features: dict[str, float | str | None]) -> None:
    nws = _as_float(features.get("nws_anchor_high_f"))
    hrrr = _as_float(features.get("hrrr_projected_high_f"))
    nbm = _as_float(features.get("nbm_projected_high_f"))
    ensemble = _as_float(features.get("ensemble_raw_median_high_f"))
    observed = _as_float(features.get("observed_high_so_far_f"))
    features["hrrr_minus_nws"] = _difference(hrrr, nws)
    features["nbm_minus_nws"] = _difference(nbm, nws)
    features["ensemble_minus_nws"] = _difference(ensemble, nws)
    features["observed_minus_nws"] = _difference(observed, nws)
    features["hrrr_minus_observed"] = _difference(hrrr, observed)
    features["nbm_minus_observed"] = _difference(nbm, observed)
    source_values = [value for value in (nws, hrrr, nbm, ensemble) if value is not None]
    if len(source_values) >= 2:
        mean = sum(source_values) / len(source_values)
        variance = sum((value - mean) ** 2 for value in source_values) / len(source_values)
        features["source_std_f"] = math.sqrt(variance)
        features["source_range_f"] = max(source_values) - min(source_values)
    else:
        features["source_std_f"] = None
        features["source_range_f"] = None


def _difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right
