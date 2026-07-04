"""Data quality reporting for frozen collector exports."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backtest.data_sources import DataSource
from libs.io_utils import write_csv
from libs.json_utils import write_json
from libs.time_utils import parse_datetime


@dataclass(frozen=True)
class QualityReport:
    table_counts: dict[str, int]
    snapshot_hours: int
    cities: list[str]
    expected_city_hours: int
    actual_city_hours: int
    missing_city_hours: list[dict[str, str]]
    provider_errors: dict[str, int]
    pending_settlements: int
    pending_final_highs: int
    latest_snapshot_utc: str | None
    storage_bytes: int
    source_export_id: str | None = None
    generated_at_utc: str | None = None


def build_quality_report(source: DataSource, source_export_id: str | None = None) -> QualityReport:
    tables = {
        "collector_runs": source.load_table("collector_runs"),
        "raw_payloads": source.load_table("raw_payloads"),
        "events": source.load_table("events"),
        "market_snapshots": source.load_table("market_snapshots"),
        "weather_snapshots": source.load_table("weather_snapshots"),
        "settlements": source.load_table("settlements"),
        "final_temperature_labels": source.load_table("final_temperature_labels"),
        "provider_errors": source.load_table("provider_errors"),
    }
    events = tables["events"]
    cities = sorted({str(row.get("city")) for row in events if row.get("city")})
    hours = sorted(
        {
            str(row.get("snapshot_time_utc"))
            for row in events
            if row.get("snapshot_time_utc") not in (None, "")
        }
    )
    seen = {
        (str(row.get("city")), str(row.get("snapshot_time_utc")))
        for row in events
        if row.get("city") and row.get("snapshot_time_utc")
    }
    missing = [
        {"city": city, "snapshot_time_utc": hour}
        for hour in hours
        for city in cities
        if (city, hour) not in seen
    ]
    settlement_keys = {
        (str(row.get("city")), str(row.get("event_ticker")))
        for row in tables["settlements"]
        if row.get("city") and row.get("event_ticker")
    }
    final_high_keys = {
        (str(row.get("city")), str(row.get("event_ticker")))
        for row in tables["final_temperature_labels"]
        if row.get("city") and row.get("event_ticker")
    }
    ended_keys = _ended_event_keys(events)
    provider_errors = Counter(
        str(row.get("provider") or "unknown") for row in tables["provider_errors"]
    )
    latest_snapshot = _latest_snapshot(events)
    storage_bytes = sum(
        int(float(row.get("compressed_size_bytes") or 0)) for row in tables["raw_payloads"]
    )
    return QualityReport(
        table_counts={table: len(rows) for table, rows in tables.items()},
        snapshot_hours=len(hours),
        cities=cities,
        expected_city_hours=len(hours) * len(cities),
        actual_city_hours=len(seen),
        missing_city_hours=missing,
        provider_errors=dict(sorted(provider_errors.items())),
        pending_settlements=len(ended_keys - settlement_keys),
        pending_final_highs=len(ended_keys - final_high_keys),
        latest_snapshot_utc=latest_snapshot.isoformat() if latest_snapshot else None,
        storage_bytes=storage_bytes,
        source_export_id=source_export_id,
        generated_at_utc=datetime.now(UTC).isoformat(),
    )


def write_quality_report(report: QualityReport, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "quality_report.json", asdict(report))
    write_csv(output / "missing_city_hours.csv", report.missing_city_hours)
    write_csv(
        output / "table_counts.csv",
        [{"table": table, "rows": count} for table, count in report.table_counts.items()],
    )
    write_csv(
        output / "provider_errors.csv",
        [
            {"provider": provider, "errors": count}
            for provider, count in report.provider_errors.items()
        ],
    )


def _ended_event_keys(events: list[dict[str, Any]]) -> set[tuple[str, str]]:
    latest_by_event: dict[tuple[str, str], datetime] = {}
    now = datetime.now().astimezone()
    for row in events:
        city = row.get("city")
        event_ticker = row.get("event_ticker")
        end_value = row.get("climate_day_end_utc")
        if not city or not event_ticker or not end_value:
            continue
        try:
            end_time = parse_datetime(str(end_value))
        except ValueError:
            continue
        key = str(city), str(event_ticker)
        if key not in latest_by_event or end_time > latest_by_event[key]:
            latest_by_event[key] = end_time
    return {key for key, end_time in latest_by_event.items() if end_time <= now}


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
