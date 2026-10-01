"""Baseline and candidate estimators for Raycaster benchmarking."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd
from features import (
    ACTIVE_CATEGORICAL_FEATURES,
    ACTIVE_FEATURE_COLUMNS,
    ACTIVE_NUMERIC_FEATURES,
    FEATURE_COLUMNS,
    FeatureRow,
    family_blend_prediction,
    feature_dicts,
    rows_with_temperature,
    source_blend_prediction,
)
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from train import (
    DEFAULT_MIN_TRAINING_EVENTS,
    RaycasterModel,
    predict_expected_high,
    predict_quantiles,
    train_raycaster_model,
)

QUANTILE_LEVELS = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
RAW_BASELINES = {
    "nws_anchor": "nws_anchor_high_f",
    "hrrr_projected": "hrrr_projected_high_f",
    "nbm_projected": "nbm_projected_high_f",
    "ensemble_median": "ensemble_raw_median_high_f",
}
DEFAULT_ESTIMATORS = (
    "nws_anchor",
    "hrrr_projected",
    "nbm_projected",
    "ensemble_median",
    "source_blend",
    "family_blend",
    "mos_ridge",
    "mos_elastic_net",
    "raycaster",
    "raycaster_family_v2",
    "raycaster_city",
    "raycaster_city_checkpoint",
    "raycaster_city_residual",
    "raycaster_source_blend_hybrid",
    "raycaster_city_residual_hybrid",
)


class TemperatureEstimator(Protocol):
    name: str

    def fit(self, rows: list[FeatureRow]) -> None:
        """Fit on prior feature rows."""

    def predict_expected_high(self, rows: list[FeatureRow]) -> list[float]:
        """Return expected final highs."""

    def predict_quantiles(self, rows: list[FeatureRow]) -> list[dict[float, float]]:
        """Return forecast quantiles for final highs."""

    @property
    def mode(self) -> str:
        """Return a concise estimator mode for diagnostics."""

    @property
    def training_rows(self) -> int:
        """Return labeled rows used for training."""


@dataclass
class SourceBaseline:
    name: str
    feature_key: str | None = None
    residuals: list[float] = field(default_factory=list)

    def fit(self, rows: list[FeatureRow]) -> None:
        self.residuals = _residuals(rows, self.predict_expected_high(rows))

    def predict_expected_high(self, rows: list[FeatureRow]) -> list[float]:
        values = []
        for row in rows:
            if self.feature_key is None:
                value = source_blend_prediction(row)
            elif self.feature_key == "__family_blend__":
                value = family_blend_prediction(row)
            else:
                value = _finite_float(row.features.get(self.feature_key))
                if value is None:
                    value = source_blend_prediction(row)
            values.append(_respect_observed_floor(value, row))
        return values

    def predict_quantiles(self, rows: list[FeatureRow]) -> list[dict[float, float]]:
        expected = self.predict_expected_high(rows)
        return [
            _residual_quantiles(value, row, self.residuals)
            for value, row in zip(expected, rows, strict=True)
        ]

    @property
    def mode(self) -> str:
        return "source_baseline"

    @property
    def training_rows(self) -> int:
        return len(self.residuals)


@dataclass
class MosResidualModel:
    name: str
    kind: str
    min_training_events: int = DEFAULT_MIN_TRAINING_EVENTS
    model: Pipeline | None = None
    residuals: list[float] = field(default_factory=list)
    _training_rows: int = 0

    def fit(self, rows: list[FeatureRow]) -> None:
        training_rows = rows_with_temperature(rows)
        self._training_rows = len(training_rows)
        source_predictions = [source_blend_prediction(row) for row in training_rows]
        self.residuals = _residuals(training_rows, source_predictions)
        if len(training_rows) < self.min_training_events:
            self.model = None
            return
        x = pd.DataFrame(feature_dicts(training_rows), columns=FEATURE_COLUMNS)
        x = x[ACTIVE_FEATURE_COLUMNS]
        y = [
            float(row.settlement_temperature_f) - source_blend_prediction(row)
            for row in training_rows
            if row.settlement_temperature_f is not None
        ]
        self.model = _linear_pipeline(self.kind)
        self.model.fit(x, y, model__sample_weight=_equal_target_date_weights(training_rows))

    def predict_expected_high(self, rows: list[FeatureRow]) -> list[float]:
        base = [source_blend_prediction(row) for row in rows]
        if self.model is None:
            return [
                _respect_observed_floor(value, row) for value, row in zip(base, rows, strict=True)
            ]
        x = pd.DataFrame(feature_dicts(rows), columns=FEATURE_COLUMNS)[ACTIVE_FEATURE_COLUMNS]
        residuals = [float(value) for value in self.model.predict(x)]
        return [
            _respect_observed_floor(base_value + residual, row)
            for base_value, residual, row in zip(base, residuals, rows, strict=True)
        ]

    def predict_quantiles(self, rows: list[FeatureRow]) -> list[dict[float, float]]:
        expected = self.predict_expected_high(rows)
        return [
            _residual_quantiles(value, row, self.residuals)
            for value, row in zip(expected, rows, strict=True)
        ]

    @property
    def mode(self) -> str:
        return f"mos_{self.kind}" if self.model is not None else "fallback_source_blend"

    @property
    def training_rows(self) -> int:
        return self._training_rows


@dataclass
class RaycasterEstimator:
    name: str = "raycaster"
    min_training_events: int = DEFAULT_MIN_TRAINING_EVENTS
    feature_profile_name: str = "weather_only"
    model: RaycasterModel | None = None

    def fit(self, rows: list[FeatureRow]) -> None:
        self.model = train_raycaster_model(
            rows,
            min_training_events=self.min_training_events,
            feature_profile_name=self.feature_profile_name,
        )

    def predict_expected_high(self, rows: list[FeatureRow]) -> list[float]:
        if self.model is None:
            raise ValueError("RaycasterEstimator must be fit before prediction")
        return predict_expected_high(self.model, rows)

    def predict_quantiles(self, rows: list[FeatureRow]) -> list[dict[float, float]]:
        if self.model is None:
            raise ValueError("RaycasterEstimator must be fit before prediction")
        return predict_quantiles(self.model, rows)

    @property
    def mode(self) -> str:
        return self.model.mode if self.model is not None else "unfit"

    @property
    def training_rows(self) -> int:
        return self.model.training_rows if self.model is not None else 0


@dataclass
class RaycasterSourceBlendHybrid:
    name: str = "raycaster_source_blend_hybrid"
    min_training_events: int = DEFAULT_MIN_TRAINING_EVENTS
    feature_profile_name: str = "weather_only"
    raycaster: RaycasterEstimator = field(init=False)
    source_blend: SourceBaseline = field(init=False)

    def __post_init__(self) -> None:
        self.raycaster = RaycasterEstimator(
            min_training_events=self.min_training_events,
            feature_profile_name=self.feature_profile_name,
        )
        self.source_blend = SourceBaseline(name="source_blend")

    def fit(self, rows: list[FeatureRow]) -> None:
        self.raycaster.fit(rows)
        self.source_blend.fit(rows)

    def predict_expected_high(self, rows: list[FeatureRow]) -> list[float]:
        return self.raycaster.predict_expected_high(rows)

    def predict_quantiles(self, rows: list[FeatureRow]) -> list[dict[float, float]]:
        return self.raycaster.predict_quantiles(rows)

    def predict_distribution_expected_high(self, rows: list[FeatureRow]) -> list[float]:
        return self.source_blend.predict_expected_high(rows)

    def predict_distribution_quantiles(self, rows: list[FeatureRow]) -> list[dict[float, float]]:
        return self.source_blend.predict_quantiles(rows)

    @property
    def mode(self) -> str:
        return f"{self.raycaster.mode}+source_blend_distribution"

    @property
    def training_rows(self) -> int:
        return self.raycaster.training_rows


def create_estimator(
    name: str,
    min_training_events: int = DEFAULT_MIN_TRAINING_EVENTS,
) -> TemperatureEstimator:
    if name in RAW_BASELINES:
        return SourceBaseline(name=name, feature_key=RAW_BASELINES[name])
    if name == "source_blend":
        return SourceBaseline(name=name)
    if name == "family_blend":
        return SourceBaseline(name=name, feature_key="__family_blend__")
    if name == "mos_ridge":
        return MosResidualModel(name=name, kind="ridge", min_training_events=min_training_events)
    if name == "mos_elastic_net":
        return MosResidualModel(
            name=name,
            kind="elastic_net",
            min_training_events=min_training_events,
        )
    if name == "raycaster":
        return RaycasterEstimator(min_training_events=min_training_events)
    if name == "raycaster_family_v2":
        return RaycasterEstimator(
            name=name,
            min_training_events=min_training_events,
            feature_profile_name="family_v2",
        )
    if name == "raycaster_city":
        return RaycasterEstimator(
            name=name,
            min_training_events=min_training_events,
            feature_profile_name="city",
        )
    if name == "raycaster_city_checkpoint":
        return RaycasterEstimator(
            name=name,
            min_training_events=min_training_events,
            feature_profile_name="city_checkpoint",
        )
    if name == "raycaster_city_residual":
        return RaycasterEstimator(
            name=name,
            min_training_events=min_training_events,
            feature_profile_name="city_residual",
        )
    if name == "raycaster_source_blend_hybrid":
        return RaycasterSourceBlendHybrid(min_training_events=min_training_events)
    if name == "raycaster_city_residual_hybrid":
        return RaycasterSourceBlendHybrid(
            name=name,
            min_training_events=min_training_events,
            feature_profile_name="city_residual",
        )
    raise ValueError(f"unknown estimator: {name}")


def _linear_pipeline(kind: str) -> Pipeline:
    if kind == "ridge":
        model = Ridge(alpha=5.0)
    elif kind == "elastic_net":
        model = ElasticNet(alpha=0.05, l1_ratio=0.25, max_iter=20000, random_state=17)
    else:
        raise ValueError(f"unknown MOS model kind: {kind}")
    return Pipeline(
        steps=[
            (
                "preprocessor",
                ColumnTransformer(
                    transformers=[
                        (
                            "numeric",
                            Pipeline(
                                steps=[
                                    (
                                        "imputer",
                                        SimpleImputer(
                                            strategy="median",
                                            keep_empty_features=True,
                                        ),
                                    ),
                                    ("scaler", StandardScaler()),
                                ]
                            ),
                            ACTIVE_NUMERIC_FEATURES,
                        ),
                        (
                            "categorical",
                            Pipeline(
                                steps=[
                                    ("imputer", SimpleImputer(strategy="most_frequent")),
                                    (
                                        "encoder",
                                        OneHotEncoder(
                                            handle_unknown="ignore",
                                            sparse_output=False,
                                        ),
                                    ),
                                ]
                            ),
                            ACTIVE_CATEGORICAL_FEATURES,
                        ),
                    ],
                    remainder="drop",
                ),
            ),
            ("model", model),
        ]
    )


def _residuals(rows: list[FeatureRow], predictions: list[float]) -> list[float]:
    values = [
        float(row.settlement_temperature_f) - prediction
        for row, prediction in zip(rows, predictions, strict=False)
        if row.settlement_temperature_f is not None
    ]
    return sorted(values)


def _residual_quantiles(
    expected_high_f: float,
    row: FeatureRow,
    residuals: list[float],
) -> dict[float, float]:
    if not residuals:
        spread = max(2.5, float(row.features.get("source_std_f") or 1.5) * 1.4)
        quantiles = {
            0.05: expected_high_f - 2.0 * spread,
            0.10: expected_high_f - 1.5 * spread,
            0.25: expected_high_f - 0.75 * spread,
            0.50: expected_high_f,
            0.75: expected_high_f + 0.75 * spread,
            0.90: expected_high_f + 1.5 * spread,
            0.95: expected_high_f + 2.0 * spread,
        }
    else:
        quantiles = {
            level: expected_high_f + _sample_quantile(residuals, level) for level in QUANTILE_LEVELS
        }
        quantiles[0.50] = expected_high_f
    observed = _observed(row)
    if observed is not None:
        return {level: max(value, observed) for level, value in quantiles.items()}
    return quantiles


def _sample_quantile(values: list[float], level: float) -> float:
    if len(values) == 1:
        return values[0]
    position = level * (len(values) - 1)
    lower = int(position)
    upper = min(len(values) - 1, lower + 1)
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def _equal_target_date_weights(rows: list[FeatureRow]) -> list[float]:
    counts = Counter(row.target_date for row in rows)
    return [1.0 / counts[row.target_date] for row in rows]


def _respect_observed_floor(value: float, row: FeatureRow) -> float:
    observed = _observed(row)
    return max(value, observed) if observed is not None else value


def _observed(row: FeatureRow) -> float | None:
    observed = _finite_float(row.features.get("settlement_observed_high_so_far_f"))
    return (
        observed
        if observed is not None
        else _finite_float(row.features.get("observed_high_so_far_f"))
    )


def _finite_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
