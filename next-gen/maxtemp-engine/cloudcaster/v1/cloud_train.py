"""Training and prediction for Cloudcaster v1."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

import pandas as pd
from cloud_features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    NUMERIC_FEATURES,
    CloudcasterRow,
    feature_dicts,
    labeled_rows,
)
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from libs.models import BracketDistribution
from libs.probabilities import apply_probability_floor, normalize

MODEL_NAME = "cloudcaster_v1"
CHAIN_MODEL_NAME = "raycaster_cloudcaster"
DEFAULT_MIN_TRAINING_ROWS = 120


@dataclass
class CloudcasterModel:
    mode: str
    classifier: Pipeline | None
    min_training_rows: int
    training_rows: int
    feature_columns: list[str]


def train_cloudcaster_model(
    rows: list[CloudcasterRow],
    min_training_rows: int = DEFAULT_MIN_TRAINING_ROWS,
) -> CloudcasterModel:
    training_rows = labeled_rows(rows)
    targets = [int(row.target) for row in training_rows if row.target is not None]
    if len(training_rows) < min_training_rows or len(set(targets)) < 2:
        return CloudcasterModel(
            mode="fallback_source_blend_distribution",
            classifier=None,
            min_training_rows=min_training_rows,
            training_rows=len(training_rows),
            feature_columns=FEATURE_COLUMNS,
        )
    x = pd.DataFrame(feature_dicts(training_rows), columns=FEATURE_COLUMNS)
    y = targets
    model = _pipeline()
    model.fit(x, y, model__sample_weight=_sample_weights(training_rows))
    return CloudcasterModel(
        mode="trained_hist_gradient_boosting_classifier",
        classifier=model,
        min_training_rows=min_training_rows,
        training_rows=len(training_rows),
        feature_columns=FEATURE_COLUMNS,
    )


def predict_distributions(
    model: CloudcasterModel,
    rows: list[CloudcasterRow],
    fallback_probabilities: dict[tuple[str, str, object], dict[str, float]],
    probability_floor: float = 0.001,
    model_name: str = CHAIN_MODEL_NAME,
    temperature: float = 1.0,
) -> list[BracketDistribution]:
    if not rows:
        return []
    scores = _positive_scores(model, rows)
    grouped: dict[tuple[str, str, object], list[tuple[CloudcasterRow, float]]] = defaultdict(list)
    for row, score in zip(rows, scores, strict=True):
        grouped[row.snapshot_key].append((row, score))
    output: list[BracketDistribution] = []
    for key, items in sorted(grouped.items()):
        if model.classifier is None:
            probabilities = fallback_probabilities.get(key, {})
        else:
            probabilities = normalize(
                _apply_temperature(
                    {row.market_ticker: score for row, score in items},
                    temperature,
                ),
                "cloudcaster bracket scores",
            )
        if probability_floor:
            probabilities = apply_probability_floor(probabilities, probability_floor)
        city, event_ticker, snapshot_hour_utc = key
        output.append(
            BracketDistribution(
                city=city,
                event_ticker=event_ticker,
                snapshot_hour_utc=snapshot_hour_utc,
                model_name=model_name,
                probabilities=probabilities,
            )
        )
    return output


def _apply_temperature(scores: dict[str, float], temperature: float) -> dict[str, float]:
    power = 1.0 / max(float(temperature), 1e-6)
    return {
        ticker: max(float(score), 1e-12) ** power
        for ticker, score in scores.items()
    }


def _positive_scores(model: CloudcasterModel, rows: list[CloudcasterRow]) -> list[float]:
    if model.classifier is None:
        return [1.0 for _ in rows]
    x = pd.DataFrame(feature_dicts(rows), columns=FEATURE_COLUMNS)
    probabilities = model.classifier.predict_proba(x)
    classes = list(model.classifier.named_steps["model"].classes_)
    positive_index = classes.index(1)
    return [max(1e-9, float(row[positive_index])) for row in probabilities]


def _pipeline() -> Pipeline:
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
    classifier = HistGradientBoostingClassifier(
        learning_rate=0.04,
        max_leaf_nodes=15,
        min_samples_leaf=20,
        l2_regularization=0.2,
        random_state=17,
    )
    return Pipeline(steps=[("preprocessor", preprocessor), ("model", classifier)])


def _sample_weights(rows: list[CloudcasterRow]) -> list[float]:
    target_date_counts = Counter(row.target_date for row in rows)
    snapshot_counts = Counter(row.snapshot_key for row in rows)
    return [
        (1.0 / target_date_counts[row.target_date])
        * (1.0 / snapshot_counts[row.snapshot_key])
        * _class_weight(row)
        for row in rows
    ]


def _class_weight(row: CloudcasterRow) -> float:
    return 5.0 if row.target == 1 else 1.0


def model_manifest(model: CloudcasterModel) -> dict[str, Any]:
    return {
        "model_name": MODEL_NAME,
        "chain_model_name": CHAIN_MODEL_NAME,
        "mode": model.mode,
        "min_training_rows": model.min_training_rows,
        "training_rows": model.training_rows,
        "feature_columns": model.feature_columns,
        "objective": "binary bracket winner classification with per-snapshot normalization",
    }
