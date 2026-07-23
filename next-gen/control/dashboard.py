"""Workbench dashboard summaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from control.artifacts.index import scan_artifacts
from control.jobs.store import JobStore, serialize_job
from control.registry.loader import Registry


def dashboard_summary(repo_root: Path, registry: Registry, job_store: JobStore) -> dict[str, Any]:
    records = scan_artifacts(
        repo_root / "data",
        repo_root / "reports/model",
        repo_root / "reports/quality",
        repo_root / "reports/strategy",
        include_counts=False,
        include_schemas=False,
    )
    by_type: dict[str, int] = {}
    for record in records:
        by_type[record.artifact_type] = by_type.get(record.artifact_type, 0) + 1
    exports = [record for record in records if record.artifact_type == "local_export"]
    jobs = [serialize_job(job) for job in job_store.list()[:8]]
    return {
        "brand": "Kalshi Weather Workbench",
        "artifact_counts": by_type,
        "exports_available": len(exports),
        "latest_export": exports[0].metadata if exports else None,
        "registered_models": len(registry.by_kind("model")),
        "registered_strategies": len(registry.by_kind("strategy")),
        "registered_export_profiles": len(registry.by_kind("export_profile")),
        "recent_jobs": jobs,
        "bot_monitoring": {"status": "deferred", "route": "/control/api/bot/status"},
    }
