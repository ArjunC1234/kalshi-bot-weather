from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pandas as pd


def load_export(path: str | Path) -> dict[str, pd.DataFrame]:
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"export directory does not exist: {root}")
    return {
        "events": _load_json_gz(root / "events.json.gz"),
        "weather": _load_json_gz(root / "weather_snapshots.json.gz"),
        "markets": _load_json_gz(root / "market_snapshots.json.gz"),
        "settlements": _load_json_gz(root / "settlements.json.gz"),
        "labels": _load_json_gz(root / "final_temperature_labels.json.gz"),
    }


def _load_json_gz(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            if isinstance(item, list):
                rows.extend(row for row in item if isinstance(row, dict))
            elif isinstance(item, dict):
                rows.append(item)
    return pd.DataFrame(rows)


def normalize_tables(tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    output = {name: frame.copy() for name, frame in tables.items()}
    for name, frame in output.items():
        for column in frame.columns:
            if column.endswith("_utc") or column in {"snapshot_time_utc"}:
                frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
        if "target_date" in frame.columns:
            frame["target_date"] = pd.to_datetime(frame["target_date"], errors="coerce").dt.date
    return output
