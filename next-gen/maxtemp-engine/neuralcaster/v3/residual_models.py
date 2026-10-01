from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import torch
from residual_features import CATEGORICAL_FEATURES, NUMERIC_FEATURES, ResidualRow
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import HuberRegressor, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from torch import nn

MODEL_KINDS = (
    "nws_baseline",
    "source_blend",
    "market_baseline",
    "ridge_residual",
    "huber_residual",
    "tree_residual",
    "mlp_residual",
    "gru_residual",
    "robust_ensemble",
    "bracket_classifier",
)


def resolve_torch_device(requested: str) -> torch.device:
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(requested)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA device requested, but this PyTorch install cannot see CUDA")
    return device


@dataclass(frozen=True)
class ResidualModel:
    kind: str
    estimator: Any
    training_rows: int
    fit_rows: int
    validation_rows: int
    global_sigma: float
    group_sigmas: dict[tuple[str, str], float]
    bucket_sigmas: dict[tuple[str, str, str, str], float]
    fallback_offset: float

    def predict_offset(self, row: ResidualRow) -> float:
        if self.kind == "bracket_classifier":
            return row.source_blend_offset_f
        if self.kind == "nws_baseline":
            return 0.0
        if self.kind == "source_blend":
            return row.source_blend_offset_f
        if self.kind == "market_baseline":
            return (
                row.market_expected_offset_f
                if row.market_expected_offset_f is not None
                else self.fallback_offset
            )
        if isinstance(self.estimator, TorchResidualRegressor | TorchSequenceResidualRegressor):
            return float(self.estimator.predict_rows([row])[0])
        if isinstance(self.estimator, RobustResidualEnsemble):
            return float(self.estimator.predict_rows([row])[0])
        return float(self.estimator.predict(_matrix([row]))[0])

    def predict_bracket_probabilities(self, row: ResidualRow) -> dict[int, float] | None:
        if self.kind != "bracket_classifier" or self.estimator is None:
            return None
        probabilities = self.estimator.predict_proba(_matrix([row]))[0]
        classes = list(_estimator_classes(self.estimator))
        raw = {
            int(class_index): max(0.0, float(probability))
            for class_index, probability in zip(classes, probabilities, strict=True)
        }
        total = sum(raw.values())
        return {index: value / total for index, value in raw.items()} if total > 0 else None

    def sigma_for(self, row: ResidualRow) -> float:
        bucket_key = _sigma_bucket_key(row)
        if bucket_key in self.bucket_sigmas:
            return self.bucket_sigmas[bucket_key]
        key = (row.base.city, str(row.features.get("checkpoint", "")))
        return self.group_sigmas.get(key, self.global_sigma)


def train_residual_model(
    rows: list[ResidualRow],
    kind: str,
    min_training_rows: int,
    seed: int,
    device: str | torch.device = "auto",
) -> ResidualModel:
    if kind not in MODEL_KINDS:
        raise ValueError(f"unknown residual model kind {kind!r}")
    if kind == "bracket_classifier":
        rows = [row for row in rows if row.base.settlement_bracket_index is not None]
    if not rows:
        return _empty_model(kind)
    target_dates = sorted({row.base.target_date for row in rows})
    validation_date = target_dates[-1] if len(target_dates) >= 3 else None
    fit_rows = (
        [row for row in rows if row.base.target_date != validation_date]
        if validation_date is not None
        else rows
    )
    validation_rows = (
        [row for row in rows if row.base.target_date == validation_date]
        if validation_date is not None
        else rows
    )
    fallback_offset = _weighted_mean(
        [row.target_offset_f for row in rows],
        [row.weight for row in rows],
    )
    torch_device = (
        resolve_torch_device(str(device)) if not isinstance(device, torch.device) else device
    )
    estimator = None
    trainable = kind not in ("nws_baseline", "source_blend", "market_baseline")
    if trainable and len(rows) >= min_training_rows:
        estimator = _fit_estimator(fit_rows, kind, seed, torch_device)
        sigma_model = ResidualModel(
            kind=kind,
            estimator=estimator,
            training_rows=len(rows),
            fit_rows=len(fit_rows),
            validation_rows=len(validation_rows),
            global_sigma=3.0,
            group_sigmas={},
            bucket_sigmas={},
            fallback_offset=fallback_offset,
        )
        residuals = [
            row.target_offset_f - sigma_model.predict_offset(row) for row in validation_rows
        ]
        final_estimator = _fit_estimator(rows, kind, seed, torch_device)
    else:
        sigma_model = ResidualModel(
            kind=kind,
            estimator=None,
            training_rows=len(rows),
            fit_rows=len(fit_rows),
            validation_rows=len(validation_rows),
            global_sigma=3.0,
            group_sigmas={},
            bucket_sigmas={},
            fallback_offset=fallback_offset,
        )
        residuals = [
            row.target_offset_f - sigma_model.predict_offset(row) for row in validation_rows
        ]
        final_estimator = None
    global_sigma, group_sigmas, bucket_sigmas = _estimate_sigmas(validation_rows, residuals)
    return ResidualModel(
        kind=kind,
        estimator=final_estimator,
        training_rows=len(rows),
        fit_rows=len(fit_rows),
        validation_rows=len(validation_rows),
        global_sigma=global_sigma,
        group_sigmas=group_sigmas,
        bucket_sigmas=bucket_sigmas,
        fallback_offset=fallback_offset,
    )


