"""Shared validation helpers."""

from __future__ import annotations

from libs.errors import DataValidationError
from libs.models import Bracket
from libs.probabilities import assert_probability_sum


def validate_brackets_contiguous(brackets: list[Bracket]) -> None:
    if len(brackets) < 2:
        raise DataValidationError("at least two brackets are required")
    ordered = sorted(brackets, key=lambda bracket: bracket.index)
    if ordered[0].lower_f is not None:
        raise DataValidationError("brackets are missing lower tail")
    if ordered[-1].upper_f is not None:
        raise DataValidationError("brackets are missing upper tail")
    previous_upper = ordered[0].upper_f
    for bracket in ordered[1:]:
        if previous_upper is None or bracket.lower_f is None:
            raise DataValidationError("middle bracket has open boundary")
        if bracket.lower_f != previous_upper + 1:
            raise DataValidationError("brackets have a gap or overlap")
        previous_upper = bracket.upper_f


def validate_distribution(probabilities: dict[str, float]) -> None:
    if any(value < 0 for value in probabilities.values()):
        raise DataValidationError("probability cannot be negative")
    assert_probability_sum(probabilities)


def validate_no_future_snapshot(snapshot_hour, target_time) -> None:
    if target_time > snapshot_hour:
        raise DataValidationError("snapshot contains future data")
