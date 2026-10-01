"""Shared date policy for Kalshi weather settlement-system eras."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

POST_SETTLEMENT_SYSTEM_START_DATE = date(2026, 8, 27)
POST_SETTLEMENT_SYSTEM_START = POST_SETTLEMENT_SYSTEM_START_DATE.isoformat()


def parse_policy_date(value: str | date, label: str = "date") -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD format") from exc


def clamp_to_post_settlement_start(value: str | date) -> date:
    parsed = parse_policy_date(value)
    return max(parsed, POST_SETTLEMENT_SYSTEM_START_DATE)


def post_settlement_target_dates(
    values: Iterable[date],
    min_date: date | None = None,
) -> list[date]:
    cutoff = min_date or POST_SETTLEMENT_SYSTEM_START_DATE
    return sorted(value for value in set(values) if value >= cutoff)
