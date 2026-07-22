"""Infer lightweight table schemas and semantic roles for variable artifacts."""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path
from typing import Any

ROLE_BY_COLUMN = {
    "city": "dimension.city",
    "event_ticker": "dimension.event",
    "market_ticker": "dimension.market",
    "target_date": "time.target_date",
    "snapshot_time_utc": "time.snapshot",
    "snapshot_hour_utc": "time.snapshot",
    "entry_time_utc": "time.entry",
    "model_name": "dimension.model",
    "report_name": "dimension.report",
    "strategy_name": "dimension.strategy",
    "expected_high_f": "measure.temperature.predicted",
    "final_high_f": "measure.temperature.actual",
    "actual_high_f": "measure.temperature.actual",
    "error_f": "measure.error.signed",
    "absolute_error_f": "measure.error.absolute",
    "winner_probability": "measure.probability.winner",
    "model_probability": "measure.probability.predicted",
    "market_probability": "measure.probability.market",
    "probabilities": "measure.probability_distribution.brackets",
    "pnl": "measure.pnl",
    "cumulative_pnl": "measure.pnl.cumulative",
    "roi": "measure.roi",
    "trades": "measure.count.trades",
}


def infer_artifact_schemas(path: Path, table_names: list[str]) -> dict[str, Any]:
    return {
        table: infer_table_schema(path, table)
        for table in table_names
        if (path / f"{table}.csv").exists()
        or (path / f"{table}.json").exists()
        or (path / f"{table}.json.gz").exists()
    }


def infer_table_schema(path: Path, table: str) -> dict[str, Any]:
    rows = _sample_rows(path, table)
    columns = sorted({column for row in rows for column in row})
    return {
        "table": table,
        "row_grain": _row_grain(columns),
        "primary_time_column": _primary_time_column(columns),
        "columns": {
            column: {
                "type": _infer_type([row.get(column) for row in rows]),
                "role": ROLE_BY_COLUMN.get(column, _role_from_name(column)),
            }
            for column in columns
        },
    }


def _sample_rows(path: Path, table: str, limit: int = 50) -> list[dict[str, Any]]:
    csv_path = path / f"{table}.csv"
    if csv_path.exists():
        try:
            with csv_path.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                return [row for _, row in zip(range(limit), reader, strict=False)]
        except OSError:
            return []
    json_path = path / f"{table}.json"
    if json_path.exists():
        return _json_rows(json_path, limit)
    json_gz_path = path / f"{table}.json.gz"
    if json_gz_path.exists():
        try:
            with gzip.open(json_gz_path, "rt", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return [row for row in value[:limit] if isinstance(row, dict)]
    return []


def _json_rows(path: Path, limit: int) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [row for row in value[:limit] if isinstance(row, dict)]


def _row_grain(columns: list[str]) -> list[str]:
    grain = [
        column
        for column in ("city", "event_ticker", "market_ticker", "snapshot_time_utc", "target_date")
        if column in columns
    ]
    return grain


def _primary_time_column(columns: list[str]) -> str | None:
    for column in ("snapshot_time_utc", "snapshot_hour_utc", "entry_time_utc", "target_date"):
        if column in columns:
            return column
    return None


def _infer_type(values: list[Any]) -> str:
    present = [value for value in values if value not in (None, "")]
    if not present:
        return "unknown"
    if all(_is_number(value) for value in present):
        return "number"
    if all(_looks_datetime(str(value)) for value in present):
        return "datetime"
    if all(str(value).strip().startswith(("{", "[")) for value in present):
        return "json"
    return "string"


def _role_from_name(column: str) -> str:
    if column.endswith("_f"):
        return "measure.temperature"
    if "probability" in column or column.endswith("_prob"):
        return "measure.probability"
    if column.endswith("_utc") or column.endswith("_date"):
        return "time"
    if column.endswith("_id") or column.endswith("_ticker"):
        return "dimension.identifier"
    return "dimension" if column in {"checkpoint", "group", "side"} else "attribute"


def _is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _looks_datetime(value: str) -> bool:
    return "T" in value and (value.endswith("Z") or "+" in value or value.endswith("+00:00"))
