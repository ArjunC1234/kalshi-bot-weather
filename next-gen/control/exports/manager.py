"""Manage local dataset exports for the Control Center."""

from __future__ import annotations

import gzip
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from control.artifacts.index import inspect_artifact, scan_artifacts


def list_exports(data_root: Path = Path("data")) -> list[dict[str, Any]]:
    records = scan_artifacts(
        data_root,
        Path("_missing"),
        Path("_missing"),
        Path("_missing"),
    )
    return [
        record.metadata
        for record in records
        if record.artifact_type == "local_export"
    ]


def inspect_export(export_path: Path) -> dict[str, Any]:
    inspected = inspect_artifact(export_path, fallback_type="local_export")
    inspected["coverage"] = export_coverage(export_path)
    inspected["metadata_health"] = export_metadata_health(inspected)
    return inspected


def validate_export(export_path: Path) -> dict[str, Any]:
    inspected = inspect_export(export_path)
    blocking: list[str] = []
    warnings: list[str] = []
    files = set(inspected.get("files", []))
    for required in ("events", "weather_snapshots", "market_snapshots"):
        if required not in files:
            blocking.append(f"Missing required table: {required}")
    coverage = inspected["coverage"]
    if not coverage.get("cities"):
        blocking.append("No cities detected")
    if not coverage.get("date_range", {}).get("start"):
        warnings.append("No target_date coverage detected")
    return {
        "valid": not blocking,
        "blocking": blocking,
        "warnings": warnings,
        "export": inspected,
    }


def compare_exports(left_path: Path, right_path: Path) -> dict[str, Any]:
    left = inspect_export(left_path)
    right = inspect_export(right_path)
    left_counts = left.get("table_counts", {})
    right_counts = right.get("table_counts", {})
    tables = sorted(set(left_counts) | set(right_counts))
    return {
        "left": left,
        "right": right,
        "table_deltas": {
            table: int(right_counts.get(table, 0)) - int(left_counts.get(table, 0))
            for table in tables
        },
        "city_delta": sorted(
            set(right.get("coverage", {}).get("cities", []))
            - set(left.get("coverage", {}).get("cities", []))
        ),
    }


def clone_export(source: Path, destination: Path) -> dict[str, Any]:
    _safe_destination(destination)
    if destination.exists():
        raise ValueError(f"destination already exists: {destination}")
    shutil.copytree(source, destination)
    _write_operation_manifest(destination, "clone", {"source": str(source)})
    return inspect_export(destination)


def archive_export(source: Path, archive_root: Path = Path("data/.archive")) -> dict[str, Any]:
    archive_root.mkdir(parents=True, exist_ok=True)
    destination = archive_root / f"{source.name}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    shutil.move(str(source), str(destination))
    _write_operation_manifest(destination, "archive", {"source": str(source)})
    return inspect_export(destination)


