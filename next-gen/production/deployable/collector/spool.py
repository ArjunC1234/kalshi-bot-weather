"""Immutable local spool queue for collector v3."""

from __future__ import annotations

import gzip
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def pending_dir(data_dir: Path) -> Path:
    return data_dir / "pending"


def synced_dir(data_dir: Path) -> Path:
    return data_dir / "synced"


def failed_dir(data_dir: Path) -> Path:
    return data_dir / "failed"


def archive_dir(data_dir: Path) -> Path:
    return data_dir / "archive"


def write_spool(data_dir: Path, name: str, payload: dict[str, Any]) -> Path:
    path = pending_dir(data_dir) / name
    write_json_gz_immutable(path, payload)
    archive_spool_file(path, data_dir)
    return path


def write_json_gz_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as fh:
            json.dump(payload, fh, sort_keys=True, separators=(",", ":"), default=str)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_json_gz(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise RuntimeError(f"spool file is malformed: {path}")
    return payload


def archive_spool_file(path: Path, data_dir: Path) -> Path:
    date_part = path.name[:8] if len(path.name) >= 8 else datetime.now(UTC).strftime("%Y%m%d")
    destination = archive_dir(data_dir) / date_part / path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(path, destination)
    return destination


def mark_synced(path: Path, data_dir: Path) -> Path:
    archive_spool_file(path, data_dir)
    synced_dir(data_dir).mkdir(parents=True, exist_ok=True)
    destination = synced_dir(data_dir) / path.name
    path.replace(destination)
    return destination


def local_status(data_dir: Path) -> dict[str, Any]:
    pending = (
        sorted(pending_dir(data_dir).glob("*.json.gz")) if pending_dir(data_dir).exists() else []
    )
    synced = sorted(synced_dir(data_dir).glob("*.json.gz")) if synced_dir(data_dir).exists() else []
    failed = sorted(failed_dir(data_dir).glob("*.json.gz")) if failed_dir(data_dir).exists() else []
    archived = (
        sorted(archive_dir(data_dir).glob("*/*.json.gz")) if archive_dir(data_dir).exists() else []
    )
    raw_bytes = sum(path.stat().st_size for path in pending + synced + failed)
    archive_bytes = sum(path.stat().st_size for path in archived)
    return {
        "data_dir": str(data_dir),
        "pending_spool_files": len(pending),
        "synced_spool_files": len(synced),
        "failed_spool_files": len(failed),
        "archived_spool_files": len(archived),
        "local_spool_bytes": raw_bytes,
        "local_archive_bytes": archive_bytes,
        "local_total_bytes": raw_bytes + archive_bytes,
    }
