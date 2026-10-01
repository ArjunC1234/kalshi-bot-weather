from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean
from typing import Any

from dataset import markets_by_snapshot
from features import FeatureRow, build_feature_rows, source_blend_prediction

from libs.models import BacktestDataset, Bracket, MarketSnapshot

MODEL_NAME = "neuralcaster_v3"

OFFSET_FEATURES = [
    "observed_minus_nws",
    "nws_hourly_window_max_minus_nws",
    "hrrr_projected_minus_nws",
    "nbm_projected_minus_nws",
    "ensemble_median_minus_nws",
    "hrrr_next_3h_max_minus_nws",
    "nbm_next_3h_max_minus_nws",
]
NUMERIC_FEATURES = [
    "hours_elapsed",
    "hours_remaining",
    "snapshot_hour_utc",
    *OFFSET_FEATURES,
    "source_range_f",
    "source_std_f",
    "warming_rate_last_1h_f_per_hour",
    "warming_rate_last_3h_f_per_hour",
    "market_expected_minus_nws",
    "market_top_probability",
    "market_entropy",
    "market_avg_spread",
    "market_probability_mass",
    "settlement_observed_source_count",
    "settlement_observed_age_hours",
    "settlement_observed_source_range_f",
    "settlement_observed_source_stddev_f",
    "settlement_observed_nws_delta_f",
    "market_expected_minus_nws_delta_1h",
    "market_top_probability_delta_1h",
    "nws_anchor_delta_1h",
    "source_range_delta_1h",
    "source_std_delta_1h",
    "warming_rate_1h_delta_1h",
    "warming_rate_3h_delta_1h",
    *[f"{name}_delta_1h" for name in OFFSET_FEATURES],
]
CATEGORICAL_FEATURES = ["city", "checkpoint"]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES


@dataclass(frozen=True)
class ResidualRow:
    base: FeatureRow
    features: dict[str, float | str | None]
    nws_anchor_high_f: float
    target_offset_f: float
    source_blend_offset_f: float
    market_expected_offset_f: float | None
    weight: float = 1.0


def build_residual_rows(dataset: BacktestDataset) -> list[ResidualRow]:
    rows = build_feature_rows(dataset)
    market_lookup = _market_lookup(dataset)
    output: list[ResidualRow] = []
    city_day_counts: dict[tuple[str, Any], int] = {}
    eligible: list[tuple[FeatureRow, float, float]] = []
    for row in rows:
        actual = _finite_float(row.settlement_temperature_f)
        anchor = _finite_float(row.features.get("nws_anchor_high_f"))
        if actual is None or anchor is None:
            continue
        eligible.append((row, actual, anchor))
        city_day_counts[(row.city, row.target_date)] = (
            city_day_counts.get((row.city, row.target_date), 0) + 1
        )
    for row, actual, anchor in eligible:
        market = market_lookup.get((row.city, row.event_ticker, row.snapshot_hour_utc), {})
        source_blend = source_blend_prediction(row)
        output.append(
            ResidualRow(
                base=row,
                features=_offset_features(row, anchor, market),
                nws_anchor_high_f=anchor,
                target_offset_f=actual - anchor,
                source_blend_offset_f=source_blend - anchor,
                market_expected_offset_f=_offset_from_market(market, anchor),
                weight=1.0 / city_day_counts[(row.city, row.target_date)],
            )
        )
    _add_delta_features(output)
    return sorted(
        output,
        key=lambda item: (item.base.target_date, item.base.city, item.base.snapshot_hour_utc),
    )


def _offset_features(
    row: FeatureRow,
    anchor: float,
    market: dict[str, float],
) -> dict[str, float | str | None]:
    return {
        "city": row.city,
        "checkpoint": str(row.features.get("checkpoint", "")),
        "hours_elapsed": _finite_float(row.features.get("hours_elapsed")),
        "hours_remaining": _finite_float(row.features.get("hours_remaining")),
        "snapshot_hour_utc": _finite_float(row.features.get("snapshot_hour_utc")),
        "observed_minus_nws": _difference_from_anchor(
            _observed_value(row), anchor
        ),
        "settlement_observed_source_count": _finite_float(
            row.features.get("settlement_observed_source_count")
        ),
        "settlement_observed_age_hours": _finite_float(
            row.features.get("settlement_observed_age_hours")
        ),
        "settlement_observed_source_range_f": _finite_float(
            row.features.get("settlement_observed_source_range_f")
        ),
        "settlement_observed_source_stddev_f": _finite_float(
            row.features.get("settlement_observed_source_stddev_f")
        ),
        "settlement_observed_nws_delta_f": _finite_float(
            row.features.get("settlement_observed_nws_delta_f")
        ),
        "nws_hourly_window_max_minus_nws": _difference_from_anchor(
            row.features.get("nws_hourly_window_max_f"), anchor
        ),
        "hrrr_projected_minus_nws": _difference_from_anchor(
            row.features.get("hrrr_projected_high_f"), anchor
        ),
        "nbm_projected_minus_nws": _difference_from_anchor(
            row.features.get("nbm_projected_high_f"), anchor
        ),
        "ensemble_median_minus_nws": _difference_from_anchor(
            row.features.get("ensemble_raw_median_high_f"), anchor
        ),
        "hrrr_next_3h_max_minus_nws": _difference_from_anchor(
            row.features.get("hrrr_next_3h_max_f"), anchor
        ),
        "nbm_next_3h_max_minus_nws": _difference_from_anchor(
            row.features.get("nbm_next_3h_max_f"), anchor
        ),
        "source_range_f": _finite_float(row.features.get("source_range_f")),
        "source_std_f": _finite_float(row.features.get("source_std_f")),
        "warming_rate_last_1h_f_per_hour": _finite_float(
            row.features.get("warming_rate_last_1h_f_per_hour")
        ),
        "warming_rate_last_3h_f_per_hour": _finite_float(
            row.features.get("warming_rate_last_3h_f_per_hour")
        ),
        "market_expected_minus_nws": _offset_from_market(market, anchor),
        "market_top_probability": _finite_float(market.get("market_top_probability")),
        "market_entropy": _finite_float(market.get("market_entropy")),
        "market_avg_spread": _finite_float(market.get("market_avg_spread")),
        "market_probability_mass": _finite_float(market.get("market_probability_mass")),
    }


