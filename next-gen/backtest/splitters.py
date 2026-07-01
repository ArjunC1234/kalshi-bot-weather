"""Train/test split helpers."""

from __future__ import annotations

from datetime import date


def expanding_window_dates(
    target_dates: list[date],
    min_train_dates: int = 1,
) -> list[tuple[list[date], date]]:
    ordered = sorted(set(target_dates))
    splits: list[tuple[list[date], date]] = []
    for index, target in enumerate(ordered):
        prior = ordered[:index]
        if len(prior) >= min_train_dates:
            splits.append((prior, target))
    return splits


def assert_prior_dates_only(train_dates: list[date], test_date: date) -> None:
    if any(train_date >= test_date for train_date in train_dates):
        raise ValueError("training dates must be strictly before the test date")
