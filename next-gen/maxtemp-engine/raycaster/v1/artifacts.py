"""Artifact helpers for Raycaster v1 outputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from features import FEATURE_COLUMNS, FeatureRow


def write_training_rows(rows: list[FeatureRow], output_dir: str | Path) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "training_rows.csv"
    fieldnames = [
        "city",
        "event_ticker",
        "target_date",
        "snapshot_hour_utc",
        "settlement_temperature_f",
        "winner_ticker",
        "settlement_bracket_index",
        *FEATURE_COLUMNS,
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "city": row.city,
                    "event_ticker": row.event_ticker,
                    "target_date": row.target_date.isoformat(),
                    "snapshot_hour_utc": row.snapshot_hour_utc.isoformat(),
                    "settlement_temperature_f": row.settlement_temperature_f,
                    "winner_ticker": row.winner_ticker,
                    "settlement_bracket_index": row.settlement_bracket_index,
                    **row.features,
                }
            )


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

