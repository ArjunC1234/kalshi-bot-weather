"""Template training logic for TheTemp v1."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
from config import MODEL_NAME
from features import FeatureRow


@dataclass(frozen=True)
class TheTempModel:
    model_name: str = MODEL_NAME
    mode: str = "template_source_blend"
    training_rows: int = 0


def train_model(rows: list[FeatureRow]) -> TheTempModel:
    training_rows = sum(row.settlement_temperature_f is not None for row in rows)
    return TheTempModel(training_rows=training_rows)


def predict_expected_high(model: TheTempModel, rows: list[FeatureRow]) -> list[float]:
    return [_source_blend(row) for row in rows]


def predict_quantiles(model: TheTempModel, rows: list[FeatureRow]) -> list[dict[float, float]]:
    expected_values = predict_expected_high(model, rows)
    return [
        _simple_quantiles(expected, row)
        for expected, row in zip(expected_values, rows, strict=True)
    ]


def save_model(model: TheTempModel, output_dir: str | Path, manifest: dict[str, Any]) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    import json

    payload = {
        "model_name": model.model_name,
        "mode": model.mode,
        "training_rows": model.training_rows,
        "market_features_used": False,
        **manifest,
    }
    (output / "model_manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    joblib.dump(model, output / "model.joblib")


def load_model(model_dir: str | Path) -> TheTempModel:
    path = Path(model_dir) / "model.joblib"
    return joblib.load(path) if path.exists() else TheTempModel()


def _source_blend(row: FeatureRow) -> float:
    prediction = row.family_baseline_high_f
    if row.observed_high_so_far_f is not None:
        return max(float(prediction), row.observed_high_so_far_f)
    return float(prediction)


def _simple_quantiles(expected_high_f: float, row: FeatureRow) -> dict[float, float]:
    floor = row.observed_high_so_far_f - 0.75 if row.observed_high_so_far_f is not None else None
    quantiles = {
        0.05: expected_high_f - 4.0,
        0.10: expected_high_f - 3.0,
        0.25: expected_high_f - 1.5,
        0.50: expected_high_f,
        0.75: expected_high_f + 1.5,
        0.90: expected_high_f + 3.0,
        0.95: expected_high_f + 4.0,
    }
    if floor is None:
        return quantiles
    return {level: max(value, floor) for level, value in quantiles.items()}