def _fit_estimator(rows: list[ResidualRow], kind: str, seed: int, device: torch.device) -> Any:
    if kind == "robust_ensemble":
        model = RobustResidualEnsemble(seed=seed, device=device)
        model.fit(rows)
        return model
    if kind == "mlp_residual":
        model = TorchResidualRegressor(seed=seed, device=device)
        model.fit(rows)
        return model
    if kind == "gru_residual":
        model = TorchSequenceResidualRegressor(seed=seed, device=device)
        model.fit(rows)
        return model
    if kind == "bracket_classifier":
        classes = sorted(set(_bracket_target(rows).tolist()))
        if len(classes) == 1:
            return SingleClassBracketClassifier(classes[0])
        classifier = HistGradientBoostingClassifier(
            max_iter=160,
            learning_rate=0.04,
            max_leaf_nodes=8,
            l2_regularization=0.1,
            random_state=seed,
        )
        pipeline = Pipeline([("preprocessor", _preprocessor()), ("classifier", classifier)])
        pipeline.fit(_matrix(rows), _bracket_target(rows))
        return pipeline
    preprocessor = _preprocessor()
    if kind == "ridge_residual":
        regressor = Ridge(alpha=2.0, random_state=seed)
    elif kind == "huber_residual":
        regressor = HuberRegressor(alpha=0.02, epsilon=1.35, max_iter=500)
    elif kind == "tree_residual":
        regressor = HistGradientBoostingRegressor(
            max_iter=120,
            learning_rate=0.05,
            max_leaf_nodes=8,
            l2_regularization=0.1,
            random_state=seed,
        )
    else:
        raise ValueError(f"unsupported estimator kind {kind!r}")
    pipeline = Pipeline([("preprocessor", preprocessor), ("regressor", regressor)])
    fit_kwargs = {"regressor__sample_weight": np.array([row.weight for row in rows])}
    pipeline.fit(_matrix(rows), _target(rows), **fit_kwargs)
    return pipeline


def _preprocessor() -> ColumnTransformer:
    numeric = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
        ]
    )
    categorical = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    return ColumnTransformer(
        [
            ("numeric", numeric, NUMERIC_FEATURES),
            ("categorical", categorical, CATEGORICAL_FEATURES),
        ]
    )


