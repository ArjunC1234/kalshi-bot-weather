"""Feature construction for Cloudcaster v1 bracket probability rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from features import FeatureRow, source_blend_prediction

from libs.models import Bracket, MarketSnapshot

NUMERIC_FEATURES = [
    "raycaster_expected_high_f",
    "source_blend_expected_high_f",
    "raycaster_minus_source_blend",
    "raycaster_iqr_f",
    "raycaster_p80_width_f",
    "raycaster_p90_width_f",
    "source_std_f",
    "source_range_f",
    "observed_high_so_far_f",
    "hours_elapsed",
    "hours_remaining",
    "bracket_index",
    "bracket_width_f",
    "bracket_center_f",
    "distance_to_center_f",
    "abs_distance_to_center_f",
    "distance_to_lower_f",
    "distance_to_upper_f",
    "expected_inside_bracket",
    "observed_inside_or_above_lower",
    "observed_above_upper",
    "market_implied_probability",
    "source_blend_probability",
]
CATEGORICAL_FEATURES = ["city", "checkpoint", "is_open_low", "is_open_high"]
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES


@dataclass(frozen=True)
class CloudcasterRow:
    city: str
    event_ticker: str
    target_date: date
    snapshot_hour_utc: datetime
    market_ticker: str
    winner_ticker: str | None
    features: dict[str, float | str | None]
    target: int | None = None

    @property
    def snapshot_key(self) -> tuple[str, str, datetime]:
        return self.city, self.event_ticker, self.snapshot_hour_utc


def build_cloudcaster_rows(
    rows: list[FeatureRow],
    grouped_markets: dict[tuple[str, str, object], list[MarketSnapshot]],
    raycaster_expected: list[float],
    raycaster_quantiles: list[dict[float, float]],
    source_probabilities: dict[tuple[str, str, datetime], dict[str, float]] | None = None,
) -> list[CloudcasterRow]:
    output: list[CloudcasterRow] = []
    for row, expected, quantiles in zip(
        rows,
        raycaster_expected,
        raycaster_quantiles,
        strict=True,
    ):
        markets = grouped_markets.get((row.city, row.event_ticker, row.snapshot_hour_utc), [])
        if not markets:
            continue
        source_distribution = (source_probabilities or {}).get(row_snapshot_key(row), {})
        for market in markets:
            output.append(
                CloudcasterRow(
                    city=row.city,
                    event_ticker=row.event_ticker,
                    target_date=row.target_date,
                    snapshot_hour_utc=row.snapshot_hour_utc,
                    market_ticker=market.market_ticker,
                    winner_ticker=row.winner_ticker,
                    target=(
                        1
                        if row.winner_ticker is not None
                        and market.market_ticker == row.winner_ticker
                        else 0
                        if row.winner_ticker is not None
                        else None
                    ),
                    features=_features_for_market(
                        row,
                        market,
                        expected,
                        quantiles,
                        source_distribution.get(market.market_ticker),
                    ),
                )
            )
    return output


def feature_dicts(rows: list[CloudcasterRow]) -> list[dict[str, float | str | None]]:
    return [{column: row.features.get(column) for column in FEATURE_COLUMNS} for row in rows]


def labeled_rows(rows: list[CloudcasterRow]) -> list[CloudcasterRow]:
    return [row for row in rows if row.target is not None]


def row_snapshot_key(row: FeatureRow) -> tuple[str, str, datetime]:
    return row.city, row.event_ticker, row.snapshot_hour_utc


def _features_for_market(
    row: FeatureRow,
    market: MarketSnapshot,
    raycaster_expected: float,
    raycaster_quantiles: dict[float, float],
    source_probability: float | None,
) -> dict[str, float | str | None]:
    bracket = market.bracket
    source_expected = source_blend_prediction(row)
    observed = _finite_float(row.features.get("observed_high_so_far_f"))
    lower = _filled_lower(bracket, raycaster_expected)
    upper = _filled_upper(bracket, raycaster_expected)
    center = (lower + upper) / 2.0
    market_probability = _market_probability(market)
    return {
        "city": row.city,
        "checkpoint": str(row.features.get("checkpoint") or "unknown"),
        "is_open_low": str(bracket.lower_f is None),
        "is_open_high": str(bracket.upper_f is None),
        "raycaster_expected_high_f": raycaster_expected,
        "source_blend_expected_high_f": source_expected,
        "raycaster_minus_source_blend": raycaster_expected - source_expected,
        "raycaster_iqr_f": _quantile_width(raycaster_quantiles, 0.25, 0.75),
        "raycaster_p80_width_f": _quantile_width(raycaster_quantiles, 0.10, 0.90),
        "raycaster_p90_width_f": _quantile_width(raycaster_quantiles, 0.05, 0.95),
        "source_std_f": _finite_float(row.features.get("source_std_f")),
        "source_range_f": _finite_float(row.features.get("source_range_f")),
        "observed_high_so_far_f": observed,
        "hours_elapsed": _finite_float(row.features.get("hours_elapsed")),
        "hours_remaining": _finite_float(row.features.get("hours_remaining")),
        "bracket_index": float(bracket.index),
        "bracket_width_f": max(1.0, upper - lower),
        "bracket_center_f": center,
        "distance_to_center_f": raycaster_expected - center,
        "abs_distance_to_center_f": abs(raycaster_expected - center),
        "distance_to_lower_f": raycaster_expected - lower,
        "distance_to_upper_f": upper - raycaster_expected,
        "expected_inside_bracket": _inside(raycaster_expected, bracket),
        "observed_inside_or_above_lower": (
            None if observed is None else 1.0 if observed >= lower else 0.0
        ),
        "observed_above_upper": None if observed is None else 1.0 if observed > upper else 0.0,
        "market_implied_probability": market_probability,
        "source_blend_probability": source_probability,
    }


def _market_probability(market: MarketSnapshot) -> float | None:
    if market.normalized_market_midpoint_probability is not None:
        return float(market.normalized_market_midpoint_probability)
    if market.yes_bid is not None and market.yes_ask is not None:
        return (float(market.yes_bid) + float(market.yes_ask)) / 2.0
    if market.last_price is not None:
        return float(market.last_price)
    return None


def _quantile_width(quantiles: dict[float, float], low: float, high: float) -> float | None:
    if low not in quantiles or high not in quantiles:
        return None
    return float(quantiles[high]) - float(quantiles[low])


def _inside(value: float, bracket: Bracket) -> float:
    if bracket.lower_f is not None and value < bracket.lower_f - 0.5:
        return 0.0
    if bracket.upper_f is not None and value > bracket.upper_f + 0.5:
        return 0.0
    return 1.0


def _filled_lower(bracket: Bracket, expected: float) -> float:
    if bracket.lower_f is not None:
        return float(bracket.lower_f) - 0.5
    if bracket.upper_f is not None:
        return float(bracket.upper_f) - 5.5
    return expected - 5.0


def _filled_upper(bracket: Bracket, expected: float) -> float:
    if bracket.upper_f is not None:
        return float(bracket.upper_f) + 0.5
    if bracket.lower_f is not None:
        return float(bracket.lower_f) + 5.5
    return expected + 5.0


def _finite_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
