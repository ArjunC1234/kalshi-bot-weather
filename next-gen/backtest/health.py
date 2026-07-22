"""Daily collector health checks for local exports or live Supabase rows."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from backtest.data_sources import DataSource
from libs.constants import CITIES
from libs.io_utils import write_csv
from libs.json_utils import write_json
from libs.time_utils import parse_datetime


@dataclass(frozen=True)
class DailyHealthReport:
    source_export_id: str | None
    target_date: str
    generated_at_utc: str
    status: str
    expected_cities: list[str]
    cities_seen: list[str]
    expected_city_hours: int
    actual_city_hours: int
    missing_city_hours: list[dict[str, Any]]
    city_coverage: list[dict[str, Any]]
    event_count: int
    market_snapshot_rows: int
    weather_snapshot_rows: int
    raw_payload_rows: int
    provider_errors: dict[str, int]
    pending_settlements: int
    pending_final_highs: int
    latest_snapshot_utc: str | None
    warnings: list[str]


def build_daily_health_report(
    source: DataSource,
    target_date: str | date,
    source_export_id: str | None = None,
    expected_cities: list[str] | None = None,
) -> DailyHealthReport:
    day = _date_key(target_date)
    cities = expected_cities or [city.key for city in CITIES]
    tables = {
        "events": source.load_table("events"),
        "market_snapshots": source.load_table("market_snapshots"),
        "weather_snapshots": source.load_table("weather_snapshots"),
        "raw_payloads": source.load_table("raw_payloads"),
        "settlements": source.load_table("settlements"),
        "final_temperature_labels": source.load_table("final_temperature_labels"),
        "provider_errors": source.load_table("provider_errors"),
    }
    events = _rows_for_collection_day(tables["events"], day)
    target_events = _rows_for_target_date(tables["events"], day)
    markets = _rows_for_collection_day(tables["market_snapshots"], day)
    weather = _rows_for_collection_day(tables["weather_snapshots"], day)
    raw_payloads = _rows_for_storage_day(tables["raw_payloads"], day)
    provider_error_rows = _rows_for_collection_day(tables["provider_errors"], day)
    observed_hours = _observed_hours(events)
    expected_hours = sorted({hour for hours in observed_hours.values() for hour in hours})
    if not expected_hours:
        expected_hours = list(range(24))
    missing = [
        {"city": city, "snapshot_local_hour": hour, "snapshot_local_date": day}
        for city in cities
        for hour in expected_hours
        if hour not in observed_hours.get(city, set())
    ]
    coverage = [
        {
            "city": city,
            "snapshot_hours": len(observed_hours.get(city, set())),
            "expected_hours": len(expected_hours),
            "coverage_ratio": (
                len(observed_hours.get(city, set())) / len(expected_hours)
                if expected_hours
                else 0.0
            ),
        }
        for city in cities
    ]
    event_keys = {
        (str(row.get("city")), str(row.get("event_ticker")))
        for row in target_events
        if row.get("city") and row.get("event_ticker")
    }
    settled_keys = {
        (str(row.get("city")), str(row.get("event_ticker")))
        for row in tables["settlements"]
        if _date_key(row.get("target_date")) == day and row.get("city") and row.get("event_ticker")
    }
    final_high_keys = {
        (str(row.get("city")), str(row.get("event_ticker")))
        for row in tables["final_temperature_labels"]
        if _date_key(row.get("target_date")) == day and row.get("city") and row.get("event_ticker")
    }
    provider_errors = Counter(str(row.get("provider") or "unknown") for row in provider_error_rows)
    latest_snapshot = _latest_snapshot(events)
    warnings = _warnings(
        cities,
        events,
        weather,
        markets,
        missing,
        provider_errors,
        latest_snapshot,
    )
    status = "ok" if not warnings else "warning"
    return DailyHealthReport(
        source_export_id=source_export_id,
        target_date=day,
        generated_at_utc=datetime.now(UTC).isoformat(),
        status=status,
        expected_cities=cities,
        cities_seen=sorted({str(row.get("city")) for row in target_events if row.get("city")}),
        expected_city_hours=len(cities) * len(expected_hours),
        actual_city_hours=sum(len(hours) for hours in observed_hours.values()),
        missing_city_hours=missing,
        city_coverage=coverage,
        event_count=len(event_keys),
        market_snapshot_rows=len(markets),
        weather_snapshot_rows=len(weather),
        raw_payload_rows=len(raw_payloads),
        provider_errors=dict(sorted(provider_errors.items())),
        pending_settlements=len(event_keys - settled_keys),
        pending_final_highs=len(event_keys - final_high_keys),
        latest_snapshot_utc=latest_snapshot.isoformat() if latest_snapshot else None,
        warnings=warnings,
    )


def write_daily_health_report(report: DailyHealthReport, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "daily_health_report.json", asdict(report))
    write_csv(output / "city_coverage.csv", report.city_coverage)
    write_csv(output / "missing_city_hours.csv", report.missing_city_hours)
    write_csv(
        output / "provider_errors.csv",
        [
            {"provider": provider, "errors": count}
            for provider, count in report.provider_errors.items()
        ],
    )


def daily_health_summary(report: DailyHealthReport) -> str:
    warnings = "; ".join(report.warnings) if report.warnings else "none"
    return (
        f"daily-health {report.target_date}: status={report.status} "
        f"cities={len(report.cities_seen)}/{len(report.expected_cities)} "
        f"city_hours={report.actual_city_hours}/{report.expected_city_hours} "
        f"events={report.event_count} weather_rows={report.weather_snapshot_rows} "
        f"market_rows={report.market_snapshot_rows} "
        f"pending_settlements={report.pending_settlements} "
        f"pending_final_highs={report.pending_final_highs} warnings={warnings}"
    )


def _rows_for_collection_day(rows: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [row for row in rows if _row_collection_day(row) == day]


def _rows_for_target_date(rows: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    return [row for row in rows if _date_key(row.get("target_date")) == day]


def _rows_for_storage_day(rows: list[dict[str, Any]], day: str) -> list[dict[str, Any]]:
    output = []
    compact_day = day.replace("-", "")
    for row in rows:
        if _row_collection_day(row) == day:
            output.append(row)
            continue
        storage_path = str(row.get("storage_path") or "")
        if day in storage_path or compact_day in storage_path:
            output.append(row)
    return output


def _row_collection_day(row: dict[str, Any]) -> str:
    for key in ("snapshot_local_date", "target_date"):
        value = row.get(key)
        if value not in (None, ""):
            return _date_key(value)
    value = (
        row.get("snapshot_time_utc")
        or row.get("created_at_utc")
        or row.get("run_started_at_utc")
    )
    return _date_key(value)


def _observed_hours(events: list[dict[str, Any]]) -> dict[str, set[int]]:
    output: dict[str, set[int]] = defaultdict(set)
    for row in events:
        city = row.get("city")
        if not city:
            continue
        hour = row.get("snapshot_local_hour")
        if hour in (None, ""):
            hour = _hour_from_timestamp(row.get("snapshot_time_utc"))
        parsed = _int_or_none(hour)
        if parsed is not None:
            output[str(city)].add(parsed)
    return output


def _latest_snapshot(events: list[dict[str, Any]]) -> datetime | None:
    values: list[datetime] = []
    for row in events:
        value = row.get("snapshot_time_utc")
        if value in (None, ""):
            continue
        try:
            values.append(parse_datetime(str(value)))
        except ValueError:
            continue
    return max(values) if values else None


def _warnings(
    cities: list[str],
    events: list[dict[str, Any]],
    weather: list[dict[str, Any]],
    markets: list[dict[str, Any]],
    missing: list[dict[str, Any]],
    provider_errors: Counter[str],
    latest_snapshot: datetime | None,
) -> list[str]:
    warnings = []
    seen_cities = {str(row.get("city")) for row in events if row.get("city")}
    missing_cities = sorted(set(cities) - seen_cities)
    if missing_cities:
        warnings.append(f"missing cities: {', '.join(missing_cities)}")
    if missing:
        warnings.append(f"missing city-hours: {len(missing)}")
    if not weather:
        warnings.append("no weather snapshot rows")
    if not markets:
        warnings.append("no market snapshot rows")
    if provider_errors:
        warnings.append(f"provider errors: {sum(provider_errors.values())}")
    if latest_snapshot is None:
        warnings.append("no snapshot timestamps")
    return warnings


def _date_key(value: Any) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if value in (None, ""):
        return ""
    text = str(value)
    if len(text) >= 10:
        return text[:10]
    return text


def _hour_from_timestamp(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return parse_datetime(str(value)).hour
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
