"""Export Supabase rows to a frozen local dataset."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backtest.data_sources import SupabaseSource
from libs.constants import SUPABASE_TABLES
from libs.io_utils import write_json_gz
from libs.json_utils import write_json
from libs.settlement_policy import (
    POST_SETTLEMENT_SYSTEM_START,
    clamp_to_post_settlement_start,
)
from libs.settlement_sources import annotate_table_rows, settlement_source_summary
from libs.supabase_client import SupabaseClient


def export_supabase(
    start: str,
    end: str,
    output: Path,
    client: SupabaseClient | None = None,
    post_settlement_system_only: bool = True,
) -> Path:
    requested_start = start
    if post_settlement_system_only:
        start = clamp_to_post_settlement_start(start).isoformat()
    active_client = client or SupabaseClient.from_env()
    source = SupabaseSource(active_client, start=start, end=end)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "start": start,
        "end": end,
        "requested_start": requested_start,
        "post_settlement_system_only": post_settlement_system_only,
        "post_settlement_system_start": POST_SETTLEMENT_SYSTEM_START,
        "exported_at_utc": datetime.now(UTC).isoformat(),
        "export_name": output.name,
        "tables": {},
    }
    exported_tables = {}
    for table in SUPABASE_TABLES:
        rows = source.load_table(table)
        rows = annotate_table_rows(table, rows)
        exported_tables[table] = rows
        write_json_gz(output / f"{table}.json.gz", rows)
        manifest["tables"][table] = len(rows)
    manifest["settlement_sources"] = settlement_source_summary(exported_tables)
    write_json(output / "manifest.json", manifest)
    return output


def timestamped_export_dir(
    start: str,
    end: str,
    root: Path = Path("data"),
    stamp: str | None = None,
) -> Path:
    """Return a unique local export folder name for a date range."""

    active_stamp = stamp or utc_filename_timestamp()
    safe_start = _safe_date_part(start)
    safe_end = _safe_date_part(end)
    return root / f"export_{safe_start}_{safe_end}_{active_stamp}"


def timestamped_report_dir(
    report_type: str,
    name: str,
    root: Path = Path("reports"),
    stamp: str | None = None,
) -> Path:
    """Return a unique local report folder under a report-type subfolder."""

    active_stamp = stamp or utc_filename_timestamp()
    return root / report_type / f"{_safe_date_part(name)}_{active_stamp}"


def utc_filename_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def dataset_name(path: Path) -> str:
    return path.name or "dataset"


def _safe_date_part(value: str) -> str:
    return (
        value.replace(":", "")
        .replace("-", "")
        .replace("+", "")
        .replace("T", "t")
        .replace("Z", "z")
    )
