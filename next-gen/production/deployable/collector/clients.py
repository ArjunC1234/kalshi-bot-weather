"""Provider, storage, and Postgres clients for collector v3."""

from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from ids import deterministic_id, gzip_json_bytes, sha256_bytes
from schema import FACT_TABLES

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover - depends on deployed env.
    psycopg = None
    dict_row = None
    Jsonb = None


@dataclass(frozen=True)
class RawPayload:
    raw_payload_id: str
    collector_run_id: str
    city: str | None
    event_ticker: str | None
    target_date: str | None
    snapshot_time_utc: str
    snapshot_time_local: str | None
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


class HttpRecorder:
    def __init__(
        self,
        user_agent: str,
        collector_run_id: str,
        snapshot_time_utc: datetime,
        bucket: str,
        schema_version: int,
        timeout: float = 25.0,
    ) -> None:
        self.collector_run_id = collector_run_id
        self.snapshot_time_utc = snapshot_time_utc
        self.bucket = bucket
        self.schema_version = schema_version
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        self.timeout = timeout
        self.raw_payloads: list[RawPayload] = []
        self.errors: list[dict[str, Any]] = []

    def get_json(
        self,
        provider: str,
        endpoint_name: str,
        url: str | None,
        params: dict[str, Any] | None = None,
        city: str | None = None,
        event_ticker: str | None = None,
        target_date: str | None = None,
        snapshot_time_local: str | None = None,
        required: bool = False,
    ) -> dict[str, Any] | None:
        if not url:
            return None
        requested = datetime.now(UTC)
        status_code: int | None = None
        payload: Any = None
        error: Exception | None = None
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            status_code = response.status_code
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - record provider details.
            error = exc
            payload = {"error": str(exc)}
        received = datetime.now(UTC)
        compressed = gzip_json_bytes(payload)
        digest = sha256_bytes(compressed)
        path = raw_storage_path(
            provider,
            target_date or self.snapshot_time_utc.date().isoformat(),
            city or "unknown-city",
            event_ticker or "unknown-event",
            self.snapshot_time_utc,
            digest,
        )
        raw_id = deterministic_id(provider, endpoint_name, path, digest)
        self.raw_payloads.append(
            RawPayload(
                raw_payload_id=raw_id,
                collector_run_id=self.collector_run_id,
                city=city,
                event_ticker=event_ticker,
                target_date=target_date,
                snapshot_time_utc=self.snapshot_time_utc.isoformat(),
                snapshot_time_local=snapshot_time_local,
                provider=provider,
                endpoint_name=endpoint_name,
                method="GET",
                url=url,
                params=params or {},
                requested_at_utc=requested.isoformat(),
                received_at_utc=received.isoformat(),
                latency_ms=round((received - requested).total_seconds() * 1000),
                status_code=status_code,
                success=error is None,
                error_type=type(error).__name__ if error else None,
                error_message=str(error) if error else None,
                content_sha256=digest,
                compressed_size_bytes=len(compressed),
                storage_bucket=self.bucket,
                storage_path=path,
                schema_version=self.schema_version,
                payload=payload,
            )
        )
        if error is not None:
            self.record_error(provider, endpoint_name, error, city, event_ticker, target_date)
            if required:
                raise RuntimeError(f"{provider}/{endpoint_name} failed: {error}") from error
            return None
        return payload if isinstance(payload, dict) else None

    def record_error(
        self,
        provider: str,
        endpoint_name: str,
        error: Exception,
        city: str | None,
        event_ticker: str | None,
        target_date: str | None,
    ) -> None:
        self.errors.append(
            {
                "provider_error_id": deterministic_id(
                    "provider_error",
                    self.collector_run_id,
                    provider,
                    endpoint_name,
                    city,
                    event_ticker,
                    len(self.errors),
                ),
                "collector_run_id": self.collector_run_id,
                "city": city,
                "event_ticker": event_ticker,
                "target_date": target_date,
                "snapshot_time_utc": self.snapshot_time_utc.isoformat(),
                "provider": provider,
                "endpoint_name": endpoint_name,
                "error_type": type(error).__name__,
                "error_message": str(error),
                "requested_at_utc": datetime.now(UTC).isoformat(),
                "metadata": {},
            }
        )

    def latest_raw_id(
        self, provider: str, endpoint_name: str, city: str | None = None
    ) -> str | None:
        for record in reversed(self.raw_payloads):
            if record.provider == provider and record.endpoint_name == endpoint_name:
                if city is None or record.city == city:
                    return record.raw_payload_id
        return None

    def retag_latest_payload(
        self,
        provider: str,
        endpoint_name: str,
        city: str,
        event_ticker: str,
        target_date: str,
        snapshot_time_local: str | None,
    ) -> str | None:
        """Attach event metadata to a payload fetched before the event was known."""
        for index in range(len(self.raw_payloads) - 1, -1, -1):
            record = self.raw_payloads[index]
            if record.provider != provider or record.endpoint_name != endpoint_name:
                continue
            if record.city not in (None, city):
                continue
            compressed = gzip_json_bytes(record.payload)
            digest = sha256_bytes(compressed)
            path = raw_storage_path(
                provider, target_date, city, event_ticker, self.snapshot_time_utc, digest
            )
            raw_id = deterministic_id(provider, endpoint_name, path, digest)
            self.raw_payloads[index] = replace(
                record,
                raw_payload_id=raw_id,
                city=city,
                event_ticker=event_ticker,
                target_date=target_date,
                snapshot_time_local=snapshot_time_local,
                content_sha256=digest,
                compressed_size_bytes=len(compressed),
                storage_path=path,
            )
            return raw_id
        return None

    def payload_ids(self, city: str, event_ticker: str, target_date: str) -> dict[str, str]:
        return {
            record.endpoint_name: record.raw_payload_id
            for record in self.raw_payloads
            if record.city == city
            and record.event_ticker == event_ticker
            and record.target_date == target_date
        }


