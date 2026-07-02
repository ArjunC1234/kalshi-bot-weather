"""Template bracket distribution helpers for TheTemp v1."""

from __future__ import annotations

from libs.models import Bracket
from libs.probabilities import apply_probability_floor, normalize


def bracket_distribution(
    brackets: list[Bracket],
    quantiles: dict[float, float],
    probability_floor: float,
) -> dict[str, float]:
    if not brackets:
        return {}
    ordered = sorted((float(level), float(value)) for level, value in quantiles.items())
    raw = {
        bracket.ticker: max(
            0.0,
            _cdf(_upper_boundary(bracket), ordered) - _cdf(_lower_boundary(bracket), ordered),
        )
        for bracket in sorted(brackets, key=lambda item: item.index)
    }
    normalized = normalize(raw, "thetemp template probabilities")
    return apply_probability_floor(normalized, probability_floor)


def _cdf(temperature_f: float, points: list[tuple[float, float]]) -> float:
    if temperature_f == float("-inf"):
        return 0.0
    if temperature_f == float("inf"):
        return 1.0
    with_tails = [(0.0, points[0][1] - 4.0), *points, (1.0, points[-1][1] + 4.0)]
    if temperature_f <= with_tails[0][1]:
        return 0.0
    if temperature_f >= with_tails[-1][1]:
        return 1.0
    for left, right in zip(with_tails, with_tails[1:], strict=False):
        left_p, left_t = left
        right_p, right_t = right
        if left_t <= temperature_f <= right_t:
            if right_t == left_t:
                return right_p
            return left_p + ((temperature_f - left_t) / (right_t - left_t)) * (right_p - left_p)
    return 1.0


def _lower_boundary(bracket: Bracket) -> float:
    return float("-inf") if bracket.lower_f is None else bracket.lower_f - 0.5


def _upper_boundary(bracket: Bracket) -> float:
    return float("inf") if bracket.upper_f is None else bracket.upper_f + 0.5