def reduce_export(
    source: Path,
    destination: Path,
    *,
    tables: list[str] | None = None,
    cities: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    _safe_destination(destination)
    destination.mkdir(parents=True, exist_ok=True)
    selected_tables = set(tables or inspect_artifact(source).get("files", []))
    for table in selected_tables:
        rows = _read_rows(source, table)
        filtered = [
            row
            for row in rows
            if _matches_city(row, cities) and _matches_date(row, start, end)
        ]
        _write_rows(destination, table, filtered)
    _copy_metadata_files(source, destination)
    _write_operation_manifest(
        destination,
        "reduce",
        {"source": str(source), "tables": tables, "cities": cities, "start": start, "end": end},
    )
    return inspect_export(destination)


def extend_export(source: Path, extension: Path, destination: Path) -> dict[str, Any]:
    _safe_destination(destination)
    destination.mkdir(parents=True, exist_ok=True)
    source_tables = set(inspect_artifact(source).get("files", []))
    extension_tables = set(inspect_artifact(extension).get("files", []))
    tables = sorted(source_tables | extension_tables)
    for table in tables:
        rows = _dedupe_rows(_read_rows(source, table) + _read_rows(extension, table))
        _write_rows(destination, table, rows)
    _copy_metadata_files(source, destination)
    _write_operation_manifest(
        destination,
        "extend",
        {"source": str(source), "extension": str(extension)},
    )
    return inspect_export(destination)


def export_coverage(export_path: Path) -> dict[str, Any]:
    cities: set[str] = set()
    dates: set[str] = set()
    snapshot_hours: set[int] = set()
    row_counts: dict[str, int] = {}
    for table in inspect_artifact(export_path).get("files", []):
        rows = _read_rows(export_path, table)
        row_counts[table] = len(rows)
        for row in rows:
            if row.get("city"):
                cities.add(str(row["city"]))
            if row.get("target_date"):
                dates.add(str(row["target_date"])[:10])
            hour = _hour(row.get("snapshot_time_utc") or row.get("snapshot_hour_utc"))
            if hour is not None:
                snapshot_hours.add(hour)
    return {
        "cities": sorted(cities),
        "date_range": {
            "start": min(dates) if dates else None,
            "end": max(dates) if dates else None,
        },
        "target_dates": len(dates),
        "snapshot_hours": sorted(snapshot_hours),
        "row_counts": row_counts,
    }


def export_metadata_health(inspected: dict[str, Any]) -> dict[str, Any]:
    return {
        "has_manifest": bool((Path(inspected["path"]) / "manifest.json").exists()),
        "has_run_manifest": bool((Path(inspected["path"]) / "run_manifest.json").exists()),
        "has_schemas": bool(inspected.get("schemas")),
        "status": inspected.get("status", "unknown"),
    }


def _read_rows(root: Path, table: str) -> list[dict[str, Any]]:
    csv_path = root / f"{table}.csv"
    json_path = root / f"{table}.json"
    json_gz_path = root / f"{table}.json.gz"
    if json_gz_path.exists():
        with gzip.open(json_gz_path, "rt", encoding="utf-8") as handle:
            value = json.load(handle)
    elif json_path.exists():
        value = json.loads(json_path.read_text(encoding="utf-8-sig"))
    elif csv_path.exists():
        import csv

        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            value = list(csv.DictReader(handle))
    else:
        return []
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _write_rows(root: Path, table: str, rows: list[dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with gzip.open(root / f"{table}.json.gz", "wt", encoding="utf-8", compresslevel=6) as handle:
        json.dump(rows, handle, sort_keys=True, default=str)


def _copy_metadata_files(source: Path, destination: Path) -> None:
    for name in ("export_profile.json",):
        path = source / name
        if path.exists():
            shutil.copy2(path, destination / name)


def _write_operation_manifest(destination: Path, operation: str, params: dict[str, Any]) -> None:
    inspected = inspect_artifact(destination, fallback_type="local_export")
    manifest = {
        "artifact_type": "local_export",
        "status": "complete",
        "operation": operation,
        "created_utc": datetime.now(UTC).isoformat(),
        "export_name": destination.name,
        "tables": {
            table: {"rows": count}
            for table, count in inspected.get("table_counts", {}).items()
        },
        "params": params,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        key = json.dumps(row, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _matches_city(row: dict[str, Any], cities: list[str] | None) -> bool:
    return not cities or str(row.get("city")) in set(cities)


def _matches_date(row: dict[str, Any], start: str | None, end: str | None) -> bool:
    value = str(row.get("target_date") or row.get("snapshot_time_utc") or "")[:10]
    if start and value < start:
        return False
    return not (end and value > end)


def _hour(value: Any) -> int | None:
    text = str(value or "")
    if "T" not in text:
        return None
    try:
        return int(text.split("T", 1)[1][:2])
    except ValueError:
        return None


def _safe_destination(path: Path) -> None:
    if ".archive" in path.parts:
        return
    if not path.parts:
        raise ValueError("destination is required")
