"""Import alternate settlement labels into a frozen local export."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from backtest.data_sources import LocalExportSource
from libs.io_utils import write_json_gz
from libs.json_utils import write_json
from libs.settlement_sources import annotate_table_rows, normalize_label_source, settlement_source_summary


def import_final_temperature_labels(
    export_path: Path,
    labels_csv: Path,
    *,
    source_provider: str,
) -> dict[str, Any]:
    source = LocalExportSource(export_path)
    existing = source.load_table("final_temperature_labels")
    incoming = _read_label_csv(labels_csv, source_provider=source_provider)
    merged = _merge_labels(existing, incoming)
    merged = annotate_table_rows("final_temperature_labels", merged)
    write_json_gz(export_path / "final_temperature_labels.json.gz", merged)
    manifest = _read_manifest(export_path)
    manifest["settlement_sources"] = settlement_source_summary(
        {
            "events": source.load_table("events"),
            "market_snapshots": source.load_table("market_snapshots"),
            "settlements": source.load_table("settlements"),
            "final_temperature_labels": merged,
        }
    )
    if "tables" in manifest and isinstance(manifest["tables"], dict):
        current = manifest["tables"].get("final_temperature_labels")
        if isinstance(current, dict):
            current["rows"] = len(merged)
        else:
            manifest["tables"]["final_temperature_labels"] = len(merged)
    write_json(export_path / "manifest.json", manifest)
    return {
        "export_path": str(export_path),
        "labels_csv": str(labels_csv),
        "source_provider": normalize_label_source(source_provider),
        "imported_rows": len(incoming),
        "total_label_rows": len(merged),
        "settlement_sources": manifest["settlement_sources"],
    }


def _read_label_csv(path: Path, *, source_provider: str) -> list[dict[str, Any]]:
    normalized_source = normalize_label_source(source_provider)
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    output = []
    for row in rows:
        required = {
            "city": row.get("city"),
            "event_ticker": row.get("event_ticker"),
            "target_date": row.get("target_date"),
            "final_high_f": row.get("final_high_f"),
        }
        missing = [key for key, value in required.items() if value in (None, "")]
        if missing:
            raise ValueError(f"label row missing required fields: {', '.join(missing)}")
        output.append(
            {
                "city": str(row["city"]),
                "event_ticker": str(row["event_ticker"]),
                "target_date": str(row["target_date"])[:10],
                "station_id": str(row.get("station_id") or ""),
                "final_high_f": float(row["final_high_f"]),
                "source_provider": normalized_source,
                "label_source": normalized_source,
                "product_id": row.get("product_id") or None,
                "issued_at_utc": row.get("issued_at_utc") or None,
                "validation_status": row.get("validation_status") or "valid",
                "warnings": row.get("warnings") or [],
            }
        )
    return output


def _merge_labels(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {
        _label_key(row): dict(row)
        for row in existing
        if row.get("event_ticker") and row.get("target_date")
    }
    for row in incoming:
        merged[_label_key(row)] = row
    return [merged[key] for key in sorted(merged)]


def _label_key(row: dict[str, Any]) -> tuple[str, str, str]:
    source = normalize_label_source(row.get("label_source") or row.get("source_provider"))
    return (str(row.get("event_ticker") or ""), str(row.get("target_date") or "")[:10], source)


def _read_manifest(export_path: Path) -> dict[str, Any]:
    path = export_path / "manifest.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