def _market_lookup(dataset: BacktestDataset) -> dict[tuple[str, str, Any], dict[str, float]]:
    grouped = markets_by_snapshot(dataset)
    return {key: _market_features(markets) for key, markets in grouped.items()}


def _market_features(markets: list[MarketSnapshot]) -> dict[str, float]:
    raw_probs = {}
    representatives = {}
    spreads = []
    for market in markets:
        probability = market.normalized_market_midpoint_probability
        if probability is None and market.yes_bid is not None and market.yes_ask is not None:
            probability = (float(market.yes_bid) + float(market.yes_ask)) / 2.0
        if probability is None:
            continue
        raw_probs[market.bracket.ticker] = max(0.0, float(probability))
        representatives[market.bracket.ticker] = _bracket_representative(market.bracket)
        if market.yes_bid is not None and market.yes_ask is not None:
            spreads.append(max(0.0, float(market.yes_ask) - float(market.yes_bid)))
    total = sum(raw_probs.values())
    if total <= 0:
        return {}
    probs = {ticker: value / total for ticker, value in raw_probs.items()}
    entropy = -sum(probability * math.log(max(probability, 1e-9)) for probability in probs.values())
    return {
        "market_expected_high_f": sum(probs[ticker] * representatives[ticker] for ticker in probs),
        "market_top_probability": max(probs.values()),
        "market_entropy": entropy,
        "market_avg_spread": mean(spreads) if spreads else 0.0,
        "market_probability_mass": total,
    }


def _add_delta_features(rows: list[ResidualRow]) -> None:
    delta_pairs = [
        ("nws_anchor_delta_1h", "_nws_anchor"),
        ("source_range_delta_1h", "source_range_f"),
        ("source_std_delta_1h", "source_std_f"),
        ("warming_rate_1h_delta_1h", "warming_rate_last_1h_f_per_hour"),
        ("warming_rate_3h_delta_1h", "warming_rate_last_3h_f_per_hour"),
        ("market_expected_minus_nws_delta_1h", "market_expected_minus_nws"),
        ("market_top_probability_delta_1h", "market_top_probability"),
        *[(f"{name}_delta_1h", name) for name in OFFSET_FEATURES],
    ]
    grouped: dict[tuple[str, str], list[ResidualRow]] = {}
    for row in rows:
        grouped.setdefault((row.base.city, row.base.event_ticker), []).append(row)
        for output_key, _ in delta_pairs:
            row.features[output_key] = None
    for event_rows in grouped.values():
        event_rows.sort(key=lambda row: row.base.snapshot_hour_utc)
        previous: ResidualRow | None = None
        for row in event_rows:
            if previous is not None:
                for output_key, input_key in delta_pairs:
                    current = (
                        row.nws_anchor_high_f
                        if input_key == "_nws_anchor"
                        else row.features.get(input_key)
                    )
                    prior = (
                        previous.nws_anchor_high_f
                        if input_key == "_nws_anchor"
                        else previous.features.get(input_key)
                    )
                    row.features[output_key] = _difference(current, prior)
            previous = row


def _bracket_representative(bracket: Bracket) -> float:
    if bracket.lower_f is None and bracket.upper_f is None:
        return 75.0
    if bracket.lower_f is None:
        return float(bracket.upper_f) - 1.0
    if bracket.upper_f is None:
        return float(bracket.lower_f) + 1.0
    return mean([float(bracket.lower_f), float(bracket.upper_f)])


def _offset_from_market(market: dict[str, float], anchor: float) -> float | None:
    expected = _finite_float(market.get("market_expected_high_f"))
    return None if expected is None else expected - anchor


def _observed_value(row: FeatureRow) -> float | None:
    observed = _finite_float(row.features.get("settlement_observed_high_so_far_f"))
    return (
        observed
        if observed is not None
        else _finite_float(row.features.get("observed_high_so_far_f"))
    )


def _difference_from_anchor(value: Any, anchor: float) -> float | None:
    parsed = _finite_float(value)
    return None if parsed is None else parsed - anchor


def _difference(left: Any, right: Any) -> float | None:
    left_value = _finite_float(left)
    right_value = _finite_float(right)
    if left_value is None or right_value is None:
        return None
    return left_value - right_value


def _finite_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
