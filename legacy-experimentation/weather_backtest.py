#!/usr/bin/env python3
"""Prospective point-in-time collection and evaluation for weather probabilities."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import random
import statistics
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt

from weather_probabilities import (
    CITIES,
    KALSHI_BASE_URL,
    NWS_BASE_URL,
    Bracket,
    City,
    DataError,
    Distribution,
    HttpClient,
    bracket_probability,
    build_distribution,
    extract_ensemble_members,
    kernel_bandwidth,
    parse_datetime,
    parse_event_date,
    point_mass_bracket_probability,
)


SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 2
CHECKPOINT_GRACE = timedelta(minutes=5)
OPEN_METEO_GFS_URL = "https://api.open-meteo.com/v1/gfs"
HRRR_TOP3_DISTANCE_PENALTY = 0.8
CHECKPOINTS = (
    ("t_minus_6h", -6),
    ("t_plus_6h", 6),
    ("t_plus_10h", 10),
    ("t_plus_14h", 14),
    ("t_plus_18h", 18),
)
CHECKPOINT_ORDER = tuple(name for name, _ in CHECKPOINTS)
CHALLENGER_NAMES = (
    "family_centered",
    "gefs_centered",
    "ecmwf_ifs_centered",
    "icon_eps_centered",
    "gem_centered",
    "hrrr_top3_rerank",
)
CLI_LOCATIONS = {
    "nyc": "NYC",
    "mia": "MIA",
    "la": "LAX",
    "den": "DEN",
    "aus": "AUS",
    "okc": "OKC",
}


@dataclass(frozen=True)
class ScheduledCheckpoint:
    city: City
    target_date: date
    name: str
    offset_hours: int
    scheduled_at: datetime


class TracingHttpClient(HttpClient):
    def __init__(self, user_agent: str, timeout: float = 20.0) -> None:
        super().__init__(user_agent, timeout)
        self.records: list[dict[str, Any]] = []

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        requested_at = datetime.now(UTC)
        payload = super().get_json(url, params)
        self.records.append(
            {
                "url": url,
                "params": params,
                "requested_at": requested_at.isoformat(),
                "received_at": datetime.now(UTC).isoformat(),
                "payload": payload,
            }
        )
        return payload


class ReplayHttpClient:
    def __init__(self, records: Iterable[dict[str, Any]]) -> None:
        self.records = list(records)
        self.index = 0

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.index >= len(self.records):
            raise DataError(f"offline replay requested uncaptured URL {url}")
        record = self.records[self.index]
        self.index += 1
        if record.get("url") != url:
            raise DataError(
                f"offline replay URL mismatch: expected {record.get('url')}, requested {url}"
            )
        if record.get("params") != params:
            raise DataError(f"offline replay parameter mismatch for {url}")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            raise DataError(f"offline replay has malformed payload for {url}")
        return payload

    def assert_consumed(self) -> None:
        if self.index != len(self.records):
            raise DataError(
                f"offline replay consumed {self.index} of {len(self.records)} responses"
            )


def climate_day_start(city: City, target_date: date) -> datetime:
    standard_zone = timezone(timedelta(hours=city.standard_utc_offset_hours))
    return datetime.combine(target_date, time.min, tzinfo=standard_zone).astimezone(UTC)


def checkpoint_schedule(city: City, target_date: date) -> tuple[ScheduledCheckpoint, ...]:
    start = climate_day_start(city, target_date)
    return tuple(
        ScheduledCheckpoint(city, target_date, name, offset, start + timedelta(hours=offset))
        for name, offset in CHECKPOINTS
    )


def city_by_key(key: str) -> City:
    for city in CITIES:
        if city.key == key:
            return city
    raise DataError(f"unknown city key {key!r}")


def model_source_hash() -> str:
    path = Path(__file__).with_name("weather_probabilities.py")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collector_source_hash() -> str:
    digest = hashlib.sha256()
    for path in (Path(__file__), Path(__file__).with_name("weather_probabilities.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def cohort_dir(root: Path, cohort: str) -> Path:
    if not cohort or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in cohort):
        raise DataError("cohort must contain only letters, numbers, hyphens, and underscores")
    return root / "cohorts" / cohort


def write_json_gz_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"immutable artifact already exists: {path}")
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                compressed.write(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                )
            raw.flush()
            os.fsync(raw.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json_gz(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise DataError(f"artifact is not a JSON object: {path}")
    return payload


def manifest_path(root: Path, cohort: str) -> Path:
    return cohort_dir(root, cohort) / "manifest.json.gz"


def ensure_manifest(root: Path, cohort: str, started_at: datetime) -> dict[str, Any]:
    path = manifest_path(root, cohort)
    if path.exists():
        manifest = read_json_gz(path)
        if manifest.get("model_source_hash") != model_source_hash():
            raise DataError(
                "model source changed after cohort creation; use a new cohort or restore its model version"
            )
        return manifest
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "cohort": cohort,
        "started_at": started_at.astimezone(UTC).isoformat(),
        "pilot_days": 15,
        "initial_evaluation_days": 60,
        "checkpoint_grace_seconds": int(CHECKPOINT_GRACE.total_seconds()),
        "checkpoints": [
            {"name": name, "offset_hours": offset} for name, offset in CHECKPOINTS
        ],
        "model_source_hash": model_source_hash(),
        "collector_source_hash": collector_source_hash(),
    }
    write_json_gz_immutable(path, manifest)
    return manifest


def load_manifest(root: Path, cohort: str) -> dict[str, Any]:
    path = manifest_path(root, cohort)
    if not path.exists():
        raise DataError(f"cohort does not exist: {cohort}")
    return read_json_gz(path)


def artifact_stem(checkpoint: ScheduledCheckpoint) -> Path:
    return Path(checkpoint.target_date.isoformat()) / checkpoint.city.key / checkpoint.name


def snapshot_path(base: Path, checkpoint: ScheduledCheckpoint) -> Path:
    return base / "snapshots" / artifact_stem(checkpoint).with_suffix(".json.gz")


def missing_path(base: Path, checkpoint: ScheduledCheckpoint) -> Path:
    return base / "missing" / artifact_stem(checkpoint).with_suffix(".json.gz")


def iter_schedules(started_at: datetime, through: datetime) -> Iterable[ScheduledCheckpoint]:
    for city in CITIES:
        standard_zone = timezone(timedelta(hours=city.standard_utc_offset_hours))
        first_date = started_at.astimezone(standard_zone).date()
        last_date = through.astimezone(standard_zone).date() + timedelta(days=1)
        current = first_date
        while current <= last_date:
            for checkpoint in checkpoint_schedule(city, current):
                if checkpoint.scheduled_at >= started_at and checkpoint.scheduled_at <= through:
                    yield checkpoint
            current += timedelta(days=1)


def distribution_to_dict(distribution: Distribution) -> dict[str, Any]:
    return {
        "city": distribution.city.key,
        "target_date": distribution.target_date.isoformat(),
        "brackets": [asdict(bracket) for bracket in distribution.brackets],
        "probabilities": list(distribution.probabilities),
        "nws_high_f": distribution.nws_high_f,
        "observed_high_f": distribution.observed_high_f,
        "observed_at": distribution.observed_at.isoformat() if distribution.observed_at else None,
        "member_highs_f": list(distribution.member_highs_f),
        "member_weights": list(distribution.member_weights),
        "bandwidth_f": distribution.bandwidth_f,
        "raw_consensus_high_f": distribution.raw_consensus_high_f,
        "center_shift_f": distribution.center_shift_f,
        "model_counts": [list(value) for value in distribution.model_counts],
        "window_start": distribution.window_start.isoformat(),
        "window_end": distribution.window_end.isoformat(),
        "warnings": list(distribution.warnings),
    }


def fetch_hrrr_guidance(
    http: HttpClient,
    city: City,
    window_start: datetime,
    window_end: datetime,
    observed_high_f: float | None,
    observed_at: datetime | None,
    as_of: datetime,
) -> dict[str, Any]:
    days_needed = max(2, (window_end.date() - as_of.astimezone(UTC).date()).days + 1)
    if days_needed > 2:
        raise DataError("HRRR guidance is only available for short-range checkpoints")
    payload = http.get_json(
        OPEN_METEO_GFS_URL,
        {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "hourly": "temperature_2m",
            "models": "gfs_hrrr",
            "forecast_days": days_needed,
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
        },
    )
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise DataError("HRRR response is missing hourly data")
    raw_times = hourly.get("time")
    raw_values = hourly.get("temperature_2m")
    if not isinstance(raw_times, list) or not isinstance(raw_values, list):
        raise DataError("HRRR response is missing hourly temperature series")
    if len(raw_times) != len(raw_values):
        raise DataError("HRRR time and temperature series do not align")
    rows: list[tuple[datetime, float]] = []
    for timestamp, value in zip(raw_times, raw_values, strict=True):
        if value is None:
            continue
        parsed = parse_datetime(str(timestamp)).astimezone(UTC)
        if window_start <= parsed < window_end:
            rows.append((parsed, float(value)))
    if not rows:
        raise DataError("HRRR response does not cover the climate-day window")
    remaining = [
        value
        for timestamp, value in rows
        if observed_at is None or timestamp > observed_at.astimezone(UTC)
    ]
    projected_candidates = list(remaining)
    if observed_high_f is not None:
        projected_candidates.append(float(observed_high_f))
    if not projected_candidates:
        raise DataError("HRRR response has no usable remaining-day temperatures")
    return {
        "provider": "open_meteo",
        "endpoint": OPEN_METEO_GFS_URL,
        "model": "gfs_hrrr",
        "latitude": city.latitude,
        "longitude": city.longitude,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "observed_high_f": observed_high_f,
        "observed_at": observed_at.isoformat() if observed_at else None,
        "full_window_high_f": max(value for _, value in rows),
        "remaining_forecast_high_f": max(remaining) if remaining else None,
        "projected_high_f": max(projected_candidates),
        "hour_count": len(rows),
        "remaining_hour_count": len(remaining),
    }


def _trace_record(
    records: Iterable[dict[str, Any]], predicate: Any
) -> dict[str, Any] | None:
    return next((record for record in records if predicate(str(record.get("url", "")))), None)


def _nws_forecast_metadata(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    properties = record.get("payload", {}).get("properties", {})
    received_at = parse_datetime(str(record["received_at"])).astimezone(UTC)
    update_time = properties.get("updateTime")
    generated_at = properties.get("generatedAt")

    def age(value: Any) -> float | None:
        if not isinstance(value, str):
            return None
        return (received_at - parse_datetime(value).astimezone(UTC)).total_seconds()

    return {
        "requested_at": record.get("requested_at"),
        "received_at": record.get("received_at"),
        "update_time": update_time,
        "generated_at": generated_at,
        "update_age_seconds_at_receipt": age(update_time),
        "generation_age_seconds_at_receipt": age(generated_at),
    }


def derive_source_metadata(snapshot: dict[str, Any]) -> dict[str, Any]:
    records = snapshot.get("http_trace")
    if not isinstance(records, list):
        raise DataError("snapshot does not contain an HTTP trace")
    auxiliary_records = snapshot.get("auxiliary_http_trace")
    if not isinstance(auxiliary_records, list):
        auxiliary_records = []
    as_of = parse_datetime(snapshot["checkpoint"]["as_of"]).astimezone(UTC)
    daily = _trace_record(
        records,
        lambda url: "api.weather.gov/gridpoints/" in url and url.endswith("/forecast"),
    )
    hourly = _trace_record(records, lambda url: url.endswith("/forecast/hourly"))
    observations = _trace_record(records, lambda url: url.endswith("/observations"))
    kalshi = _trace_record(records, lambda url: url.endswith("/markets"))
    open_meteo = _trace_record(records, lambda url: "ensemble-api.open-meteo.com" in url)
    hrrr = _trace_record(auxiliary_records, lambda url: "api.open-meteo.com/v1/gfs" in url)

    latest_observation: datetime | None = None
    observation_count = 0
    if observations is not None:
        features = observations.get("payload", {}).get("features", [])
        if isinstance(features, list):
            timestamps: list[datetime] = []
            for feature in features:
                try:
                    timestamps.append(
                        parse_datetime(feature["properties"]["timestamp"]).astimezone(UTC)
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            observation_count = len(timestamps)
            latest_observation = max(timestamps) if timestamps else None

    def retrieval(record: dict[str, Any] | None) -> dict[str, Any] | None:
        if record is None:
            return None
        return {
            "requested_at": record.get("requested_at"),
            "received_at": record.get("received_at"),
        }

    open_meteo_metadata = retrieval(open_meteo)
    if open_meteo_metadata is not None and open_meteo is not None:
        open_meteo_metadata["api_generation_time_ms"] = open_meteo.get("payload", {}).get(
            "generationtime_ms"
        )
    observation_metadata = retrieval(observations)
    if observation_metadata is not None:
        observation_metadata.update(
            {
                "observation_count": observation_count,
                "latest_observation_at": (
                    latest_observation.isoformat() if latest_observation else None
                ),
                "observation_age_seconds_at_checkpoint": (
                    (as_of - latest_observation).total_seconds()
                    if latest_observation
                    else None
                ),
            }
        )
    return {
        "nws_daily": _nws_forecast_metadata(daily),
        "nws_hourly": _nws_forecast_metadata(hourly),
        "nws_observations": observation_metadata,
        "kalshi": retrieval(kalshi),
        "open_meteo": open_meteo_metadata,
        "open_meteo_hrrr": retrieval(hrrr),
    }


def source_metadata(snapshot: dict[str, Any]) -> dict[str, Any]:
    stored = snapshot.get("source_metadata")
    return stored if isinstance(stored, dict) else derive_source_metadata(snapshot)


def flatten_source_metadata(snapshot: dict[str, Any]) -> dict[str, Any]:
    metadata = source_metadata(snapshot)
    row: dict[str, Any] = {}
    for source, values in metadata.items():
        if isinstance(values, dict):
            for key, value in values.items():
                row[f"{source}_{key}"] = value
    return row


def probability_vectors_match(left: Distribution, right: Distribution) -> bool:
    return (
        [bracket.ticker for bracket in left.brackets]
        == [bracket.ticker for bracket in right.brackets]
        and len(left.probabilities) == len(right.probabilities)
        and all(
            math.isclose(a, b, rel_tol=0.0, abs_tol=1e-12)
            for a, b in zip(left.probabilities, right.probabilities)
        )
    )


def validate_trace_observation_times(records: Iterable[dict[str, Any]], as_of: datetime) -> None:
    for record in records:
        if "/observations" not in str(record.get("url")):
            continue
        features = record.get("payload", {}).get("features", [])
        if not isinstance(features, list):
            raise DataError("captured NWS observation payload is malformed")
        for feature in features:
            try:
                timestamp = parse_datetime(feature["properties"]["timestamp"]).astimezone(UTC)
            except (KeyError, TypeError, ValueError) as exc:
                raise DataError("captured observation is missing a valid timestamp") from exc
            if timestamp > as_of:
                raise DataError(
                    f"captured observation {timestamp.isoformat()} is later than as-of time"
                )


def captured_event_markets(records: Iterable[dict[str, Any]], target_date: date) -> list[dict[str, Any]]:
    for record in records:
        if str(record.get("url", "")).endswith("/markets"):
            markets = record.get("payload", {}).get("markets", [])
            selected = [
                market
                for market in markets
                if isinstance(market, dict)
                and isinstance(market.get("event_ticker"), str)
                and parse_event_date(market["event_ticker"]) == target_date
            ]
            if selected:
                return selected
    raise DataError("captured trace does not contain selected Kalshi event markets")


def capture_checkpoint(
    root: Path,
    cohort: str,
    checkpoint: ScheduledCheckpoint,
    as_of: datetime,
    user_agent: str,
) -> Path:
    base = cohort_dir(root, cohort)
    output = snapshot_path(base, checkpoint)
    if output.exists():
        return output
    client = TracingHttpClient(user_agent)
    full = build_distribution(
        client,
        checkpoint.city,
        checkpoint.target_date,
        as_of=as_of,
        condition_on_observations=True,
    )
    validate_trace_observation_times(client.records, as_of)

    replay = ReplayHttpClient(client.records)
    replayed = build_distribution(
        replay,  # type: ignore[arg-type]
        checkpoint.city,
        checkpoint.target_date,
        as_of=as_of,
        condition_on_observations=True,
    )
    replay.assert_consumed()
    if not probability_vectors_match(full, replayed):
        raise DataError("captured trace does not exactly replay the full distribution")

    ablation_replay = ReplayHttpClient(client.records)
    forecast_only = build_distribution(
        ablation_replay,  # type: ignore[arg-type]
        checkpoint.city,
        checkpoint.target_date,
        as_of=as_of,
        condition_on_observations=False,
    )
    ablation_replay.assert_consumed()
    event_markets = captured_event_markets(client.records, checkpoint.target_date)
    event_tickers = {str(market["event_ticker"]) for market in event_markets}
    if len(event_tickers) != 1:
        raise DataError("captured event markets have inconsistent event tickers")

    auxiliary_client = TracingHttpClient(user_agent)
    auxiliary_inputs: dict[str, Any] = {}
    auxiliary_errors: list[dict[str, Any]] = []
    try:
        auxiliary_inputs["open_meteo_hrrr"] = fetch_hrrr_guidance(
            auxiliary_client,
            checkpoint.city,
            full.window_start,
            full.window_end,
            full.observed_high_f,
            full.observed_at,
            as_of,
        )
    except Exception as exc:
        auxiliary_errors.append(
            {
                "source": "open_meteo_hrrr",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )

    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "cohort": cohort,
        "model_source_hash": model_source_hash(),
        "collector_source_hash": collector_source_hash(),
        "checkpoint": {
            "name": checkpoint.name,
            "offset_hours": checkpoint.offset_hours,
            "scheduled_at": checkpoint.scheduled_at.isoformat(),
            "as_of": as_of.astimezone(UTC).isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "latency_seconds": (as_of - checkpoint.scheduled_at).total_seconds(),
        },
        "event": {
            "city": checkpoint.city.key,
            "series_ticker": checkpoint.city.series_ticker,
            "event_ticker": event_tickers.pop(),
            "target_date": checkpoint.target_date.isoformat(),
            "window_start": full.window_start.isoformat(),
            "window_end": full.window_end.isoformat(),
            "markets": event_markets,
        },
        "distribution": distribution_to_dict(full),
        "forecast_only_distribution": distribution_to_dict(forecast_only),
        "http_trace": client.records,
        "auxiliary_http_trace": auxiliary_client.records,
        "auxiliary_inputs": auxiliary_inputs,
        "auxiliary_errors": auxiliary_errors,
    }
    payload["source_metadata"] = derive_source_metadata(payload)
    write_json_gz_immutable(output, payload)
    return output


def record_attempt_failure(
    base: Path, checkpoint: ScheduledCheckpoint, as_of: datetime, exc: Exception
) -> Path:
    suffix = as_of.strftime("%Y%m%dT%H%M%S%fZ")
    path = base / "attempts" / artifact_stem(checkpoint) / f"{suffix}.json.gz"
    write_json_gz_immutable(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "scheduled_at": checkpoint.scheduled_at.isoformat(),
            "attempted_at": as_of.isoformat(),
            "error_type": type(exc).__name__,
            "error": str(exc),
        },
    )
    return path


def collect_due(root: Path, cohort: str, user_agent: str, now: datetime | None = None) -> int:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    print(f"[{current.isoformat()}] collect-due started")
    manifest = ensure_manifest(root, cohort, current - CHECKPOINT_GRACE)
    started_at = parse_datetime(str(manifest["started_at"])).astimezone(UTC)
    base = cohort_dir(root, cohort)
    collected = failed = missed = 0
    for checkpoint in iter_schedules(started_at, current):
        snapshot = snapshot_path(base, checkpoint)
        missing = missing_path(base, checkpoint)
        if snapshot.exists() or missing.exists():
            continue
        deadline = checkpoint.scheduled_at + CHECKPOINT_GRACE
        if current > deadline:
            write_json_gz_immutable(
                missing,
                {
                    "schema_version": SCHEMA_VERSION,
                    "reason": "collector did not complete within checkpoint grace period",
                    "scheduled_at": checkpoint.scheduled_at.isoformat(),
                    "deadline_at": deadline.isoformat(),
                    "marked_at": current.isoformat(),
                },
            )
            print(
                f"[{current.isoformat()}] Marked missing "
                f"{checkpoint.city.key} {checkpoint.target_date} {checkpoint.name}; "
                f"scheduled={checkpoint.scheduled_at.isoformat()} "
                f"deadline={deadline.isoformat()}"
            )
            missed += 1
            continue
        if current < checkpoint.scheduled_at:
            continue
        try:
            path = capture_checkpoint(root, cohort, checkpoint, current, user_agent)
            print(
                f"[{current.isoformat()}] Collected {checkpoint.city.name} "
                f"{checkpoint.target_date} {checkpoint.name}: {path}"
            )
            collected += 1
        except Exception as exc:
            record_attempt_failure(base, checkpoint, current, exc)
            print(
                f"[{current.isoformat()}] Collection failed for "
                f"{checkpoint.city.name} {checkpoint.target_date} "
                f"{checkpoint.name}: {exc}",
                file=sys.stderr,
            )
            failed += 1
    finished = datetime.now(UTC) if now is None else current
    print(
        f"[{finished.isoformat()}] Collection summary: collected={collected} "
        f"failed={failed} newly_missing={missed} "
        f"duration_seconds={(finished - current).total_seconds():.1f}"
    )
    return 1 if failed else 0


def settlement_path(base: Path, target_date: date, city_key: str) -> Path:
    return base / "settlements" / target_date.isoformat() / f"{city_key}.json.gz"


def list_settled_event_markets(
    http: HttpClient, series_ticker: str, event_ticker: str
) -> list[dict[str, Any]]:
    cursor: str | None = None
    for _ in range(20):
        params: dict[str, Any] = {
            "series_ticker": series_ticker,
            "status": "settled",
            "limit": 1000,
        }
        if cursor:
            params["cursor"] = cursor
        payload = http.get_json(f"{KALSHI_BASE_URL}/markets", params)
        markets = payload.get("markets")
        if not isinstance(markets, list):
            raise DataError("Kalshi settled response is missing markets")
        selected = [
            market
            for market in markets
            if isinstance(market, dict) and market.get("event_ticker") == event_ticker
        ]
        if selected:
            return selected
        cursor = payload.get("cursor")
        if not isinstance(cursor, str) or not cursor:
            break
    return []


def validate_settlement(
    snapshot: dict[str, Any], settled_markets: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    markets = list(settled_markets)
    expected_tickers = {
        str(bracket["ticker"])
        for bracket in snapshot.get("distribution", {}).get("brackets", [])
    }
    by_ticker = {
        str(market.get("ticker")): market
        for market in markets
        if str(market.get("ticker")) in expected_tickers
    }
    if set(by_ticker) != expected_tickers:
        missing = sorted(expected_tickers - set(by_ticker))
        raise DataError(f"settlement is missing expected bracket contracts: {missing}")
    winners = [
        ticker
        for ticker, market in by_ticker.items()
        if str(market.get("result", "")).lower() == "yes"
    ]
    unresolved = [
        ticker
        for ticker, market in by_ticker.items()
        if str(market.get("result", "")).lower() not in ("yes", "no")
    ]
    if unresolved:
        raise DataError(f"settlement has unresolved contracts: {unresolved}")
    if len(winners) != 1:
        raise DataError(f"settlement must contain exactly one Yes contract, found {len(winners)}")
    return {
        "winner_ticker": winners[0],
        "markets": [by_ticker[ticker] for ticker in sorted(by_ticker)],
    }


def fetch_cli_audit(
    http: HttpClient, city_key: str, window_end: datetime
) -> dict[str, Any]:
    location = CLI_LOCATIONS[city_key]
    listing = http.get_json(f"{NWS_BASE_URL}/products/types/CLI/locations/{location}")
    graph = listing.get("@graph")
    if not isinstance(graph, list):
        raise DataError("NWS CLI product listing is malformed")
    candidates: list[dict[str, Any]] = []
    for item in graph:
        try:
            issued = parse_datetime(item["issuanceTime"]).astimezone(UTC)
            product_id = str(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if window_end - timedelta(hours=12) <= issued <= window_end + timedelta(days=2):
            candidates.append(
                {
                    "metadata": item,
                    "product": http.get_json(f"{NWS_BASE_URL}/products/{product_id}"),
                }
            )
    return {"location": location, "listing": listing, "products": candidates}


def settle_pending(
    root: Path, cohort: str, user_agent: str, now: datetime | None = None
) -> int:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    load_manifest(root, cohort)
    base = cohort_dir(root, cohort)
    snapshots = sorted((base / "snapshots").glob("*/*/*.json.gz"))
    events: dict[tuple[str, str], dict[str, Any]] = {}
    for path in snapshots:
        snapshot = read_json_gz(path)
        event = snapshot.get("event", {})
        city_key = str(event.get("city"))
        target = str(event.get("target_date"))
        if city_key and target:
            events.setdefault((city_key, target), snapshot)

    http = HttpClient(user_agent)
    settled_count = pending_count = failed_count = 0
    for (city_key, target_text), snapshot in sorted(events.items()):
        target = date.fromisoformat(target_text)
        output = settlement_path(base, target, city_key)
        if output.exists():
            continue
        event = snapshot["event"]
        window_end = parse_datetime(event["window_end"]).astimezone(UTC)
        if current <= window_end:
            pending_count += 1
            continue
        try:
            markets = list_settled_event_markets(
                http, str(event["series_ticker"]), str(event["event_ticker"])
            )
            if not markets:
                pending_count += 1
                continue
            result = validate_settlement(snapshot, markets)
            warnings: list[str] = []
            try:
                cli_audit = fetch_cli_audit(http, city_key, window_end)
            except Exception as exc:
                cli_audit = None
                warnings.append(f"NWS CLI audit unavailable: {exc}")
            write_json_gz_immutable(
                output,
                {
                    "schema_version": SCHEMA_VERSION,
                    "cohort": cohort,
                    "city": city_key,
                    "target_date": target.isoformat(),
                    "event_ticker": event["event_ticker"],
                    "winner_ticker": result["winner_ticker"],
                    "settled_at": current.isoformat(),
                    "markets": result["markets"],
                    "nws_cli_audit": cli_audit,
                    "warnings": warnings,
                },
            )
            settled_count += 1
            print(f"Settled {city_key} {target}: {result['winner_ticker']}")
        except Exception as exc:
            failed_count += 1
            print(f"Settlement failed for {city_key} {target}: {exc}", file=sys.stderr)
    print(
        f"Settlement summary: settled={settled_count} pending={pending_count} failed={failed_count}"
    )
    return 1 if failed_count else 0


def probabilities_from_snapshot(snapshot: dict[str, Any], key: str) -> tuple[list[str], list[float]]:
    distribution = snapshot.get(key)
    if not isinstance(distribution, dict):
        raise DataError(f"snapshot is missing {key}")
    brackets = distribution.get("brackets")
    probabilities = distribution.get("probabilities")
    if not isinstance(brackets, list) or not isinstance(probabilities, list):
        raise DataError(f"snapshot {key} is malformed")
    tickers = [str(bracket["ticker"]) for bracket in brackets]
    values = [float(value) for value in probabilities]
    if len(tickers) != len(values) or not math.isclose(sum(values), 1.0, abs_tol=1e-9):
        raise DataError(f"snapshot {key} probabilities are invalid")
    return tickers, values


def _stored_brackets(snapshot: dict[str, Any]) -> list[Bracket]:
    rows = snapshot.get("distribution", {}).get("brackets", [])
    if not isinstance(rows, list):
        raise DataError("snapshot brackets are malformed")
    return [
        Bracket(
            str(row["ticker"]),
            str(row["label"]),
            int(row["lower"]) if row["lower"] is not None else None,
            int(row["upper"]) if row["upper"] is not None else None,
        )
        for row in rows
    ]


def _family_slug(name: str) -> str:
    return {
        "GEFS": "gefs_centered",
        "ECMWF IFS": "ecmwf_ifs_centered",
        "ICON EPS": "icon_eps_centered",
        "GEM": "gem_centered",
    }[name]


def _centered_probabilities(
    brackets: list[Bracket],
    means: list[float],
    weights: list[float],
    observed_floor: float | None,
) -> tuple[list[float], float]:
    if not means:
        if observed_floor is None:
            raise DataError("challenger has no projected member highs")
        return (
            [point_mass_bracket_probability(bracket, observed_floor) for bracket in brackets],
            1.0,
        )
    bandwidth = kernel_bandwidth(means, weights)
    probabilities = [
        bracket_probability(bracket, means, bandwidth, weights, observed_floor)
        for bracket in brackets
    ]
    if not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise DataError("challenger probabilities do not sum to one")
    return probabilities, bandwidth


def _bracket_index_for_temperature(brackets: list[Bracket], value: float) -> int:
    matches = [
        index
        for index, bracket in enumerate(brackets)
        if point_mass_bracket_probability(bracket, value)
    ]
    if len(matches) != 1:
        raise DataError(f"temperature {value} does not map to exactly one bracket")
    return matches[0]


def _centered_three_indexes(center_index: int, count: int) -> list[int]:
    if count < 3:
        raise DataError("at least three brackets are required")
    start = min(max(center_index - 1, 0), count - 3)
    return list(range(start, start + 3))


def hrrr_top3_rerank_distribution(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    guidance = snapshot.get("auxiliary_inputs", {}).get("open_meteo_hrrr")
    if not isinstance(guidance, dict):
        return None
    projected_high = guidance.get("projected_high_f")
    if projected_high is None:
        return None
    baseline = snapshot["distribution"]
    probabilities = [float(value) for value in baseline["probabilities"]]
    if len(probabilities) < 3:
        return None
    brackets = _stored_brackets(snapshot)
    hrrr_index = _bracket_index_for_temperature(brackets, float(projected_high))
    top3 = _centered_three_indexes(hrrr_index, len(probabilities))
    total_top3_mass = sum(probabilities[index] for index in top3)
    scores = [
        probabilities[index]
        * math.exp(-HRRR_TOP3_DISTANCE_PENALTY * abs(index - hrrr_index))
        for index in top3
    ]
    score_total = sum(scores)
    if total_top3_mass <= 0 or score_total <= 0:
        return None
    reranked = list(probabilities)
    for index, score in zip(top3, scores, strict=True):
        reranked[index] = total_top3_mass * score / score_total
    total = sum(reranked)
    if total <= 0:
        raise DataError("HRRR top-3 rerank produced no probability mass")
    reranked = [value / total for value in reranked]
    member_highs = baseline.get("member_highs_f")
    return {
        "probabilities": reranked,
        "bandwidth_f": baseline.get("bandwidth_f"),
        "member_count": len(member_highs) if isinstance(member_highs, list) else None,
        "hrrr_projected_high_f": float(projected_high),
        "hrrr_bracket_index": hrrr_index,
        "hrrr_centered_top3_indexes": top3,
        "hrrr_centered_original_probabilities": [
            probabilities[index] for index in top3
        ],
        "distance_penalty": HRRR_TOP3_DISTANCE_PENALTY,
        "source": "open_meteo_gfs_hrrr",
    }


def challenger_distributions(
    snapshot: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    records = snapshot.get("http_trace")
    if not isinstance(records, list):
        raise DataError("snapshot does not contain an HTTP trace")
    ensemble_record = _trace_record(
        records, lambda url: "ensemble-api.open-meteo.com" in url
    )
    if ensemble_record is None:
        raise DataError("snapshot trace does not contain an ensemble response")
    hourly = ensemble_record.get("payload", {}).get("hourly")
    if not isinstance(hourly, dict):
        raise DataError("snapshot ensemble response is malformed")

    event = snapshot["event"]
    baseline = snapshot["distribution"]
    observed_at = (
        parse_datetime(baseline["observed_at"]).astimezone(UTC)
        if baseline.get("observed_at")
        else None
    )
    members, model_counts, warnings = extract_ensemble_members(
        hourly,
        parse_datetime(event["window_start"]).astimezone(UTC),
        parse_datetime(event["window_end"]).astimezone(UTC),
        observed_at,
    )
    if warnings:
        raise DataError(f"challenger ensemble is incomplete: {', '.join(warnings)}")
    nws_high = float(baseline["nws_high_f"])
    observed_floor = (
        float(baseline["observed_high_f"])
        if baseline.get("observed_high_f") is not None
        else None
    )
    brackets = _stored_brackets(snapshot)

    family_rows: dict[str, list[Any]] = {
        name: [member for member in members if member.model == name]
        for name, _ in model_counts
    }
    diagnostics: list[dict[str, Any]] = []
    offsets: dict[str, float] = {}
    for family, rows in family_rows.items():
        full_highs = sorted(member.full_high_f for member in rows)
        median = statistics.median(full_highs)
        quartiles = statistics.quantiles(full_highs, n=4, method="inclusive")
        offset = nws_high - median
        offsets[family] = offset
        diagnostics.append(
            {
                "family": family,
                "member_count": len(rows),
                "minimum_high_f": min(full_highs),
                "q25_high_f": quartiles[0],
                "median_high_f": median,
                "q75_high_f": quartiles[2],
                "maximum_high_f": max(full_highs),
                "nws_anchor_f": nws_high,
                "center_offset_f": offset,
                "centered_median_f": median + offset,
                "centered_minimum_high_f": min(full_highs) + offset,
                "centered_maximum_high_f": max(full_highs) + offset,
            }
        )

    variants: dict[str, dict[str, Any]] = {}
    pooled_means: list[float] = []
    pooled_weights: list[float] = []
    active_families = list(family_rows)
    for family, rows in family_rows.items():
        projected = [
            member
            for member in rows
            if observed_at is None or member.remaining_high_f is not None
        ]
        means = [
            (
                member.full_high_f
                if observed_at is None
                else float(member.remaining_high_f)
            )
            + offsets[family]
            for member in projected
        ]
        weights = [1.0 / len(means)] * len(means) if means else []
        probabilities, bandwidth = _centered_probabilities(
            brackets, means, weights, observed_floor
        )
        variants[_family_slug(family)] = {
            "probabilities": probabilities,
            "bandwidth_f": bandwidth,
            "member_count": len(means),
            "families": [family],
        }
        if means:
            family_weight = 1.0 / len(active_families)
            pooled_means.extend(means)
            pooled_weights.extend([family_weight / len(means)] * len(means))

    pooled_probabilities, pooled_bandwidth = _centered_probabilities(
        brackets, pooled_means, pooled_weights, observed_floor
    )
    variants["family_centered"] = {
        "probabilities": pooled_probabilities,
        "bandwidth_f": pooled_bandwidth,
        "member_count": len(pooled_means),
        "families": active_families,
        "family_total_weights": {
            family: 1.0 / len(active_families) for family in active_families
        },
    }
    hrrr_variant = hrrr_top3_rerank_distribution(snapshot)
    if hrrr_variant is not None:
        variants["hrrr_top3_rerank"] = hrrr_variant
    return variants, diagnostics


def score_probabilities(
    tickers: list[str], probabilities: list[float], winner_ticker: str
) -> dict[str, Any]:
    if winner_ticker not in tickers:
        raise DataError(f"winner {winner_ticker} is not in forecast brackets")
    outcome_index = tickers.index(winner_ticker)
    outcome_probability = probabilities[outcome_index]
    brier = sum(
        (probability - float(index == outcome_index)) ** 2
        for index, probability in enumerate(probabilities)
    )
    cumulative_forecast = 0.0
    cumulative_outcome = 0.0
    ranked_terms: list[float] = []
    for index in range(len(probabilities) - 1):
        cumulative_forecast += probabilities[index]
        cumulative_outcome += float(index == outcome_index)
        ranked_terms.append((cumulative_forecast - cumulative_outcome) ** 2)
    ranked_probability = sum(ranked_terms) / max(1, len(ranked_terms))
    entropy = -sum(value * math.log(value) for value in probabilities if value > 0)
    maximum = max(probabilities)
    leaders = [
        index
        for index, probability in enumerate(probabilities)
        if math.isclose(probability, maximum, rel_tol=0.0, abs_tol=1e-12)
    ]
    unique = len(leaders) == 1
    return {
        "outcome_probability": outcome_probability,
        "log_loss": -math.log(outcome_probability) if outcome_probability > 0 else None,
        "zero_probability": outcome_probability <= 0,
        "brier": brier,
        "ranked_probability_score": ranked_probability,
        "entropy": entropy,
        "top_one_covered": unique,
        "top_one_correct": bool(unique and leaders[0] == outcome_index) if unique else None,
    }


def nws_top_one_correct(snapshot: dict[str, Any], winner_ticker: str) -> bool:
    distribution = snapshot["distribution"]
    nws_high = float(distribution["nws_high_f"])
    matching = [
        str(row["ticker"])
        for row in distribution["brackets"]
        if point_mass_bracket_probability(
            Bracket(
                str(row["ticker"]),
                str(row["label"]),
                int(row["lower"]) if row["lower"] is not None else None,
                int(row["upper"]) if row["upper"] is not None else None,
            ),
            nws_high,
        )
    ]
    if len(matching) != 1:
        raise DataError("NWS high does not map to exactly one bracket")
    return matching[0] == winner_ticker


def replay_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("model_source_hash") != model_source_hash():
        raise DataError("snapshot model hash does not match the current model source")
    event = snapshot["event"]
    city = city_by_key(str(event["city"]))
    target = date.fromisoformat(str(event["target_date"]))
    as_of = parse_datetime(snapshot["checkpoint"]["as_of"]).astimezone(UTC)
    records = snapshot.get("http_trace")
    if not isinstance(records, list):
        raise DataError("snapshot does not contain an HTTP trace")
    validate_trace_observation_times(records, as_of)
    for condition, key in ((True, "distribution"), (False, "forecast_only_distribution")):
        replay = ReplayHttpClient(records)
        calculated = build_distribution(
            replay,  # type: ignore[arg-type]
            city,
            target,
            as_of=as_of,
            condition_on_observations=condition,
        )
        replay.assert_consumed()
        _, stored = probabilities_from_snapshot(snapshot, key)
        if len(stored) != len(calculated.probabilities) or not all(
            math.isclose(a, b, rel_tol=0.0, abs_tol=1e-12)
            for a, b in zip(stored, calculated.probabilities)
        ):
            raise DataError(f"offline replay mismatch for {key}")


def mean(values: Iterable[float]) -> float | None:
    rows = list(values)
    return sum(rows) / len(rows) if rows else None


def percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires values")
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def date_cluster_bootstrap(
    rows: list[dict[str, Any]], metric: str, samples: int
) -> tuple[float, float] | None:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get(metric) is not None:
            by_date[str(row["target_date"])].append(row)
    dates = sorted(by_date)
    if len(dates) < 2:
        return None
    generator = random.Random(20260620)
    estimates: list[float] = []
    for _ in range(samples):
        sampled = [generator.choice(dates) for _ in dates]
        values = [
            float(row[metric])
            for selected_date in sampled
            for row in by_date[selected_date]
            if row.get(metric) is not None
        ]
        estimates.append(sum(values) / len(values))
    return percentile(estimates, 0.025), percentile(estimates, 0.975)


def build_score_rows(
    base: Path,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    score_rows: list[dict[str, Any]] = []
    calibration_rows: list[dict[str, Any]] = []
    snapshot_meta: list[dict[str, Any]] = []
    family_diagnostics: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    for path in sorted((base / "snapshots").glob("*/*/*.json.gz")):
        snapshot = read_json_gz(path)
        event = snapshot["event"]
        target = date.fromisoformat(str(event["target_date"]))
        settlement_file = settlement_path(base, target, str(event["city"]))
        if not settlement_file.exists():
            continue
        settlement = read_json_gz(settlement_file)
        winner = str(settlement["winner_ticker"])
        replay_snapshot(snapshot)
        checkpoint = snapshot["checkpoint"]
        common = {
            "city": event["city"],
            "target_date": event["target_date"],
            "event_ticker": event["event_ticker"],
            "checkpoint": checkpoint["name"],
            "scheduled_at": checkpoint["scheduled_at"],
            "as_of": checkpoint["as_of"],
            "latency_seconds": float(checkpoint["latency_seconds"]),
            "winner_ticker": winner,
        }
        snapshot_meta.append(common)
        source_rows.append(
            {
                **common,
                "snapshot_schema_version": snapshot.get("schema_version", 1),
                **flatten_source_metadata(snapshot),
            }
        )
        for model_name, key in (
            ("full", "distribution"),
            ("forecast_only", "forecast_only_distribution"),
        ):
            tickers, probabilities = probabilities_from_snapshot(snapshot, key)
            row = {**common, "model": model_name}
            row.update(score_probabilities(tickers, probabilities, winner))
            row["nws_top_one_correct"] = nws_top_one_correct(snapshot, winner)
            score_rows.append(row)
            if model_name == "full":
                outcome_index = tickers.index(winner)
                for index, probability in enumerate(probabilities):
                    calibration_rows.append(
                        {
                            **common,
                            "model": model_name,
                            "probability": probability,
                            "outcome": float(index == outcome_index),
                        }
                    )
        variants, diagnostics = challenger_distributions(snapshot)
        family_diagnostics.extend({**common, **row} for row in diagnostics)
        tickers, _ = probabilities_from_snapshot(snapshot, "distribution")
        outcome_index = tickers.index(winner)
        for model_name, variant in variants.items():
            probabilities = [float(value) for value in variant["probabilities"]]
            row = {
                **common,
                "model": model_name,
                "bandwidth_f": variant["bandwidth_f"],
                "member_count": variant["member_count"],
            }
            row.update(score_probabilities(tickers, probabilities, winner))
            row["nws_top_one_correct"] = nws_top_one_correct(snapshot, winner)
            score_rows.append(row)
            for index, probability in enumerate(probabilities):
                calibration_rows.append(
                    {
                        **common,
                        "model": model_name,
                        "probability": probability,
                        "outcome": float(index == outcome_index),
                    }
                )
        uniform = [1.0 / len(tickers)] * len(tickers)
        uniform_row = {**common, "model": "uniform"}
        uniform_row.update(score_probabilities(tickers, uniform, winner))
        uniform_row["nws_top_one_correct"] = nws_top_one_correct(snapshot, winner)
        score_rows.append(uniform_row)
    return score_rows, calibration_rows, snapshot_meta, family_diagnostics, source_rows


def aggregate_scores(
    rows: list[dict[str, Any]], bootstrap_samples: int
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["model"]), str(row["checkpoint"]))].append(row)
    aggregates: list[dict[str, Any]] = []
    for (model, checkpoint), group in sorted(grouped.items()):
        result: dict[str, Any] = {
            "model": model,
            "checkpoint": checkpoint,
            "forecast_count": len(group),
            "unique_event_count": len(
                {(row["city"], row["target_date"]) for row in group}
            ),
            "date_count": len({row["target_date"] for row in group}),
            "zero_probability_count": sum(bool(row["zero_probability"]) for row in group),
            "top_one_coverage": mean(float(row["top_one_covered"]) for row in group),
            "top_one_accuracy": mean(
                float(row["top_one_correct"])
                for row in group
                if row["top_one_correct"] is not None
            ),
            "nws_top_one_accuracy": mean(float(row["nws_top_one_correct"]) for row in group),
        }
        for metric in ("log_loss", "brier", "ranked_probability_score", "entropy"):
            result[metric] = mean(
                float(row[metric]) for row in group if row[metric] is not None
            )
            interval = date_cluster_bootstrap(group, metric, bootstrap_samples)
            result[f"{metric}_ci95"] = list(interval) if interval else None
        aggregates.append(result)
    return aggregates


def paired_checkpoint_deltas(
    rows: list[dict[str, Any]], bootstrap_samples: int = 2000
) -> list[dict[str, Any]]:
    full_rows = [row for row in rows if row["model"] == "full"]
    lookup = {
        (row["city"], row["target_date"], row["checkpoint"]): row for row in full_rows
    }
    results: list[dict[str, Any]] = []
    for checkpoint in CHECKPOINT_ORDER[1:]:
        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in full_rows:
            if row["checkpoint"] != checkpoint:
                continue
            baseline = lookup.get((row["city"], row["target_date"], CHECKPOINT_ORDER[0]))
            if baseline:
                pairs.append((row, baseline))
        delta_rows: list[dict[str, Any]] = []
        for current, baseline in pairs:
            delta_rows.append(
                {
                    "target_date": current["target_date"],
                    "log_loss_delta": (
                        float(current["log_loss"]) - float(baseline["log_loss"])
                        if current["log_loss"] is not None and baseline["log_loss"] is not None
                        else None
                    ),
                    "brier_delta": float(current["brier"]) - float(baseline["brier"]),
                    "ranked_probability_score_delta": (
                        float(current["ranked_probability_score"])
                        - float(baseline["ranked_probability_score"])
                    ),
                }
            )
        result: dict[str, Any] = {"checkpoint": checkpoint, "pair_count": len(pairs)}
        for metric in (
            "log_loss_delta",
            "brier_delta",
            "ranked_probability_score_delta",
        ):
            result[metric] = mean(
                float(row[metric]) for row in delta_rows if row[metric] is not None
            )
            interval = date_cluster_bootstrap(delta_rows, metric, bootstrap_samples)
            result[f"{metric}_ci95"] = list(interval) if interval else None
        results.append(result)
    return results


def paired_model_deltas(
    rows: list[dict[str, Any]], bootstrap_samples: int = 2000
) -> list[dict[str, Any]]:
    lookup = {
        (row["city"], row["target_date"], row["checkpoint"], row["model"]): row
        for row in rows
    }
    baseline_rows = [row for row in rows if row["model"] == "full"]
    results: list[dict[str, Any]] = []
    for challenger in CHALLENGER_NAMES:
        delta_rows: list[dict[str, Any]] = []
        for baseline in baseline_rows:
            challenger_row = lookup.get(
                (
                    baseline["city"],
                    baseline["target_date"],
                    baseline["checkpoint"],
                    challenger,
                )
            )
            if challenger_row is None:
                continue
            delta_rows.append(
                {
                    "target_date": baseline["target_date"],
                    "log_loss_delta": (
                        float(challenger_row["log_loss"]) - float(baseline["log_loss"])
                        if challenger_row["log_loss"] is not None
                        and baseline["log_loss"] is not None
                        else None
                    ),
                    "brier_delta": float(challenger_row["brier"])
                    - float(baseline["brier"]),
                    "ranked_probability_score_delta": float(
                        challenger_row["ranked_probability_score"]
                    )
                    - float(baseline["ranked_probability_score"]),
                }
            )
        result: dict[str, Any] = {
            "challenger": challenger,
            "pair_count": len(delta_rows),
            "date_count": len({row["target_date"] for row in delta_rows}),
            "status": (
                "eligible_for_manual_review"
                if len({row["target_date"] for row in delta_rows}) >= 60
                else "exploratory_minimum_60_dates"
            ),
            "automatic_promotion": False,
        }
        for metric in (
            "log_loss_delta",
            "brier_delta",
            "ranked_probability_score_delta",
        ):
            result[metric] = mean(
                float(row[metric]) for row in delta_rows if row[metric] is not None
            )
            interval = date_cluster_bootstrap(delta_rows, metric, bootstrap_samples)
            result[f"{metric}_ci95"] = list(interval) if interval else None
        results.append(result)
    return results


def aggregate_scores_by_city(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["model"] == "full":
            grouped[(str(row["city"]), str(row["checkpoint"]))].append(row)
    results: list[dict[str, Any]] = []
    for (city, checkpoint), group in sorted(grouped.items()):
        results.append(
            {
                "city": city,
                "checkpoint": checkpoint,
                "forecast_count": len(group),
                "log_loss": mean(
                    float(row["log_loss"]) for row in group if row["log_loss"] is not None
                ),
                "brier": mean(float(row["brier"]) for row in group),
                "ranked_probability_score": mean(
                    float(row["ranked_probability_score"]) for row in group
                ),
                "top_one_coverage": mean(float(row["top_one_covered"]) for row in group),
                "top_one_accuracy": mean(
                    float(row["top_one_correct"])
                    for row in group
                    if row["top_one_correct"] is not None
                ),
            }
        )
    return results


def calibration_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["model"])].append(row)
    result: list[dict[str, Any]] = []
    for model, model_rows in sorted(grouped.items()):
        bins: list[list[dict[str, Any]]] = [[] for _ in range(10)]
        for row in model_rows:
            index = min(9, int(float(row["probability"]) * 10))
            bins[index].append(row)
        result.extend(
            {
                "model": model,
                "lower": index / 10,
                "upper": (index + 1) / 10,
                "count": len(group),
                "mean_probability": mean(float(row["probability"]) for row in group),
                "observed_frequency": mean(float(row["outcome"]) for row in group),
            }
            for index, group in enumerate(bins)
        )
    return result


def completeness_summary(base: Path, manifest: dict[str, Any], now: datetime) -> dict[str, Any]:
    started = parse_datetime(str(manifest["started_at"])).astimezone(UTC)
    expected = list(iter_schedules(started, now))
    completed = sum(snapshot_path(base, checkpoint).exists() for checkpoint in expected)
    marked_missing = sum(missing_path(base, checkpoint).exists() for checkpoint in expected)
    snapshots = [read_json_gz(path) for path in (base / "snapshots").glob("*/*/*.json.gz")]
    latencies = [float(snapshot["checkpoint"]["latency_seconds"]) for snapshot in snapshots]
    events = {
        (snapshot["event"]["city"], snapshot["event"]["target_date"])
        for snapshot in snapshots
    }
    settled = sum(
        settlement_path(base, date.fromisoformat(target), city).exists()
        for city, target in events
    )
    return {
        "expected_checkpoints": len(expected),
        "completed_checkpoints": completed,
        "marked_missing_checkpoints": marked_missing,
        "unaccounted_checkpoints": len(expected) - completed - marked_missing,
        "completion_rate": completed / len(expected) if expected else None,
        "mean_latency_seconds": mean(latencies),
        "max_latency_seconds": max(latencies) if latencies else None,
        "unique_events": len(events),
        "settled_events": settled,
        "pending_settlements": len(events) - settled,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def plot_evaluation(
    aggregates: list[dict[str, Any]],
    paired: list[dict[str, Any]],
    score_rows: list[dict[str, Any]],
    completeness: dict[str, Any],
    output: Path,
) -> None:
    figure, axes = plt.subplots(2, 3, figsize=(17, 10), constrained_layout=True)
    labels = list(CHECKPOINT_ORDER)
    short_labels = ["T-6", "T+6", "T+10", "T+14", "T+18"]
    plotted_models = ("full", "family_centered", *CHALLENGER_NAMES[1:])
    colors = {
        "full": "#111827",
        "family_centered": "#167D8D",
        "gefs_centered": "#D97706",
        "ecmwf_ifs_centered": "#2563EB",
        "icon_eps_centered": "#16A34A",
        "gem_centered": "#C2410C",
    }
    lookup = {(row["model"], row["checkpoint"]): row for row in aggregates}

    for axis, metric, title in zip(
        axes.flat[:3],
        ("log_loss", "brier", "ranked_probability_score"),
        ("Log Loss", "Brier Score", "Ranked Probability Score"),
    ):
        for model in plotted_models:
            values = [lookup.get((model, checkpoint), {}).get(metric) for checkpoint in labels]
            if any(value is not None for value in values):
                axis.plot(
                    short_labels,
                    values,
                    marker="o",
                    label=model.replace("_", " "),
                    color=colors[model],
                )
        axis.set_title(title)
        axis.set_ylabel("Lower is better")
        axis.grid(alpha=0.2)
        axis.legend(fontsize=8)

    full_by_checkpoint = {
        row["checkpoint"]: row for row in aggregates if row["model"] == "full"
    }
    axes[1, 0].plot(
        short_labels,
        [full_by_checkpoint.get(checkpoint, {}).get("top_one_accuracy") for checkpoint in labels],
        marker="o",
        label="model",
        color="#167D8D",
    )
    family_centered_by_checkpoint = {
        row["checkpoint"]: row
        for row in aggregates
        if row["model"] == "family_centered"
    }
    axes[1, 0].plot(
        short_labels,
        [
            family_centered_by_checkpoint.get(checkpoint, {}).get("top_one_accuracy")
            for checkpoint in labels
        ],
        marker="o",
        label="family centered",
        color="#2563EB",
    )
    axes[1, 0].plot(
        short_labels,
        [full_by_checkpoint.get(checkpoint, {}).get("nws_top_one_accuracy") for checkpoint in labels],
        marker="o",
        label="NWS bracket",
        color="#D97706",
    )
    axes[1, 0].set_title("Event-Level Top-One Accuracy")
    axes[1, 0].set_ylim(0, 1)
    axes[1, 0].legend(fontsize=8)
    axes[1, 0].grid(alpha=0.2)

    city_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in score_rows:
        if row["model"] == "full":
            city_rows[str(row["city"])].append(row)
    city_names = [city.key for city in CITIES]
    axes[1, 1].bar(
        city_names,
        [
            mean(float(row["ranked_probability_score"]) for row in city_rows[city]) or 0
            for city in city_names
        ],
        color="#167D8D",
    )
    axes[1, 1].set_title("Full-Model RPS by City")
    axes[1, 1].set_ylabel("Lower is better")
    axes[1, 1].grid(axis="y", alpha=0.2)

    paired_lookup = {row["checkpoint"]: row for row in paired}
    axes[1, 2].bar(
        short_labels[1:],
        [
            paired_lookup.get(checkpoint, {}).get("ranked_probability_score_delta") or 0
            for checkpoint in labels[1:]
        ],
        color="#C2410C",
    )
    axes[1, 2].axhline(0, color="black", linewidth=0.8)
    axes[1, 2].set_title("Paired RPS Change vs T-6")
    axes[1, 2].set_ylabel("Negative is improvement")
    axes[1, 2].grid(axis="y", alpha=0.2)
    figure.suptitle(
        "Point-in-Time Weather Model Evaluation\n"
        f"{completeness['unique_events']} events · "
        f"{completeness['completed_checkpoints']}/{completeness['expected_checkpoints']} checkpoints",
        fontsize=17,
        fontweight="bold",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(figure)


def plot_diagnostics(
    calibration: list[dict[str, Any]],
    snapshot_meta: list[dict[str, Any]],
    completeness: dict[str, Any],
    output: Path,
) -> None:
    figure, axes = plt.subplots(1, 3, figsize=(17, 5), constrained_layout=True)
    for model, color in (("full", "#111827"), ("family_centered", "#167D8D")):
        calibration_rows = [
            row for row in calibration if row["model"] == model and row["count"]
        ]
        if calibration_rows:
            axes[0].plot(
                [row["mean_probability"] for row in calibration_rows],
                [row["observed_frequency"] for row in calibration_rows],
                marker="o",
                label=model.replace("_", " "),
                color=color,
            )
    axes[0].plot([0, 1], [0, 1], linestyle="--", color="#6B7280")
    axes[0].set_xlim(0, 1)
    axes[0].set_ylim(0, 1)
    axes[0].set_title("Exploratory Calibration")
    axes[0].set_xlabel("Mean forecast probability")
    axes[0].set_ylabel("Observed frequency")
    axes[0].grid(alpha=0.2)
    axes[0].legend(fontsize=8)

    labels = ["completed", "missing", "unaccounted"]
    values = [
        completeness["completed_checkpoints"],
        completeness["marked_missing_checkpoints"],
        completeness["unaccounted_checkpoints"],
    ]
    axes[1].bar(labels, values, color=["#167D8D", "#C2410C", "#6B7280"])
    axes[1].set_title("Checkpoint Completeness")
    axes[1].set_ylabel("Count")
    axes[1].grid(axis="y", alpha=0.2)

    latency_by_checkpoint: dict[str, list[float]] = defaultdict(list)
    for row in snapshot_meta:
        latency_by_checkpoint[str(row["checkpoint"])].append(float(row["latency_seconds"]))
    axes[2].bar(
        ["T-6", "T+6", "T+10", "T+14", "T+18"],
        [mean(latency_by_checkpoint[name]) or 0 for name in CHECKPOINT_ORDER],
        color="#D97706",
    )
    axes[2].axhline(CHECKPOINT_GRACE.total_seconds(), color="#C2410C", linestyle="--")
    axes[2].set_title("Mean Collection Latency")
    axes[2].set_ylabel("Seconds after checkpoint")
    axes[2].grid(axis="y", alpha=0.2)
    figure.suptitle("Backtest Data Quality Diagnostics", fontsize=17, fontweight="bold")
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(figure)


def evaluate(
    root: Path,
    cohort: str,
    output: Path | None,
    bootstrap_samples: int,
) -> int:
    manifest = load_manifest(root, cohort)
    if manifest.get("model_source_hash") != model_source_hash():
        raise DataError(
            "current model source differs from the frozen cohort; restore it before evaluation"
        )
    base = cohort_dir(root, cohort)
    (
        score_rows,
        calibration_rows,
        snapshot_meta,
        family_diagnostics,
        source_rows,
    ) = build_score_rows(base)
    if not score_rows:
        raise DataError("no settled snapshots are available for evaluation")
    aggregates = aggregate_scores(score_rows, bootstrap_samples)
    city_aggregates = aggregate_scores_by_city(score_rows)
    paired = paired_checkpoint_deltas(score_rows, bootstrap_samples)
    model_deltas = paired_model_deltas(score_rows, bootstrap_samples)
    calibration = calibration_summary(calibration_rows)
    completeness = completeness_summary(base, manifest, datetime.now(UTC))
    report_dir = output or (
        base / "reports" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    report_dir.mkdir(parents=True, exist_ok=False)
    write_csv(report_dir / "forecast_scores.csv", score_rows)
    write_csv(report_dir / "aggregate_scores.csv", aggregates)
    write_csv(report_dir / "city_scores.csv", city_aggregates)
    write_csv(report_dir / "paired_model_deltas.csv", model_deltas)
    write_csv(report_dir / "model_family_diagnostics.csv", family_diagnostics)
    write_csv(report_dir / "source_freshness.csv", source_rows)
    with (report_dir / "evaluation.json").open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "schema_version": SCHEMA_VERSION,
                "cohort": cohort,
                "generated_at": datetime.now(UTC).isoformat(),
                "challenger_source_hash": collector_source_hash(),
                "challenger_status": "exploratory_no_automatic_promotion",
                "aggregates": aggregates,
                "city_aggregates": city_aggregates,
                "paired_checkpoint_deltas": paired,
                "paired_model_deltas": model_deltas,
                "model_family_diagnostics": family_diagnostics,
                "source_freshness": source_rows,
                "calibration": calibration,
                "completeness": completeness,
                "limitations": {
                    "pilot": "15 days is operational validation, not conclusive accuracy evidence",
                    "initial": "60 days supports initial pooled comparisons but remains seasonal",
                    "independence": "checkpoints for one city-day share a settlement outcome",
                    "excluded": "prices, fills, PnL, and trading edge are not evaluated",
                },
            },
            handle,
            indent=2,
            sort_keys=True,
        )
    plot_evaluation(
        aggregates, paired, score_rows, completeness, report_dir / "evaluation_dashboard.png"
    )
    plot_diagnostics(
        calibration,
        snapshot_meta,
        completeness,
        report_dir / "data_quality_dashboard.png",
    )
    print(f"Evaluation written to {report_dir.resolve()}")
    for row in aggregates:
        if row["model"] == "full":
            print(
                f"{row['checkpoint']:<12} n={row['unique_event_count']:<4} "
                f"log={row['log_loss']!s:<8} brier={row['brier']!s:<8} "
                f"rps={row['ranked_probability_score']!s:<8} "
                f"top1={row['top_one_accuracy']!s}"
            )
    return 0


def show_status(root: Path, cohort: str) -> int:
    manifest = load_manifest(root, cohort)
    base = cohort_dir(root, cohort)
    summary = completeness_summary(base, manifest, datetime.now(UTC))
    attempts = len(list((base / "attempts").glob("*/*/*/*.json.gz")))
    summary["failed_attempts"] = attempts
    summary["cohort"] = cohort
    summary["started_at"] = manifest["started_at"]
    summary["model_hash_matches"] = manifest.get("model_source_hash") == model_source_hash()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect and evaluate point-in-time Kalshi weather forecasts."
    )
    parser.add_argument("--root", type=Path, default=Path("backtest_data"))
    parser.add_argument("--cohort", default="pilot-v1")
    parser.add_argument(
        "--nws-user-agent",
        default=os.getenv("NWS_USER_AGENT"),
        help="Descriptive NWS user agent with contact information.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="Append stdout and stderr to this file (for background collection).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("collect-due", help="Collect checkpoints currently due.")
    commands.add_parser("settle", help="Fetch finalized outcomes for collected events.")
    commands.add_parser("status", help="Show collection and settlement completeness.")
    evaluate_parser = commands.add_parser(
        "evaluate", help="Replay and score settled snapshots without network access."
    )
    evaluate_parser.add_argument("--output", type=Path)
    evaluate_parser.add_argument("--bootstrap-samples", type=int, default=2000)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    log_handle = None
    if args.log_file:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = args.log_file.open("a", encoding="utf-8", buffering=1)
        sys.stdout = log_handle
        sys.stderr = log_handle
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        if args.command == "collect-due":
            if not args.nws_user_agent:
                raise DataError("collect-due requires NWS_USER_AGENT or --nws-user-agent")
            return collect_due(args.root, args.cohort, args.nws_user_agent)
        if args.command == "settle":
            if not args.nws_user_agent:
                raise DataError("settle requires NWS_USER_AGENT or --nws-user-agent")
            return settle_pending(args.root, args.cohort, args.nws_user_agent)
        if args.command == "status":
            return show_status(args.root, args.cohort)
        if args.command == "evaluate":
            if args.bootstrap_samples < 100:
                raise DataError("--bootstrap-samples must be at least 100")
            return evaluate(args.root, args.cohort, args.output, args.bootstrap_samples)
        raise DataError(f"unknown command {args.command}")
    except (DataError, OSError, ValueError) as exc:
        print(f"Backtest error: {exc}", file=sys.stderr)
        return 1
    finally:
        if log_handle:
            log_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
