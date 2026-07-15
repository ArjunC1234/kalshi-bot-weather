"""Normalize exported rows into typed backtest structures."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from backtest.data_sources import DataSource
from libs.models import (
    BacktestDataset,
    Bracket,
    BracketDistribution,
    CollectorRun,
    EventSnapshot,
    FinalTemperatureLabel,
    MarketSnapshot,
    Settlement,
    WeatherSnapshot,
)
from libs.time_utils import parse_datetime


def load_dataset(source: DataSource) -> BacktestDataset:
    events = [_event(row) for row in source.load_table("events")]
    markets = [_market(row) for row in source.load_table("market_snapshots")]
    weather = [_weather(row) for row in source.load_table("weather_snapshots")]
    settlements = [_settlement(row) for row in source.load_table("settlements")]
    final_temperature_labels = [
        _final_temperature_label(row) for row in source.load_table("final_temperature_labels")
    ]
    model_outputs = [_distribution(row) for row in source.load_table("model_outputs")]
    collector_runs = [_collector_run(row) for row in source.load_table("collector_runs")]
    return BacktestDataset(
        collector_runs=collector_runs,
        events=events,
        markets=markets,
        weather=weather,
        settlements=settlements,
        final_temperature_labels=final_temperature_labels,
        model_outputs=model_outputs,
    )


def _date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def _collector_run(row: dict[str, Any]) -> CollectorRun:
    return CollectorRun(
        collector_run_id=str(row["collector_run_id"]),
        snapshot_hour_utc=parse_datetime(
            str(_first(row, "snapshot_hour_utc", "snapshot_time_utc"))
        ),
        schema_version=int(row.get("schema_version") or 0),
    )


def _event(row: dict[str, Any]) -> EventSnapshot:
    return EventSnapshot(
        city=str(row["city"]),
        event_ticker=str(row["event_ticker"]),
        target_date=_date(row["target_date"]),
        snapshot_hour_utc=parse_datetime(
            str(_first(row, "snapshot_hour_utc", "snapshot_time_utc"))
        ),
        climate_window_start_utc=parse_datetime(
            str(_first(row, "climate_window_start_utc", "climate_day_start_utc"))
        ),
        climate_window_end_utc=parse_datetime(
            str(_first(row, "climate_window_end_utc", "climate_day_end_utc"))
        ),
        station_id=str(row["station_id"]),
    )


def _market(row: dict[str, Any]) -> MarketSnapshot:
    bracket = Bracket(
        ticker=str(row["market_ticker"]),
        label=str(row.get("bracket_label") or ""),
        lower_f=_optional_int(row.get("bracket_lower_f")),
        upper_f=_optional_int(row.get("bracket_upper_f")),
        index=int(row.get("bracket_index") or 0),
    )
    return MarketSnapshot(
        city=str(row["city"]),
        event_ticker=str(row["event_ticker"]),
        market_ticker=str(row["market_ticker"]),
        target_date=_date(row["target_date"]),
        snapshot_hour_utc=parse_datetime(
            str(_first(row, "snapshot_hour_utc", "snapshot_time_utc"))
        ),
        bracket=bracket,
        yes_bid=_optional_float(row.get("yes_bid_dollars")),
        yes_ask=_optional_float(row.get("yes_ask_dollars")),
        no_bid=_optional_float(row.get("no_bid_dollars")),
        no_ask=_optional_float(row.get("no_ask_dollars")),
        yes_bid_size=_optional_float(row.get("yes_bid_size")),
        yes_ask_size=_optional_float(row.get("yes_ask_size")),
        no_bid_size=_optional_float(row.get("no_bid_size")),
        no_ask_size=_optional_float(row.get("no_ask_size")),
        last_price=_optional_float(row.get("last_price_dollars")),
        normalized_market_midpoint_probability=_optional_float(
            row.get("normalized_market_midpoint_probability")
        ),
    )


def _weather(row: dict[str, Any]) -> WeatherSnapshot:
    features = row.get("features")
    if isinstance(features, str) and features:
        try:
            features = json.loads(features)
        except json.JSONDecodeError:
            features = {}
    return WeatherSnapshot(
        city=str(row["city"]),
        event_ticker=str(row["event_ticker"]),
        target_date=_date(row["target_date"]),
        snapshot_hour_utc=parse_datetime(
            str(_first(row, "snapshot_hour_utc", "snapshot_time_utc"))
        ),
        nws_anchor_high_f=_optional_float(row.get("nws_anchor_high_f")),
        observed_high_so_far_f=_optional_float(row.get("observed_high_so_far_f")),
        hrrr_projected_high_f=_optional_float(row.get("hrrr_projected_high_f")),
        nbm_projected_high_f=_optional_float(row.get("nbm_projected_high_f")),
        ensemble_raw_median_high_f=_optional_float(row.get("ensemble_raw_median_high_f")),
        features=features if isinstance(features, dict) else {},
    )


def _settlement(row: dict[str, Any]) -> Settlement:
    return Settlement(
        city=str(row["city"]),
        event_ticker=str(row["event_ticker"]),
        target_date=_date(row["target_date"]),
        settled_at_utc=parse_datetime(str(row["settled_at_utc"])),
        winner_ticker=str(row["winner_ticker"]),
        settlement_temperature_f=_optional_float(row.get("settlement_temperature_f")),
        settlement_bracket_index=_optional_int(row.get("settlement_bracket_index")),
        validation_status=str(row.get("validation_status") or "valid"),
    )


def _final_temperature_label(row: dict[str, Any]) -> FinalTemperatureLabel:
    issued_at = row.get("issued_at_utc")
    return FinalTemperatureLabel(
        city=str(row["city"]),
        event_ticker=str(row["event_ticker"]),
        target_date=_date(row["target_date"]),
        station_id=str(row.get("station_id") or ""),
        final_high_f=float(row["final_high_f"]),
        source_provider=str(row.get("source_provider") or "nws_cli"),
        product_id=str(row["product_id"]) if row.get("product_id") not in (None, "") else None,
        issued_at_utc=parse_datetime(str(issued_at)) if issued_at not in (None, "") else None,
        validation_status=str(row.get("validation_status") or "valid"),
        warnings=_json_list(row.get("warnings")),
    )


def _distribution(row: dict[str, Any]) -> BracketDistribution:
    probabilities = row.get("probabilities")
    if not isinstance(probabilities, dict):
        probabilities = {}
    return BracketDistribution(
        city=str(row["city"]),
        event_ticker=str(row["event_ticker"]),
        snapshot_hour_utc=parse_datetime(str(row["snapshot_hour_utc"])),
        model_name=str(row["model_name"]),
        probabilities={str(key): float(value) for key, value in probabilities.items()},
    )


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    raise KeyError(f"missing required value; tried {', '.join(keys)}")


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def _json_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value]
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return [str(value)]
