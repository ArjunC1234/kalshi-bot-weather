"""Template feature extraction for TheTemp v1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from libs.models import BacktestDataset, EventSnapshot, Settlement, WeatherSnapshot
from libs.time_utils import checkpoint_label


@dataclass(frozen=True)
class FeatureRow:
    city: str
    event_ticker: str
    target_date: date
    snapshot_hour_utc: datetime
    checkpoint: str
    source_values_f: tuple[float, ...]
    observed_high_so_far_f: float | None = None
    settlement_temperature_f: float | None = None
    winner_ticker: str | None = None


def build_feature_rows(dataset: BacktestDataset) -> list[FeatureRow]:
    events = {
        (event.city, event.event_ticker, event.snapshot_hour_utc): event for event in dataset.events
    }
    settlements = {settlement.event_ticker: settlement for settlement in dataset.settlements}
    rows = [
        feature_row_from_snapshot(
            weather,
            events.get((weather.city, weather.event_ticker, weather.snapshot_hour_utc)),
            settlements.get(weather.event_ticker),
        )
        for weather in dataset.weather
    ]
    return sorted(rows, key=lambda row: (row.target_date, row.city, row.snapshot_hour_utc))


def feature_row_from_snapshot(
    weather: WeatherSnapshot,
    event: EventSnapshot | None = None,
    settlement: Settlement | None = None,
) -> FeatureRow:
    return FeatureRow(
        city=weather.city,
        event_ticker=weather.event_ticker,
        target_date=weather.target_date,
        snapshot_hour_utc=weather.snapshot_hour_utc,
        checkpoint=_checkpoint(weather, event),
        source_values_f=tuple(
            value
            for value in (
                weather.nws_anchor_high_f,
                weather.hrrr_projected_high_f,
                weather.nbm_projected_high_f,
                weather.ensemble_raw_median_high_f,
            )
            if value is not None
        ),
        observed_high_so_far_f=weather.observed_high_so_far_f,
        settlement_temperature_f=(
            settlement.settlement_temperature_f if settlement is not None else None
        ),
        winner_ticker=settlement.winner_ticker if settlement is not None else None,
    )


def _checkpoint(weather: WeatherSnapshot, event: EventSnapshot | None) -> str:
    if event is None:
        return f"utc_{weather.snapshot_hour_utc.hour:02d}"
    return checkpoint_label(weather.snapshot_hour_utc, event.climate_window_start_utc)
