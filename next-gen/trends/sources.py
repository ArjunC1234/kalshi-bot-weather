"""Discover and validate local Trends data sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPORT_MARKERS = ("events",)
EXPORT_COMPANIONS = (
    "weather_snapshots",
    "market_snapshots",
    "settlements",
    "final_temperature_labels",
)
REPORT_MARKERS = (
    "predictions",
    "bracket_distributions",
    "errors",
    "by_checkpoint",
    "by_city",
    "temperature_metrics",
    "bracket_metrics",
)
QUALITY_FILES = (
    "quality_report.json",
    "table_counts.csv",
    "missing_city_hours.csv",
    "provider_errors.csv",
)


@dataclass(frozen=True)
class SourceRoots:
    data_root: Path
    report_root: Path
    quality_root: Path

    def normalized(self) -> SourceRoots:
        return SourceRoots(
            self.data_root.resolve(),
            self.report_root.resolve(),
            self.quality_root.resolve(),
        )


def discover_sources(roots: SourceRoots) -> dict[str, Any]:
    normalized = roots.normalized()
    return {
        "roots": {
            "data": str(normalized.data_root),
            "report": str(normalized.report_root),
            "quality": str(normalized.quality_root),
        },
        "exports": _discover(normalized.data_root, "export"),
        "reports": _discover(normalized.report_root, "report"),
        "quality_reports": _discover(normalized.quality_root, "quality"),
    }


def resolve_source(roots: SourceRoots, kind: str, source_id: str | None) -> Path | None:
    if not source_id:
        return None
    root = _root_for_kind(roots.normalized(), kind)
    candidate = (root / source_id).resolve()
    if not _is_relative_to(candidate, root):
        raise ValueError(f"{kind} source is outside configured root")
    if not candidate.is_dir():
        raise ValueError(f"{kind} source does not exist: {source_id}")
    if kind == "export" and not _is_export(candidate):
        raise ValueError(f"folder is not a valid export: {source_id}")
    if kind == "report" and not _is_report(candidate):
        raise ValueError(f"folder is not a valid model report: {source_id}")
    if kind == "quality" and not _is_quality(candidate):
        raise ValueError(f"folder is not a valid quality report: {source_id}")
    return candidate


def _discover(root: Path, kind: str) -> list[dict[str, Any]]:
    root = root.resolve()
    if not root.exists():
        return []
    candidates = [root, *[path for path in root.rglob("*") if path.is_dir()]]
    sources = []
    for path in candidates:
        if _skip_dir(path):
            continue
        if kind == "export" and not _is_export(path):
            continue
        if kind == "report" and not _is_report(path):
            continue
        if kind == "quality" and not _is_quality(path):
            continue
        sources.append(_source_info(root, path, kind))
    return sorted(sources, key=lambda item: (item["modified_utc"], item["id"]), reverse=True)


def _source_info(root: Path, path: Path, kind: str) -> dict[str, Any]:
    table_names = _table_names(path)
    source_id = "." if path == root else path.relative_to(root).as_posix()
    return {
        "id": source_id,
        "name": path.name if source_id != "." else root.name,
        "path": str(path),
        "kind": kind,
        "modified_utc": _modified_utc(path),
        "files": table_names,
        "file_count": len(table_names),
    }


def _is_export(path: Path) -> bool:
    return all(_has_table(path, marker) for marker in EXPORT_MARKERS) and any(
        _has_table(path, marker) for marker in EXPORT_COMPANIONS
    )


def _is_report(path: Path) -> bool:
    return any(_has_table(path, marker) for marker in REPORT_MARKERS)


def _is_quality(path: Path) -> bool:
    return any((path / marker).exists() for marker in QUALITY_FILES)


def _has_table(path: Path, name: str) -> bool:
    return any(
        candidate.exists() and candidate.stat().st_size > 0
        for candidate in (
            path / f"{name}.csv",
            path / f"{name}.json",
            path / f"{name}.json.gz",
        )
    )


def _table_names(path: Path) -> list[str]:
    names: set[str] = set()
    for child in path.iterdir() if path.exists() else []:
        if child.suffix == ".csv":
            names.add(child.stem)
        elif child.suffix == ".json":
            names.add(child.stem)
        elif child.name.endswith(".json.gz"):
            names.add(child.name.removesuffix(".json.gz"))
    return sorted(names)


def _modified_utc(path: Path) -> str:
    newest = path.stat().st_mtime
    for child in path.iterdir() if path.exists() else []:
        if child.is_file():
            newest = max(newest, child.stat().st_mtime)
    from datetime import UTC, datetime

    return datetime.fromtimestamp(newest, tz=UTC).isoformat()


def _root_for_kind(roots: SourceRoots, kind: str) -> Path:
    if kind == "export":
        return roots.data_root
    if kind == "report":
        return roots.report_root
    if kind == "quality":
        return roots.quality_root
    raise ValueError(f"unknown source kind: {kind}")


def _skip_dir(path: Path) -> bool:
    return any(part.startswith(".") or part in {"__pycache__", "charts"} for part in path.parts)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
