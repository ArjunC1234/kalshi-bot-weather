"""Local artifact discovery and SQLite indexing."""

from __future__ import annotations

import csv
import gzip
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from control.artifacts.schemas import infer_artifact_schemas


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    artifact_type: str
    path: str
    status: str
    modified_utc: str
    created_utc: str | None = None
    source_export_id: str | None = None
    contract: str | None = None
    files: list[str] = field(default_factory=list)
    table_counts: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class ArtifactIndex:
    def __init__(self, path: Path = Path(".control/artifacts.sqlite")) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def replace_all(self, records: list[ArtifactRecord]) -> None:
        with closing(self._connect()) as db:
            db.execute("delete from artifacts")
            db.executemany(
                """
                insert into artifacts (
                    id, artifact_type, path, status, modified_utc, created_utc,
                    source_export_id, contract, files_json, table_counts_json, metadata_json
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.id,
                        record.artifact_type,
                        record.path,
                        record.status,
                        record.modified_utc,
                        record.created_utc,
                        record.source_export_id,
                        record.contract,
                        json.dumps(record.files, sort_keys=True),
                        json.dumps(record.table_counts, sort_keys=True),
                        json.dumps(record.metadata, sort_keys=True, default=str),
                    )
                    for record in records
                ],
            )
            db.commit()

    def list(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """
                select id, artifact_type, path, status, modified_utc, created_utc,
                       source_export_id, contract, files_json, table_counts_json, metadata_json
                from artifacts
                order by modified_utc desc, id desc
                """
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def _init(self) -> None:
        with closing(self._connect()) as db:
            db.execute(
                """
                create table if not exists artifacts (
                    id text primary key,
                    artifact_type text not null,
                    path text not null,
                    status text not null,
                    modified_utc text not null,
                    created_utc text,
                    source_export_id text,
                    contract text,
                    files_json text not null,
                    table_counts_json text not null,
                    metadata_json text not null
                )
                """
            )
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


def scan_artifacts(
    data_root: Path = Path("data"),
    model_root: Path = Path("reports/model"),
    quality_root: Path = Path("reports/quality"),
    strategy_root: Path = Path("reports/strategy"),
    *,
    include_counts: bool = True,
    include_schemas: bool = True,
) -> list[ArtifactRecord]:
    roots = (
        (data_root, "local_export"),
        (model_root, "model_report"),
        (quality_root, "quality_report"),
        (strategy_root, "strategy_report"),
    )
    records: list[ArtifactRecord] = []
    for root, fallback_type in roots:
        if not root.exists():
            continue
        for path in [root, *[item for item in root.rglob("*") if item.is_dir()]]:
            if _skip(path):
                continue
            inspected = inspect_artifact(
                path,
                fallback_type=fallback_type,
                include_counts=include_counts,
                include_schemas=include_schemas,
            )
            if inspected["artifact_type"] == "unknown":
                continue
            records.append(
                ArtifactRecord(
                    id=inspected["id"],
                    artifact_type=inspected["artifact_type"],
                    path=inspected["path"],
                    status=inspected["status"],
                    modified_utc=inspected["modified_utc"],
                    created_utc=inspected.get("created_utc"),
                    source_export_id=inspected.get("source_export_id"),
                    contract=inspected.get("contract"),
                    files=inspected.get("files", []),
                    table_counts=inspected.get("table_counts", {}),
                    metadata=inspected,
                )
            )
    return sorted(records, key=lambda item: (item.modified_utc, item.id), reverse=True)


def inspect_artifact(
    path: Path,
    fallback_type: str | None = None,
    *,
    include_counts: bool = True,
    include_schemas: bool = True,
) -> dict[str, Any]:
    path = path.resolve()
    files = _table_names(path)
    manifest = _read_json(path / "run_manifest.json")
    export_manifest = _read_json(path / "manifest.json")
    summary = _read_json(path / "summary.json")
    artifact_type = (
        manifest.get("artifact_type")
        or export_manifest.get("artifact_type")
        or _legacy_type(path, files, fallback_type)
    )
    artifact_id = _artifact_id(path, artifact_type)
    table_counts = _manifest_table_counts(export_manifest)
    if include_counts and not table_counts:
        table_counts = {name: _count_rows(path, name) for name in files}
    source_export_id = (
        manifest.get("source_export_id")
        or export_manifest.get("source_export_id")
        or summary.get("source_export_id")
        or _source_export_id_from_path(summary.get("data_path"))
    )
    excluded_columns = export_manifest.get("excluded_columns", {})
    status = str(manifest.get("status") or export_manifest.get("status") or "complete")
    return {
        "id": artifact_id,
        "artifact_type": artifact_type,
        "path": str(path),
        "status": status,
        "modified_utc": _modified_utc(path),
        "created_utc": (
            manifest.get("created_utc")
            or export_manifest.get("created_utc")
            or export_manifest.get("exported_at_utc")
            or summary.get("generated_at_utc")
        ),
        "source_export_id": source_export_id,
        "contract": manifest.get("contract"),
        "files": files,
        "table_counts": table_counts,
        "excluded_columns": excluded_columns,
        "schemas": infer_artifact_schemas(path, files) if include_schemas else {},
        "summary": summary,
    }


def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    metadata = json.loads(row[10])
    output = {
        "id": row[0],
        "artifact_type": row[1],
        "path": row[2],
        "status": row[3],
        "modified_utc": row[4],
        "created_utc": row[5],
        "source_export_id": row[6],
        "contract": row[7],
        "files": json.loads(row[8]),
        "table_counts": json.loads(row[9]),
        "metadata": metadata,
    }
    if isinstance(metadata, dict):
        for key in (
            "excluded_columns",
            "schemas",
            "summary",
            "coverage",
            "metadata_health",
        ):
            if key in metadata:
                output[key] = metadata[key]
    return output


def _legacy_type(path: Path, files: list[str], fallback_type: str | None) -> str:
    names = set(files)
    if "events" in names and names & {"weather_snapshots", "market_snapshots", "settlements"}:
        return "local_export"
    if names & {"predictions", "bracket_distributions", "errors", "by_checkpoint"}:
        if fallback_type == "strategy_report" and names & {
            "trades",
            "daily_pnl",
            "threshold_sweep",
        }:
            return "strategy_report"
        return "model_report"
    if names & {"table_counts", "city_coverage", "missing_city_hours"} or (
        path / "quality_report.json"
    ).exists():
        return "quality_report"
    if names & {"trades", "daily_pnl", "threshold_sweep", "validation_threshold_sweep"}:
        return "strategy_report"
    return fallback_type if fallback_type and names else "unknown"


def _table_names(path: Path) -> list[str]:
    names: set[str] = set()
    if not path.exists():
        return []
    for child in path.iterdir():
        if child.suffix == ".csv":
            names.add(child.stem)
        elif child.suffix == ".json" and child.name not in {
            "manifest.json",
            "run_manifest.json",
            "summary.json",
            "config.json",
        }:
            names.add(child.stem)
        elif child.name.endswith(".json.gz"):
            names.add(child.name.removesuffix(".json.gz"))
    return sorted(names)


def _manifest_table_counts(manifest: dict[str, Any]) -> dict[str, int]:
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        return {}
    output: dict[str, int] = {}
    for name, value in tables.items():
        if isinstance(value, dict):
            count = value.get("rows")
        else:
            count = value
        try:
            output[str(name)] = int(count)
        except (TypeError, ValueError):
            continue
    return output


def _count_rows(path: Path, name: str) -> int:
    csv_path = path / f"{name}.csv"
    if csv_path.exists():
        try:
            with csv_path.open("r", newline="", encoding="utf-8") as handle:
                return max(sum(1 for _ in csv.reader(handle)) - 1, 0)
        except OSError:
            return 0
    json_path = path / f"{name}.json"
    if json_path.exists():
        value = _read_json_any(json_path)
        return len(value) if isinstance(value, list) else 0
    json_gz_path = path / f"{name}.json.gz"
    if json_gz_path.exists():
        value = _read_json_gz_any(json_gz_path)
        return len(value) if isinstance(value, list) else 0
    return 0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_json_any(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_json_gz_any(path: Path) -> Any:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _modified_utc(path: Path) -> str:
    newest = path.stat().st_mtime
    for child in path.iterdir() if path.exists() else []:
        if child.is_file():
            newest = max(newest, child.stat().st_mtime)
    return datetime.fromtimestamp(newest, tz=UTC).isoformat()


def _artifact_id(path: Path, artifact_type: str | None = None) -> str:
    name = path.name or path.resolve().name
    if artifact_type in {"model_report", "strategy_report", "quality_report"}:
        return f"{artifact_type}:{name}"
    return name


def _source_export_id_from_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    for part in Path(value).parts:
        if part.startswith("export_"):
            return part
    return None


def _skip(path: Path) -> bool:
    return any(part.startswith(".") or part in {"__pycache__", "charts"} for part in path.parts)
