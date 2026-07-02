"""Versioned model artifact registry helpers."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from libs.json_utils import read_json, write_json


@dataclass(frozen=True)
class ModelArtifactManifest:
    model_family: str
    model_version: str
    artifact_id: str
    created_at_utc: str
    data_start: str
    data_end: str
    training_rows: int
    settled_city_days: int
    source_hash: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    promoted: bool = False
    promotion_status: str = "candidate"
    notes: list[str] = field(default_factory=list)


def new_artifact_id(
    model_family: str, model_version: str, created_at: datetime | None = None
) -> str:
    timestamp = (created_at or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{model_family}-{model_version}-{timestamp}"


def create_artifact_manifest(
    model_family: str,
    model_version: str,
    data_start: str,
    data_end: str,
    training_rows: int,
    settled_city_days: int,
    metrics: dict[str, Any] | None = None,
    source_hash: str | None = None,
    notes: list[str] | None = None,
) -> ModelArtifactManifest:
    created_at = datetime.now(UTC)
    return ModelArtifactManifest(
        model_family=model_family,
        model_version=model_version,
        artifact_id=new_artifact_id(model_family, model_version, created_at),
        created_at_utc=created_at.isoformat(),
        data_start=data_start,
        data_end=data_end,
        training_rows=training_rows,
        settled_city_days=settled_city_days,
        source_hash=source_hash,
        metrics=metrics or {},
        notes=notes or [],
    )


def write_artifact_manifest(artifact_dir: Path, manifest: ModelArtifactManifest) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / "artifact_manifest.json"
    write_json(path, asdict(manifest))
    return path


def read_artifact_manifest(artifact_dir: Path) -> ModelArtifactManifest:
    payload = read_json(artifact_dir / "artifact_manifest.json")
    if not isinstance(payload, dict):
        raise ValueError("artifact manifest must be a JSON object")
    return ModelArtifactManifest(**payload)


def promote_artifact(artifact_dir: Path, registry_dir: Path) -> Path:
    manifest = read_artifact_manifest(artifact_dir)
    promoted = ModelArtifactManifest(
        **{
            **asdict(manifest),
            "promoted": True,
            "promotion_status": "promoted",
        }
    )
    write_artifact_manifest(artifact_dir, promoted)
    registry_dir.mkdir(parents=True, exist_ok=True)
    pointer = registry_dir / f"{manifest.model_family}_{manifest.model_version}_latest.txt"
    pointer.write_text(str(artifact_dir.resolve()) + "\n", encoding="utf-8")
    return pointer


def copy_artifact_to_registry(artifact_dir: Path, registry_dir: Path) -> Path:
    manifest = read_artifact_manifest(artifact_dir)
    destination = (
        registry_dir / manifest.model_family / manifest.model_version / manifest.artifact_id
    )
    if destination.exists():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(artifact_dir, destination)
    return destination
