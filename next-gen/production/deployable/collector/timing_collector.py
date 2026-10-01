#!/usr/bin/env python3
"""Five-minute raw NWS/quote capture, isolated from hourly training snapshots."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import socket
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests
from clients import HttpRecorder, raw_payload_dicts, raw_payload_row
from config import City, load_dotenv, parse_cities, settings_from_env
from ids import deterministic_id, sha256_bytes
from normalizers import parse_bracket, select_event, validate_brackets
from schema import FACT_TABLES
from spool import mark_synced, pending_dir, read_json_gz, write_spool
from time_utils import city_clock, target_date_for_snapshot

from collector import (
    KALSHI_BASE_URL,
    NWS_BASE_URL,
    SCHEMA_VERSION,
    build_clients,
    post_event_collector_run_row,
)

JOB = "timing-v1"
HEADER_NAMES = ("Date", "Age", "Cache-Control", "Expires", "ETag", "Last-Modified", "Retry-After")


def retry_after_seconds(value: str | None, now: datetime) -> float:
    try:
        seconds = float(value) if value else 300.0
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value).astimezone(UTC) - now).total_seconds()
        except (ValueError, TypeError, OverflowError):
            seconds = 300.0
    if not math.isfinite(seconds) or seconds > 365 * 86400:
        seconds = 300.0
    return max(300.0, seconds)


class TimingRecorder(HttpRecorder):
    """Preserve every receipt, even when an unchanged body reuses a storage blob."""

    def __init__(self, *args, state_dir: Path, **kwargs):
        super().__init__(*args, timeout=8.0, **kwargs)
        self.state_dir = state_dir
        self.response_headers: dict[str, dict[str, str]] = {}
        self.last_headers: dict[str, str] = {}

    def _get_with_retries(self, provider, url, params):
        # One attempt per endpoint: the next timer cycle is the retry. A 429
        # persists a provider-wide cooldown across processes, not just cities.
        self.last_headers = {}
        state = self.state_dir / f"{provider}-cooldown.json"
        now = datetime.now(UTC)
        if state.exists():
            until = datetime.fromisoformat(json.loads(state.read_text())["until"])
            if now < until:
                error = RuntimeError(f"{provider} rate-limit cooldown until {until.isoformat()}")
                return {"error": str(error)}, 429, error, 0
        status = None
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            status = response.status_code
            self.last_headers = {
                name: response.headers[name] for name in HEADER_NAMES if name in response.headers
            }
            if status == 429:
                until = now + timedelta(
                    seconds=retry_after_seconds(response.headers.get("Retry-After"), now)
                )
                self.state_dir.mkdir(parents=True, exist_ok=True)
                temporary = state.with_suffix(".tmp")
                temporary.write_text(json.dumps({"until": until.isoformat()}), encoding="utf-8")
                temporary.replace(state)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            return body, status, None, 1
        except (requests.RequestException, ValueError) as error:
            return {"error": str(error)}, status, error, 1

    def get_json(self, *args, **kwargs):
        before = len(self.raw_payloads)
        try:
            return super().get_json(*args, **kwargs)
        finally:
            if len(self.raw_payloads) > before:
                record = self.raw_payloads[-1]
                raw_id = deterministic_id(JOB, self.collector_run_id, before)
                self.raw_payloads[-1] = replace(record, raw_payload_id=raw_id)
                self.response_headers[raw_id] = dict(self.last_headers)

    def retag_latest_payload(self, provider, endpoint_name, city, *args):
        original_id = self.latest_raw_id(provider, endpoint_name, city)
        result = super().retag_latest_payload(provider, endpoint_name, city, *args)
        if result is not None:
            for index, record in enumerate(self.raw_payloads):
                if record.raw_payload_id == result:
                    self.raw_payloads[index] = replace(record, raw_payload_id=original_id)
                    return original_id
        return result


def capture_city(recorder: TimingRecorder, city: City) -> dict[str, Any]:
    snapshot = recorder.snapshot_time_utc
    target = target_date_for_snapshot(city, snapshot)
    context = {
        "city": city.key,
        "target_date": target.isoformat(),
        "snapshot_time_local": city_clock(city, snapshot, target).snapshot_time_local.isoformat(),
    }
    points = recorder.get_json(
        "nws",
        "timing_nws_points",
        f"{NWS_BASE_URL}/points/{city.latitude:.4f},{city.longitude:.4f}",
        **context,
    )
    props = (points or {}).get("properties") or {}
    forecasts = []
    for endpoint, key in (
        ("timing_nws_daily_forecast", "forecast"),
        ("timing_nws_hourly_forecast", "forecastHourly"),
    ):
        url = props.get(key)
        if not url:
            recorder.record_error(
                "nws",
                endpoint,
                RuntimeError(f"NWS points missing {key}"),
                city.key,
                None,
                target.isoformat(),
            )
        forecasts.append(recorder.get_json("nws", endpoint, url, **context))

    # No request for the entry quote is issued until both forecast calls finish.
    markets_payload = recorder.get_json(
        "kalshi",
        "timing_kalshi_post_forecast_markets",
        f"{KALSHI_BASE_URL}/markets",
        {"series_ticker": city.series_ticker, "status": "open", "limit": 1000},
        required=True,
        **context,
    )
    if markets_payload.get("cursor"):
        raise RuntimeError("market response is paginated; refusing incomplete bracket capture")
    target, markets = select_event(markets_payload.get("markets", []), target)
    validate_brackets([parse_bracket(market) for market in markets])
    event = str(markets[0]["event_ticker"])
    local_time = city_clock(city, snapshot, target).snapshot_time_local.isoformat()
    for provider, endpoint in (
        ("nws", "timing_nws_points"),
        ("nws", "timing_nws_daily_forecast"),
        ("nws", "timing_nws_hourly_forecast"),
        ("kalshi", "timing_kalshi_post_forecast_markets"),
    ):
        recorder.retag_latest_payload(
            provider, endpoint, city.key, event, target.isoformat(), local_time
        )
    records = [record for record in recorder.raw_payloads if record.city == city.key]
    weather = [
        record
        for record in records
        if record.endpoint_name in ("timing_nws_daily_forecast", "timing_nws_hourly_forecast")
        and record.success
    ]
    quote = records[-1]
    complete = all(
        forecast and (forecast.get("properties") or {}).get("periods") for forecast in forecasts
    )
    if not complete:
        recorder.record_error(
            "collector",
            "timing_forecasts",
            RuntimeError("missing or empty forecast periods"),
            city.key,
            event,
            target.isoformat(),
        )
    ready = max((record.received_at_utc for record in weather), default=None)
    ordered = bool(complete and ready and quote.requested_at_utc >= ready)
    return {
        "city": city.key,
        "event_ticker": event,
        "target_date": target.isoformat(),
        "complete": bool(complete),
        "forecast_ready_at_utc": ready,
        "quote_requested_at_utc": quote.requested_at_utc,
        "quote_received_at_utc": quote.received_at_utc,
        "post_forecast_order_verified": ordered,
        "market_count": len(markets),
        "raw_payload_ids": {record.endpoint_name: record.raw_payload_id for record in records},
    }


def collect_timing(
    data_dir: Path,
    user_agent: str,
    bucket: str,
    cities: tuple[City, ...],
    snapshot: datetime | None = None,
) -> Path:
    started = datetime.now(UTC)
    snapshot = (snapshot or started).astimezone(UTC)
    run_id = deterministic_id(JOB, snapshot.isoformat(), started.isoformat(), socket.gethostname())
    recorder = TimingRecorder(
        user_agent, run_id, snapshot, bucket, SCHEMA_VERSION, state_dir=data_dir / "state"
    )
    results = []
    for city in cities:
        try:
            results.append(capture_city(recorder, city))
        except Exception as error:  # Keep failures observable while other cities continue.
            recorder.record_error("collector", "timing_city", error, city.key, None, None)
            results.append({"city": city.key, "complete": False, "error": str(error)})
    tables = {table: [] for table in FACT_TABLES}
    raw = raw_payload_dicts(recorder.raw_payloads)
    tables["raw_payloads"] = [raw_payload_row(record) for record in raw]
    tables["provider_errors"] = recorder.errors
    run = post_event_collector_run_row(recorder, started, JOB, 0, len(raw) + len(recorder.errors))
    run.update(
        collector_version=JOB,
        collector_source_hash=sha256_bytes(Path(__file__).read_bytes()),
        city_count_attempted=len(cities),
        city_count_completed=sum(
            bool(result.get("post_forecast_order_verified")) for result in results
        ),
        spool_status="created",
        config_hash=sha256_bytes(
            json.dumps(
                {"cities": [city.key for city in cities], "bucket": bucket, "job": JOB},
                sort_keys=True,
            ).encode()
        ),
    )
    run["metadata"].update(
        cities=results,
        response_headers=recorder.response_headers,
        cadence_seconds=300,
        raw_only=True,
    )
    tables["collector_runs"] = [run]
    return write_spool(
        data_dir,
        f"{snapshot.strftime('%Y%m%dT%H%M%S%fZ')}_{run_id}.json.gz",
        {
            "schema_version": SCHEMA_VERSION,
            "collector_run_id": run_id,
            "snapshot_time_utc": snapshot.isoformat(),
            "created_at_utc": started.isoformat(),
            "raw_payloads": raw,
            "tables": tables,
        },
    )


def sync_timing(data_dir: Path, storage, postgres, limit: int = 3) -> int:
    synced = 0
    for path in sorted(pending_dir(data_dir).glob("*.json.gz"))[:limit]:
        payload = read_json_gz(path)
        for record in payload["raw_payloads"]:
            storage.upload_raw_payload(record)
        postgres.insert_facts(payload["tables"])
        mark_synced(path, data_dir)
        synced += 1
    return synced


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cities")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    load_dotenv()
    settings = settings_from_env()
    data_dir = args.data_dir or settings.collector_data_dir / "timing"
    storage, postgres = build_clients(settings)
    if not args.dry_run and (storage is None or postgres is None):
        raise RuntimeError("timing sync requires both database and raw storage credentials")
    data_dir.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(data_dir).free < 512 * 1024 * 1024:
        raise RuntimeError(
            "less than 512 MiB free; timing capture stopped to protect hourly collection"
        )
    path = collect_timing(
        data_dir,
        settings.nws_user_agent,
        settings.supabase_storage_bucket,
        parse_cities(args.cities),
    )
    run = read_json_gz(path)["tables"]["collector_runs"][0]
    synced = 0 if args.dry_run else sync_timing(data_dir, storage, postgres)
    print(
        json.dumps(
            {
                "spool": str(path),
                "run_id": run["collector_run_id"],
                "cities_completed": run["city_count_completed"],
                "cities_attempted": run["city_count_attempted"],
                "provider_errors": run["provider_error_count"],
                "raw_payloads": run["raw_payload_count"],
                "synced_spool_files": synced,
            }
        ),
        flush=True,
    )
    return 0 if run["city_count_completed"] == run["city_count_attempted"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from None
