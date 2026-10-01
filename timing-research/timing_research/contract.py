"""Versioned boundary for collector v3 JSON, not a production-code dependency.

Unit and bracket semantics are adapted from the collector's existing normalizers.
Only the fields needed by this experiment are accepted; missing prices stay missing.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta


@dataclass(frozen=True)
class City:
    series_ticker: str
    standard_offset: int


CITY_BY_KEY = {
    "nyc": City("KXHIGHNY", -5),
    "mia": City("KXHIGHMIA", -5),
    "la": City("KXHIGHLAX", -8),
    "den": City("KXHIGHDEN", -7),
    "aus": City("KXHIGHAUS", -6),
    "okc": City("KXHIGHTOKC", -6),
}


@dataclass(frozen=True)
class Clock:
    climate_day_start_utc: datetime
    climate_day_end_utc: datetime


def city_clock(city: City, snapshot: datetime, target: date) -> Clock:
    start = datetime.combine(target, time.min, UTC) - timedelta(hours=city.standard_offset)
    return Clock(start, start + timedelta(days=1))


def parse_event_date(event: str) -> date:
    match = re.fullmatch(r"[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})", event)
    months = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
    if not match or match[2] not in months:
        raise ValueError("invalid_event_ticker")
    return date(2000 + int(match[1]), months.index(match[2]) + 1, int(match[3]))


@dataclass(frozen=True)
class Bracket:
    ticker: str
    lower_f: int | None
    upper_f: int | None


def parse_bracket(market: dict) -> Bracket:
    label = market.get("yes_sub_title") or market.get("subtitle") or market.get("title")
    if not isinstance(label, str) or not market.get("ticker"):
        raise ValueError("missing_bracket_label")
    label = label.lower().replace("\u00b0", "").replace("fahrenheit", "").strip()
    label = label.replace("\u2013", "-").replace("\u2014", "-")
    middle = re.fullmatch(r"(-?\d+)\s*(?:to|-)\s*(-?\d+)(?:\s*f)?", label)
    lower = re.fullmatch(r"(-?\d+)(?:\s*f)?\s*(?:or below|or less|and below)", label)
    upper = re.fullmatch(r"(-?\d+)(?:\s*f)?\s*(?:or above|or more|and above)", label)
    if middle:
        return Bracket(market["ticker"], int(middle[1]), int(middle[2]))
    if lower:
        return Bracket(market["ticker"], None, int(lower[1]))
    if upper:
        return Bracket(market["ticker"], int(upper[1]), None)
    raise ValueError("unrecognized_bracket_label")


def validate_brackets(brackets: list[Bracket]) -> tuple[Bracket, ...]:
    rows = sorted(brackets, key=lambda row: -math.inf if row.lower_f is None else row.lower_f)
    if len(rows) != 6 or len({row.ticker for row in rows}) != 6:
        raise ValueError("duplicate_or_missing_brackets")
    if rows[0].lower_f is not None or rows[-1].upper_f is not None:
        raise ValueError("missing_tail_bracket")
    for index, row in enumerate(rows):
        if index > 0 and row.lower_f is None or index < 5 and row.upper_f is None:
            raise ValueError("misplaced_tail_bracket")
        if row.lower_f is not None and row.upper_f is not None and row.lower_f > row.upper_f:
            raise ValueError("reversed_bracket")
        if index and rows[index - 1].upper_f + 1 != row.lower_f:
            raise ValueError("noncontiguous_brackets")
    return tuple(rows)


def market_float(market: dict, *keys: str) -> float | None:
    cents = {f"{side}_{kind}" for side in ("yes", "no") for kind in ("bid", "ask")}
    for key in keys:
        if market.get(key) is None:
            continue
        try:
            value = float(market[key])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        return value / 100 if key in cents else value
    return None


def daily_high_from_payload(payload: dict, clock: Clock) -> float | None:
    values = []
    for period in payload.get("properties", {}).get("periods", []):
        if period.get("isDaytime") is not True:
            continue
        start = datetime.fromisoformat(period["startTime"].replace("Z", "+00:00"))
        if start.tzinfo is None:
            raise ValueError("naive_forecast_period")
        if clock.climate_day_start_utc <= start < clock.climate_day_end_utc:
            value = float(period["temperature"])
            unit = period.get("temperatureUnit")
            if unit not in ("F", "C") or not math.isfinite(value):
                raise ValueError("invalid_forecast_temperature")
            values.append(value if unit == "F" else value * 9 / 5 + 32)
    return max(values) if values else None
