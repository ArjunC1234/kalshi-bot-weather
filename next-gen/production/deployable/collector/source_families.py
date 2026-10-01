"""Correlation-aware weather source family features for deployable collector."""

from __future__ import annotations

import math
from collections.abc import Mapping
from statistics import median
from typing import Any


def source_family_features(values: Mapping[str, Any]) -> dict[str, float | int | None]:
    """Return family-level values while preserving every raw source upstream."""
    nws = _number(values.get("nws_anchor_high_f"))
    if nws is None:
        nws = _first_number(values, "nws_daily_daytime_high_f", "nws_hourly_window_max_f")
    hrrr = _number(values.get("hrrr_projected_high_f"))
    nbm = _number(values.get("nbm_projected_high_f"))
    ensemble = _number(values.get("ensemble_raw_median_high_f"))
    observed = _first_number(
        values,
        "settlement_observed_high_so_far_f",
        "observed_high_so_far_f",
    )
    numerical_anchor = nbm if nbm is not None else hrrr
    independent_ensemble = ensemble if _ensemble_is_independent(values) else None
    forecast_values = [
        value for value in (nws, numerical_anchor, independent_ensemble) if value is not None
    ]
    baseline = float(median(forecast_values)) if forecast_values else observed
    if baseline is None:
        baseline = 75.0
    if observed is not None:
        baseline = max(baseline, observed)
    disagreement_values = [
        value for value in (nws, numerical_anchor, independent_ensemble) if value is not None
    ]
    return {
        "family_baseline_high_f": baseline,
        "family_numerical_anchor_high_f": numerical_anchor,
        "family_nws_minus_nbm_f": _difference(nws, nbm),
        "family_hrrr_minus_nbm_f": _difference(hrrr, nbm),
        "family_ensemble_minus_nbm_f": _difference(ensemble, nbm),
        "family_numerical_disagreement_f": _absolute_difference(hrrr, nbm),
        "family_forecast_count": len(forecast_values),
        "family_independent_ensemble": int(independent_ensemble is not None),
        "family_disagreement_range_f": _range(disagreement_values),
        "family_disagreement_std_f": _stddev(disagreement_values),
    }


def _ensemble_is_independent(values: Mapping[str, Any]) -> bool:
    explicit = values.get("ensemble_independent")
    if explicit is True or str(explicit).strip().lower() in {"1", "true", "yes"}:
        return True
    count = _number(values.get("ensemble_independent_family_count"))
    return count is not None and count >= 2


def _first_number(values: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = _number(values.get(key))
        if number is not None:
            return number
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _difference(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def _absolute_difference(left: float | None, right: float | None) -> float | None:
    difference = _difference(left, right)
    return None if difference is None else abs(difference)


def _range(values: list[float]) -> float | None:
    return max(values) - min(values) if len(values) >= 2 else None


def _stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / len(values))
