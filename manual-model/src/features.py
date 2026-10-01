from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd


SOURCE_COLUMNS = [
    "nws_anchor_high_f",
    "hrrr_projected_high_f",
    "nbm_projected_high_f",
    "ensemble_raw_median_high_f",
]


def build_weather_rows(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    weather = tables["weather"].copy()
    labels = tables["labels"][["event_ticker", "final_high_f"]].copy()
    settlements = tables["settlements"][["event_ticker", "winner_ticker"]].copy()
    events = tables["events"][
        ["city", "event_ticker", "target_date", "snapshot_time_utc", "climate_day_start_utc", "climate_day_end_utc"]
    ].copy()

    weather = weather.merge(labels, on="event_ticker", how="left")
    weather = weather.merge(settlements, on="event_ticker", how="left")
    weather = weather.merge(
        events,
        on=["city", "event_ticker", "target_date", "snapshot_time_utc"],
        how="left",
    )

    weather["checkpoint"] = weather.apply(_checkpoint, axis=1)
    weather["baseline_high_f"] = weather.apply(source_blend, axis=1)
    weather["residual_f"] = weather["final_high_f"] - weather["baseline_high_f"]
    weather["source_range_f"] = weather.apply(_source_range, axis=1)
    weather["source_std_f"] = weather.apply(_source_std, axis=1)
    return weather.sort_values(["target_date", "city", "snapshot_time_utc"]).reset_index(drop=True)


def source_blend(row: pd.Series) -> float:
    values = [_finite(row.get(column)) for column in SOURCE_COLUMNS]
    values = [value for value in values if value is not None]
    if not values:
        return 75.0

    nws = _finite(row.get("nws_anchor_high_f"))
    hrrr = _finite(row.get("hrrr_projected_high_f"))
    nbm = _finite(row.get("nbm_projected_high_f"))
    ens = _finite(row.get("ensemble_raw_median_high_f"))
    weighted = [
        (nws, 0.35),
        (hrrr, 0.25),
        (nbm, 0.25),
        (ens, 0.15),
    ]
    total_weight = sum(weight for value, weight in weighted if value is not None)
    baseline = sum(float(value) * weight for value, weight in weighted if value is not None) / total_weight
    observed = _finite(row.get("observed_high_so_far_f"))
    return max(baseline, observed) if observed is not None else baseline


def _checkpoint(row: pd.Series) -> str:
    start = row.get("climate_day_start_utc")
    snap = row.get("snapshot_time_utc")
    if pd.isna(start) or pd.isna(snap):
        return f"utc_{int(snap.hour):02d}" if isinstance(snap, datetime) else "unknown"
    hours = int(round((snap - start).total_seconds() / 3600))
    return f"t_plus_{max(0, hours)}h"


def _source_range(row: pd.Series) -> float | None:
    values = [_finite(row.get(column)) for column in SOURCE_COLUMNS]
    clean = [value for value in values if value is not None]
    if len(clean) < 2:
        return None
    return float(max(clean) - min(clean))


def _source_std(row: pd.Series) -> float | None:
    values = [_finite(row.get(column)) for column in SOURCE_COLUMNS]
    clean = [value for value in values if value is not None]
    if len(clean) < 2:
        return None
    return float(np.std(clean))


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed):
        return None
    return parsed
