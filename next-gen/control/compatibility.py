"""Compatibility checks between registry-defined models and artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from control.artifacts.index import inspect_artifact
from control.registry.loader import RegistryEntry


def check_model_dataset_compatibility(
    model: RegistryEntry,
    dataset_path: Path,
) -> dict[str, Any]:
    artifact = inspect_artifact(dataset_path)
    required_tables = (
        model.spec.get("inputs", {})
        .get("dataset", {})
        .get("required_tables", [])
    )
    files = set(artifact.get("files", []))
    table_counts = artifact.get("table_counts", {})
    missing = [
        table
        for table in required_tables
        if table not in files and table not in table_counts
    ]
    warnings: list[str] = []
    if artifact.get("artifact_type") != "local_export":
        warnings.append("Selected dataset does not advertise artifact_type=local_export.")
    excluded = artifact.get("excluded_columns", {})
    excluded_items = excluded.items() if isinstance(excluded, dict) else []
    for table, columns in excluded_items:
        if columns:
            warnings.append(f"{table} excludes columns: {', '.join(map(str, columns))}")
    return {
        "compatible": not missing,
        "blocking": [f"Missing required table: {table}" for table in missing],
        "warnings": warnings,
        "artifact": artifact,
    }
