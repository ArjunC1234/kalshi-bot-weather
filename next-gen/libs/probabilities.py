"""Probability and ordered-bracket helpers."""

from __future__ import annotations

import math

from libs.errors import DataValidationError
from libs.models import Bracket


def normalize(values: dict[str, float], label: str = "probabilities") -> dict[str, float]:
    total = sum(max(0.0, float(value)) for value in values.values())
    if total <= 0:
        raise DataValidationError(f"{label} has no positive mass")
    return {key: max(0.0, float(value)) / total for key, value in values.items()}


def apply_probability_floor(values: dict[str, float], floor: float = 0.001) -> dict[str, float]:
    if floor < 0:
        raise ValueError("probability floor cannot be negative")
    floored = {key: max(float(value), floor) for key, value in values.items()}
    return normalize(floored, "floored probabilities")


def entropy(values: dict[str, float]) -> float:
    normalized = normalize(values)
    return -sum(value * math.log(value) for value in normalized.values() if value > 0)


def assert_probability_sum(values: dict[str, float], tolerance: float = 1e-6) -> None:
    total = sum(values.values())
    if abs(total - 1.0) > tolerance:
        raise DataValidationError(f"probabilities sum to {total}, not 1.0")


def bracket_for_temperature(brackets: list[Bracket], temperature_f: float) -> Bracket:
    matches = [
        bracket
        for bracket in brackets
        if bracket.contains_rounded_temperature(temperature_f)
    ]
    if len(matches) != 1:
        raise DataValidationError(f"temperature {temperature_f} maps to {len(matches)} brackets")
    return matches[0]


def top_ticker(values: dict[str, float]) -> str:
    if not values:
        raise DataValidationError("cannot select top ticker from empty probabilities")
    return max(values.items(), key=lambda item: item[1])[0]