class TorchResidualRegressor:
    def __init__(
        self,
        seed: int,
        epochs: int = 200,
        patience: int = 20,
        device: torch.device | None = None,
    ) -> None:
        self.seed = seed
        self.epochs = epochs
        self.patience = patience
        self.device = device or torch.device("cpu")
        self.preprocessor = _preprocessor()
        self.model: _ResidualMlp | None = None

    def fit(self, rows: list[ResidualRow]) -> None:
        _seed_everything(self.seed)
        x = self.preprocessor.fit_transform(_matrix(rows))
        x_array = x.toarray() if hasattr(x, "toarray") else np.asarray(x)
        y = _target(rows)
        weights = np.array([row.weight for row in rows], dtype=np.float32)
        model = _ResidualMlp(x_array.shape[1]).to(self.device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.003, weight_decay=0.02)
        x_tensor = torch.tensor(x_array, dtype=torch.float32, device=self.device)
        y_tensor = torch.tensor(y, dtype=torch.float32, device=self.device)
        weight_tensor = torch.tensor(
            weights / max(1e-9, weights.sum()),
            dtype=torch.float32,
            device=self.device,
        )
        best_state = None
        best_loss = float("inf")
        stale = 0
        for _ in range(self.epochs):
            optimizer.zero_grad()
            prediction = model(x_tensor)
            loss = ((prediction - y_tensor).square() * weight_tensor).sum()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 2.0)
            optimizer.step()
            value = float(loss.detach())
            if value + 1e-6 < best_loss:
                best_loss = value
                best_state = {
                    key: value.detach().cpu().clone() for key, value in model.state_dict().items()
                }
                stale = 0
            else:
                stale += 1
            if stale >= self.patience:
                break
        if best_state is not None:
            model.load_state_dict(best_state)
        self.model = model

    def predict_rows(self, rows: list[ResidualRow]) -> np.ndarray:
        if self.model is None:
            raise ValueError("MLP residual model has not been fit")
        x = self.preprocessor.transform(_matrix(rows))
        x_array = x.toarray() if hasattr(x, "toarray") else np.asarray(x)
        self.model.to(self.device)
        self.model.eval()
        with torch.no_grad():
            x_tensor = torch.tensor(x_array, dtype=torch.float32, device=self.device)
            return self.model(x_tensor).cpu().numpy()


class SingleClassBracketClassifier:
    def __init__(self, class_index: int) -> None:
        self.classes_ = np.array([class_index], dtype=np.int64)

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return np.ones((len(frame), 1), dtype=np.float32)


class RobustResidualEnsemble:
    """Small-data ensemble that favors stable residual models over high-capacity nets."""

    def __init__(self, seed: int, device: torch.device) -> None:
        self.seed = seed
        self.device = device
        self.weights: dict[str, float] = {}
        self.models: dict[str, Any] = {}

    def fit(self, rows: list[ResidualRow]) -> None:
        target_dates = sorted({row.base.target_date for row in rows})
        validation_dates = (
            set(target_dates[-2:]) if len(target_dates) >= 6 else set(target_dates[-1:])
        )
        fit_rows = [row for row in rows if row.base.target_date not in validation_dates] or rows
        validation_rows = [row for row in rows if row.base.target_date in validation_dates] or rows
        candidate_models = {
            "huber_residual": _fit_estimator(fit_rows, "huber_residual", self.seed, self.device),
            "ridge_residual": _fit_estimator(
                fit_rows,
                "ridge_residual",
                self.seed + 11,
                self.device,
            ),
        }
        losses = {
            "market_baseline": _weighted_mae(
                [
                    _market_or_source_offset(row)
                    for row in validation_rows
                ],
                [row.target_offset_f for row in validation_rows],
                [row.weight for row in validation_rows],
            ),
            "source_blend": _weighted_mae(
                [row.source_blend_offset_f for row in validation_rows],
                [row.target_offset_f for row in validation_rows],
                [row.weight for row in validation_rows],
            ),
        }
        for name, estimator in candidate_models.items():
            losses[name] = _weighted_mae(
                [float(estimator.predict(_matrix([row]))[0]) for row in validation_rows],
                [row.target_offset_f for row in validation_rows],
                [row.weight for row in validation_rows],
            )
        self.weights = _soft_inverse_error_weights(losses)
        self.models = {
            "huber_residual": _fit_estimator(rows, "huber_residual", self.seed, self.device),
            "ridge_residual": _fit_estimator(rows, "ridge_residual", self.seed + 11, self.device),
        }

    def predict_rows(self, rows: list[ResidualRow]) -> np.ndarray:
        predictions = []
        for row in rows:
            value = 0.0
            value += self.weights.get("market_baseline", 0.0) * _market_or_source_offset(row)
            value += self.weights.get("source_blend", 0.0) * row.source_blend_offset_f
            for name, estimator in self.models.items():
                value += self.weights.get(name, 0.0) * float(estimator.predict(_matrix([row]))[0])
            predictions.append(value)
        return np.array(predictions, dtype=np.float32)


