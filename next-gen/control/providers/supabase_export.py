"""Registry-profiled Supabase exports."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from control.registry.loader import RegistryEntry
from libs.io_utils import write_json_gz
from libs.json_utils import write_json
from libs.supabase_client import SupabaseClient


def export_with_profile(
    profile: RegistryEntry,
    start: str,
    end: str,
    output: Path,
    client: SupabaseClient | None = None,
) -> Path:
    active_client = client or SupabaseClient.from_env()
    output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "artifact_type": "local_export",
        "export_profile_id": profile.id,
        "source": profile.spec.get("source"),
        "start": start,
        "end": end,
        "created_utc": datetime.now(UTC).isoformat(),
        "exported_at_utc": datetime.now(UTC).isoformat(),
        "export_name": output.name,
        "tables": {},
        "excluded_columns": {},
    }
    for table_name, table_spec in _included_tables(profile).items():
        source_table = str(table_spec.get("source_table") or table_name)
        params = _date_params(source_table, start, end)
        select = _select_clause(table_spec)
        if select:
            params["select"] = select
        rows = active_client.select(source_table, params)
        rows = [_filter_row(row, table_spec) for row in rows]
        write_json_gz(output / f"{table_name}.json.gz", rows)
        manifest["tables"][table_name] = {"rows": len(rows), "source_table": source_table}
        excluded = table_spec.get("exclude_columns")
        if isinstance(excluded, list) and excluded:
            manifest["excluded_columns"][table_name] = excluded
    write_json(output / "manifest.json", manifest)
    write_json(output / "export_profile.json", profile.spec)
    return output


def preview_export_profile(profile: RegistryEntry) -> dict[str, Any]:
    tables = {}
    for table_name, table_spec in _included_tables(profile).items():
        tables[table_name] = {
            "source_table": table_spec.get("source_table", table_name),
            "required": bool(table_spec.get("required", False)),
            "protected_columns": _text_list(table_spec.get("protected_columns")),
            "required_columns": _text_list(table_spec.get("required_columns")),
            "include_columns": table_spec.get("include_columns"),
            "exclude_columns": table_spec.get("exclude_columns", []),
            "effective_columns": _effective_columns(table_spec),
        }
    return {
        "id": profile.id,
        "label": profile.label,
        "source": profile.spec.get("source"),
        "tables": tables,
    }


def _included_tables(profile: RegistryEntry) -> dict[str, dict[str, Any]]:
    tables = profile.spec.get("tables", {})
    if not isinstance(tables, dict):
        return {}
    return {
        str(name): spec
        for name, spec in tables.items()
        if isinstance(spec, dict) and spec.get("include") is not False
    }


def _select_clause(table_spec: dict[str, Any]) -> str | None:
    columns = _effective_columns(table_spec)
    return ",".join(columns) if columns else None


def _filter_row(row: dict[str, Any], table_spec: dict[str, Any]) -> dict[str, Any]:
    effective = _effective_columns(table_spec)
    if effective:
        allowed = set(effective)
        return {key: value for key, value in row.items() if key in allowed}
    excluded = table_spec.get("exclude_columns")
    if isinstance(excluded, list) and excluded:
        protected = set(_protected_columns(table_spec))
        blocked = {str(column) for column in excluded if str(column) not in protected}
        return {key: value for key, value in row.items() if key not in blocked}
    return row


def _effective_columns(table_spec: dict[str, Any]) -> list[str]:
    included = _text_list(table_spec.get("include_columns"))
    specified_required = _text_list(table_spec.get("protected_columns")) + _text_list(
        table_spec.get("required_columns")
    )
    if not included and not specified_required:
        return []
    columns = set(included)
    columns.update(_protected_columns(table_spec))
    columns.update(_text_list(table_spec.get("required_columns")))
    excluded = set(_text_list(table_spec.get("exclude_columns")))
    protected = set(_protected_columns(table_spec)) | set(
        _text_list(table_spec.get("required_columns"))
    )
    return sorted(column for column in columns if column not in excluded or column in protected)


def _protected_columns(table_spec: dict[str, Any]) -> list[str]:
    base = ["city", "event_ticker", "target_date", "snapshot_time_utc"]
    return sorted(set(base + _text_list(table_spec.get("protected_columns"))))


def _text_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _date_params(table: str, start: str, end: str) -> dict[str, str]:
    date_column = (
        "target_date"
        if table in {"settlements", "final_temperature_labels"}
        else "snapshot_time_utc"
    )
    left = start if date_column == "target_date" else _timestamp_start(start)
    right = end if date_column == "target_date" else _timestamp_end(end)
    return {"and": f"({date_column}.gte.{left},{date_column}.lte.{right})"}


def _timestamp_start(value: str) -> str:
    return f"{value}T00:00:00+00:00" if len(value) == 10 else value


def _timestamp_end(value: str) -> str:
    return f"{value}T23:59:59.999999+00:00" if len(value) == 10 else value
