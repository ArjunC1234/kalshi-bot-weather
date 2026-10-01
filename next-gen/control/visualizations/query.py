"""Schema-checked query engine for local visualization artifacts."""

from __future__ import annotations

import csv
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from control.artifacts.schemas import infer_table_schema

MAX_PAGE_SIZE = 5000
DEFAULT_PAGE_SIZE = 500
DEFAULT_DENSITY_BINS = 40
AGGREGATIONS = {"count", "sum", "avg", "min", "max"}


def execute_visualization_query(artifact_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Run a bounded visualization query over one or more artifact tables.

    The request is intentionally JSON-shaped so it can be used from the HTTP
    server and tests without coupling to a UI model.
    """

    tables = _query_tables(request)
    table = tables[0]
    schemas = {name: infer_table_schema(artifact_path, name) for name in tables}
    schema = _merged_schema(table, schemas)
    columns = set(schema["columns"])
    if not columns:
        raise ValueError(f"table not found or empty: {', '.join(tables)}")

    x_field = _optional_string(request.get("x"))
    y_field = _optional_string(request.get("y"))
    group_fields = _string_list(request.get("group") or request.get("groups"))
    join = _join_request(request.get("join"))
    if len(tables) > 1 and join:
        raise ValueError("join is only supported for single-base-table queries")
    if join:
        schemas[join["table"]] = infer_table_schema(artifact_path, join["table"])
        schema = _joined_schema(schema, schemas[join["table"]], join)
        columns = set(schema["columns"])
    if len(tables) > 1 and request.get("group_by_table", True) and "source_table" not in group_fields:
        group_fields = [*group_fields, "source_table"]
    filters = _dict(request.get("filters", {}))
    hour_blocks = _optional_int(request.get("hour_blocks") or filters.get("hour_blocks"))
    aggregation = _aggregation(request.get("aggregation"))

    _require_column(columns, x_field, "x")
    _require_column(columns, y_field, "y")
    for field in group_fields:
        _require_column(columns, field, "group")
    _validate_filters(columns, filters)

    rows = _read_query_rows(artifact_path, tables, schemas)
    if join:
        rows = _join_rows(rows, _read_rows(artifact_path, join["table"]), join)
    total_rows = len(rows)
    rows = [_coerce_row(row, schema) for row in rows]
    rows = [_with_hour_block(row, hour_blocks) for row in rows]
    rows = [row for row in rows if _matches_filters(row, filters)]
    filtered_rows = len(rows)

    if aggregation is not None:
        rows = _aggregate(rows, x_field, y_field, group_fields, aggregation)
    else:
        rows = _project(rows, x_field, y_field, group_fields, hour_blocks)

    result_rows = len(rows)
    sample_meta: dict[str, Any] = {"applied": False}
    rows, sample_meta = _sample(rows, request.get("sample"))
    rows_after_sampling = len(rows)
    decimation_meta: dict[str, Any] = {"applied": False}
    rows, decimation_meta = _decimate(rows, request.get("decimate_to"))
    rows_after_decimation = len(rows)

    density = _density(rows, x_field, y_field, request.get("density"))
    page = max(_optional_int(request.get("page")) or 1, 1)
    page_size = min(
        max(_optional_int(request.get("page_size")) or DEFAULT_PAGE_SIZE, 1),
        MAX_PAGE_SIZE,
    )
    start = (page - 1) * page_size
    end = start + page_size
    page_rows = rows[start:end]

    return {
        "table": table if len(tables) == 1 else "__combined__",
        "tables": tables,
        "schema": schema,
        "rows": page_rows,
        "metadata": {
            "total_rows": total_rows,
            "filtered_rows": filtered_rows,
            "result_rows": result_rows,
            "rows_after_sampling": rows_after_sampling,
            "rows_after_decimation": rows_after_decimation,
            "returned_rows": len(page_rows),
            "pagination": {
                "page": page,
                "page_size": page_size,
                "offset": start,
                "has_next_page": end < len(rows),
            },
            "fields": {
                "x": x_field,
                "y": y_field,
                "groups": group_fields,
                "hour_blocks": hour_blocks,
            },
            "join": join or {},
            "aggregation": aggregation or {"op": "none"},
            "sampling": sample_meta,
            "decimation": decimation_meta,
            "density": density,
        },
}


def _query_tables(request: dict[str, Any]) -> list[str]:
    tables = _string_list(request.get("tables"))
    if not tables:
        tables = [_required_string(request, "table")]
    deduped: list[str] = []
    for table in tables:
        if table not in deduped:
            deduped.append(table)
    if len(deduped) > 12:
        raise ValueError("at most 12 tables can be compared in one visualization query")
    return deduped


def _read_query_rows(
    artifact_path: Path,
    tables: list[str],
    schemas: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(tables) == 1:
        return _read_rows(artifact_path, tables[0])
    rows: list[dict[str, Any]] = []
    for table in tables:
        table_columns = set(schemas[table]["columns"])
        for row in _read_rows(artifact_path, table):
            rows.append({column: row.get(column) for column in table_columns} | {"source_table": table})
    return rows


def _merged_schema(table: str, schemas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if len(schemas) == 1:
        return next(iter(schemas.values()))
    shared = set.intersection(*(set(schema["columns"]) for schema in schemas.values()))
    columns = {
        column: _merged_column_schema([schema["columns"][column] for schema in schemas.values()])
        for column in sorted(shared)
    }
    columns["source_table"] = {"type": "string", "role": "dimension.table"}
    return {
        "table": "__combined__",
        "tables": sorted(schemas),
        "row_grain": [],
        "primary_time_column": _first_existing(
            ["target_date", "snapshot_time_utc", "snapshot_hour_utc", "entry_time_utc"],
            set(columns),
        ),
        "columns": columns,
    }


def _joined_schema(
    base_schema: dict[str, Any],
    join_schema: dict[str, Any],
    join: dict[str, Any],
) -> dict[str, Any]:
    columns = dict(base_schema["columns"])
    join_columns = join_schema["columns"]
    for key in join["keys"]:
        if key not in columns:
            raise ValueError(f"join key is not in base table: {key}")
        if key not in join_columns:
            raise ValueError(f"join key is not in joined table: {key}")
    for field in join["fields"]:
        if field not in join_columns:
            raise ValueError(f"join field is not in joined table: {field}")
        columns[f"{join['table']}.{field}"] = join_columns[field]
    return {
        **base_schema,
        "columns": columns,
        "join": {
            "table": join["table"],
            "keys": join["keys"],
            "fields": join["fields"],
        },
    }


def _merged_column_schema(items: list[dict[str, Any]]) -> dict[str, Any]:
    types = {str(item.get("type") or "string") for item in items}
    roles = [str(item.get("role") or "") for item in items if item.get("role")]
    return {
        "type": "number" if types == {"number"} else "string",
        "role": roles[0] if roles and all(role == roles[0] for role in roles) else "dimension.mixed",
    }


def _first_existing(candidates: list[str], values: set[str]) -> str | None:
    return next((candidate for candidate in candidates if candidate in values), None)


def _join_rows(
    base_rows: list[dict[str, Any]],
    join_rows: list[dict[str, Any]],
    join: dict[str, Any],
) -> list[dict[str, Any]]:
    keys = join["keys"]
    fields = join["fields"]
    table = join["table"]
    index: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in join_rows:
        key = tuple(row.get(field) for field in keys)
        if key not in index:
            index[key] = row
    output: list[dict[str, Any]] = []
    for row in base_rows:
        key = tuple(row.get(field) for field in keys)
        match = index.get(key, {})
        output.append({
            **row,
            **{f"{table}.{field}": match.get(field) for field in fields},
        })
    return output


def _read_rows(path: Path, table: str) -> list[dict[str, Any]]:
    csv_path = path / f"{table}.csv"
    if csv_path.exists():
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    json_path = path / f"{table}.json"
    if json_path.exists():
        return _json_rows(json_path)
    json_gz_path = path / f"{table}.json.gz"
    if json_gz_path.exists():
        with gzip.open(json_gz_path, "rt", encoding="utf-8") as handle:
            value = json.load(handle)
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []
    return []


def _json_rows(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _coerce_row(row: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for column, value in row.items():
        column_type = schema["columns"].get(column, {}).get("type")
        if value in (None, ""):
            output[column] = None
        elif column_type == "number":
            try:
                output[column] = float(value)
            except (TypeError, ValueError):
                output[column] = None
        else:
            output[column] = value
    return output


def _with_hour_block(row: dict[str, Any], hour_blocks: int | None) -> dict[str, Any]:
    if hour_blocks is None:
        return row
    if hour_blocks < 1 or 24 % hour_blocks != 0:
        raise ValueError("hour_blocks must divide 24")
    hour = row.get("snapshot_local_hour")
    if hour is None:
        hour = _hour_from_timestamp(row.get("snapshot_time_utc"))
    if hour is None:
        return {**row, "hour_block": None}
    block_size = 24 // hour_blocks
    block = int(float(hour)) // block_size
    return {**row, "hour_block": block, "hour_block_label": _hour_block_label(block, block_size)}


def _hour_from_timestamp(value: Any) -> int | None:
    text = str(value or "")
    if "T" not in text:
        return None
    time_part = text.split("T", 1)[1]
    try:
        return int(time_part[:2])
    except ValueError:
        return None


def _hour_block_label(block: int, block_size: int) -> str:
    start = block * block_size
    return f"{start:02d}:00-{start + block_size:02d}:00"


def _matches_filters(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    cities = {str(city) for city in _string_list(filters.get("cities") or filters.get("city"))}
    if cities and "city" in row and str(row.get("city")) not in cities:
        return False
    requested_date_column = _optional_string(filters.get("date_column"))
    date_column = requested_date_column or (
        "target_date"
        if "target_date" in row
        else "snapshot_time_utc"
        if "snapshot_time_utc" in row
        else None
    )
    if not date_column:
        return True
    value = row.get(date_column)
    if filters.get("date_from") and _date_key(value) < str(filters["date_from"]):
        return False
    if filters.get("date_to") and _date_key(value) > str(filters["date_to"]):
        return False
    return True


def _date_key(value: Any) -> str:
    text = str(value or "")
    return text[:10]


def _aggregate(
    rows: list[dict[str, Any]],
    x_field: str | None,
    y_field: str | None,
    group_fields: list[str],
    aggregation: dict[str, Any],
) -> list[dict[str, Any]]:
    op = aggregation["op"]
    field = aggregation.get("field") or y_field
    if op != "count" and not field:
        raise ValueError("aggregation field or y is required")
    keys = [field for field in [x_field, *group_fields] if field]
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(key) for key in keys)].append(row)

    output: list[dict[str, Any]] = []
    value_name = aggregation.get("as") or (f"{op}_{field}" if field else "count")
    for key_values, members in grouped.items():
        item = dict(zip(keys, key_values, strict=False))
        values = [_number(row.get(field)) for row in members] if field else []
        values = [value for value in values if value is not None]
        item[value_name] = _aggregate_value(op, values, len(members))
        item["row_count"] = len(members)
        output.append(item)
    return sorted(output, key=lambda item: tuple(str(item.get(key, "")) for key in keys))


def _aggregate_value(op: str, values: list[float], count: int) -> float | int | None:
    if op == "count":
        return count
    if not values:
        return None
    if op == "sum":
        return sum(values)
    if op == "avg":
        return sum(values) / len(values)
    if op == "min":
        return min(values)
    if op == "max":
        return max(values)
    raise ValueError(f"unsupported aggregation: {op}")


def _project(
    rows: list[dict[str, Any]],
    x_field: str | None,
    y_field: str | None,
    group_fields: list[str],
    hour_blocks: int | None,
) -> list[dict[str, Any]]:
    fields = [field for field in [x_field, y_field, *group_fields] if field]
    if hour_blocks is not None:
        fields.extend(["hour_block", "hour_block_label"])
    if not fields:
        return rows
    seen: set[str] = set()
    selected = [field for field in fields if not (field in seen or seen.add(field))]
    return [{field: row.get(field) for field in selected} for row in rows]


def _sample(rows: list[dict[str, Any]], value: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if value in (None, False):
        return rows, {"applied": False}
    limit = _optional_int(value if not isinstance(value, dict) else value.get("limit"))
    if limit is None or limit <= 0 or len(rows) <= limit:
        return rows, {"applied": False}
    stride = math.ceil(len(rows) / limit)
    sampled = rows[::stride][:limit]
    return sampled, {
        "applied": True,
        "method": "deterministic_stride",
        "limit": limit,
        "stride": stride,
    }


def _decimate(
    rows: list[dict[str, Any]],
    value: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    limit = _optional_int(value)
    if limit is None or limit <= 0 or len(rows) <= limit:
        return rows, {"applied": False}
    stride = math.ceil(len(rows) / limit)
    decimated = rows[::stride][:limit]
    return decimated, {"applied": True, "method": "stride", "target": limit, "stride": stride}


def _density(
    rows: list[dict[str, Any]],
    x_field: str | None,
    y_field: str | None,
    value: Any,
) -> dict[str, Any]:
    if not value or not x_field or not y_field:
        return {"available": False}
    requested_bins = value if not isinstance(value, dict) else value.get("bins")
    bins = _optional_int(requested_bins) or DEFAULT_DENSITY_BINS
    points = [(_number(row.get(x_field)), _number(row.get(y_field))) for row in rows]
    numeric = [(x, y) for x, y in points if x is not None and y is not None]
    if not numeric:
        return {"available": False, "reason": "x/y are not numeric"}
    x_values = [point[0] for point in numeric]
    y_values = [point[1] for point in numeric]
    occupied: set[tuple[int, int]] = set()
    for x_value, y_value in numeric:
        occupied.add(
            (
                _bin_index(x_value, min(x_values), max(x_values), bins),
                _bin_index(y_value, min(y_values), max(y_values), bins),
            )
        )
    return {
        "available": True,
        "bins": bins,
        "points": len(numeric),
        "occupied_cells": len(occupied),
        "max_cells": bins * bins,
        "x_min": min(x_values),
        "x_max": max(x_values),
        "y_min": min(y_values),
        "y_max": max(y_values),
    }


def _bin_index(value: float, low: float, high: float, bins: int) -> int:
    if high <= low:
        return 0
    return min(int(((value - low) / (high - low)) * bins), bins - 1)


def _aggregation(value: Any) -> dict[str, Any] | None:
    if value in (None, "", "none", False):
        return None
    if isinstance(value, str):
        value = {"op": value}
    if not isinstance(value, dict):
        raise ValueError("aggregation must be an object or string")
    op = str(value.get("op", "")).lower()
    if op not in AGGREGATIONS:
        raise ValueError(f"unsupported aggregation: {op}")
    return {**value, "op": op}


def _join_request(value: Any) -> dict[str, Any] | None:
    if value in (None, "", False):
        return None
    if not isinstance(value, dict):
        raise ValueError("join must be an object")
    table = _optional_string(value.get("table"))
    if not table:
        raise ValueError("join.table is required")
    keys = _string_list(value.get("keys"))
    if not keys:
        raise ValueError("join.keys is required")
    fields = _string_list(value.get("fields"))
    if not fields:
        raise ValueError("join.fields is required")
    return {"table": table, "keys": keys, "fields": fields}


def _validate_filters(columns: set[str], filters: dict[str, Any]) -> None:
    date_column = _optional_string(filters.get("date_column"))
    if date_column:
        _require_column(columns, date_column, "date_column")


def _require_column(columns: set[str], column: str | None, label: str) -> None:
    if column and column not in columns and column not in {"hour_block", "hour_block_label"}:
        raise ValueError(f"unknown {label} field: {column}")


def _required_string(request: dict[str, Any], key: str) -> str:
    value = _optional_string(request.get(key))
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _optional_string(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    raise ValueError("expected a string or string list")


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError("expected an integer") from None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    return value
