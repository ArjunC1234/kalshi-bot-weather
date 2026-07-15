"""Training and artifact creation for Raycaster v1."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from features import (
    ACTIVE_CATEGORICAL_FEATURES,
    ACTIVE_FEATURE_COLUMNS,
    ACTIVE_NUMERIC_FEATURES,
    CATEGORICAL_FEATURES,
    DEFAULT_FEATURE_PROFILE,
    FEATURE_COLUMNS,
    NUMERIC_FEATURES,
    RESIDUAL_FEATURE_PROFILE,
    FeatureProfile,
    FeatureRow,
    baseline_prediction,
    feature_dicts,
    feature_profile,
    rows_with_temperature,
)
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

QUANTILE_LEVELS = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
DEFAULT_MIN_TRAINING_EVENTS = 60
TARGET_MODE = "source_blend_residual"
DIRECT_TARGET_MODE = "direct_final_high"
PREDICTION_BLEND_WEIGHT = 0.65
SAMPLE_WEIGHTING = "equal_target_date"
TREE_REGULARIZATION = {
    "learning_rate": 0.04,
    "max_leaf_nodes": 15,
    "min_samples_leaf": 20,
    "l2_regularization": 0.2,
    "random_state": 17,
}
MIN_UNCERTAINTY_F = 2.25
THIN_HISTORY_EVENTS = 180


@dataclass
class ResidualOffset:
    offset_f: float
    count: int


@dataclass
class CityResidualCalibrator:
    alpha: float = 12.0
    global_offset_f: float = 0.0
    city_checkpoint_offsets: dict[tuple[str, str], ResidualOffset] | None = None
    city_offsets: dict[str, ResidualOffset] | None = None
    checkpoint_offsets: dict[str, ResidualOffset] | None = None

    def correction(self, row: FeatureRow) -> float:
        checkpoint = str(row.features.get("checkpoint") or "")
        city_checkpoint = (row.city, checkpoint)
        for store, key in (
            (self.city_checkpoint_offsets or {}, city_checkpoint),
            (self.city_offsets or {}, row.city),
            (self.checkpoint_offsets or {}, checkpoint),
        ):
            offset = store.get(key)
            if offset is not None:
                return offset.offset_f
        return self.global_offset_f


@dataclass
class RaycasterModel:
    mode: str
    point_model: Pipeline | None
    quantile_models: dict[float, Pipeline]
    min_training_events: int
    training_rows: int
    target_mode: str = TARGET_MODE
    prediction_blend_weight: float = PREDICTION_BLEND_WEIGHT
    sample_weighting: str = SAMPLE_WEIGHTING
    tree_regularization: dict[str, Any] | None = None
    feature_profile_name: str = DEFAULT_FEATURE_PROFILE
    active_numeric_features: list[str] | None = None
    active_categorical_features: list[str] | None = None
    residual_calibrator: CityResidualCalibrator | None = None


def train_raycaster_model(
    rows: list[FeatureRow],
    min_training_events: int = DEFAULT_MIN_TRAINING_EVENTS,
    feature_profile_name: str = DEFAULT_FEATURE_PROFILE,
) -> RaycasterModel:
    profile = feature_profile(feature_profile_name)
    training_rows = rows_with_temperature(rows)
    if len(training_rows) < min_training_events:
        return RaycasterModel(
            mode="fallback_source_blend",
            point_model=None,
            quantile_models={},
            min_training_events=min_training_events,
            training_rows=len(training_rows),
            feature_profile_name=profile.name,
            active_numeric_features=profile.numeric_features,
            active_categorical_features=profile.categorical_features,
        )
    x = pd.DataFrame(feature_dicts(training_rows), columns=FEATURE_COLUMNS)[profile.feature_columns]
    y = [_residual_target(row, profile.name) for row in training_rows]
    weights = _equal_target_date_weights(training_rows)
    point_model = _pipeline(_regressor(loss="squared_error"), profile)
    point_model.fit(x, y, model__sample_weight=weights)
    quantile_models: dict[float, Pipeline] = {}
    for level in QUANTILE_LEVELS:
        model = _pipeline(_regressor(loss="quantile", quantile=level), profile)
        model.fit(x, y, model__sample_weight=weights)
        quantile_models[level] = model
    base_model = RaycasterModel(
        mode="trained_hist_gradient_boosting",
        point_model=point_model,
        quantile_models=quantile_models,
        min_training_events=min_training_events,
        training_rows=len(training_rows),
        target_mode=TARGET_MODE,
        prediction_blend_weight=PREDICTION_BLEND_WEIGHT,
        sample_weighting=SAMPLE_WEIGHTING,
        tree_regularization=TREE_REGULARIZATION,
        feature_profile_name=profile.name,
        active_numeric_features=profile.numeric_features,
        active_categorical_features=profile.categorical_features,
    )
    calibrator = (
        _fit_city_residual_calibrator(training_rows, base_model)
        if profile.name == RESIDUAL_FEATURE_PROFILE
        else None
    )
    return RaycasterModel(
        mode="trained_hist_gradient_boosting",
        point_model=point_model,
        quantile_models=quantile_models,
        min_training_events=min_training_events,
        training_rows=len(training_rows),
        target_mode=TARGET_MODE,
        prediction_blend_weight=PREDICTION_BLEND_WEIGHT,
        sample_weighting=SAMPLE_WEIGHTING,
        tree_regularization=TREE_REGULARIZATION,
        feature_profile_name=profile.name,
        active_numeric_features=profile.numeric_features,
        active_categorical_features=profile.categorical_features,
        residual_calibrator=calibrator,
    )


def predict_expected_high(model: RaycasterModel, rows: list[FeatureRow]) -> list[float]:
    if model.point_model is None:
        return [baseline_prediction(row, model.feature_profile_name) for row in rows]
    x = pd.DataFrame(feature_dicts(rows), columns=FEATURE_COLUMNS)
    x = x[_model_feature_columns(model)]
    values = [float(value) for value in model.point_model.predict(x)]
    if model.target_mode == TARGET_MODE:
        predictions = [
            _respect_observed_floor(
                _blend_with_source(
                    baseline_prediction(row, model.feature_profile_name), residual, model
                ),
                row,
            )
            for residual, row in zip(values, rows, strict=True)
        ]
    else:
        predictions = [
            _respect_observed_floor(value, row) for value, row in zip(values, rows, strict=True)
        ]
    return _apply_city_residual_corrections(model, rows, predictions)


def predict_quantiles(model: RaycasterModel, rows: list[FeatureRow]) -> list[dict[float, float]]:
    expected = predict_expected_high(model, rows)
    if not model.quantile_models:
        return [_fallback_quantiles(value, row) for value, row in zip(expected, rows, strict=True)]
    x = pd.DataFrame(feature_dicts(rows), columns=FEATURE_COLUMNS)[_model_feature_columns(model)]
    per_level = {
        level: [float(value) for value in quantile_model.predict(x)]
        for level, quantile_model in model.quantile_models.items()
    }
    output: list[dict[float, float]] = []
    for index, row in enumerate(rows):
        if model.target_mode == TARGET_MODE:
            source_prediction = baseline_prediction(row, model.feature_profile_name)
            quantiles = {
                level: _blend_with_source(source_prediction, values[index], model)
                for level, values in per_level.items()
            }
        else:
            quantiles = {level: values[index] for level, values in per_level.items()}
        quantiles = _calibrated_quantiles(
            expected[index],
            row,
            quantiles,
            training_rows=model.training_rows,
        )
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
                "active_numeric_features": ACTIVE_NUMERIC_FEATURES,
                "active_categorical_features": ACTIVE_CATEGORICAL_FEATURES,
                "active_feature_columns": ACTIVE_FEATURE_COLUMNS,
                "feature_profile": model.feature_profile_name,
                "profile_numeric_features": _model_numeric_features(model),
                "profile_categorical_features": _model_categorical_features(model),
                "profile_feature_columns": _model_feature_columns(model),
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
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
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
        target_mode=str(manifest.get("target_mode") or DIRECT_TARGET_MODE),
        prediction_blend_weight=float(
            manifest.get("prediction_blend_weight") or PREDICTION_BLEND_WEIGHT
        ),
        sample_weighting=str(manifest.get("sample_weighting") or SAMPLE_WEIGHTING),
        tree_regularization=dict(manifest.get("tree_regularization") or TREE_REGULARIZATION),
        feature_profile_name=str(manifest.get("feature_profile") or DEFAULT_FEATURE_PROFILE),
        active_numeric_features=list(
            manifest.get("profile_numeric_features")
            or feature_profile(
                str(manifest.get("feature_profile") or DEFAULT_FEATURE_PROFILE)
            ).numeric_features
        ),
        active_categorical_features=list(
            manifest.get("profile_categorical_features")
            or feature_profile(
                str(manifest.get("feature_profile") or DEFAULT_FEATURE_PROFILE)
            ).categorical_features
        ),
        residual_calibrator=_load_residual_calibrator(manifest.get("residual_calibrator")),
    )


def _regressor(loss: str, quantile: float | None = None) -> HistGradientBoostingRegressor:
    kwargs: dict[str, Any] = {**TREE_REGULARIZATION, "loss": loss}
    if quantile is not None:
        kwargs["quantile"] = quantile
    return HistGradientBoostingRegressor(**kwargs)


def _pipeline(regressor: HistGradientBoostingRegressor, profile: FeatureProfile) -> Pipeline:
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
                profile.numeric_features,
            ),
            (
                "categorical",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("encoder", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                    ]
                ),
                profile.categorical_features,
            ),
        ],
        remainder="drop",
    )
    return Pipeline(steps=[("preprocessor", preprocessor), ("model", regressor)])


def _residual_target(row: FeatureRow, feature_profile_name: str = DEFAULT_FEATURE_PROFILE) -> float:
    if row.settlement_temperature_f is None:
        raise ValueError("residual target requires a final temperature")
    return float(row.settlement_temperature_f) - baseline_prediction(row, feature_profile_name)


def _equal_target_date_weights(rows: list[FeatureRow]) -> list[float]:
    counts = Counter(row.target_date for row in rows)
    return [1.0 / counts[row.target_date] for row in rows]


def _blend_with_source(source_prediction: float, residual: float, model: RaycasterModel) -> float:
    weight = min(1.0, max(0.0, model.prediction_blend_weight))
    return source_prediction + weight * residual


def _fallback_quantiles(expected_high_f: float, row: FeatureRow) -> dict[float, float]:
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
    return _calibrated_quantiles(expected_high_f, row, quantiles, training_rows=0)


def _model_numeric_features(model: RaycasterModel) -> list[str]:
    profile = feature_profile(model.feature_profile_name)
    return list(model.active_numeric_features or profile.numeric_features)


def _model_categorical_features(model: RaycasterModel) -> list[str]:
    return list(
        model.active_categorical_features
        or feature_profile(model.feature_profile_name).categorical_features
    )


def _model_feature_columns(model: RaycasterModel) -> list[str]:
    return _model_numeric_features(model) + _model_categorical_features(model)


def _apply_city_residual_corrections(
    model: RaycasterModel,
    rows: list[FeatureRow],
    predictions: list[float],
) -> list[float]:
    if model.residual_calibrator is None:
        return predictions
    return [
        _respect_observed_floor(prediction + model.residual_calibrator.correction(row), row)
        for row, prediction in zip(rows, predictions, strict=True)
    ]


def _fit_city_residual_calibrator(
    rows: list[FeatureRow],
    model: RaycasterModel,
    alpha: float = 12.0,
) -> CityResidualCalibrator:
    predictions = predict_expected_high(model, rows)
    residual_rows = [
        (row, float(row.settlement_temperature_f) - prediction)
        for row, prediction in zip(rows, predictions, strict=True)
        if row.settlement_temperature_f is not None
    ]
    global_offset = (
        sum(residual for _, residual in residual_rows) / len(residual_rows)
        if residual_rows
        else 0.0
    )
    return CityResidualCalibrator(
        alpha=alpha,
        global_offset_f=_shrunk_mean([residual for _, residual in residual_rows], alpha),
        city_checkpoint_offsets=_offsets(
            residual_rows,
            lambda row: (row.city, str(row.features.get("checkpoint") or "")),
            alpha,
            global_offset,
        ),
        city_offsets=_offsets(residual_rows, lambda row: row.city, alpha, global_offset),
        checkpoint_offsets=_offsets(
            residual_rows,
            lambda row: str(row.features.get("checkpoint") or ""),
            alpha,
            global_offset,
        ),
    )


def _offsets(
    residual_rows: list[tuple[FeatureRow, float]],
    key_func,
    alpha: float,
    global_offset: float,
) -> dict[Any, ResidualOffset]:
    grouped: dict[Any, list[float]] = {}
    for row, residual in residual_rows:
        grouped.setdefault(key_func(row), []).append(residual)
    return {
        key: ResidualOffset(
            offset_f=_shrunk_mean(values, alpha, global_offset),
            count=len(values),
        )
        for key, values in grouped.items()
        if values
    }


def _shrunk_mean(values: list[float], alpha: float, prior: float = 0.0) -> float:
    if not values:
        return prior
    weight = len(values) / (len(values) + alpha)
    return prior + weight * ((sum(values) / len(values)) - prior)


def _calibrated_quantiles(
    expected_high_f: float,
    row: FeatureRow,
    quantiles: dict[float, float],
    training_rows: int,
) -> dict[float, float]:
    spread = _uncertainty_spread(row, training_rows)
    observed = _observed(row)
    minimum_offsets = {
        0.05: 1.50 * spread,
        0.10: 1.15 * spread,
        0.25: 0.55 * spread,
        0.50: 0.0,
        0.75: 0.55 * spread,
        0.90: 1.15 * spread,
        0.95: 1.50 * spread,
    }
    widened = {}
    for level, value in quantiles.items():
        level = float(level)
        if level < 0.50:
            widened[level] = min(value, expected_high_f - minimum_offsets.get(level, spread))
        elif level > 0.50:
            widened[level] = max(value, expected_high_f + minimum_offsets.get(level, spread))
        else:
            widened[level] = expected_high_f
    if observed is not None:
        return {level: max(value, observed - 0.75) for level, value in widened.items()}
    return widened


def _uncertainty_spread(row: FeatureRow, training_rows: int) -> float:
    source_std = _finite_float(row.features.get("source_std_f"))
    source_range = _finite_float(row.features.get("source_range_f"))
    spread = max(
        MIN_UNCERTAINTY_F,
        (source_std or 0.0) * 1.20,
        (source_range or 0.0) * 0.40,
    )
    if training_rows < THIN_HISTORY_EVENTS:
        spread *= 1.25
    return spread


def _finite_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


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
        "target_mode": model.target_mode,
        "prediction_blend_weight": model.prediction_blend_weight,
        "sample_weighting": model.sample_weighting,
        "tree_regularization": model.tree_regularization or TREE_REGULARIZATION,
        "minimum_uncertainty_f": MIN_UNCERTAINTY_F,
        "feature_profile": model.feature_profile_name,
        "profile_numeric_features": _model_numeric_features(model),
        "profile_categorical_features": _model_categorical_features(model),
        "residual_calibrator": _residual_calibrator_manifest(model.residual_calibrator),
    }


def _residual_calibrator_manifest(
    calibrator: CityResidualCalibrator | None,
) -> dict[str, Any] | None:
    if calibrator is None:
        return None
    return {
        "alpha": calibrator.alpha,
        "global_offset_f": calibrator.global_offset_f,
        "city_checkpoint_offsets": [
            {
                "city": city,
                "checkpoint": checkpoint,
                "offset_f": offset.offset_f,
                "count": offset.count,
            }
            for (city, checkpoint), offset in sorted(
                (calibrator.city_checkpoint_offsets or {}).items()
            )
        ],
        "city_offsets": [
            {"city": city, "offset_f": offset.offset_f, "count": offset.count}
            for city, offset in sorted((calibrator.city_offsets or {}).items())
        ],
        "checkpoint_offsets": [
            {"checkpoint": checkpoint, "offset_f": offset.offset_f, "count": offset.count}
            for checkpoint, offset in sorted((calibrator.checkpoint_offsets or {}).items())
        ],
    }


def _load_residual_calibrator(payload: Any) -> CityResidualCalibrator | None:
    if not isinstance(payload, dict):
        return None
    return CityResidualCalibrator(
        alpha=float(payload.get("alpha") or 12.0),
        global_offset_f=float(payload.get("global_offset_f") or 0.0),
        city_checkpoint_offsets={
            (str(row["city"]), str(row["checkpoint"])): ResidualOffset(
                offset_f=float(row["offset_f"]),
                count=int(row["count"]),
            )
            for row in payload.get("city_checkpoint_offsets", [])
        },
        city_offsets={
            str(row["city"]): ResidualOffset(
                offset_f=float(row["offset_f"]),
                count=int(row["count"]),
            )
            for row in payload.get("city_offsets", [])
        },
        checkpoint_offsets={
            str(row["checkpoint"]): ResidualOffset(
                offset_f=float(row["offset_f"]),
                count=int(row["count"]),
            )
            for row in payload.get("checkpoint_offsets", [])
        },
    )
