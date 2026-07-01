from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backtest.data_sources import LocalExportSource, SupabaseSource
from libs.io_utils import write_json_gz


class FakeSupabaseClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, str]]] = []

    def select(self, table: str, params: dict[str, str] | None = None) -> list[dict]:
        self.calls.append((table, params or {}))
        return self.rows


class DataSourceTests(unittest.TestCase):
    def test_local_and_supabase_sources_return_same_shape(self) -> None:
        rows = [{"city": "den", "value": 1}]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json_gz(root / "events.json.gz", rows)
            local = LocalExportSource(root)
            supabase = SupabaseSource(FakeSupabaseClient(rows))  # type: ignore[arg-type]

            self.assertEqual(local.load_table("events"), rows)
            self.assertEqual(supabase.load_table("events"), rows)


if __name__ == "__main__":
    unittest.main()
