#!/usr/bin/env python3
"""Immutable Supabase/Postgres collector v3."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from clients import (
    HttpRecorder,
    PostgresClient,
    StorageClient,
    raw_payload_dicts,
    raw_payload_row,
)
from config import City, load_dotenv, parse_cities, settings_from_env
from ids import deterministic_id, sha256_bytes
from normalizers import (
    event_row,
    market_rows,
    parse_bracket,
    select_event,
    settlement_row,
    validate_brackets,
    weather_row,
)
from schema import FACT_TABLES, INIT_SQL
from spool import local_status, mark_synced, pending_dir, read_json_gz, write_spool
from time_utils import city_clock, parse_datetime, target_date_for_snapshot, utc_hour

SCHEMA_VERSION = 3
KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
NWS_BASE_URL = "https://api.weather.gov"
OPEN_METEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
OPEN_METEO_GFS_URL = "https://api.open-meteo.com/v1/gfs"
NBM_HOURLY_FIELDS = ",".join(
    (
        "temperature_2m",
        "relative_humidity_2m",
        "dew_point_2m",
        "precipitation_probability",
        "wind_speed_10m",
        "cloud_cover",
    )
)


def collect_once(
    data_dir: Path,
    user_agent: str,
    bucket: str,
    snapshot_time: datetime | None = None,
    cities: tuple[City, ...] = (),
) -> Path:
    started = datetime.now(UTC)
    snapshot_utc = utc_hour(snapshot_time)
    collector_run_id = deterministic_id("run-v3", snapshot_utc.isoformat(), socket.gethostname())
    recorder = HttpRecorder(user_agent, collector_run_id, snapshot_utc, bucket, SCHEMA_VERSION)
    selected_cities = cities
    tables: dict[str, list[dict[str, Any]]] = {table: [] for table in FACT_TABLES}
    completed = 0
    for city in selected_cities:
        try:
            city_rows = collect_city(recorder, collector_run_id, snapshot_utc, city)
            for table, rows in city_rows.items():
                tables[table].extend(rows)
            completed += 1
        except Exception as exc:  # noqa: BLE001 - keep collecting other cities.
            recorder.record_error("collector", "collect_city", exc, city.key, None, None)
    completed_at = datetime.now(UTC)
    tables["raw_payloads"] = [
        raw_payload_row(row) for row in raw_payload_dicts(recorder.raw_payloads)
    ]
    tables["provider_errors"].extend(recorder.errors)
    normalized_count = sum(len(rows) for table, rows in tables.items() if table != "collector_runs")
    tables["collector_runs"].append(
        {
            "collector_run_id": collector_run_id,
            "schema_version": SCHEMA_VERSION,
            "started_at_utc": started.isoformat(),
            "completed_at_utc": completed_at.isoformat(),
            "snapshot_time_utc": snapshot_utc.isoformat(),
            "server_hostname": socket.gethostname(),
            "collector_version": "immutable-supabase-v3",
            "collector_source_hash": source_hash(),
            "config_hash": sha256_bytes(
                json.dumps(
                    {"bucket": bucket, "cities": [city.key for city in selected_cities]},
                    sort_keys=True,
                ).encode("utf-8")
            ),
            "city_count_attempted": len(selected_cities),
            "city_count_completed": completed,
            "provider_error_count": len(recorder.errors),
            "raw_payload_count": len(recorder.raw_payloads),
            "normalized_row_count": normalized_count,
            "spool_status": "created",
            "metadata": {"duration_seconds": (completed_at - started).total_seconds()},
        }
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "collector_run_id": collector_run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "snapshot_time_utc": snapshot_utc.isoformat(),
        "raw_payloads": raw_payload_dicts(recorder.raw_payloads),
        "tables": tables,
    }
    name = f"{snapshot_utc.strftime('%Y%m%dT%HZ')}_{collector_run_id}.json.gz"
    return write_spool(data_dir, name, payload)


def collect_city(
    recorder: HttpRecorder,
    collector_run_id: str,
    snapshot_time_utc: datetime,
    city: City,
) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {
        "events": [],
        "market_snapshots": [],
        "weather_snapshots": [],
        "settlements": [],
        "provider_errors": [],
    }
    active_target_date = target_date_for_snapshot(city, snapshot_time_utc)
    markets_payload = recorder.get_json(
        "kalshi",
        "kalshi_open_markets",
        f"{KALSHI_BASE_URL}/markets",
        {"series_ticker": city.series_ticker, "status": "open", "limit": 1000},
        city=city.key,
        required=True,
    )
    markets = [
        item for item in (markets_payload or {}).get("markets", []) if isinstance(item, dict)
    ]
    target_date, event_markets = select_event(markets, active_target_date)
    event_ticker = str(event_markets[0]["event_ticker"])
    clock = city_clock(city, snapshot_time_utc, target_date)
    brackets = validate_brackets([parse_bracket(market) for market in event_markets])
    raw_market = recorder.retag_latest_payload(
        "kalshi",
        "kalshi_open_markets",
        city.key,
        event_ticker,
        target_date.isoformat(),
        clock.snapshot_time_local.isoformat(),
    ) or recorder.latest_raw_id("kalshi", "kalshi_open_markets", city.key)
    rows["events"].append(
        event_row(
            collector_run_id,
            city,
            target_date,
            event_ticker,
            clock,
            raw_market,
            event_markets,
            brackets,
        )
    )
    rows["market_snapshots"].extend(
        market_rows(
            collector_run_id,
            city,
            target_date,
            event_ticker,
            clock,
            brackets,
            event_markets,
            raw_market,
        )
    )

    point = recorder.get_json(
        "nws",
        "nws_points",
        f"{NWS_BASE_URL}/points/{city.latitude:.4f},{city.longitude:.4f}",
        city=city.key,
        event_ticker=event_ticker,
        target_date=target_date.isoformat(),
        snapshot_time_local=clock.snapshot_time_local.isoformat(),
    )
    props = point.get("properties", {}) if isinstance(point, dict) else {}
    daily = recorder.get_json(
        "nws",
        "nws_daily_forecast",
        props.get("forecast"),
        city=city.key,
        event_ticker=event_ticker,
        target_date=target_date.isoformat(),
        snapshot_time_local=clock.snapshot_time_local.isoformat(),
    )
    hourly = recorder.get_json(
        "nws",
        "nws_hourly_forecast",
        props.get("forecastHourly"),
        city=city.key,
        event_ticker=event_ticker,
        target_date=target_date.isoformat(),
        snapshot_time_local=clock.snapshot_time_local.isoformat(),
    )
    observations = recorder.get_json(
        "nws",
        "nws_observations",
        f"{NWS_BASE_URL}/stations/{city.station_id}/observations",
        {
            "start": clock.climate_day_start_utc.isoformat().replace("+00:00", "Z"),
            "end": min(clock.snapshot_time_utc, clock.climate_day_end_utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "limit": 500,
        },
        city=city.key,
        event_ticker=event_ticker,
        target_date=target_date.isoformat(),
        snapshot_time_local=clock.snapshot_time_local.isoformat(),
    )
    forecast_days = max(2, (clock.climate_day_end_utc.date() - snapshot_time_utc.date()).days + 1)
    ensemble = recorder.get_json(
        "open_meteo",
        "open_meteo_ensemble",
        OPEN_METEO_ENSEMBLE_URL,
        {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "hourly": "temperature_2m",
            "models": "gfs_seamless,ecmwf_ifs025,icon_seamless,gem_global",
            "forecast_days": max(3, forecast_days),
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
        },
        city=city.key,
        event_ticker=event_ticker,
        target_date=target_date.isoformat(),
        snapshot_time_local=clock.snapshot_time_local.isoformat(),
    )
    hrrr = recorder.get_json(
        "open_meteo",
        "open_meteo_hrrr",
        OPEN_METEO_GFS_URL,
        {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "hourly": "temperature_2m",
            "models": "gfs_hrrr",
            "forecast_days": forecast_days,
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
        },
        city=city.key,
        event_ticker=event_ticker,
        target_date=target_date.isoformat(),
        snapshot_time_local=clock.snapshot_time_local.isoformat(),
    )
    nbm = recorder.get_json(
        "open_meteo",
        "open_meteo_nbm",
        OPEN_METEO_GFS_URL,
        {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "hourly": NBM_HOURLY_FIELDS,
            "models": "ncep_nbm_conus",
            "forecast_days": forecast_days,
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
        },
        city=city.key,
        event_ticker=event_ticker,
        target_date=target_date.isoformat(),
        snapshot_time_local=clock.snapshot_time_local.isoformat(),
    )
    rows["weather_snapshots"].append(
        weather_row(
            collector_run_id,
            city,
            target_date,
            event_ticker,
            clock,
            daily,
            hourly,
            observations,
            ensemble,
            hrrr,
            nbm,
            recorder.payload_ids(city.key, event_ticker, target_date.isoformat()),
        )
    )
    return rows


def sync_spool(data_dir: Path, storage: StorageClient | None, postgres: PostgresClient) -> int:
    synced = 0
    for path in sorted(pending_dir(data_dir).glob("*.json.gz")):
        payload = read_json_gz(path)
        raw_payloads = payload.get("raw_payloads", [])
        if storage is not None:
            for record in raw_payloads if isinstance(raw_payloads, list) else []:
                if isinstance(record, dict):
                    storage.upload_raw_payload(record)
        tables = payload.get("tables")
        if not isinstance(tables, dict):
            raise RuntimeError(f"spool tables are malformed: {path}")
        postgres.insert_facts(tables)
        mark_synced(path, data_dir)
        synced += 1
    return synced


def settle_pending(
    recorder: HttpRecorder,
    postgres: PostgresClient,
    storage: StorageClient | None,
    limit: int = 100,
) -> int:
    inserted = 0
    tables: dict[str, list[dict[str, Any]]] = {table: [] for table in FACT_TABLES}
    for event in postgres.fetch_unsettled_events(limit):
        city = parse_cities(str(event["city"]))[0]
        event_ticker = str(event["event_ticker"])
        target_date = _date_value(event["target_date"])
        payload = recorder.get_json(
            "kalshi",
            "kalshi_settled_markets",
            f"{KALSHI_BASE_URL}/markets",
            {"event_ticker": event_ticker, "status": "settled", "limit": 1000},
            city=city.key,
            event_ticker=event_ticker,
            target_date=str(target_date),
            required=False,
        )
        markets = [item for item in (payload or {}).get("markets", []) if isinstance(item, dict)]
        if not markets:
            continue
        brackets = validate_brackets([parse_bracket(market) for market in markets])
        raw_id = recorder.latest_raw_id("kalshi", "kalshi_settled_markets", city.key)
        try:
            tables["settlements"].append(
                settlement_row(
                    city, target_date, event_ticker, markets, brackets, raw_id, datetime.now(UTC)
                )
            )
            inserted += 1
        except Exception as exc:  # noqa: BLE001 - preserve invalid settlement attempt.
            recorder.record_error(
                "kalshi", "settlement_parse", exc, city.key, event_ticker, str(target_date)
            )
    tables["raw_payloads"] = [
        raw_payload_row(row) for row in raw_payload_dicts(recorder.raw_payloads)
    ]
    tables["provider_errors"] = recorder.errors
    if storage is not None:
        for record in raw_payload_dicts(recorder.raw_payloads):
            storage.upload_raw_payload(record)
    postgres.insert_facts(tables)
    return inserted


def status(data_dir: Path, postgres: PostgresClient | None) -> dict[str, Any]:
    output = {"local": local_status(data_dir)}
    if postgres is not None:
        output["postgres"] = postgres.status()
    return output


def export_tables(
    postgres: PostgresClient, output_dir: Path, start: str, end: str
) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for table in (
        "collector_runs",
        "raw_payloads",
        "events",
        "market_snapshots",
        "weather_snapshots",
        "settlements",
        "provider_errors",
    ):
        counts[table] = postgres.export_table(table, output_dir / f"{table}.csv", start, end)
    (output_dir / "manifest.json").write_text(
        json.dumps({"start": start, "end": end, "counts": counts}, indent=2),
        encoding="utf-8",
    )
    return counts


def source_hash() -> str:
    return sha256_bytes(Path(__file__).read_bytes())


def _date_value(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def build_clients(settings):
    postgres = PostgresClient(settings.database_url) if settings.database_url else None
    storage = (
        StorageClient(
            settings.supabase_url,
            settings.supabase_service_role_key,
            settings.supabase_storage_bucket,
        )
        if settings.supabase_url and settings.supabase_service_role_key
        else None
    )
    return storage, postgres


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Immutable Supabase/Postgres weather collector v3")
    parser.add_argument("--cities")
    parser.add_argument("--data-dir", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init-db")
    init.add_argument("--print-sql", action="store_true")
    collect = commands.add_parser("collect-once")
    collect.add_argument("--dry-run", action="store_true")
    collect.add_argument("--snapshot-hour")
    commands.add_parser("sync-spool")
    commands.add_parser("settle-pending")
    commands.add_parser("status")
    export = commands.add_parser("export")
    export.add_argument("--start", required=True)
    export.add_argument("--end", required=True)
    export.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "init-db":
        if args.print_sql:
            print(INIT_SQL.strip())
            return 0
        settings = settings_from_env()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is required to apply schema")
        PostgresClient(settings.database_url).execute_schema(INIT_SQL)
        return 0
    settings = settings_from_env()
    data_dir = args.data_dir or settings.collector_data_dir
    storage, postgres = build_clients(settings)
    if args.command == "collect-once":
        snapshot = parse_datetime(args.snapshot_hour) if args.snapshot_hour else None
        path = collect_once(
            data_dir,
            settings.nws_user_agent,
            settings.supabase_storage_bucket,
            snapshot,
            parse_cities(args.cities),
        )
        print(f"created spool {path}")
        if args.dry_run:
            return 0
        if postgres is None:
            raise RuntimeError("DATABASE_URL is required for non-dry-run sync")
        synced = sync_spool(data_dir, storage, postgres)
        recorder = HttpRecorder(
            settings.nws_user_agent,
            deterministic_id("settle", datetime.now(UTC).isoformat()),
            datetime.now(UTC),
            settings.supabase_storage_bucket,
            SCHEMA_VERSION,
        )
        settled = settle_pending(recorder, postgres, storage)
        print(f"synced_spool_files={synced} inserted_settlements={settled}")
        return 0
    if args.command == "sync-spool":
        if postgres is None:
            raise RuntimeError("DATABASE_URL is required")
        print(f"synced_spool_files={sync_spool(data_dir, storage, postgres)}")
        return 0
    if args.command == "settle-pending":
        if postgres is None:
            raise RuntimeError("DATABASE_URL is required")
        recorder = HttpRecorder(
            settings.nws_user_agent,
            deterministic_id("settle", datetime.now(UTC).isoformat()),
            datetime.now(UTC),
            settings.supabase_storage_bucket,
            SCHEMA_VERSION,
        )
        print(f"inserted_settlements={settle_pending(recorder, postgres, storage)}")
        return 0
    if args.command == "status":
        print(json.dumps(status(data_dir, postgres), indent=2, default=str))
        return 0
    if args.command == "export":
        if postgres is None:
            raise RuntimeError("DATABASE_URL is required")
        print(json.dumps(export_tables(postgres, args.output_dir, args.start, args.end), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
