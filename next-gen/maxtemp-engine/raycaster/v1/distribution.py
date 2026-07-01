"""Convert Raycaster temperature forecasts into Kalshi bracket probabilities."""

from __future__ import annotations

from collections.abc import Mapping

from libs.models import Bracket
from libs.probabilities import apply_probability_floor, normalize

DEFAULT_QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)


def monotonic_quantiles(quantiles: Mapping[float, float]) -> dict[float, float]:
    ordered_levels = sorted(float(level) for level in quantiles)
    ordered_values = sorted(float(quantiles[level]) for level in ordered_levels)
    return dict(zip(ordered_levels, ordered_values, strict=True))


def bracket_distribution(
    brackets: list[Bracket],
    expected_high_f: float,
    quantiles: Mapping[float, float],
    observed_high_so_far_f: float | None = None,
    probability_floor: float = 0.001,
) -> dict[str, float]:
    if not brackets:
        return {}
    cleaned = monotonic_quantiles(quantiles) if quantiles else _default_quantiles(expected_high_f)
    cdf = PiecewiseQuantileCdf(cleaned, observed_floor=_observed_floor(observed_high_so_far_f))
    raw: dict[str, float] = {}
    for bracket in sorted(brackets, key=lambda item: item.index):
        lower = float("-inf") if bracket.lower_f is None else bracket.lower_f - 0.5
        upper = float("inf") if bracket.upper_f is None else bracket.upper_f + 0.5
        raw[bracket.ticker] = max(0.0, cdf.at(upper) - cdf.at(lower))
    normalized = normalize(raw, "raycaster bracket probabilities")
    if probability_floor:
        return apply_probability_floor(normalized, probability_floor)
    return normalized


class PiecewiseQuantileCdf:
    def __init__(
        self,
        quantiles: Mapping[float, float],
        observed_floor: float | None = None,
    ) -> None:
        cleaned = monotonic_quantiles(quantiles)
        if not cleaned:
            raise ValueError("quantiles are required")
        self.points = _with_tail_points(cleaned)
        self.observed_floor = observed_floor
        self.floor_base_cdf = self._base_at(observed_floor) if observed_floor is not None else 0.0

    def at(self, temperature_f: float) -> float:
        if temperature_f == float("-inf"):
            return 0.0
        if temperature_f == float("inf"):
            return 1.0
        if self.observed_floor is not None and temperature_f <= self.observed_floor:
            return 0.0
        base = self._base_at(temperature_f)
        if self.observed_floor is None:
            return base
        remaining = max(1e-9, 1.0 - self.floor_base_cdf)
        return min(1.0, max(0.0, (base - self.floor_base_cdf) / remaining))

    def _base_at(self, temperature_f: float | None) -> float:
        if temperature_f is None:
            return 0.0
        points = self.points
        if temperature_f <= points[0][1]:
            return 0.0
        if temperature_f >= points[-1][1]:
            return 1.0
        for (left_p, left_t), (right_p, right_t) in zip(points, points[1:], strict=False):
            if left_t <= temperature_f <= right_t:
                if right_t == left_t:
                    return right_p
                fraction = (temperature_f - left_t) / (right_t - left_t)
                return left_p + fraction * (right_p - left_p)
        return 1.0


def _default_quantiles(expected_high_f: float) -> dict[float, float]:
    return {
        0.05: expected_high_f - 4.0,
        0.10: expected_high_f - 3.0,
        0.25: expected_high_f - 1.5,
        0.50: expected_high_f,
        0.75: expected_high_f + 1.5,
        0.90: expected_high_f + 3.0,
        0.95: expected_high_f + 4.0,
    }


def _with_tail_points(quantiles: Mapping[float, float]) -> list[tuple[float, float]]:
    points = sorted((float(level), float(value)) for level, value in quantiles.items())
    low = points[0][1]
    high = points[-1][1]
    tail_scale = max(1.0, (high - low) / 3.0)
    return [(0.0, low - tail_scale), *points, (1.0, high + tail_scale)]


def _observed_floor(observed_high_so_far_f: float | None) -> float | None:
    if observed_high_so_far_f is None:
        return None
    return observed_high_so_far_f - 0.75