class _ResidualMlp(nn.Module):
    def __init__(self, input_dim: int) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_dim, 24),
            nn.ReLU(),
            nn.Dropout(0.15),
            nn.Linear(24, 12),
            nn.ReLU(),
            nn.Linear(12, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layers(x).squeeze(-1)


class TorchSequenceResidualRegressor:
    def __init__(
        self,
        seed: int,
        max_seq_len: int = 24,
        epochs: int = 80,
        patience: int = 10,
        device: torch.device | None = None,
    ) -> None:
        self.seed = seed
        self.max_seq_len = max_seq_len
        self.epochs = epochs
        self.patience = patience
        self.device = device or torch.device("cpu")
        self.numeric_normalizer = NumericSequenceNormalizer()
        self.city_index: dict[str, int] = {}
        self.checkpoint_index: dict[str, int] = {}
        self.rows_by_event: dict[tuple[str, str], list[ResidualRow]] = {}
        self.model: _ResidualGru | None = None

    def fit(self, rows: list[ResidualRow]) -> None:
        _seed_everything(self.seed)
        self.rows_by_event = _rows_by_event(rows)
        self.city_index = _vocabulary([row.base.city for row in rows])
        self.checkpoint_index = _vocabulary(
            [str(row.features.get("checkpoint", "")) for row in rows]
        )
        self.numeric_normalizer.fit(rows)
        sequence, lengths, static = self._tensors(rows)
        target = torch.tensor(_target(rows), dtype=torch.float32, device=self.device)
        weights = torch.tensor(
            np.array([row.weight for row in rows], dtype=np.float32),
            dtype=torch.float32,
            device=self.device,
        )
        weights = weights / torch.clamp(weights.sum(), min=1e-9)
        model = _ResidualGru(
            sequence_dim=sequence.shape[-1],
            static_dim=static.shape[-1],
            hidden_size=32,
        ).to(self.device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.002, weight_decay=0.03)
        best_state = None
        best_loss = float("inf")
        stale = 0
        for _ in range(self.epochs):
            model.train()
            optimizer.zero_grad()
            prediction = model(sequence, lengths, static)
            loss = ((prediction - target).square() * weights).sum()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 2.0)
            optimizer.step()
            value = float(loss.detach())
            if value + 1e-6 < best_loss:
                best_loss = value
                best_state = {
                    key: value.detach().cpu().clone() for key, value in model.state_dict().items()
                }
                stale = 0
            else:
                stale += 1
            if stale >= self.patience:
                break
        if best_state is not None:
            model.load_state_dict(best_state)
        self.model = model

    def predict_rows(self, rows: list[ResidualRow]) -> np.ndarray:
        if self.model is None:
            raise ValueError("GRU residual model has not been fit")
        sequence, lengths, static = self._tensors(rows)
        self.model.to(self.device)
        self.model.eval()
        with torch.no_grad():
            return self.model(sequence, lengths, static).cpu().numpy()

    def _tensors(self, rows: list[ResidualRow]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        sequence_dim = len(NUMERIC_FEATURES) * 2
        static_dim = len(self.city_index) + len(self.checkpoint_index) + 3
        sequence = np.zeros((len(rows), self.max_seq_len, sequence_dim), dtype=np.float32)
        lengths = np.ones(len(rows), dtype=np.int64)
        static = np.zeros((len(rows), static_dim), dtype=np.float32)
        for row_index, row in enumerate(rows):
            history = self._history(row)
            lengths[row_index] = max(1, len(history))
            start = self.max_seq_len - len(history)
            for seq_index, history_row in enumerate(history, start=start):
                for feature_index, name in enumerate(NUMERIC_FEATURES):
                    normalized, present = self.numeric_normalizer.transform(
                        name,
                        history_row.features.get(name),
                    )
                    sequence[row_index, seq_index, feature_index] = normalized
                    sequence[row_index, seq_index, feature_index + len(NUMERIC_FEATURES)] = present
            offset = 0
            city = self.city_index.get(row.base.city)
            if city is not None:
                static[row_index, offset + city] = 1.0
            offset += len(self.city_index)
            checkpoint = self.checkpoint_index.get(str(row.features.get("checkpoint", "")))
            if checkpoint is not None:
                static[row_index, offset + checkpoint] = 1.0
            offset += len(self.checkpoint_index)
            static[row_index, offset] = float(row.base.target_date.timetuple().tm_yday) / 366.0
            static[row_index, offset + 1] = float(row.base.snapshot_hour_utc.hour) / 23.0
            static[row_index, offset + 2] = float(row.features.get("hours_elapsed") or 0.0) / 24.0
        return (
            torch.tensor(sequence, dtype=torch.float32, device=self.device),
            torch.tensor(lengths, dtype=torch.int64, device=self.device),
            torch.tensor(static, dtype=torch.float32, device=self.device),
        )

    def _history(self, row: ResidualRow) -> list[ResidualRow]:
        rows = self.rows_by_event.get((row.base.city, row.base.event_ticker), [row])
        eligible = [
            candidate
            for candidate in rows
            if candidate.base.snapshot_hour_utc <= row.base.snapshot_hour_utc
        ]
        return eligible[-self.max_seq_len :] or [row]


class NumericSequenceNormalizer:
    def __init__(self) -> None:
        self.means: dict[str, float] = {}
        self.stds: dict[str, float] = {}

    def fit(self, rows: list[ResidualRow]) -> None:
        for name in NUMERIC_FEATURES:
            values = [_finite_float(row.features.get(name)) for row in rows]
            cleaned = [value for value in values if value is not None]
            if not cleaned:
                self.means[name] = 0.0
                self.stds[name] = 1.0
                continue
            mean_value = float(np.mean(cleaned))
            std_value = float(np.std(cleaned))
            self.means[name] = mean_value
            self.stds[name] = max(1e-6, std_value)

    def transform(self, name: str, value: Any) -> tuple[float, float]:
        parsed = _finite_float(value)
        if parsed is None:
            return 0.0, 0.0
        return (parsed - self.means.get(name, 0.0)) / self.stds.get(name, 1.0), 1.0


class _ResidualGru(nn.Module):
    def __init__(self, sequence_dim: int, static_dim: int, hidden_size: int) -> None:
        super().__init__()
        self.gru = nn.GRU(
            input_size=sequence_dim,
            hidden_size=hidden_size,
            num_layers=1,
            batch_first=True,
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_size + static_dim, 32),
            nn.ReLU(),
            nn.Dropout(0.20),
            nn.Linear(32, 1),
        )

    def forward(
        self,
        sequence: torch.Tensor,
        lengths: torch.Tensor,
        static: torch.Tensor,
    ) -> torch.Tensor:
        packed = nn.utils.rnn.pack_padded_sequence(
            sequence,
            lengths.cpu(),
            batch_first=True,
            enforce_sorted=False,
        )
        _, hidden = self.gru(packed)
        combined = torch.cat([hidden[-1], static], dim=1)
        return self.head(combined).squeeze(-1)


