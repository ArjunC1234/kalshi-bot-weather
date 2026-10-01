"""Discover and validate local Trends data sources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
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
    "daily_health_report.json",
    "table_counts.csv",
    "city_coverage.csv",
    "missing_city_hours.csv",
    "provider_errors.csv",
)
STRATEGY_MARKERS = (
    "trades",
    "daily_pnl",
    "threshold_sweep",
    "validation_threshold_sweep",
    "train_gate_sweep",
    "candidates",
    "policy_calibration",
)


@dataclass(frozen=True)
class SourceRoots:
    data_root: Path
    report_root: Path
    quality_root: Path
    strategy_root: Path = Path("reports/strategy")

    def normalized(self) -> SourceRoots:
        return SourceRoots(
            self.data_root.resolve(),
            self.report_root.resolve(),
            self.quality_root.resolve(),
            self.strategy_root.resolve(),
        )


def discover_sources(roots: SourceRoots) -> dict[str, Any]:
    normalized = roots.normalized()
    exports = _discover(normalized.data_root, "export")
    reports = _discover(normalized.report_root, "report")
    quality_reports = _discover(normalized.quality_root, "quality")
    strategy_reports = _merge_sources(
        _discover(normalized.strategy_root, "strategy"),
        _discover(normalized.report_root, "strategy", required_artifact_type="strategy_report"),
    )
    _link_to_exports(reports, exports)
    _link_to_exports(quality_reports, exports)
    _link_to_exports(strategy_reports, exports)
    _link_to_model_reports(strategy_reports, reports)
    return {
        "roots": {
            "data": str(normalized.data_root),
            "report": str(normalized.report_root),
            "quality": str(normalized.quality_root),
            "strategy": str(normalized.strategy_root),
        },
        "exports": exports,
        "reports": reports,
        "quality_reports": quality_reports,
        "strategy_reports": strategy_reports,
    }


def resolve_source(roots: SourceRoots, kind: str, source_id: str | None) -> Path | None:
    if not source_id:
        return None
    roots_to_search = _roots_for_kind(roots.normalized(), kind)
    candidate = None
    for root in roots_to_search:
        current = (root / source_id).resolve()
        if not _is_relative_to(current, root):
            continue
        if current.is_dir():
            candidate = current
            break
    if candidate is None:
        raise ValueError(f"{kind} source does not exist: {source_id}")
    if kind == "export" and not _is_export(candidate):
        raise ValueError(f"folder is not a valid export: {source_id}")
    if kind == "report" and not _is_report(candidate):
        raise ValueError(f"folder is not a valid model report: {source_id}")
    if kind == "quality" and not _is_quality(candidate):
        raise ValueError(f"folder is not a valid quality report: {source_id}")
    if kind == "strategy" and not _is_strategy(candidate):
        raise ValueError(f"folder is not a valid strategy report: {source_id}")
    return candidate


def _discover(
    root: Path, kind: str, required_artifact_type: str | None = None
) -> list[dict[str, Any]]:
    root = root.resolve()
    if not root.exists():
        return []
    candidates = [root, *[path for path in root.rglob("*") if path.is_dir()]]
    sources = []
    for path in candidates:
        if _skip_dir(path):
            continue
        if required_artifact_type and _manifest_artifact_type(path) != required_artifact_type:
            continue
        if kind == "export" and not _is_export(path):
            continue
        if kind == "report" and not _is_report(path):
            continue
        if kind == "report" and _manifest_artifact_type(path) == "strategy_report":
            continue
        if kind == "quality" and not _is_quality(path):
            continue
        if kind == "strategy" and not _is_strategy(path):
            continue
        sources.append(_source_info(root, path, kind))
    return sorted(sources, key=lambda item: (item["modified_utc"], item["id"]), reverse=True)


def _merge_sources(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            by_path[str(item.get("path", item["id"]))] = item
    return sorted(
        by_path.values(), key=lambda item: (item["modified_utc"], item["id"]), reverse=True
    )


def _source_info(root: Path, path: Path, kind: str) -> dict[str, Any]:
    table_names = _table_names(path)
    source_id = "." if path == root else path.relative_to(root).as_posix()
    info: dict[str, Any] = {
        "id": source_id,
        "name": path.name if source_id != "." else root.name,
        "path": str(path),
        "kind": kind,
        "modified_utc": _modified_utc(path),
        "files": table_names,
        "file_count": len(table_names),
    }
    if kind == "export":
        _enrich_export_info(info, path)
    elif kind == "report":
        _enrich_report_info(info, path)
    elif kind == "quality":
        _enrich_quality_info(info, path)
    elif kind == "strategy":
        _enrich_strategy_info(info, path)
    return info


def _enrich_export_info(info: dict[str, Any], path: Path) -> None:
    manifest = _read_json_safe(path / "manifest.json")
    info["date_start"] = manifest.get("start")
    info["date_end"] = manifest.get("end")
    info["created_utc"] = manifest.get("exported_at_utc")
    info["table_counts"] = manifest.get("tables", {})


def _enrich_report_info(info: dict[str, Any], path: Path) -> None:
    summary = _read_json_safe(path / "summary.json")
    config = _read_json_safe(path / "config.json")
    manifest = _read_json_safe(path / "model_manifest.json")
    run_manifest = _read_json_safe(path / "run_manifest.json")
    info["model_name"] = summary.get("model_name") or config.get("model_name")
    info["mode"] = summary.get("mode")
    info["neural_mode"] = summary.get("neural_mode") or config.get("neural_mode")
    info["model_family"] = summary.get("model_family") or config.get("model_family")
    info["registry_id"] = run_manifest.get("registry_id")
    info["entrypoint"] = run_manifest.get("entrypoint")
    info["contract"] = run_manifest.get("contract")
    source_export_id = (
        run_manifest.get("source_export_id")
        or summary.get("source_export_id")
        or config.get("source_export_id")
        or manifest.get("source_export_id")
    )
    if source_export_id:
        info["source_export_id"] = source_export_id
    info["created_utc"] = (
        run_manifest.get("created_utc")
        or summary.get("generated_at_utc")
        or config.get("generated_at_utc")
        or manifest.get("generated_at_utc")
    )
    info["generated_at_utc"] = summary.get("generated_at_utc")
    for key in (
        "train_days",
        "test_days",
        "train_start_date",
        "train_end_date",
        "test_start_date",
        "test_end_date",
        "temperature_prediction_count",
        "bracket_prediction_count",
        "independent_city_days",
        "snapshot_rows_with_labels",
        "market_probability_blend",
        "epochs",
        "seed",
    ):
        if key in summary:
            info[key] = summary[key]
    fold_range = _fold_date_ranges(summary.get("folds"))
    for key, value in fold_range.items():
        info.setdefault(key, value)
    for metric in summary.get("temperature_metrics", []):
        if isinstance(metric, dict) and metric.get("metric") in {
            "mae",
            "rmse",
            "bias",
            "within_1f",
            "within_2f",
        }:
            info[str(metric["metric"])] = metric.get("value")
    for metric in summary.get("bracket_metrics", []):
        if isinstance(metric, dict) and metric.get("metric") in {
            "log_loss",
            "brier",
            "top_one_accuracy",
            "winner_probability",
        }:
            info[str(metric["metric"])] = metric.get("value")
    info["temperature_metrics"] = summary.get("temperature_metrics", [])
    info["bracket_metrics"] = summary.get("bracket_metrics", [])


def _fold_date_ranges(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    output: dict[str, str] = {}
    for output_key, fold_key, reducer in (
        ("train_start_date", "train_start_date", min),
        ("train_end_date", "train_end_date", max),
        ("test_start_date", "test_start_date", min),
        ("test_end_date", "test_end_date", max),
    ):
        dates = [
            str(fold.get(fold_key))
            for fold in value
            if isinstance(fold, dict) and fold.get(fold_key)
        ]
        if dates:
            output[output_key] = reducer(dates)
    return output


def _enrich_quality_info(info: dict[str, Any], path: Path) -> None:
    quality = _read_json_safe(path / "quality_report.json") or _read_json_safe(
        path / "daily_health_report.json"
    )
    if quality.get("source_export_id"):
        info["source_export_id"] = quality.get("source_export_id")
    info["created_utc"] = quality.get("generated_at_utc")
    info["cities"] = quality.get("cities", quality.get("cities_seen", []))
    info["snapshot_hours"] = quality.get("snapshot_hours")


def _enrich_strategy_info(info: dict[str, Any], path: Path) -> None:
    manifest = _read_json_safe(path / "run_manifest.json")
    summary = _read_json_safe(path / "summary.json")
    config = summary.get("config", {})
    if not isinstance(config, dict):
        config = {}
    info["mode"] = summary.get("mode") or summary.get("experiment")
    source_export_id = (
        manifest.get("source_export_id")
        or summary.get("source_export_id")
        or _source_export_id_from_path(summary.get("data_path"))
        or _source_export_id_from_path(config.get("data_path"))
    )
    if source_export_id:
        info["source_export_id"] = source_export_id
    model_report = summary.get("model_report") or config.get("model_report")
    if isinstance(model_report, str) and model_report:
        info["model_report_path"] = model_report
        model_report_id = _report_id_from_path(model_report)
        if model_report_id:
            info["model_report_id"] = model_report_id
    info["created_utc"] = manifest.get("created_utc") or summary.get("generated_at_utc")
    for key in (
        "trades",
        "total_pnl",
        "roi",
        "hit_rate",
        "max_drawdown",
        "positive_clv_rate",
    ):
        if key in summary:
            info[key] = summary[key]


def _source_export_id_from_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    for part in Path(value).parts:
        if (
            part.startswith("export_")
            or part.startswith("codex_export_")
            or part.startswith("codex_latest_")
        ):
            return part
    return None


def _report_id_from_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1] or None


def _read_json_safe(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _link_to_exports(items: list[dict[str, Any]], exports: list[dict[str, Any]]) -> None:
    # Model reports and quality reports usually record their source export id
    # explicitly (see _enrich_report_info/_enrich_quality_info). Older artifacts
    # generated before that metadata existed are linked here on a best-effort
    # basis by matching the export id as a (case-insensitive) substring of the
    # report/quality folder name, since export ids are always embedded in those
    # folder names by convention.
    export_ids = sorted({export["id"] for export in exports}, key=len, reverse=True)
    for item in items:
        if item.get("source_export_id"):
            continue
        lowered = item["id"].lower()
        match = next((export_id for export_id in export_ids if export_id.lower() in lowered), None)
        if match:
            item["source_export_id"] = match
            item["source_export_inferred"] = True


def _link_to_model_reports(
    strategy_reports: list[dict[str, Any]],
    model_reports: list[dict[str, Any]],
) -> None:
    reports_by_id = {str(report.get("id")): report for report in model_reports}
    reports_by_path = {
        _normalize_path(str(report.get("path", ""))): report
        for report in model_reports
        if report.get("path")
    }
    for strategy in strategy_reports:
        model_report_id = strategy.get("model_report_id")
        model_report_path = strategy.get("model_report_path")
        report = reports_by_id.get(str(model_report_id)) if model_report_id else None
        if report is None and model_report_path:
            report = reports_by_path.get(_normalize_path(str(model_report_path)))
        if report is None:
            report = _infer_model_report_from_strategy(strategy, model_reports)
        if report is None:
            continue
        strategy["model_report_id"] = report["id"]
        strategy["model_report_name"] = report.get("name") or report["id"]
        strategy["model_report_path"] = report.get("path") or model_report_path


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").rstrip("/").lower()


def _infer_model_report_from_strategy(
    strategy: dict[str, Any],
    model_reports: list[dict[str, Any]],
) -> dict[str, Any] | None:
    prefix = _inferred_model_prefix(str(strategy.get("id") or ""))
    if not prefix:
        return None
    source_export_id = strategy.get("source_export_id")
    candidates = [
        report
        for report in model_reports
        if (
            not source_export_id
            or not report.get("source_export_id")
            or report.get("source_export_id") == source_export_id
        )
    ]
    return next(
        (
            report
            for report in candidates
            if str(report.get("id") or "").startswith(f"{prefix}_")
            or str(report.get("id") or "") == prefix
        ),
        None,
    )


def _inferred_model_prefix(strategy_id: str) -> str:
    lowered = strategy_id.lower()
    for marker in (
        "_ev_validation_train_",
        "_ev_train_",
        "_fixed_train_",
        "_validation_train_",
    ):
        index = lowered.find(marker)
        if index > 0:
            return strategy_id[:index]
    return ""


def _is_export(path: Path) -> bool:
    return all(_has_table(path, marker) for marker in EXPORT_MARKERS) and any(
        _has_table(path, marker) for marker in EXPORT_COMPANIONS
    )


def _is_report(path: Path) -> bool:
    return any(_has_table(path, marker) for marker in REPORT_MARKERS)


def _is_quality(path: Path) -> bool:
    return any((path / marker).exists() for marker in QUALITY_FILES)


def _is_strategy(path: Path) -> bool:
    if _manifest_artifact_type(path) == "strategy_report":
        return True
    return any(_has_table(path, marker) for marker in STRATEGY_MARKERS) or (
        (path / "summary.json").exists()
        and any(_has_table(path, marker) for marker in ("predictions", "candidates"))
    )


def _manifest_artifact_type(path: Path) -> str:
    return str(_read_json_safe(path / "run_manifest.json").get("artifact_type") or "")


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
    return datetime.fromtimestamp(newest, tz=UTC).isoformat()


def _roots_for_kind(roots: SourceRoots, kind: str) -> list[Path]:
    if kind == "export":
        return [roots.data_root]
    if kind == "report":
        return [roots.report_root]
    if kind == "quality":
        return [roots.quality_root]
    if kind == "strategy":
        return [roots.strategy_root, roots.report_root]
    raise ValueError(f"unknown source kind: {kind}")


def _skip_dir(path: Path) -> bool:
    return any(part.startswith(".") or part in {"__pycache__", "charts"} for part in path.parts)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
