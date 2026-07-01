"""Shared feature extraction helpers."""

from __future__ import annotations

from statistics import pstdev


def diff(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else float(left) - float(right)


def source_range(*values: float | None) -> float | None:
    usable = [float(value) for value in values if value is not None]
    return max(usable) - min(usable) if usable else None


def source_stddev(*values: float | None) -> float | None:
    usable = [float(value) for value in values if value is not None]
    return pstdev(usable) if len(usable) >= 2 else None
