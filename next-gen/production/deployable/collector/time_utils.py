"""City-local and climate-day time helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from config import City


@dataclass(frozen=True)
class SnapshotClock:
    snapshot_time_utc: datetime
    snapshot_time_local: datetime
    snapshot_local_date: date
    snapshot_local_hour: int
    target_date_local: date
    climate_day_start_utc: datetime
    climate_day_end_utc: datetime
    climate_day_start_local: datetime
    climate_day_end_local: datetime
    hours_since_climate_start: float
    hours_until_climate_end: float
    checkpoint_label: str


def parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def utc_hour(value: datetime | None = None) -> datetime:
    current = (value or datetime.now(UTC)).astimezone(UTC)
    return current.replace(minute=0, second=0, microsecond=0)


def city_clock(city: City, snapshot_time_utc: datetime, target_date: date) -> SnapshotClock:
    snapshot_utc = snapshot_time_utc.astimezone(UTC)
    local_zone = ZoneInfo(city.timezone_name)
    snapshot_local = snapshot_utc.astimezone(local_zone)
    start_utc, end_utc = climate_window(city, target_date)
    start_local = start_utc.astimezone(local_zone)
    end_local = end_utc.astimezone(local_zone)
    elapsed = (snapshot_utc - start_utc).total_seconds() / 3600.0
    remaining = (end_utc - snapshot_utc).total_seconds() / 3600.0
    return SnapshotClock(
        snapshot_time_utc=snapshot_utc,
        snapshot_time_local=snapshot_local,
        snapshot_local_date=snapshot_local.date(),
        snapshot_local_hour=snapshot_local.hour,
        target_date_local=target_date,
        climate_day_start_utc=start_utc,
        climate_day_end_utc=end_utc,
        climate_day_start_local=start_local,
        climate_day_end_local=end_local,
        hours_since_climate_start=elapsed,
        hours_until_climate_end=remaining,
        checkpoint_label=checkpoint_label(elapsed),
    )


def target_date_for_snapshot(city: City, snapshot_time_utc: datetime) -> date:
    fixed_standard_zone = timezone(timedelta(hours=city.standard_utc_offset_hours))
    return snapshot_time_utc.astimezone(fixed_standard_zone).date()


def climate_window(city: City, target_date: date) -> tuple[datetime, datetime]:
    fixed_standard_zone = timezone(timedelta(hours=city.standard_utc_offset_hours))
    start = datetime.combine(target_date, time.min, tzinfo=fixed_standard_zone)
    start_utc = start.astimezone(UTC)
    return start_utc, start_utc + timedelta(hours=24)


def checkpoint_label(hours_since_start: float) -> str:
    rounded = round(hours_since_start)
    return f"t_plus_{rounded}h" if rounded >= 0 else f"t_minus_{abs(rounded)}h"
