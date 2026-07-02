"""Prepare trend datasets from frozen exports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class TrendMetric:
    key: str
    label: str
    table: str
    x_column: str
    y_column: str
    group_column: str
    aggregation: str
    description: str


METRICS: tuple[TrendMetric, ...] = (
    TrendMetric(
        "final_high_by_day",
        "Final NWS High By Day",
        "final_temperature_labels",
        "target_date",
        "final_high_f",
        "city",
        "mean",
        "Final NWS max temperature for each city and target date.",
    ),
    TrendMetric(
        "nws_anchor_by_snapshot",
        "NWS Anchor By Snapshot",
        "weather_snapshots",
        "snapshot_time_utc",
        "nws_anchor_high_f",
        "city",
        "mean",
        "NWS daily/hourly anchor captured at each snapshot.",
    ),
    TrendMetric(
        "observed_high_by_snapshot",
        "Observed High So Far",
        "weather_snapshots",
        "snapshot_time_utc",
        "observed_high_so_far_f",
        "city",
        "mean",
        "Station observed high available at each snapshot.",
    ),
    TrendMetric(
        "hrrr_projected_high_by_snapshot",
        "HRRR Projected High",
        "weather_snapshots",
        "snapshot_time_utc",
        "hrrr_projected_high_f",
        "city",
        "mean",
        "Open-Meteo HRRR projected high captured at each snapshot.",
    ),
    TrendMetric(
        "nbm_projected_high_by_snapshot",
        "NBM Projected High",
        "weather_snapshots",
        "snapshot_time_utc",
        "nbm_projected_high_f",
        "city",
        "mean",
        "Open-Meteo NBM projected high captured at each snapshot.",
    ),
    TrendMetric(
        "ensemble_median_by_snapshot",
        "Ensemble Median High",
        "weather_snapshots",
        "snapshot_time_utc",
        "ensemble_raw_median_high_f",
        "city",
        "mean",
        "Open-Meteo ensemble raw median high by snapshot.",
    ),
    TrendMetric(
        "settled_bracket_ask_by_snapshot",
        "Settled Bracket Ask Price",
        "market_snapshots",
        "snapshot_time_utc",
        "yes_ask_dollars",
        "city",
        "mean",
        "Average YES ask price of the eventually winning bracket over time.",
    ),
    TrendMetric(
        "market_top_probability_by_snapshot",
        "Market Top Probability",
        "market_snapshots",
        "snapshot_time_utc",
        "market_top_probability",
        "city",
        "mean",
        "Market midpoint probability of the leading bracket by snapshot.",
    ),
)


def metric_catalog() -> list[dict[str, str]]:
    return [
        {
            "key": metric.key,
            "label": metric.label,
            "description": metric.description,
        }
        for metric in METRICS
    ]


def build_trend_payload(data_dir: Path) -> dict[str, Any]:
    tables = _load_tables(data_dir)
    cities = _cities(tables)
    return {
        "metadata": {
            "data_dir": str(data_dir),
            "cities": cities,
            "metrics": metric_catalog(),
        },
        "series": {
            metric.key: _metric_series(metric, tables)
            for metric in METRICS
            if metric.table in tables
        },
    }


def _metric_series(metric: TrendMetric, tables: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    frame = _metric_frame(metric, tables)
    if frame.empty:
        return []
    grouped = (
        frame.groupby([metric.x_column, metric.group_column], as_index=False)[metric.y_column]
        .agg(metric.aggregation)
        .sort_values([metric.x_column, metric.group_column])
    )
    return [
        {
            "x": str(row[metric.x_column]),
            "group": str(row[metric.group_column]),
            "value": float(row[metric.y_column]),
        }
        for row in grouped.to_dict("records")
        if pd.notna(row[metric.y_column])
    ]


def _metric_frame(metric: TrendMetric, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frame = tables.get(metric.table, pd.DataFrame()).copy()
    if frame.empty:
        return frame
    if metric.key == "settled_bracket_ask_by_snapshot":
        settlements = tables.get("settlements", pd.DataFrame())
        if settlements.empty:
            return pd.DataFrame()
        winners = settlements[["city", "event_ticker", "winner_ticker"]].copy()
        frame = frame.merge(winners, on=["city", "event_ticker"], how="inner")
        frame = frame[frame["market_ticker"] == frame["winner_ticker"]]
    required = {metric.x_column, metric.y_column, metric.group_column}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    frame[metric.y_column] = pd.to_numeric(frame[metric.y_column], errors="coerce")
    return frame.dropna(subset=[metric.x_column, metric.y_column, metric.group_column])


def _load_tables(data_dir: Path) -> dict[str, pd.DataFrame]:
    names = (
        "events",
        "weather_snapshots",
        "market_snapshots",
        "settlements",
        "final_temperature_labels",
    )
    return {name: _read_table(data_dir, name) for name in names}


def _read_table(data_dir: Path, name: str) -> pd.DataFrame:
    csv_path = data_dir / f"{name}.csv"
    json_gz_path = data_dir / f"{name}.json.gz"
    json_path = data_dir / f"{name}.json"
    if csv_path.exists():
        return pd.read_csv(csv_path)
    if json_gz_path.exists():
        return pd.read_json(json_gz_path, compression="gzip")
    if json_path.exists():
        return pd.read_json(json_path)
    return pd.DataFrame()


def _cities(tables: dict[str, pd.DataFrame]) -> list[str]:
    values: set[str] = set()
    for frame in tables.values():
        if "city" in frame.columns:
            values.update(str(value) for value in frame["city"].dropna().unique())
    return sorted(values)

