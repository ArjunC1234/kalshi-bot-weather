"""Hourly Supabase collector for Kalshi weather-market research.

This collector is intentionally independent from the existing pilot-v1 archive
and demo trading bot. It writes an immutable local spool first, then syncs to
Supabase Storage/Postgres with deterministic IDs so retries are safe.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import socket
import sys
import tempfile
import time as time_module
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests


SCHEMA_VERSION = 2
DEFAULT_DATA_DIR = Path("collector_spool")
DEFAULT_BUCKET = "weather-research-raw"
KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
NWS_BASE_URL = "https://api.weather.gov"
OPEN_METEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
OPEN_METEO_GFS_URL = "https://api.open-meteo.com/v1/gfs"
DEFAULT_TIMEOUT_SECONDS = 25.0
DEFAULT_RETRIES = 1
SNAPSHOT_HOUR_FORMAT = "%Y-%m-%dT%H:00:00Z"
MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
ENSEMBLE_MODELS = {
    "GEFS": ("gfs_seamless", "ncep_gefs_seamless"),
    "ECMWF IFS": ("ecmwf_ifs025", "ecmwf_ifs025_ensemble"),
    "ICON EPS": ("icon_seamless", "icon_seamless_eps"),
    "GEM": ("gem_global", "gem_global_ensemble"),
}

TABLES = (
    "collector_runs",
    "raw_payloads",
    "events",
    "market_snapshots",
    "weather_snapshots",
    "model_outputs",
    "settlements",
    "provider_errors",
)

RUN_CHILD_TABLES = (
    "provider_errors",
    "model_outputs",
    "weather_snapshots",
    "market_snapshots",
    "events",
    "raw_payloads",
)


class DataError(RuntimeError):
    """Raised when source data cannot be normalized."""


@dataclass(frozen=True)
class City:
    key: str
    name: str
    series_ticker: str
    station_id: str
    latitude: float
    longitude: float
    timezone_name: str
    standard_utc_offset_hours: int
    settlement_aliases: tuple[str, ...]


@dataclass(frozen=True)
class Bracket:
    ticker: str
    label: str
    lower: int | None
    upper: int | None


@dataclass(frozen=True)
class EnsembleMember:
    model: str
    full_high_f: float
    remaining_high_f: float | None


CITIES = (
    City("nyc", "New York City", "KXHIGHNY", "KNYC", 40.77898, -73.96925, "America/New_York", -5, ("central park",)),
    City("mia", "Miami", "KXHIGHMIA", "KMIA", 25.79536, -80.29012, "America/New_York", -5, ("miami international airport",)),
    City("la", "Los Angeles", "KXHIGHLAX", "KLAX", 33.93817, -118.38660, "America/Los_Angeles", -8, ("los angeles airport",)),
    City("den", "Denver", "KXHIGHDEN", "KDEN", 39.84657, -104.65623, "America/Denver", -7, ("denver, co", "denver international airport")),
    City("aus", "Austin", "KXHIGHAUS", "KAUS", 30.19453, -97.66988, "America/Chicago", -6, ("austin bergstrom",)),
    City("okc", "Oklahoma City", "KXHIGHTOKC", "KOKC", 35.39309, -97.60073, "America/Chicago", -6, ("oklahoma city", "will rogers")),
)

INIT_SQL = r"""
create table if not exists collector_runs (
  collector_run_id text primary key,
  schema_version integer not null,
  started_at_utc timestamptz not null,
  completed_at_utc timestamptz,
  snapshot_hour_utc timestamptz not null,
  server_hostname text,
  collector_version text,
  collector_source_hash text,
  config_hash text,
  collection_interval_minutes integer,
  city_count_attempted integer,
  city_count_completed integer,
  provider_error_count integer,
  raw_payload_count integer,
  normalized_row_count integer,
  spool_status text,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists raw_payloads (
  raw_payload_id text primary key,
  collector_run_id text references collector_runs(collector_run_id),
  city text,
  event_ticker text,
  target_date date,
  snapshot_hour_utc timestamptz not null,
  provider text not null,
  endpoint_name text not null,
  method text not null default 'GET',
  url text not null,
  params jsonb not null default '{}'::jsonb,
  requested_at_utc timestamptz not null,
  received_at_utc timestamptz not null,
  latency_ms integer,
  status_code integer,
  success boolean not null,
  error_type text,
  error_message text,
  content_sha256 text not null,
  compressed_size_bytes integer not null,
  storage_bucket text not null,
  storage_path text not null,
  schema_version integer not null
);

create table if not exists events (
  event_id text primary key,
  collector_run_id text references collector_runs(collector_run_id),
  city text not null,
  city_name text not null,
  station_id text not null,
  latitude double precision not null,
  longitude double precision not null,
  timezone_name text not null,
  standard_utc_offset_hours integer not null,
  series_ticker text not null,
  event_ticker text not null,
  target_date date not null,
  snapshot_hour_utc timestamptz not null,
  climate_window_start_utc timestamptz not null,
  climate_window_end_utc timestamptz not null,
  market_close_time_utc timestamptz,
  is_active_climate_window boolean not null,
  hours_since_window_start double precision,
  hours_until_window_end double precision,
  metadata jsonb not null default '{}'::jsonb,
  unique(city, event_ticker, snapshot_hour_utc)
);

create table if not exists market_snapshots (
  market_snapshot_id text primary key,
  collector_run_id text references collector_runs(collector_run_id),
  city text not null,
  target_date date not null,
  snapshot_hour_utc timestamptz not null,
  event_ticker text not null,
  market_ticker text not null,
  bracket_index integer not null,
  bracket_label text,
  bracket_lower_f integer,
  bracket_upper_f integer,
  is_lower_tail boolean not null,
  is_upper_tail boolean not null,
  yes_bid_dollars double precision,
  yes_ask_dollars double precision,
  no_bid_dollars double precision,
  no_ask_dollars double precision,
  last_price_dollars double precision,
  previous_yes_bid_dollars double precision,
  previous_yes_ask_dollars double precision,
  previous_price_dollars double precision,
  volume double precision,
  volume_24h double precision,
  liquidity_dollars double precision,
  open_interest double precision,
  yes_bid_size double precision,
  yes_ask_size double precision,
  no_bid_size double precision,
  no_ask_size double precision,
  yes_midpoint double precision,
  yes_spread double precision,
  normalized_market_midpoint_probability double precision,
  market_top_ticker text,
  market_top_probability double precision,
  market_top_two_gap double precision,
  sum_yes_asks double precision,
  market_overround_ask double precision,
  raw_payload_id text references raw_payloads(raw_payload_id),
  metadata jsonb not null default '{}'::jsonb,
  unique(city, event_ticker, snapshot_hour_utc, market_ticker)
);

create table if not exists weather_snapshots (
  weather_snapshot_id text primary key,
  collector_run_id text references collector_runs(collector_run_id),
  city text not null,
  target_date date not null,
  event_ticker text not null,
  snapshot_hour_utc timestamptz not null,
  nws_anchor_high_f double precision,
  nws_daily_daytime_high_f double precision,
  nws_hourly_window_max_f double precision,
  nws_next_3h_max_f double precision,
  nws_next_6h_max_f double precision,
  nws_next_8h_max_f double precision,
  nws_remaining_day_max_f double precision,
  observed_high_so_far_f double precision,
  latest_observation_time_utc timestamptz,
  latest_observation_temp_f double precision,
  observation_age_seconds double precision,
  warming_rate_last_1h_f_per_hour double precision,
  warming_rate_last_3h_f_per_hour double precision,
  ensemble_raw_median_high_f double precision,
  ensemble_raw_mean_high_f double precision,
  ensemble_member_stddev_f double precision,
  ensemble_family_count integer,
  ensemble_member_count integer,
  hrrr_projected_high_f double precision,
  hrrr_next_3h_max_f double precision,
  hrrr_next_6h_max_f double precision,
  hrrr_next_8h_max_f double precision,
  hrrr_next_3h_slope_f_per_hour double precision,
  hrrr_next_6h_slope_f_per_hour double precision,
  hrrr_next_8h_slope_f_per_hour double precision,
  nbm_projected_high_f double precision,
  nbm_next_3h_max_f double precision,
  nbm_next_6h_max_f double precision,
  nbm_next_8h_max_f double precision,
  nbm_next_3h_slope_f_per_hour double precision,
  nbm_next_6h_slope_f_per_hour double precision,
  nbm_next_8h_slope_f_per_hour double precision,
  all_weather_sources_range_f double precision,
  weather_source_stddev_f double precision,
  nws_hrrr_disagreement_f double precision,
  nws_nbm_disagreement_f double precision,
  hrrr_nbm_disagreement_f double precision,
  source_payload_ids jsonb not null default '{}'::jsonb,
  features jsonb not null default '{}'::jsonb,
  unique(city, event_ticker, snapshot_hour_utc)
);

create table if not exists model_outputs (
  model_output_id text primary key,
  collector_run_id text references collector_runs(collector_run_id),
  city text not null,
  target_date date not null,
  event_ticker text not null,
  snapshot_hour_utc timestamptz not null,
  model_name text not null,
  model_version text,
  probabilities jsonb not null,
  top_ticker text,
  top_probability double precision,
  second_ticker text,
  second_probability double precision,
  top_two_gap double precision,
  entropy double precision,
  features jsonb not null default '{}'::jsonb,
  unique(city, event_ticker, snapshot_hour_utc, model_name)
);

create table if not exists settlements (
  settlement_id text primary key,
  collector_run_id text,
  city text not null,
  target_date date not null,
  event_ticker text not null,
  settled_at_utc timestamptz not null,
  winner_ticker text not null,
  winner_label text,
  settlement_temperature_f double precision,
  settlement_bracket_index integer,
  raw_payload_id text,
  validation_status text not null,
  warnings jsonb not null default '[]'::jsonb,
  unique(city, event_ticker)
);

create table if not exists provider_errors (
  provider_error_id text primary key,
  collector_run_id text references collector_runs(collector_run_id),
  city text,
  event_ticker text,
  target_date date,
  snapshot_hour_utc timestamptz not null,
  provider text not null,
  endpoint_name text not null,
  error_type text not null,
  error_message text not null,
  requested_at_utc timestamptz,
  metadata jsonb not null default '{}'::jsonb
);
"""


@dataclass(frozen=True)
class RawPayload:
    raw_payload_id: str
    collector_run_id: str
    city: str | None
    event_ticker: str | None
    target_date: str | None
    snapshot_hour_utc: str
    provider: str
    endpoint_name: str
    method: str
    url: str
    params: dict[str, Any]
    requested_at_utc: str
    received_at_utc: str
    latency_ms: int
    status_code: int | None
    success: bool
    error_type: str | None
    error_message: str | None
    content_sha256: str
    compressed_size_bytes: int
    storage_bucket: str
    storage_path: str
    schema_version: int
    payload: Any


class CollectorError(RuntimeError):
    """Raised for collector-specific failures."""


class HttpRecorder:
    def __init__(
        self,
        user_agent: str,
        collector_run_id: str,
        snapshot_hour: datetime,
        bucket: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.collector_run_id = collector_run_id
        self.snapshot_hour = snapshot_hour
        self.bucket = bucket
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        self.raw_payloads: list[RawPayload] = []
        self.errors: list[dict[str, Any]] = []

    def get_json(
        self,
        provider: str,
        endpoint_name: str,
        url: str,
        params: dict[str, Any] | None = None,
        city: str | None = None,
        event_ticker: str | None = None,
        target_date: str | None = None,
        required: bool = False,
    ) -> dict[str, Any] | None:
        params = params or {}
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            requested = datetime.now(UTC)
            status_code: int | None = None
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                status_code = response.status_code
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise CollectorError(f"{endpoint_name} returned non-object JSON")
                received = datetime.now(UTC)
                self.record_payload(
                    provider,
                    endpoint_name,
                    url,
                    params,
                    requested,
                    received,
                    status_code,
                    payload,
                    city,
                    event_ticker,
                    target_date,
                )
                return payload
            except Exception as exc:  # noqa: PERF203 - clarity matters here
                last_error = exc
                if attempt < self.retries:
                    time_module.sleep(0.4 * (attempt + 1))
                    continue
                received = datetime.now(UTC)
                self.record_error(
                    provider,
                    endpoint_name,
                    exc,
                    requested,
                    received,
                    status_code,
                    city,
                    event_ticker,
                    target_date,
                    {"url": url, "params": params},
                )
        if required:
            raise CollectorError(f"{endpoint_name} failed: {last_error}")
        return None

    def record_payload(
        self,
        provider: str,
        endpoint_name: str,
        url: str,
        params: dict[str, Any],
        requested: datetime,
        received: datetime,
        status_code: int,
        payload: Any,
        city: str | None,
        event_ticker: str | None,
        target_date: str | None,
    ) -> RawPayload:
        compressed = gzip_json_bytes(payload)
        digest = sha256_bytes(canonical_json_bytes(payload))
        snapshot_hour = self.snapshot_hour.strftime(SNAPSHOT_HOUR_FORMAT)
        storage_path = raw_storage_path(
            provider,
            target_date or "unknown-date",
            city or "all",
            event_ticker or "unknown-event",
            snapshot_hour,
            digest,
        )
        raw_id = deterministic_id(
            "raw",
            city or "",
            event_ticker or "",
            target_date or "",
            snapshot_hour,
            provider,
            endpoint_name,
            digest,
        )
        record = RawPayload(
            raw_payload_id=raw_id,
            collector_run_id=self.collector_run_id,
            city=city,
            event_ticker=event_ticker,
            target_date=target_date,
            snapshot_hour_utc=self.snapshot_hour.isoformat(),
            provider=provider,
            endpoint_name=endpoint_name,
            method="GET",
            url=url,
            params=params,
            requested_at_utc=requested.isoformat(),
            received_at_utc=received.isoformat(),
            latency_ms=round((received - requested).total_seconds() * 1000),
            status_code=status_code,
            success=True,
            error_type=None,
            error_message=None,
            content_sha256=digest,
            compressed_size_bytes=len(compressed),
            storage_bucket=self.bucket,
            storage_path=storage_path,
            schema_version=SCHEMA_VERSION,
            payload=payload,
        )
        self.raw_payloads.append(record)
        return record

    def retag_latest_payload(
        self,
        provider: str,
        endpoint_name: str,
        city: str,
        event_ticker: str,
        target_date: str,
    ) -> RawPayload | None:
        for index in range(len(self.raw_payloads) - 1, -1, -1):
            record = self.raw_payloads[index]
            if record.provider != provider or record.endpoint_name != endpoint_name or record.city != city:
                continue
            snapshot_hour = self.snapshot_hour.strftime(SNAPSHOT_HOUR_FORMAT)
            updated = replace(
                record,
                raw_payload_id=deterministic_id(
                    "raw",
                    city,
                    event_ticker,
                    target_date,
                    snapshot_hour,
                    provider,
                    endpoint_name,
                    record.content_sha256,
                ),
                event_ticker=event_ticker,
                target_date=target_date,
                storage_path=raw_storage_path(
                    provider,
                    target_date,
                    city,
                    event_ticker,
                    snapshot_hour,
                    record.content_sha256,
                ),
            )
            self.raw_payloads[index] = updated
            return updated
        return None

    def record_error(
        self,
        provider: str,
        endpoint_name: str,
        exc: Exception,
        requested: datetime,
        received: datetime,
        status_code: int | None,
        city: str | None,
        event_ticker: str | None,
        target_date: str | None,
        metadata: dict[str, Any],
    ) -> None:
        snapshot_hour = self.snapshot_hour.isoformat()
        row = {
            "provider_error_id": deterministic_id(
                "err",
                self.collector_run_id,
                city or "",
                event_ticker or "",
                provider,
                endpoint_name,
                requested.isoformat(),
            ),
            "collector_run_id": self.collector_run_id,
            "city": city,
            "event_ticker": event_ticker,
            "target_date": target_date,
            "snapshot_hour_utc": snapshot_hour,
            "provider": provider,
            "endpoint_name": endpoint_name,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "requested_at_utc": requested.isoformat(),
            "metadata": {**metadata, "status_code": status_code, "received_at_utc": received.isoformat()},
        }
        self.errors.append(row)


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def gzip_json_bytes(payload: Any) -> bytes:
    return gzip.compress(canonical_json_bytes(payload), compresslevel=6, mtime=0)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def deterministic_id(*parts: Any) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return digest[:32]


def source_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def config_hash(values: dict[str, Any]) -> str:
    safe = {key: value for key, value in values.items() if "KEY" not in key and "SECRET" not in key}
    return sha256_bytes(canonical_json_bytes(safe))


def utc_hour(value: datetime | None = None) -> datetime:
    current = (value or datetime.now(UTC)).astimezone(UTC)
    return current.replace(minute=0, second=0, microsecond=0)


def parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def market_float(market: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = parse_float(market.get(key))
        if value is not None:
            return value
    return None


def raw_storage_path(
    provider: str,
    target_date: str,
    city: str,
    event_ticker: str,
    snapshot_hour: str,
    digest: str,
) -> str:
    date_part = target_date or snapshot_hour[:10]
    safe_hour = snapshot_hour.replace(":", "").replace("-", "").replace("Z", "Z")
    return f"raw/{provider}/{date_part}/{city}/{event_ticker}/{safe_hour}/{digest}.json.gz"


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def parse_event_date(event_ticker: str) -> date:
    match = re.search(
        r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(?:-|$)",
        event_ticker.upper(),
    )
    if not match:
        raise DataError(f"cannot parse event date from {event_ticker!r}")
    return date(2000 + int(match.group(1)), MONTHS[match.group(2)], int(match.group(3)))


def c_to_f(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


def parse_bracket(market: dict[str, Any]) -> Bracket:
    ticker = str(market.get("ticker", ""))
    raw_label = market.get("yes_sub_title") or market.get("subtitle")
    if not ticker or not isinstance(raw_label, str):
        raise DataError("market is missing ticker or bracket label")
    label = raw_label.replace("Ã‚", "").strip()
    cleaned = label.lower().replace("fahrenheit", "").replace("Â°", "").replace("°", "")
    cleaned = cleaned.replace("â€“", "-").replace("â€”", "-")
    match = re.search(r"(-?\d+)\s*(?:to|-)\s*(-?\d+)", cleaned)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise DataError(f"invalid bracket range {label!r}")
        return Bracket(ticker, label, lower, upper)
    match = re.search(r"(-?\d+)\s*(?:or\s*)?(?:below|lower|less)", cleaned)
    if match:
        return Bracket(ticker, label, None, int(match.group(1)))
    match = re.search(r"(-?\d+)\s*(?:or\s*)?(?:above|higher|more)", cleaned)
    if match:
        return Bracket(ticker, label, int(match.group(1)), None)
    match = re.search(r"(?:less than|below)\s*(-?\d+)", cleaned)
    if match:
        return Bracket(ticker, label, None, int(match.group(1)) - 1)
    match = re.search(r"(?:greater than|above)\s*(-?\d+)", cleaned)
    if match:
        return Bracket(ticker, label, int(match.group(1)) + 1, None)
    raise DataError(f"cannot parse bracket label {label!r} for {ticker}")


def validate_brackets(brackets: Iterable[Bracket]) -> tuple[Bracket, ...]:
    ordered = tuple(
        sorted(brackets, key=lambda item: float("-inf") if item.lower is None else item.lower)
    )
    if len(ordered) < 2:
        raise DataError("event must contain at least two brackets")
    if ordered[0].lower is not None or ordered[-1].upper is not None:
        raise DataError("event brackets must have lower and upper tails")
    for left, right in zip(ordered, ordered[1:], strict=False):
        if left.upper is None or right.lower is None or left.upper + 1 != right.lower:
            raise DataError(f"bracket gap or overlap between {left.label!r} and {right.label!r}")
    return ordered


def select_event(
    markets: list[dict[str, Any]], requested_date: date | None, today: date | None = None
) -> tuple[date, list[dict[str, Any]]]:
    grouped: dict[date, list[dict[str, Any]]] = {}
    for market in markets:
        event_ticker = market.get("event_ticker")
        if not isinstance(event_ticker, str):
            continue
        try:
            grouped.setdefault(parse_event_date(event_ticker), []).append(market)
        except DataError:
            continue
    if requested_date is not None:
        if requested_date not in grouped:
            raise DataError(f"no open event exists for {requested_date.isoformat()}")
        return requested_date, grouped[requested_date]
    current = today or date.today()
    upcoming = sorted(event_date for event_date in grouped if event_date >= current)
    if not upcoming:
        raise DataError("no current or future open event exists")
    selected = upcoming[0]
    return selected, grouped[selected]


def weighted_quantile(values: Iterable[float], weights: Iterable[float], quantile: float) -> float:
    pairs = sorted(zip(values, weights, strict=True), key=lambda pair: pair[0])
    if not pairs:
        raise ValueError("weighted quantile requires samples")
    total = sum(weight for _, weight in pairs)
    threshold = quantile * total
    cumulative = 0.0
    for index, (value, weight) in enumerate(pairs):
        cumulative += weight
        if cumulative >= threshold:
            if math.isclose(cumulative, threshold, abs_tol=1e-12) and index + 1 < len(pairs):
                return (value + pairs[index + 1][0]) / 2.0
            return value
    return pairs[-1][0]


def extract_ensemble_members(
    hourly: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
    observed_at: datetime | None = None,
) -> tuple[list[EnsembleMember], tuple[tuple[str, int], ...], tuple[str, ...]]:
    raw_times = hourly.get("time")
    if not isinstance(raw_times, list):
        raise DataError("ensemble response is missing hourly timestamps")
    timestamps = [parse_datetime(str(value)).astimezone(UTC) for value in raw_times]
    full_indexes = [
        index for index, timestamp in enumerate(timestamps) if window_start <= timestamp < window_end
    ]
    remaining_indexes = [
        index
        for index in full_indexes
        if observed_at is None or timestamps[index] > observed_at
    ]
    members: list[EnsembleMember] = []
    model_counts: list[tuple[str, int]] = []
    missing_models: list[str] = []
    for model, (_, suffix) in ENSEMBLE_MODELS.items():
        fields = sorted(
            key
            for key in hourly
            if key.startswith("temperature_2m") and key.endswith(f"_{suffix}")
        )
        model_members: list[EnsembleMember] = []
        for field in fields:
            values = hourly.get(field)
            if not isinstance(values, list) or len(values) != len(timestamps):
                continue
            full_values = [float(values[index]) for index in full_indexes if values[index] is not None]
            remaining_values = [
                float(values[index]) for index in remaining_indexes if values[index] is not None
            ]
            if full_values:
                model_members.append(
                    EnsembleMember(
                        model,
                        max(full_values),
                        max(remaining_values) if remaining_values else None,
                    )
                )
        if len(model_members) < 10:
            missing_models.append(model)
            continue
        members.extend(model_members)
        model_counts.append((model, len(model_members)))
    if len(model_counts) < 3:
        available = ", ".join(f"{name}={count}" for name, count in model_counts) or "none"
        raise DataError(f"ensemble returned fewer than three usable model families ({available})")
    warnings = tuple(
        f"{model} ensemble is unavailable or has fewer than 10 members"
        for model in missing_models
    )
    return members, tuple(model_counts), warnings


def model_balanced_weights(
    members: Iterable[EnsembleMember], model_counts: Iterable[tuple[str, int]]
) -> list[float]:
    rows = list(members)
    counts = dict(model_counts)
    if not rows or not counts:
        raise DataError("cannot weight an empty ensemble")
    model_weight = 1.0 / len(counts)
    return [model_weight / counts[member.model] for member in rows]


def climate_day_start(city: City, target_date: date) -> datetime:
    standard_zone = timezone(timedelta(hours=city.standard_utc_offset_hours))
    return datetime.combine(target_date, time.min, tzinfo=standard_zone).astimezone(UTC)


def settlement_window(city: City, target_date: date, markets: Iterable[dict[str, Any]]) -> tuple[datetime, datetime]:
    rows = list(markets)
    close_times = {
        parse_datetime(str(market["close_time"])).astimezone(UTC)
        for market in rows
        if isinstance(market.get("close_time"), str)
    }
    if len(close_times) == 1:
        close = close_times.pop()
        expected = datetime.combine(
            target_date + timedelta(days=1),
            time.min,
            tzinfo=timezone(timedelta(hours=city.standard_utc_offset_hours)),
        ).astimezone(UTC)
        if close in (expected, expected - timedelta(minutes=1)):
            return expected - timedelta(hours=24), expected
    start = climate_day_start(city, target_date)
    return start, start + timedelta(hours=24)


def city_by_key(key: str) -> City:
    for city in CITIES:
        if city.key == key:
            return city
    raise DataError(f"unknown city {key!r}")


def point_mass_bracket_index(brackets: tuple[Bracket, ...], temperature_f: float) -> int | None:
    rounded = math.floor(float(temperature_f) + 0.5)
    for index, bracket in enumerate(brackets):
        if bracket.lower is not None and rounded < bracket.lower:
            continue
        if bracket.upper is not None and rounded > bracket.upper:
            continue
        return index
    return None


def linear_slope(values: list[tuple[datetime, float]]) -> float | None:
    if len(values) < 2:
        return None
    start = values[0][0]
    xs = [(timestamp - start).total_seconds() / 3600.0 for timestamp, _ in values]
    ys = [value for _, value in values]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom <= 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denom


def max_in_next_hours(rows: list[tuple[datetime, float]], as_of: datetime, hours: int) -> float | None:
    end = as_of + timedelta(hours=hours)
    values = [value for timestamp, value in rows if as_of <= timestamp <= end]
    return max(values) if values else None


def slope_in_next_hours(rows: list[tuple[datetime, float]], as_of: datetime, hours: int) -> float | None:
    end = as_of + timedelta(hours=hours)
    return linear_slope([(timestamp, value) for timestamp, value in rows if as_of <= timestamp <= end])


def observations_from_payload(payload: dict[str, Any], window_start: datetime, as_of: datetime) -> list[tuple[datetime, float]]:
    features = payload.get("features")
    if not isinstance(features, list):
        return []
    values: list[tuple[datetime, float]] = []
    for feature in features:
        try:
            props = feature["properties"]
            timestamp = parse_datetime(str(props["timestamp"])).astimezone(UTC)
            raw_temp = props.get("temperature", {}).get("value")
            if raw_temp is None or not (window_start <= timestamp <= as_of):
                continue
            values.append((timestamp, c_to_f(float(raw_temp))))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(values)


def hourly_rows_from_payload(payload: dict[str, Any], window_start: datetime, window_end: datetime) -> list[tuple[datetime, float]]:
    periods = payload.get("properties", {}).get("periods", [])
    rows: list[tuple[datetime, float]] = []
    for period in periods if isinstance(periods, list) else []:
        try:
            timestamp = parse_datetime(str(period["startTime"])).astimezone(UTC)
            value = float(period["temperature"])
            if str(period.get("temperatureUnit", "F")).upper() == "C":
                value = c_to_f(value)
            if window_start <= timestamp < window_end:
                rows.append((timestamp, value))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(rows)


def daily_high_from_payload(payload: dict[str, Any], window_start: datetime, window_end: datetime) -> float | None:
    periods = payload.get("properties", {}).get("periods", [])
    highs: list[float] = []
    for period in periods if isinstance(periods, list) else []:
        try:
            timestamp = parse_datetime(str(period["startTime"])).astimezone(UTC)
            if not bool(period.get("isDaytime")) or not (window_start <= timestamp < window_end):
                continue
            value = float(period["temperature"])
            if str(period.get("temperatureUnit", "F")).upper() == "C":
                value = c_to_f(value)
            highs.append(value)
        except (KeyError, TypeError, ValueError):
            continue
    return max(highs) if highs else None


def open_meteo_rows(payload: dict[str, Any], window_start: datetime, window_end: datetime) -> list[tuple[datetime, float]]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return []
    times = hourly.get("time")
    temps = hourly.get("temperature_2m")
    if not isinstance(times, list) or not isinstance(temps, list):
        return []
    rows: list[tuple[datetime, float]] = []
    for raw_time, raw_temp in zip(times, temps, strict=False):
        if raw_temp is None:
            continue
        timestamp = parse_datetime(str(raw_time)).astimezone(UTC)
        if window_start <= timestamp < window_end:
            rows.append((timestamp, float(raw_temp)))
    return sorted(rows)


def time_series_features(
    prefix: str,
    rows: list[tuple[datetime, float]],
    as_of: datetime,
    observed_high: float | None,
) -> dict[str, float | str | None]:
    remaining = [(timestamp, value) for timestamp, value in rows if timestamp >= as_of]
    full_high = max((value for _, value in rows), default=None)
    remaining_high = max((value for _, value in remaining), default=None)
    projected_candidates = [value for value in (observed_high, remaining_high) if value is not None]
    projected_high = max(projected_candidates) if projected_candidates else full_high
    peak_time = None
    if remaining:
        peak_time = max(remaining, key=lambda item: item[1])[0].isoformat()
    return {
        f"{prefix}_full_window_high_f": full_high,
        f"{prefix}_remaining_forecast_high_f": remaining_high,
        f"{prefix}_projected_high_f": projected_high,
        f"{prefix}_next_3h_max_f": max_in_next_hours(rows, as_of, 3),
        f"{prefix}_next_6h_max_f": max_in_next_hours(rows, as_of, 6),
        f"{prefix}_next_8h_max_f": max_in_next_hours(rows, as_of, 8),
        f"{prefix}_next_3h_slope_f_per_hour": slope_in_next_hours(rows, as_of, 3),
        f"{prefix}_next_6h_slope_f_per_hour": slope_in_next_hours(rows, as_of, 6),
        f"{prefix}_next_8h_slope_f_per_hour": slope_in_next_hours(rows, as_of, 8),
        f"{prefix}_peak_time_utc": peak_time,
    }


def market_feature_rows(
    collector_run_id: str,
    snapshot_hour: datetime,
    city: City,
    target_date: date,
    event_ticker: str,
    brackets: tuple[Bracket, ...],
    markets: list[dict[str, Any]],
    raw_payload_id: str | None,
) -> list[dict[str, Any]]:
    by_ticker = {str(market.get("ticker")): market for market in markets}
    midpoints: list[float] = []
    asks: list[float] = []
    for bracket in brackets:
        market = by_ticker.get(bracket.ticker, {})
        bid = parse_float(market.get("yes_bid_dollars") or market.get("yes_bid"))
        ask = parse_float(market.get("yes_ask_dollars") or market.get("yes_ask"))
        asks.append(ask or 0.0)
        midpoints.append((bid + ask) / 2.0 if bid is not None and ask is not None else 0.0)
    midpoint_total = sum(midpoints)
    normalized = [value / midpoint_total if midpoint_total > 0 else 0.0 for value in midpoints]
    top_index = max(range(len(normalized)), key=lambda index: normalized[index]) if normalized else 0
    sorted_probs = sorted(normalized, reverse=True)
    top_gap = sorted_probs[0] - sorted_probs[1] if len(sorted_probs) > 1 else sorted_probs[0]
    sum_asks = sum(asks)
    rows: list[dict[str, Any]] = []
    for index, bracket in enumerate(brackets):
        market = by_ticker.get(bracket.ticker, {})
        bid = parse_float(market.get("yes_bid_dollars") or market.get("yes_bid"))
        ask = parse_float(market.get("yes_ask_dollars") or market.get("yes_ask"))
        yes_bid_size = market_float(market, "yes_bid_size", "yes_bid_size_fp")
        yes_ask_size = market_float(market, "yes_ask_size", "yes_ask_size_fp")
        raw_no_bid_size = market_float(market, "no_bid_size", "no_bid_size_fp")
        raw_no_ask_size = market_float(market, "no_ask_size", "no_ask_size_fp")
        no_bid_size = raw_no_bid_size if raw_no_bid_size is not None else yes_ask_size
        no_ask_size = raw_no_ask_size if raw_no_ask_size is not None else yes_bid_size
        row = {
            "market_snapshot_id": deterministic_id(
                "market", city.key, event_ticker, snapshot_hour.isoformat(), bracket.ticker
            ),
            "collector_run_id": collector_run_id,
            "city": city.key,
            "target_date": target_date.isoformat(),
            "snapshot_hour_utc": snapshot_hour.isoformat(),
            "event_ticker": event_ticker,
            "market_ticker": bracket.ticker,
            "bracket_index": index,
            "bracket_label": bracket.label,
            "bracket_lower_f": bracket.lower,
            "bracket_upper_f": bracket.upper,
            "is_lower_tail": bracket.lower is None,
            "is_upper_tail": bracket.upper is None,
            "yes_bid_dollars": bid,
            "yes_ask_dollars": ask,
            "no_bid_dollars": parse_float(market.get("no_bid_dollars") or market.get("no_bid")),
            "no_ask_dollars": parse_float(market.get("no_ask_dollars") or market.get("no_ask")),
            "last_price_dollars": parse_float(market.get("last_price_dollars") or market.get("last_price")),
            "previous_yes_bid_dollars": parse_float(market.get("previous_yes_bid_dollars")),
            "previous_yes_ask_dollars": parse_float(market.get("previous_yes_ask_dollars")),
            "previous_price_dollars": parse_float(market.get("previous_price_dollars")),
            "volume": market_float(market, "volume", "volume_fp"),
            "volume_24h": market_float(market, "volume_24h", "volume_24h_fp"),
            "liquidity_dollars": parse_float(market.get("liquidity_dollars")),
            "open_interest": parse_float(market.get("open_interest") or market.get("open_interest_fp")),
            "yes_bid_size": yes_bid_size,
            "yes_ask_size": yes_ask_size,
            "no_bid_size": no_bid_size,
            "no_ask_size": no_ask_size,
            "yes_midpoint": (bid + ask) / 2.0 if bid is not None and ask is not None else None,
            "yes_spread": ask - bid if bid is not None and ask is not None else None,
            "normalized_market_midpoint_probability": normalized[index],
            "market_top_ticker": brackets[top_index].ticker,
            "market_top_probability": normalized[top_index],
            "market_top_two_gap": top_gap,
            "sum_yes_asks": sum_asks,
            "market_overround_ask": sum_asks - 1.0,
            "raw_payload_id": raw_payload_id,
            "metadata": {
                "status": market.get("status"),
                "open_time": market.get("open_time"),
                "close_time": market.get("close_time"),
                "expiration_time": market.get("expiration_time"),
                "expected_expiration_time": market.get("expected_expiration_time"),
                "can_close_early": market.get("can_close_early"),
                "rules_primary": market.get("rules_primary"),
                "floor_strike": market.get("floor_strike"),
                "cap_strike": market.get("cap_strike"),
                "volume_source": "volume" if market.get("volume") is not None else "volume_fp",
                "volume_24h_source": "volume_24h" if market.get("volume_24h") is not None else "volume_24h_fp",
                "no_bid_size_inferred_from_yes_ask_size": raw_no_bid_size is None and yes_ask_size is not None,
                "no_ask_size_inferred_from_yes_bid_size": raw_no_ask_size is None and yes_bid_size is not None,
            },
        }
        rows.append(row)
    return rows


def model_output_row(
    collector_run_id: str,
    snapshot_hour: datetime,
    city: City,
    target_date: date,
    event_ticker: str,
    market_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not market_rows:
        return None
    probabilities = {
        str(row["market_ticker"]): float(row["normalized_market_midpoint_probability"] or 0.0)
        for row in market_rows
    }
    ordered = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
    top = ordered[0]
    second = ordered[1] if len(ordered) > 1 else ("", 0.0)
    entropy = -sum(value * math.log(value) for value in probabilities.values() if value > 0)
    return {
        "model_output_id": deterministic_id(
            "model", city.key, event_ticker, snapshot_hour.isoformat(), "market_midpoint"
        ),
        "collector_run_id": collector_run_id,
        "city": city.key,
        "target_date": target_date.isoformat(),
        "event_ticker": event_ticker,
        "snapshot_hour_utc": snapshot_hour.isoformat(),
        "model_name": "market_midpoint",
        "model_version": "collector-v1",
        "probabilities": probabilities,
        "top_ticker": top[0],
        "top_probability": top[1],
        "second_ticker": second[0],
        "second_probability": second[1],
        "top_two_gap": top[1] - second[1],
        "entropy": entropy,
        "features": {},
    }


def collect_city(
    recorder: HttpRecorder,
    collector_run_id: str,
    snapshot_hour: datetime,
    city: City,
) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {
        "events": [],
        "market_snapshots": [],
        "weather_snapshots": [],
        "model_outputs": [],
        "settlements": [],
    }
    markets_payload = recorder.get_json(
        "kalshi",
        "kalshi_open_markets",
        f"{KALSHI_BASE_URL}/markets",
        {"series_ticker": city.series_ticker, "status": "open", "limit": 1000},
        city=city.key,
        required=True,
    )
    markets = [item for item in (markets_payload or {}).get("markets", []) if isinstance(item, dict)]
    target_date, event_markets = select_event(markets, None, today=snapshot_hour.date() - timedelta(days=1))
    event_ticker = str(event_markets[0]["event_ticker"])
    recorder.retag_latest_payload(
        "kalshi",
        "kalshi_open_markets",
        city.key,
        event_ticker,
        target_date.isoformat(),
    )
    brackets = validate_brackets(parse_bracket(market) for market in event_markets)
    window_start, window_end = settlement_window(city, target_date, event_markets)
    raw_market = latest_raw_id(recorder, "kalshi", "kalshi_open_markets", city.key)
    market_rows = market_feature_rows(
        collector_run_id,
        snapshot_hour,
        city,
        target_date,
        event_ticker,
        brackets,
        event_markets,
        raw_market,
    )
    rows["market_snapshots"].extend(market_rows)
    model_row = model_output_row(
        collector_run_id, snapshot_hour, city, target_date, event_ticker, market_rows
    )
    if model_row:
        rows["model_outputs"].append(model_row)

    is_active = window_start <= snapshot_hour < window_end
    rows["events"].append(
        {
            "event_id": deterministic_id("event", city.key, event_ticker, snapshot_hour.isoformat()),
            "collector_run_id": collector_run_id,
            "city": city.key,
            "city_name": city.name,
            "station_id": city.station_id,
            "latitude": city.latitude,
            "longitude": city.longitude,
            "timezone_name": city.timezone_name,
            "standard_utc_offset_hours": city.standard_utc_offset_hours,
            "series_ticker": city.series_ticker,
            "event_ticker": event_ticker,
            "target_date": target_date.isoformat(),
            "snapshot_hour_utc": snapshot_hour.isoformat(),
            "climate_window_start_utc": window_start.isoformat(),
            "climate_window_end_utc": window_end.isoformat(),
            "market_close_time_utc": event_markets[0].get("close_time"),
            "is_active_climate_window": is_active,
            "hours_since_window_start": (snapshot_hour - window_start).total_seconds() / 3600.0,
            "hours_until_window_end": (window_end - snapshot_hour).total_seconds() / 3600.0,
            "metadata": {
                "settlement_aliases": city.settlement_aliases,
                "brackets": [asdict(bracket) for bracket in brackets],
            },
        }
    )

    point = recorder.get_json(
        "nws",
        "nws_points",
        f"{NWS_BASE_URL}/points/{city.latitude:.4f},{city.longitude:.4f}",
        city=city.key,
        event_ticker=event_ticker,
        target_date=target_date.isoformat(),
    )
    props = point.get("properties", {}) if isinstance(point, dict) else {}
    daily_url = props.get("forecast")
    hourly_url = props.get("forecastHourly")
    daily = (
        recorder.get_json(
            "nws",
            "nws_daily_forecast",
            daily_url,
            city=city.key,
            event_ticker=event_ticker,
            target_date=target_date.isoformat(),
        )
        if isinstance(daily_url, str)
        else None
    )
    hourly = (
        recorder.get_json(
            "nws",
            "nws_hourly_forecast",
            hourly_url,
            city=city.key,
            event_ticker=event_ticker,
            target_date=target_date.isoformat(),
        )
        if isinstance(hourly_url, str)
        else None
    )
    observations = recorder.get_json(
        "nws",
        "nws_observations",
        f"{NWS_BASE_URL}/stations/{city.station_id}/observations",
        {
            "start": window_start.isoformat().replace("+00:00", "Z"),
            "end": min(snapshot_hour, window_end).isoformat().replace("+00:00", "Z"),
            "limit": 500,
        },
        city=city.key,
        event_ticker=event_ticker,
        target_date=target_date.isoformat(),
    )
    ensemble = recorder.get_json(
        "open_meteo",
        "open_meteo_ensemble",
        OPEN_METEO_ENSEMBLE_URL,
        {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "hourly": "temperature_2m",
            "models": ",".join(value[0] for value in ENSEMBLE_MODELS.values()),
            "forecast_days": max(3, (window_end.date() - snapshot_hour.date()).days + 1),
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
        },
        city=city.key,
        event_ticker=event_ticker,
        target_date=target_date.isoformat(),
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
            "forecast_days": max(2, (window_end.date() - snapshot_hour.date()).days + 1),
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
        },
        city=city.key,
        event_ticker=event_ticker,
        target_date=target_date.isoformat(),
    )
    nbm = recorder.get_json(
        "open_meteo",
        "open_meteo_nbm",
        OPEN_METEO_GFS_URL,
        {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m,precipitation_probability,wind_speed_10m,cloud_cover",
            "models": "ncep_nbm_conus",
            "forecast_days": max(2, (window_end.date() - snapshot_hour.date()).days + 1),
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
        },
        city=city.key,
        event_ticker=event_ticker,
        target_date=target_date.isoformat(),
    )

    rows["weather_snapshots"].append(
        build_weather_snapshot_row(
            collector_run_id,
            snapshot_hour,
            city,
            target_date,
            event_ticker,
            brackets,
            window_start,
            window_end,
            daily,
            hourly,
            observations,
            ensemble,
            hrrr,
            nbm,
            source_payload_ids(recorder, city.key, event_ticker, target_date.isoformat()),
        )
    )
    settlement = maybe_settlement_row(
        collector_run_id, snapshot_hour, city, target_date, event_ticker, brackets, event_markets
    )
    if settlement:
        rows["settlements"].append(settlement)
    return rows


def latest_raw_id(recorder: HttpRecorder, provider: str, endpoint_name: str, city: str) -> str | None:
    for record in reversed(recorder.raw_payloads):
        if record.provider == provider and record.endpoint_name == endpoint_name and record.city == city:
            return record.raw_payload_id
    return None


def source_payload_ids(
    recorder: HttpRecorder, city: str, event_ticker: str, target_date: str
) -> dict[str, str]:
    output: dict[str, str] = {}
    for record in recorder.raw_payloads:
        if record.city == city and record.event_ticker == event_ticker and record.target_date == target_date:
            output[record.endpoint_name] = record.raw_payload_id
    return output


def build_weather_snapshot_row(
    collector_run_id: str,
    snapshot_hour: datetime,
    city: City,
    target_date: date,
    event_ticker: str,
    brackets: tuple[Bracket, ...],
    window_start: datetime,
    window_end: datetime,
    daily: dict[str, Any] | None,
    hourly: dict[str, Any] | None,
    observations: dict[str, Any] | None,
    ensemble: dict[str, Any] | None,
    hrrr: dict[str, Any] | None,
    nbm: dict[str, Any] | None,
    payload_ids: dict[str, str],
) -> dict[str, Any]:
    daily_high = daily_high_from_payload(daily or {}, window_start, window_end)
    hourly_rows = hourly_rows_from_payload(hourly or {}, window_start, window_end)
    hourly_high = max((value for _, value in hourly_rows), default=None)
    nws_anchor = max([value for value in (daily_high, hourly_high) if value is not None], default=None)
    obs_rows = observations_from_payload(observations or {}, window_start, snapshot_hour)
    observed_high = max((value for _, value in obs_rows), default=None)
    latest_obs = obs_rows[-1] if obs_rows else None
    observed_bracket = point_mass_bracket_index(brackets, observed_high) if observed_high is not None else None

    ensemble_features: dict[str, Any] = {}
    if ensemble and isinstance(ensemble.get("hourly"), dict):
        try:
            observed_at = latest_obs[0] if latest_obs else None
            members, model_counts, _ = extract_ensemble_members(
                ensemble["hourly"], window_start, window_end, observed_at
            )
            weights = model_balanced_weights(members, model_counts)
            highs = [member.full_high_f for member in members]
            mean = sum(value * weight for value, weight in zip(highs, weights, strict=True))
            variance = sum(weight * (value - mean) ** 2 for value, weight in zip(highs, weights, strict=True))
            ensemble_features = {
                "ensemble_raw_median_high_f": weighted_quantile(highs, weights, 0.5),
                "ensemble_raw_mean_high_f": mean,
                "ensemble_member_stddev_f": math.sqrt(max(0.0, variance)),
                "ensemble_family_count": len(model_counts),
                "ensemble_member_count": len(members),
                "ensemble_model_counts": dict(model_counts),
            }
        except Exception as exc:
            ensemble_features = {"ensemble_error": str(exc)}

    hrrr_rows = open_meteo_rows(hrrr or {}, window_start, window_end)
    nbm_rows = open_meteo_rows(nbm or {}, window_start, window_end)
    hrrr_features = time_series_features("hrrr", hrrr_rows, snapshot_hour, observed_high)
    nbm_features = time_series_features("nbm", nbm_rows, snapshot_hour, observed_high)

    source_highs = [
        value
        for value in (
            nws_anchor,
            ensemble_features.get("ensemble_raw_median_high_f"),
            hrrr_features.get("hrrr_projected_high_f"),
            nbm_features.get("nbm_projected_high_f"),
        )
        if isinstance(value, (int, float))
    ]
    row = {
        "weather_snapshot_id": deterministic_id(
            "weather", city.key, event_ticker, snapshot_hour.isoformat()
        ),
        "collector_run_id": collector_run_id,
        "city": city.key,
        "target_date": target_date.isoformat(),
        "event_ticker": event_ticker,
        "snapshot_hour_utc": snapshot_hour.isoformat(),
        "nws_anchor_high_f": nws_anchor,
        "nws_daily_daytime_high_f": daily_high,
        "nws_hourly_window_max_f": hourly_high,
        "nws_next_3h_max_f": max_in_next_hours(hourly_rows, snapshot_hour, 3),
        "nws_next_6h_max_f": max_in_next_hours(hourly_rows, snapshot_hour, 6),
        "nws_next_8h_max_f": max_in_next_hours(hourly_rows, snapshot_hour, 8),
        "nws_remaining_day_max_f": max((value for ts, value in hourly_rows if ts >= snapshot_hour), default=None),
        "observed_high_so_far_f": observed_high,
        "latest_observation_time_utc": latest_obs[0].isoformat() if latest_obs else None,
        "latest_observation_temp_f": latest_obs[1] if latest_obs else None,
        "observation_age_seconds": (snapshot_hour - latest_obs[0]).total_seconds() if latest_obs else None,
        "warming_rate_last_1h_f_per_hour": observed_slope(obs_rows, snapshot_hour, 1),
        "warming_rate_last_3h_f_per_hour": observed_slope(obs_rows, snapshot_hour, 3),
        "ensemble_raw_median_high_f": ensemble_features.get("ensemble_raw_median_high_f"),
        "ensemble_raw_mean_high_f": ensemble_features.get("ensemble_raw_mean_high_f"),
        "ensemble_member_stddev_f": ensemble_features.get("ensemble_member_stddev_f"),
        "ensemble_family_count": ensemble_features.get("ensemble_family_count"),
        "ensemble_member_count": ensemble_features.get("ensemble_member_count"),
        "hrrr_projected_high_f": hrrr_features.get("hrrr_projected_high_f"),
        "hrrr_next_3h_max_f": hrrr_features.get("hrrr_next_3h_max_f"),
        "hrrr_next_6h_max_f": hrrr_features.get("hrrr_next_6h_max_f"),
        "hrrr_next_8h_max_f": hrrr_features.get("hrrr_next_8h_max_f"),
        "hrrr_next_3h_slope_f_per_hour": hrrr_features.get("hrrr_next_3h_slope_f_per_hour"),
        "hrrr_next_6h_slope_f_per_hour": hrrr_features.get("hrrr_next_6h_slope_f_per_hour"),
        "hrrr_next_8h_slope_f_per_hour": hrrr_features.get("hrrr_next_8h_slope_f_per_hour"),
        "nbm_projected_high_f": nbm_features.get("nbm_projected_high_f"),
        "nbm_next_3h_max_f": nbm_features.get("nbm_next_3h_max_f"),
        "nbm_next_6h_max_f": nbm_features.get("nbm_next_6h_max_f"),
        "nbm_next_8h_max_f": nbm_features.get("nbm_next_8h_max_f"),
        "nbm_next_3h_slope_f_per_hour": nbm_features.get("nbm_next_3h_slope_f_per_hour"),
        "nbm_next_6h_slope_f_per_hour": nbm_features.get("nbm_next_6h_slope_f_per_hour"),
        "nbm_next_8h_slope_f_per_hour": nbm_features.get("nbm_next_8h_slope_f_per_hour"),
        "all_weather_sources_range_f": max(source_highs) - min(source_highs) if len(source_highs) >= 2 else None,
        "weather_source_stddev_f": stddev(source_highs),
        "nws_hrrr_disagreement_f": diff(nws_anchor, hrrr_features.get("hrrr_projected_high_f")),
        "nws_nbm_disagreement_f": diff(nws_anchor, nbm_features.get("nbm_projected_high_f")),
        "hrrr_nbm_disagreement_f": diff(
            hrrr_features.get("hrrr_projected_high_f"),
            nbm_features.get("nbm_projected_high_f"),
        ),
        "source_payload_ids": payload_ids,
        "features": {
            "nws_daily_update_time": (daily or {}).get("properties", {}).get("updateTime"),
            "nws_daily_generated_at": (daily or {}).get("properties", {}).get("generatedAt"),
            "nws_hourly_update_time": (hourly or {}).get("properties", {}).get("updateTime"),
            "nws_hourly_generated_at": (hourly or {}).get("properties", {}).get("generatedAt"),
            "observed_high_bracket_index": observed_bracket,
            "hrrr_peak_time_utc": hrrr_features.get("hrrr_peak_time_utc"),
            "nbm_peak_time_utc": nbm_features.get("nbm_peak_time_utc"),
            "ensemble_model_counts": ensemble_features.get("ensemble_model_counts"),
            "ensemble_error": ensemble_features.get("ensemble_error"),
        },
    }
    return row


def observed_slope(rows: list[tuple[datetime, float]], as_of: datetime, hours: int) -> float | None:
    start = as_of - timedelta(hours=hours)
    return linear_slope([(timestamp, value) for timestamp, value in rows if start <= timestamp <= as_of])


def diff(left: Any, right: Any) -> float | None:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) - float(right)
    return None


def stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def maybe_settlement_row(
    collector_run_id: str,
    snapshot_hour: datetime,
    city: City,
    target_date: date,
    event_ticker: str,
    brackets: tuple[Bracket, ...],
    markets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    rows = [market for market in markets if market.get("result") in ("yes", "no")]
    if not rows:
        return None
    winners = [market for market in rows if market.get("result") == "yes"]
    if len(winners) != 1:
        return None
    winner = winners[0]
    ticker = str(winner["ticker"])
    index = next((idx for idx, bracket in enumerate(brackets) if bracket.ticker == ticker), None)
    return {
        "settlement_id": deterministic_id("settlement", city.key, event_ticker),
        "collector_run_id": collector_run_id,
        "city": city.key,
        "target_date": target_date.isoformat(),
        "event_ticker": event_ticker,
        "settled_at_utc": snapshot_hour.isoformat(),
        "winner_ticker": ticker,
        "winner_label": winner.get("yes_sub_title") or winner.get("subtitle"),
        "settlement_temperature_f": parse_float(winner.get("expiration_value")),
        "settlement_bracket_index": index,
        "raw_payload_id": None,
        "validation_status": "valid",
        "warnings": [],
    }


def raw_payload_db_row(record: RawPayload) -> dict[str, Any]:
    row = asdict(record)
    row.pop("payload")
    return row


def collect_once(
    data_dir: Path,
    user_agent: str,
    bucket: str,
    snapshot_hour: datetime | None = None,
    cities: tuple[str, ...] | None = None,
) -> Path:
    started = datetime.now(UTC)
    hour = utc_hour(snapshot_hour)
    collector_run_id = deterministic_id("run", hour.isoformat(), socket.gethostname())
    recorder = HttpRecorder(user_agent, collector_run_id, hour, bucket)
    selected_cities = [city for city in CITIES if cities is None or city.key in cities]
    table_rows: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLES}
    completed = 0
    for city in selected_cities:
        try:
            city_rows = collect_city(recorder, collector_run_id, hour, city)
            for table, rows in city_rows.items():
                table_rows[table].extend(rows)
            completed += 1
        except Exception as exc:
            recorder.record_error(
                "collector",
                "collect_city",
                exc,
                datetime.now(UTC),
                datetime.now(UTC),
                None,
                city.key,
                None,
                None,
                {},
            )
    completed_at = datetime.now(UTC)
    table_rows["raw_payloads"] = [raw_payload_db_row(record) for record in recorder.raw_payloads]
    table_rows["provider_errors"] = recorder.errors
    normalized_count = sum(len(rows) for table, rows in table_rows.items() if table != "collector_runs")
    table_rows["collector_runs"].append(
        {
            "collector_run_id": collector_run_id,
            "schema_version": SCHEMA_VERSION,
            "started_at_utc": started.isoformat(),
            "completed_at_utc": completed_at.isoformat(),
            "snapshot_hour_utc": hour.isoformat(),
            "server_hostname": socket.gethostname(),
            "collector_version": "hourly-supabase-v1",
            "collector_source_hash": source_hash(),
            "config_hash": config_hash(
                {
                    "bucket": bucket,
                    "cities": [city.key for city in selected_cities],
                    "interval_minutes": 60,
                }
            ),
            "collection_interval_minutes": 60,
            "city_count_attempted": len(selected_cities),
            "city_count_completed": completed,
            "provider_error_count": len(recorder.errors),
            "raw_payload_count": len(recorder.raw_payloads),
            "normalized_row_count": normalized_count,
            "spool_status": "pending",
            "metadata": {
                "duration_seconds": (completed_at - started).total_seconds(),
                "data_dir": str(data_dir),
            },
        }
    )
    spool = {
        "schema_version": SCHEMA_VERSION,
        "collector_run_id": collector_run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "snapshot_hour_utc": hour.isoformat(),
        "synced_at_utc": None,
        "raw_payloads": [asdict(record) for record in recorder.raw_payloads],
        "tables": table_rows,
    }
    path = pending_dir(data_dir) / f"{hour.strftime('%Y%m%dT%HZ')}_{collector_run_id}.json.gz"
    write_json_gz_immutable(path, spool)
    archive_spool_file(path, data_dir)
    return path


def pending_dir(data_dir: Path) -> Path:
    return data_dir / "pending"


def synced_dir(data_dir: Path) -> Path:
    return data_dir / "synced"


def failed_dir(data_dir: Path) -> Path:
    return data_dir / "failed"


def archive_dir(data_dir: Path) -> Path:
    return data_dir / "archive"


def archive_path_for_spool(path: Path, data_dir: Path) -> Path:
    parts = path.name.split("_", 1)
    date_part = parts[0][:8] if parts else datetime.now(UTC).strftime("%Y%m%d")
    return archive_dir(data_dir) / date_part / path.name


def archive_spool_file(path: Path, data_dir: Path) -> Path:
    """Keep a permanent local copy on the droplet SSD.

    Pending/synced files are the operational queue. The archive is the local
    research backup and remains useful even if the Supabase project is reset.
    """

    destination = archive_path_for_spool(path, data_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(path, destination)
    return destination


def write_json_gz_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as fh:
            json.dump(payload, fh, sort_keys=True, separators=(",", ":"))
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_json_gz(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise CollectorError(f"malformed spool payload: {path}")
    return payload


class SupabaseClient:
    def __init__(self, url: str, service_role_key: str, bucket: str) -> None:
        self.url = url.rstrip("/")
        self.key = service_role_key
        self.bucket = bucket
        self.session = requests.Session()
        self.session.headers.update(
            {
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
            }
        )

    def upload_raw_payload(self, record: dict[str, Any]) -> None:
        payload = record.get("payload")
        path = str(record["storage_path"])
        data = gzip_json_bytes(payload)
        response = self.session.post(
            f"{self.url}/storage/v1/object/{self.bucket}/{path}",
            data=data,
            headers={
                "Content-Type": "application/gzip",
                "Cache-Control": "3600",
                "x-upsert": "true",
            },
            timeout=30,
        )
        if response.status_code not in (200, 201):
            raise CollectorError(f"storage upload failed {response.status_code}: {response.text[:500]}")

    def upsert(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        response = self.session.post(
            f"{self.url}/rest/v1/{table}",
            params={"on_conflict": primary_conflict(table)},
            json=rows,
            headers={
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            timeout=30,
        )
        if response.status_code not in (200, 201, 204):
            raise CollectorError(f"upsert {table} failed {response.status_code}: {response.text[:500]}")

    def delete_run_rows(self, table: str, collector_run_id: str) -> None:
        response = self.session.delete(
            f"{self.url}/rest/v1/{table}",
            params={"collector_run_id": f"eq.{collector_run_id}"},
            headers={"Prefer": "return=minimal"},
            timeout=30,
        )
        if response.status_code not in (200, 204):
            raise CollectorError(
                f"delete {table} rows for run failed {response.status_code}: {response.text[:500]}"
            )

    def select(
        self, table: str, params: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        query = {"select": "*", **(params or {})}
        response = self.session.get(
            f"{self.url}/rest/v1/{table}",
            params=query,
            headers={"Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise CollectorError(f"select {table} returned non-list payload")
        return [row for row in payload if isinstance(row, dict)]


def primary_conflict(table: str) -> str:
    return {
        "collector_runs": "collector_run_id",
        "raw_payloads": "raw_payload_id",
        "events": "event_id",
        "market_snapshots": "market_snapshot_id",
        "weather_snapshots": "weather_snapshot_id",
        "model_outputs": "model_output_id",
        "settlements": "settlement_id",
        "provider_errors": "provider_error_id",
    }[table]


def sync_spool(data_dir: Path, client: SupabaseClient) -> int:
    synced = 0
    for path in sorted(pending_dir(data_dir).glob("*.json.gz")):
        try:
            payload = read_json_gz(path)
            raw_payloads = payload.get("raw_payloads", [])
            if not isinstance(raw_payloads, list):
                raise CollectorError("spool raw_payloads is malformed")
            for record in raw_payloads:
                if isinstance(record, dict):
                    client.upload_raw_payload(record)
            tables = payload.get("tables")
            if not isinstance(tables, dict):
                raise CollectorError("spool tables is malformed")
            collector_rows = tables.get("collector_runs")
            run_ids: set[str] = set()
            if isinstance(collector_rows, list):
                for row in collector_rows:
                    if isinstance(row, dict):
                        row["spool_status"] = "synced"
                        run_id = row.get("collector_run_id")
                        if isinstance(run_id, str) and run_id:
                            run_ids.add(run_id)
                client.upsert("collector_runs", collector_rows)
            for run_id in sorted(run_ids):
                for table in RUN_CHILD_TABLES:
                    client.delete_run_rows(table, run_id)
            for table in TABLES:
                if table == "collector_runs":
                    continue
                rows = tables.get(table, [])
                if isinstance(rows, list):
                    client.upsert(table, rows)
            mark_synced(path, data_dir)
            synced += 1
        except Exception:
            failed_dir(data_dir).mkdir(parents=True, exist_ok=True)
            raise
    return synced


def mark_synced(path: Path, data_dir: Path) -> Path:
    archive_spool_file(path, data_dir)
    synced_dir(data_dir).mkdir(parents=True, exist_ok=True)
    destination = synced_dir(data_dir) / path.name
    path.replace(destination)
    return destination


def status(data_dir: Path) -> dict[str, Any]:
    pending = sorted(pending_dir(data_dir).glob("*.json.gz")) if pending_dir(data_dir).exists() else []
    synced = sorted(synced_dir(data_dir).glob("*.json.gz")) if synced_dir(data_dir).exists() else []
    failed = sorted(failed_dir(data_dir).glob("*.json.gz")) if failed_dir(data_dir).exists() else []
    archived = sorted(archive_dir(data_dir).glob("*/*.json.gz")) if archive_dir(data_dir).exists() else []
    raw_bytes = sum(path.stat().st_size for path in pending + synced + failed)
    archive_bytes = sum(path.stat().st_size for path in archived)
    last_synced = max((path.stat().st_mtime for path in synced), default=None)
    last_archived = max((path.stat().st_mtime for path in archived), default=None)
    return {
        "data_dir": str(data_dir),
        "pending_spool_files": len(pending),
        "synced_spool_files": len(synced),
        "failed_spool_files": len(failed),
        "archived_spool_files": len(archived),
        "local_spool_bytes": raw_bytes,
        "local_archive_bytes": archive_bytes,
        "local_total_bytes": raw_bytes + archive_bytes,
        "last_synced_local_mtime_utc": datetime.fromtimestamp(last_synced, UTC).isoformat()
        if last_synced
        else None,
        "last_archived_local_mtime_utc": datetime.fromtimestamp(last_archived, UTC).isoformat()
        if last_archived
        else None,
        "estimated_safe_droplet_data_bytes": 7_000_000_000,
        "estimated_droplet_days_at_7mb_per_day": round(
            max(0, 7_000_000_000 - (raw_bytes + archive_bytes)) / 7_000_000, 1
        ),
        "estimated_storage_limit_bytes": 1_000_000_000,
        "estimated_database_limit_bytes": 500_000_000,
        "notes": [
            "Local archive is the durable droplet copy; pending/synced are the operational queue.",
            "Remote Supabase usage is not queried by this command; use Supabase dashboard for exact DB/storage usage.",
            "Projected days remaining depends on actual synced raw object size and row volume.",
        ],
    }


def export_rows(
    client: SupabaseClient,
    output_dir: Path,
    start: str,
    end: str,
    city: str | None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for table in ("events", "market_snapshots", "weather_snapshots", "model_outputs", "provider_errors"):
        params = {
            "snapshot_hour_utc": f"gte.{start}T00:00:00+00:00",
            "and": f"(snapshot_hour_utc.lt.{end}T23:59:59+00:00)",
        }
        if city:
            params["city"] = f"eq.{city}"
        rows = client.select(table, params)
        write_csv(output_dir / f"{table}.csv", rows)
    settlements_params: dict[str, str] = {
        "target_date": f"gte.{start}",
        "and": f"(target_date.lte.{end})",
    }
    if city:
        settlements_params["city"] = f"eq.{city}"
    write_csv(output_dir / "settlements.csv", client.select("settlements", settlements_params))
    (output_dir / "metadata.json").write_text(
        json.dumps(
            {
                "exported_at_utc": datetime.now(UTC).isoformat(),
                "start": start,
                "end": end,
                "city": city,
                "tables": [
                    "events",
                    "market_snapshots",
                    "weather_snapshots",
                    "model_outputs",
                    "provider_errors",
                    "settlements",
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise CollectorError(f"missing required environment variable {name}")
    return value


def supabase_from_env(bucket: str) -> SupabaseClient:
    return SupabaseClient(require_env("SUPABASE_URL"), require_env("SUPABASE_SERVICE_ROLE_KEY"), bucket)


def parse_cities(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    cities = tuple(part.strip().lower() for part in value.split(",") if part.strip())
    valid = {city.key for city in CITIES}
    unknown = set(cities) - valid
    if unknown:
        raise CollectorError(f"unknown cities: {', '.join(sorted(unknown))}")
    return cities


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Hourly Supabase Kalshi weather collector")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("COLLECTOR_DATA_DIR", DEFAULT_DATA_DIR)))
    parser.add_argument("--bucket", default=os.environ.get("SUPABASE_STORAGE_BUCKET", DEFAULT_BUCKET))
    parser.add_argument("--cities", help="Comma-separated city keys")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-db")
    init.add_argument("--print-sql", action="store_true")
    collect = sub.add_parser("collect-once")
    collect.add_argument("--dry-run", action="store_true")
    collect.add_argument("--snapshot-hour")
    sub.add_parser("sync-spool")
    sub.add_parser("status")
    export = sub.add_parser("export")
    export.add_argument("--start", required=True)
    export.add_argument("--end", required=True)
    export.add_argument("--output-dir", type=Path, default=Path("output/supabase_export"))
    args = parser.parse_args()

    if args.command == "init-db":
        if args.print_sql:
            print(INIT_SQL.strip())
            return 0
        raise CollectorError("init-db currently supports --print-sql only")

    data_dir: Path = args.data_dir
    if args.command == "status":
        print(json.dumps(status(data_dir), indent=2))
        return 0

    if args.command == "collect-once":
        user_agent = require_env("NWS_USER_AGENT")
        hour = parse_datetime(args.snapshot_hour).astimezone(UTC) if args.snapshot_hour else None
        path = collect_once(data_dir, user_agent, args.bucket, hour, parse_cities(args.cities))
        print(f"created spool {path}")
        if args.dry_run:
            return 0
        synced = sync_spool(data_dir, supabase_from_env(args.bucket))
        print(f"synced_spool_files={synced}")
        return 0

    if args.command == "sync-spool":
        synced = sync_spool(data_dir, supabase_from_env(args.bucket))
        print(f"synced_spool_files={synced}")
        return 0

    if args.command == "export":
        export_rows(supabase_from_env(args.bucket), args.output_dir, args.start, args.end, args.cities)
        print(f"exported {args.output_dir}")
        return 0

    raise CollectorError(f"unknown command {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
