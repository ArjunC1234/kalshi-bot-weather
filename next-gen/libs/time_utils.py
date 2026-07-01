"""UTC parsing, fixed-standard-time climate windows, and checkpoint helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

from libs.models import City


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def fixed_standard_midnight_utc(city: City, target_date: date) -> datetime:
    fixed_tz = timezone(timedelta(hours=city.standard_utc_offset_hours))
    local_midnight = datetime.combine(target_date, time.min, tzinfo=fixed_tz)
    return local_midnight.astimezone(UTC)


def climate_window(city: City, target_date: date) -> tuple[datetime, datetime]:
    start = fixed_standard_midnight_utc(city, target_date)
    return start, start + timedelta(hours=24)


def checkpoint_label(snapshot_hour_utc: datetime, window_start_utc: datetime) -> str:
    hours = round((snapshot_hour_utc - window_start_utc).total_seconds() / 3600)
    return f"t_plus_{hours}h" if hours >= 0 else f"t_minus_{abs(hours)}h"


def target_date_key(value: date | datetime | str) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]