def _estimate_sigmas(
    validation_rows: list[ResidualRow],
    residuals: list[float],
    prior_count: int = 10,
    min_sigma: float = 0.75,
    max_sigma: float = 8.0,
) -> tuple[
    float,
    dict[tuple[str, str], float],
    dict[tuple[str, str, str, str], float],
]:
    if not residuals:
        return 3.0, {}, {}
    global_sigma = _clamp_sigma(_rmse(residuals), min_sigma, max_sigma)
    grouped: dict[tuple[str, str], list[float]] = {}
    bucketed: dict[tuple[str, str, str, str], list[float]] = {}
    for row, residual in zip(validation_rows, residuals, strict=True):
        key = (row.base.city, str(row.features.get("checkpoint", "")))
        grouped.setdefault(key, []).append(residual)
        bucketed.setdefault(_sigma_bucket_key(row), []).append(residual)
    group_sigmas = {}
    for key, values in grouped.items():
        local_variance = _rmse(values) ** 2
        global_variance = global_sigma**2
        shrunk = math.sqrt(
            (len(values) * local_variance + prior_count * global_variance)
            / (len(values) + prior_count)
        )
        group_sigmas[key] = _clamp_sigma(shrunk, min_sigma, max_sigma)
    bucket_sigmas = {}
    for key, values in bucketed.items():
        if len(values) < 3:
            continue
        local_variance = _rmse(values) ** 2
        global_variance = global_sigma**2
        shrunk = math.sqrt(
            (len(values) * local_variance + prior_count * global_variance)
            / (len(values) + prior_count)
        )
        bucket_sigmas[key] = _clamp_sigma(shrunk, min_sigma, max_sigma)
    return global_sigma, group_sigmas, bucket_sigmas


def _matrix(rows: list[ResidualRow]) -> pd.DataFrame:
    records = [
        {column: row.features.get(column) for column in NUMERIC_FEATURES + CATEGORICAL_FEATURES}
        for row in rows
    ]
    return pd.DataFrame.from_records(records, columns=NUMERIC_FEATURES + CATEGORICAL_FEATURES)


def _target(rows: list[ResidualRow]) -> np.ndarray:
    return np.array([row.target_offset_f for row in rows], dtype=np.float32)


