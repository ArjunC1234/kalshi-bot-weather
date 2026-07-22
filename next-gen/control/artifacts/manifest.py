"""Run manifest helpers for registry-created artifacts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def write_run_manifest(
    output_path: Path,
    *,
    artifact_type: str,
    registry_id: str,
    entrypoint: str,
    status: str,
    params: dict[str, Any],
    command: list[str],
    job_id: str | None = None,
    source_export_id: str | None = None,
    contract: str | None = None,
    returncode: int | None = None,
    error: str | None = None,
) -> Path:
    output_path.mkdir(parents=True, exist_ok=True)
    manifest = {
        "artifact_type": artifact_type,
        "registry_id": registry_id,
        "entrypoint": entrypoint,
        "status": status,
        "params": params,
        "command": command,
        "job_id": job_id,
        "source_export_id": source_export_id,
        "contract": contract,
        "returncode": returncode,
        "error": error,
        "created_utc": datetime.now(UTC).isoformat(),
        "files": _files(output_path),
    }
    path = output_path / "run_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def _files(path: Path) -> dict[str, dict[str, int]]:
    output = {}
    for child in path.iterdir() if path.exists() else []:
        if child.is_file():
            output[child.name] = {"bytes": child.stat().st_size}
    return output

