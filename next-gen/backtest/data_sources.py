"""Local and Supabase-backed dataset sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from libs.constants import SUPABASE_TABLES
from libs.io_utils import read_json_gz
from libs.supabase_client import SupabaseClient


class DataSource(ABC):
    @abstractmethod
    def load_table(self, table: str) -> list[dict]:
        """Load normalized rows for a table."""


class LocalExportSource(DataSource):
    def __init__(self, root: Path) -> None:
        self.root = root

    def load_table(self, table: str) -> list[dict]:
        json_path = self.root / f"{table}.json"
        json_gz_path = self.root / f"{table}.json.gz"
        csv_path = self.root / f"{table}.csv"
        if json_gz_path.exists():
            payload = read_json_gz(json_gz_path)
        elif json_path.exists():
            import json

            payload = json.loads(json_path.read_text(encoding="utf-8-sig"))
        elif csv_path.exists():
            import csv

            with csv_path.open("r", newline="", encoding="utf-8") as handle:
                payload = list(csv.DictReader(handle))
        else:
            return []
        if not isinstance(payload, list):
            raise ValueError(f"{table} export must contain a list")
        return [row for row in payload if isinstance(row, dict)]


class SupabaseSource(DataSource):
    def __init__(
        self,
        client: SupabaseClient,
        start: str | None = None,
        end: str | None = None,
    ) -> None:
        self.client = client
        self.start = start
        self.end = end

    def load_table(self, table: str) -> list[dict]:
        params: dict[str, str] = {}
        date_column = (
            "target_date"
            if table in ("settlements", "final_temperature_labels")
            else "snapshot_time_utc"
        )
        filters: list[str] = []
        if self.start:
            filters.append(f"{date_column}.gte.{self.start}")
        if self.end:
            filters.append(f"{date_column}.lte.{self.end}")
        if filters:
            params["and"] = f"({','.join(filters)})"
        return self.client.select(table, params)


def all_tables(source: DataSource) -> dict[str, list[dict]]:
    return {table: source.load_table(table) for table in SUPABASE_TABLES}