def _bracket_target(rows: list[ResidualRow]) -> np.ndarray:
    return np.array(
        [
            int(row.base.settlement_bracket_index)
            if row.base.settlement_bracket_index is not None
            else 0
            for row in rows
        ],
        dtype=np.int64,
    )


def _estimator_classes(estimator: Any) -> np.ndarray:
    if hasattr(estimator, "classes_"):
        return estimator.classes_
    if hasattr(estimator, "named_steps"):
        for step in reversed(list(estimator.named_steps.values())):
            if hasattr(step, "classes_"):
                return step.classes_
    return np.array([], dtype=np.int64)


def _rows_by_event(rows: list[ResidualRow]) -> dict[tuple[str, str], list[ResidualRow]]:
    grouped: dict[tuple[str, str], list[ResidualRow]] = {}
    for row in rows:
        grouped.setdefault((row.base.city, row.base.event_ticker), []).append(row)
    for values in grouped.values():
        values.sort(key=lambda row: row.base.snapshot_hour_utc)
    return grouped


def _vocabulary(values: list[str]) -> dict[str, int]:
    return {value: index for index, value in enumerate(sorted(set(values)))}


def _finite_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    total = sum(weights)
    weighted_sum = sum(value * weight for value, weight in zip(values, weights, strict=True))
    return weighted_sum / max(total, 1e-9)


def _weighted_mae(predicted: list[float], actual: list[float], weights: list[float]) -> float:
    total = sum(weights)
    error = sum(
        abs(prediction - target) * weight
        for prediction, target, weight in zip(predicted, actual, weights, strict=True)
    )
    return error / max(total, 1e-9)


def _soft_inverse_error_weights(losses: dict[str, float]) -> dict[str, float]:
    # Shrink toward equal weighting so one lucky validation day cannot dominate.
    raw = {name: 1.0 / max(0.25, value) for name, value in losses.items()}
    total = sum(raw.values()) or 1.0
    equal = 1.0 / max(1, len(raw))
    return {
        name: 0.65 * (value / total) + 0.35 * equal
        for name, value in raw.items()
    }


def _market_or_source_offset(row: ResidualRow) -> float:
    return (
        row.market_expected_offset_f
        if row.market_expected_offset_f is not None
        else row.source_blend_offset_f
    )


def _rmse(values: list[float]) -> float:
    return math.sqrt(sum(value * value for value in values) / max(1, len(values)))


def _clamp_sigma(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def _empty_model(kind: str) -> ResidualModel:
    return ResidualModel(
        kind=kind,
        estimator=None,
        training_rows=0,
        fit_rows=0,
        validation_rows=0,
        global_sigma=3.0,
        group_sigmas={},
        bucket_sigmas={},
        fallback_offset=0.0,
    )


def _sigma_bucket_key(row: ResidualRow) -> tuple[str, str, str, str]:
    return (
        _checkpoint_bucket(row),
        _hours_remaining_bucket(row.features.get("hours_remaining")),
        _source_uncertainty_bucket(row.features.get("source_std_f")),
        _market_disagreement_bucket(row.features.get("market_expected_minus_nws")),
    )


def _checkpoint_bucket(row: ResidualRow) -> str:
    hours_elapsed = _finite_float(row.features.get("hours_elapsed"))
    if hours_elapsed is None:
        return "checkpoint_unknown"
    if hours_elapsed < 6:
        return "early"
    if hours_elapsed < 14:
        return "mid"
    return "late"


def _hours_remaining_bucket(value: Any) -> str:
    parsed = _finite_float(value)
    if parsed is None:
        return "remaining_unknown"
    if parsed <= 4:
        return "remaining_low"
    if parsed <= 12:
        return "remaining_mid"
    return "remaining_high"


def _source_uncertainty_bucket(value: Any) -> str:
    parsed = _finite_float(value)
    if parsed is None:
        return "source_unknown"
    if parsed < 1.0:
        return "source_low"
    if parsed < 2.5:
        return "source_mid"
    return "source_high"


def _market_disagreement_bucket(value: Any) -> str:
    parsed = _finite_float(value)
    if parsed is None:
        return "market_unknown"
    magnitude = abs(parsed)
    if magnitude < 1.0:
        return "market_near_nws"
    if magnitude < 3.0:
        return "market_mid_disagree"
    return "market_far_disagree"


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
