"""Settlement-source coverage and label-difference reports."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backtest.data_sources import DataSource
from libs.io_utils import write_csv
from libs.settlement_sources import (
    NWS_CLI_DAILY,
    WEATHER_COMPANY_DAILY,
    normalize_label_source,
    settlement_source_summary,
)


def write_settlement_source_report(
    source: DataSource,
    output: Path,
    *,
    source_export_id: str | None = None,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    tables = {
        "events": source.load_table("events"),
        "market_snapshots": source.load_table("market_snapshots"),
        "settlements": source.load_table("settlements"),
        "final_temperature_labels": source.load_table("final_temperature_labels"),
    }
    summary = {
        "artifact_type": "quality_report",
        "contract": "settlement_source_report.v1",
        "source_export_id": source_export_id,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "settlement_sources": settlement_source_summary(tables),
    }
    coverage_rows = _coverage_rows(tables)
    comparison_rows = _comparison_rows(tables)
    summary.update(
        {
            "label_rows": len(tables["final_temperature_labels"]),
            "coverage_rows": len(coverage_rows),
            "comparison_rows": len(comparison_rows),
            "nws_weather_company_pairs": sum(
                1 for row in comparison_rows if row.get("both_sources") is True
            ),
            "warnings": _warnings(summary["settlement_sources"], comparison_rows),
        }
    )
    write_csv(output / "source_coverage.csv", coverage_rows)
    _write_csv_with_header(
        output / "label_source_comparison.csv",
        comparison_rows,
        [
            "city",
            "target_date",
            "event_ticker",
            "nws_cli_daily_high_f",
            "weather_company_daily_high_f",
            "temperature_diff_f",
            "both_sources",
        ],
    )
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, default=_json_default),
        encoding="utf-8",
    )
    (output / "settlement_source_report.md").write_text(
        _markdown(summary),
        encoding="utf-8",
    )
    return {**summary, "output_dir": str(output)}


def _coverage_rows(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for row in tables["final_temperature_labels"]:
        source = normalize_label_source(row.get("label_source") or row.get("source_provider"))
        counts[(str(row.get("city") or ""), str(row.get("target_date") or ""), source)] += 1
    return [
        {"city": city, "target_date": target_date, "label_source": source, "rows": rows}
        for (city, target_date, source), rows in sorted(counts.items())
    ]


def _comparison_rows(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, float]] = defaultdict(dict)
    for row in tables["final_temperature_labels"]:
        source = normalize_label_source(row.get("label_source") or row.get("source_provider"))
        key = (
            str(row.get("city") or ""),
            str(row.get("target_date") or ""),
            str(row.get("event_ticker") or ""),
        )
        value = _float_or_none(row.get("final_high_f"))
        if value is not None:
            grouped[key][source] = value
    rows: list[dict[str, Any]] = []
    for (city, target_date, event_ticker), values in sorted(grouped.items()):
        nws = values.get(NWS_CLI_DAILY)
        weather_company = values.get(WEATHER_COMPANY_DAILY)
        diff = (
            None
            if nws is None or weather_company is None
            else float(weather_company) - float(nws)
        )
        rows.append(
            {
                "city": city,
                "target_date": target_date,
                "event_ticker": event_ticker,
                "nws_cli_daily_high_f": nws,
                "weather_company_daily_high_f": weather_company,
                "temperature_diff_f": diff,
                "both_sources": nws is not None and weather_company is not None,
            }
        )
    return rows


def _warnings(source_summary: dict[str, Any], comparison_rows: list[dict[str, Any]]) -> list[str]:
    warnings = list(source_summary.get("warnings") or [])
    sources = source_summary.get("label_sources")
    if isinstance(sources, dict) and WEATHER_COMPANY_DAILY not in sources:
        warnings.append("No Weather Company daily labels are present; comparison is coverage-only.")
    if not any(row.get("both_sources") for row in comparison_rows):
        warnings.append("No event has both NWS CLI and Weather Company daily labels yet.")
    return warnings


def _markdown(summary: dict[str, Any]) -> str:
    sources = summary["settlement_sources"]
    lines = [
        "# Settlement Source Report",
        "",
        f"- Source export: {summary.get('source_export_id') or 'unknown'}",
        f"- Label rows: {summary['label_rows']}",
        f"- Dominant label source: {sources.get('label_source')}",
        f"- Dominant market source: {sources.get('market_settlement_source')}",
        f"- NWS/Weather Company comparable events: {summary['nws_weather_company_pairs']}",
        "",
        "## Warnings",
        "",
    ]
    warnings = summary.get("warnings") or []
    lines.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def _write_csv_with_header(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    if rows:
        write_csv(path, rows)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_default(value: Any) -> Any:
    try:
        return asdict(value)
    except TypeError:
        return str(value)
