"""Export Supabase rows to a frozen local dataset."""

from __future__ import annotations

from pathlib import Path

from backtest.data_sources import SupabaseSource
from libs.constants import SUPABASE_TABLES
from libs.io_utils import write_json_gz
from libs.json_utils import write_json
from libs.supabase_client import SupabaseClient


def export_supabase(
    start: str,
    end: str,
    output: Path,
    client: SupabaseClient | None = None,
) -> Path:
    active_client = client or SupabaseClient.from_env()
    source = SupabaseSource(active_client, start=start, end=end)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"start": start, "end": end, "tables": {}}
    for table in SUPABASE_TABLES:
        rows = source.load_table(table)
        write_json_gz(output / f"{table}.json.gz", rows)
        manifest["tables"][table] = len(rows)
    write_json(output / "manifest.json", manifest)
    return output
