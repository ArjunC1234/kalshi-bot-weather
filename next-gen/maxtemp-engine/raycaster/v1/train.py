"""Training and artifact creation for Raycaster v1."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    NUMERIC_FEATURES,
    FeatureRow,
    feature_dicts,
    rows_with_temperature,
    source_blend_prediction,
)
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

QUANTILE_LEVELS = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
DEFAULT_MIN_TRAINING_EVENTS = 60


@dataclass
class RaycasterModel:
    mode: str
    point_model: Pipeline | None
    quantile_models: dict[float, Pipeline]
    min_training_events: int
    training_rows: int


def train_raycaster_model(
    rows: list[FeatureRow],
    min_training_events: int = DEFAULT_MIN_TRAINING_EVENTS,
) -> RaycasterModel:
    training_rows = rows_with_temperature(rows)
    if len(training_rows) < min_training_events:
        return RaycasterModel(
            mode="fallback_source_blend",
            point_model=None,
            quantile_models={},
            min_training_events=min_training_events,
            training_rows=len(training_rows),
        )
    x = pd.DataFrame(feature_dicts(training_rows), columns=FEATURE_COLUMNS)
    y = [float(row.settlement_temperature_f) for row in training_rows]
    point_model = _pipeline(
        HistGradientBoostingRegressor(loss="squared_error", random_state=17)
    )
    point_model.fit(x, y)
    quantile_models: dict[float, Pipeline] = {}
    for level in QUANTILE_LEVELS:
        model = _pipeline(
            HistGradientBoostingRegressor(loss="quantile", quantile=level, random_state=17)
        )
        model.fit(x, y)
        quantile_models[level] = model
    return RaycasterModel(
        mode="trained_hist_gradient_boosting",
        point_model=point_model,
        quantile_models=quantile_models,
        min_training_events=min_training_events,
        training_rows=len(training_rows),
    )


def predict_expected_high(model: RaycasterModel, rows: list[FeatureRow]) -> list[float]:
    if model.point_model is None:
        return [source_blend_prediction(row) for row in rows]
    x = pd.DataFrame(feature_dicts(rows), columns=FEATURE_COLUMNS)
    values = [float(value) for value in model.point_model.predict(x)]
    return [_respect_observed_floor(value, row) for value, row in zip(values, rows, strict=True)]


def predict_quantiles(model: RaycasterModel, rows: list[FeatureRow]) -> list[dict[float, float]]:
    expected = predict_expected_high(model, rows)
    if not model.quantile_models:
        return [_fallback_quantiles(value, row) for value, row in zip(expected, rows, strict=True)]
    x = pd.DataFrame(feature_dicts(rows), columns=FEATURE_COLUMNS)
    per_level = {
        level: [float(value) for value in quantile_model.predict(x)]
        for level, quantile_model in model.quantile_models.items()
    }
    output: list[dict[float, float]] = []
    for index, row in enumerate(rows):
        quantiles = {level: values[index] for level, values in per_level.items()}
        observed = _observed(row)
        if observed is not None:
            quantiles = {level: max(value, observed - 0.75) for level, value in quantiles.items()}
        output.append(quantiles)
    return output


def save_model(model: RaycasterModel, output_dir: str | Path, manifest: dict[str, Any]) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    import json

    (output / "model_manifest.json").write_text(
        json.dumps({**manifest, **_model_manifest(model)}, indent=2, default=str),
        encoding="utf-8",
    )
    (output / "feature_schema.json").write_text(
        json.dumps(
            {
                "numeric_features": NUMERIC_FEATURES,
                "categorical_features": CATEGORICAL_FEATURES,
                "feature_columns": FEATURE_COLUMNS,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if model.point_model is not None:
        joblib.dump(model.point_model, output / "point_model.joblib")
    if model.quantile_models:
        joblib.dump(model.quantile_models, output / "quantile_models.joblib")


def load_model(model_dir: str | Path) -> RaycasterModel:
    model_path = Path(model_dir)
    import json

    manifest_path = model_path / "model_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    point_path = model_path / "point_model.joblib"
    quantile_path = model_path / "quantile_models.joblib"
    point_model = joblib.load(point_path) if point_path.exists() else None
    quantile_models = joblib.load(quantile_path) if quantile_path.exists() else {}
    return RaycasterModel(
        mode=str(
            manifest.get("mode")
            or ("trained_hist_gradient_boosting" if point_model else "fallback_source_blend")
        ),
        point_model=point_model,
        quantile_models=quantile_models,
        min_training_events=int(manifest.get("min_training_events") or DEFAULT_MIN_TRAINING_EVENTS),
        training_rows=int(manifest.get("training_rows") or 0),
    )


def _pipeline(regressor: HistGradientBoostingRegressor) -> Pipeline:
    preprocessor = ColumnTransformer(
        transformers=[
            (
                "numeric",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                        ("scaler", StandardScaler()),
                    ]
                ),
                NUMERIC_FEATURES,
            ),
            (
                "categorical",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("encoder", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                    ]
                ),
                CATEGORICAL_FEATURES,
            ),
        ],
        remainder="drop",
    )
    return Pipeline(steps=[("preprocessor", preprocessor), ("model", regressor)])


def _fallback_quantiles(expected_high_f: float, row: FeatureRow) -> dict[float, float]:
    observed = _observed(row)
    spread = max(1.5, float(row.features.get("source_std_f") or 1.5))
    quantiles = {
        0.05: expected_high_f - 2.25 * spread,
        0.10: expected_high_f - 1.75 * spread,
        0.25: expected_high_f - 0.75 * spread,
        0.50: expected_high_f,
        0.75: expected_high_f + 0.75 * spread,
        0.90: expected_high_f + 1.75 * spread,
        0.95: expected_high_f + 2.25 * spread,
    }
    if observed is not None:
        return {level: max(value, observed - 0.75) for level, value in quantiles.items()}
    return quantiles


def _respect_observed_floor(value: float, row: FeatureRow) -> float:
    observed = _observed(row)
    return max(value, observed) if observed is not None else value


def _observed(row: FeatureRow) -> float | None:
    value = row.features.get("observed_high_so_far_f")
    return float(value) if value is not None else None


def _model_manifest(model: RaycasterModel) -> dict[str, Any]:
    return {
        "model_name": "raycaster_v1",
        "mode": model.mode,
        "min_training_events": model.min_training_events,
        "training_rows": model.training_rows,
        "quantile_levels": list(QUANTILE_LEVELS),
        "market_features_used": False,
    }
