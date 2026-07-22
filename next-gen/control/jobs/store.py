"""SQLite-backed local job store."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class JobRecord:
    id: str
    kind: str
    registry_id: str
    entrypoint: str
    status: str
    created_utc: str
    updated_utc: str
    params: dict[str, Any]
    command: list[str]
    cwd: str | None
    output_path: str | None
    log_path: str
    returncode: int | None = None
    error: str | None = None


class JobStore:
    def __init__(self, path: Path = Path(".control/jobs/jobs.sqlite")) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def create(
        self,
        kind: str,
        registry_id: str,
        entrypoint: str,
        params: dict[str, Any],
        command: list[str],
        cwd: Path | None,
        output_path: Path | None,
    ) -> JobRecord:
        now = _now()
        job_id = f"job_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}"
        log_path = self.path.parent / "logs" / f"{job_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = JobRecord(
            id=job_id,
            kind=kind,
            registry_id=registry_id,
            entrypoint=entrypoint,
            status="queued",
            created_utc=now,
            updated_utc=now,
            params=params,
            command=command,
            cwd=str(cwd) if cwd else None,
            output_path=str(output_path) if output_path else None,
            log_path=str(log_path),
        )
        with closing(self._connect()) as db:
            db.execute(
                """
                insert into jobs (
                    id, kind, registry_id, entrypoint, status, created_utc, updated_utc,
                    params_json, command_json, cwd, output_path, log_path, returncode, error
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _record_tuple(record),
            )
            db.commit()
        return record

    def update(
        self,
        job_id: str,
        status: str,
        returncode: int | None = None,
        error: str | None = None,
    ) -> None:
        with closing(self._connect()) as db:
            db.execute(
                """
                update jobs
                set status = ?, updated_utc = ?, returncode = ?, error = ?
                where id = ?
                """,
                (status, _now(), returncode, error, job_id),
            )
            db.commit()

    def get(self, job_id: str) -> JobRecord | None:
        with closing(self._connect()) as db:
            row = db.execute("select * from jobs where id = ?", (job_id,)).fetchone()
        return _row_to_record(row) if row else None

    def list(self) -> list[JobRecord]:
        with closing(self._connect()) as db:
            rows = db.execute("select * from jobs order by created_utc desc").fetchall()
        return [_row_to_record(row) for row in rows]

    def _init(self) -> None:
        with closing(self._connect()) as db:
            db.execute(
                """
                create table if not exists jobs (
                    id text primary key,
                    kind text not null,
                    registry_id text not null,
                    entrypoint text not null,
                    status text not null,
                    created_utc text not null,
                    updated_utc text not null,
                    params_json text not null,
                    command_json text not null,
                    cwd text,
                    output_path text,
                    log_path text not null,
                    returncode integer,
                    error text
                )
                """
            )
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


def serialize_job(record: JobRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "kind": record.kind,
        "registry_id": record.registry_id,
        "entrypoint": record.entrypoint,
        "status": record.status,
        "created_utc": record.created_utc,
        "updated_utc": record.updated_utc,
        "params": record.params,
        "command": record.command,
        "cwd": record.cwd,
        "output_path": record.output_path,
        "log_path": record.log_path,
        "returncode": record.returncode,
        "error": record.error,
    }


def _record_tuple(record: JobRecord) -> tuple[Any, ...]:
    return (
        record.id,
        record.kind,
        record.registry_id,
        record.entrypoint,
        record.status,
        record.created_utc,
        record.updated_utc,
        json.dumps(record.params, sort_keys=True),
        json.dumps(record.command),
        record.cwd,
        record.output_path,
        record.log_path,
        record.returncode,
        record.error,
    )


def _row_to_record(row: tuple[Any, ...]) -> JobRecord:
    return JobRecord(
        id=row[0],
        kind=row[1],
        registry_id=row[2],
        entrypoint=row[3],
        status=row[4],
        created_utc=row[5],
        updated_utc=row[6],
        params=json.loads(row[7]),
        command=json.loads(row[8]),
        cwd=row[9],
        output_path=row[10],
        log_path=row[11],
        returncode=row[12],
        error=row[13],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