class StorageClient:
    def __init__(self, supabase_url: str, service_role_key: str, bucket: str) -> None:
        self.url = supabase_url.rstrip("/")
        self.key = service_role_key
        self.bucket = bucket
        self.session = requests.Session()
        self.session.headers.update({"apikey": self.key, "Authorization": f"Bearer {self.key}"})

    def upload_raw_payload(self, record: dict[str, Any]) -> None:
        response = self.session.post(
            f"{self.url}/storage/v1/object/{self.bucket}/{record['storage_path']}",
            data=gzip_json_bytes(record.get("payload")),
            headers={
                "Content-Type": "application/gzip",
                "Cache-Control": "3600",
                "x-upsert": "false",
            },
            timeout=30,
        )
        if response.status_code == 409 or _is_duplicate_storage_response(response):
            return
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"storage upload failed {response.status_code}: {response.text[:500]}"
            )


class PostgresClient:
    def __init__(self, database_url: str) -> None:
        if psycopg is None:
            raise RuntimeError("psycopg is required when DATABASE_URL is set")
        self.database_url = database_url

    def execute_schema(self, sql: str) -> None:
        with psycopg.connect(self.database_url) as conn:
            conn.execute(sql)
            conn.commit()

    def insert_facts(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        with psycopg.connect(self.database_url) as conn:
            for table in FACT_TABLES:
                rows = tables.get(table, [])
                if rows:
                    _insert_rows(conn, table, rows)
            conn.commit()

    def fetch_unsettled_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            rows = conn.execute(
                "select * from v_unsettled_events order by target_date, city limit %s",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def status(self) -> dict[str, Any]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            row = conn.execute("select * from v_collector_health").fetchone()
        return dict(row) if row else {}

    def export_table(self, table: str, output: Path, start: str, end: str) -> int:
        output.parent.mkdir(parents=True, exist_ok=True)
        date_column = "target_date" if table == "settlements" else "snapshot_time_utc"
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            rows = conn.execute(
                f"select * from {table} where {date_column} >= %s and {date_column} < %s",
                _export_bounds(table, start, end),
            ).fetchall()
        write_csv(output, [dict(row) for row in rows])
        return len(rows)


def _insert_rows(conn: Any, table: str, rows: list[dict[str, Any]]) -> None:
    columns = sorted({key for row in rows for key in row if key != "payload"})
    placeholders = ", ".join(["%s"] * len(columns))
    quoted_columns = ", ".join(columns)
    sql = f"insert into {table} ({quoted_columns}) values ({placeholders}) on conflict do nothing"
    values = [tuple(_json_value(row.get(column)) for column in columns) for row in rows]
    with conn.cursor() as cur:
        cur.executemany(sql, values)


def raw_payload_row(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "payload"}


def raw_storage_path(
    provider: str,
    target_date: str,
    city: str,
    event_ticker: str,
    snapshot_time_utc: datetime,
    digest: str,
) -> str:
    safe_hour = snapshot_time_utc.strftime("%Y%m%dT%H0000Z")
    return f"raw/{provider}/{target_date}/{city}/{event_ticker}/{safe_hour}/{digest}.json.gz"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})


def _json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        if Jsonb is not None:
            return Jsonb(value)
        return json.dumps(value, default=str)
    return value


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _export_bounds(table: str, start: str, end: str) -> tuple[str, str]:
    if table == "settlements":
        return start, end
    return f"{start}T00:00:00+00:00", f"{end}T23:59:59+00:00"


def _is_duplicate_storage_response(response: requests.Response) -> bool:
    if response.status_code != 400:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return str(payload.get("statusCode")) == "409" or payload.get("error") == "Duplicate"


def raw_payload_dicts(records: list[RawPayload]) -> list[dict[str, Any]]:
    return [asdict(record) for record in records]


def sleep_backoff(attempt: int) -> None:
    time.sleep(min(2**attempt, 10))
