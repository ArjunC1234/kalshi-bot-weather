"""Command line interface for the experimental Neuralcaster v1 model."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import pstdev
from typing import Any

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

CURRENT_DIR = Path(__file__).resolve().parent
NEXT_GEN_DIR = CURRENT_DIR.parents[2]
RAYCASTER_DIR = NEXT_GEN_DIR / "maxtemp-engine" / "raycaster" / "v1"
for path in (CURRENT_DIR, RAYCASTER_DIR, NEXT_GEN_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import evaluate as raycaster_evaluate
from dataset import brackets_for_snapshot, load_local_dataset, markets_by_snapshot
from distribution import bracket_distribution, monotonic_quantiles
from features import (
    DEFAULT_FEATURE_PROFILE,
    FEATURE_PROFILES,
    FeatureProfile,
    FeatureRow,
    baseline_prediction,
    build_feature_rows,
    feature_dicts,
    feature_profile,
    rows_with_temperature,
)

from libs.models import BacktestDataset, BracketDistribution, TemperaturePrediction

MODEL_NAME = "neuralcaster_v1"
DEFAULT_TRAIN_DAYS = 5
DEFAULT_TEST_DAYS = 1
DEFAULT_MIN_TRAINING_EVENTS = 60
DEFAULT_HIDDEN_LAYERS = (24, 12)
DEFAULT_ALPHA = 0.25
QUANTILE_Z = {
    0.05: -1.645,
    0.10: -1.282,
    0.25: -0.674,
    0.50: 0.0,
    0.75: 0.674,
    0.90: 1.282,
    0.95: 1.645,
}


@dataclass(frozen=True)
class NeuralcasterModel:
    pipeline: Pipeline | None
    profile: FeatureProfile
    training_rows: int
    residual_std_f: float
    mode: str
    hidden_layers: tuple[int, ...]
    alpha: float


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Experimental neural-network residual model for max-temp markets"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_rolling_eval(subparsers)
    _add_report(subparsers)
    args = parser.parse_args(argv)
    if args.command == "rolling-eval":
        return _rolling_eval(args)
    if args.command == "report":
        return _report(args)
    parser.error(f"unknown command {args.command}")
    return 2


def _add_rolling_eval(subparsers) -> None:
    parser = subparsers.add_parser(
        "rolling-eval",
        help="train on prior target dates and score the next target date",
    )
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--train-days", type=int, default=DEFAULT_TRAIN_DAYS)
    parser.add_argument("--test-days", type=int, default=DEFAULT_TEST_DAYS)
    parser.add_argument("--min-training-events", type=int, default=DEFAULT_MIN_TRAINING_EVENTS)
    parser.add_argument("--probability-floor", type=float, default=0.001)
    parser.add_argument(
        "--feature-profile",
        choices=FEATURE_PROFILES,
        default=DEFAULT_FEATURE_PROFILE,
    )
    parser.add_argument(
        "--hidden-layers",
        default=",".join(str(size) for size in DEFAULT_HIDDEN_LAYERS),
        help="comma-separated MLP hidden layer sizes",
    )
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    parser.add_argument("--random-state", type=int, default=17)


def _add_report(subparsers) -> None:
    parser = subparsers.add_parser("report", help="print a concise evaluation report")
    parser.add_argument("--run", required=True)


def _rolling_eval(args) -> int:
    dataset = load_local_dataset(args.data)
    hidden_layers = _parse_hidden_layers(args.hidden_layers)
    summary = evaluate_rolling_window(
        dataset=dataset,
        output_dir=args.output,
        train_days=args.train_days,
        test_days=args.test_days,
        min_training_events=args.min_training_events,
        probability_floor=args.probability_floor,
        source_export_id=Path(args.data).name,
        feature_profile_name=args.feature_profile,
        hidden_layers=hidden_layers,
        alpha=args.alpha,
        random_state=args.random_state,
    )
    print(
        f"evaluated {MODEL_NAME}: mode={summary['mode']} "
        f"temperature_rows={summary['temperature_rows']} "
        f"bracket_rows={summary['bracket_rows']} output={summary['output_dir']}"
    )
    return 0


def evaluate_rolling_window(
    dataset: BacktestDataset,
    output_dir: str | Path,
    train_days: int,
    test_days: int = DEFAULT_TEST_DAYS,
    min_training_events: int = DEFAULT_MIN_TRAINING_EVENTS,
    probability_floor: float = 0.001,
    source_export_id: str | None = None,
    feature_profile_name: str = DEFAULT_FEATURE_PROFILE,
    hidden_layers: tuple[int, ...] = DEFAULT_HIDDEN_LAYERS,
    alpha: float = DEFAULT_ALPHA,
    random_state: int = 17,
) -> dict[str, Any]:
    if train_days < 1:
        raise ValueError("train_days must be at least 1")
    if test_days < 1:
        raise ValueError("test_days must be at least 1")
    rows = build_feature_rows(dataset)
    labeled_rows = rows_with_temperature(rows)
    target_dates = sorted({row.target_date for row in labeled_rows})
    grouped_markets = markets_by_snapshot(dataset)
    predictions: list[TemperaturePrediction] = []
    distributions: list[BracketDistribution] = []
    diagnostics: list[dict[str, Any]] = []
    start_index = train_days
    while start_index < len(target_dates):
        train_dates = target_dates[start_index - train_days : start_index]
        test_dates = target_dates[start_index : start_index + test_days]
        train_set = set(train_dates)
        test_set = set(test_dates)
        train_rows = [row for row in labeled_rows if row.target_date in train_set]
        test_rows = [row for row in rows if row.target_date in test_set]
        model = train_neuralcaster_model(
            train_rows,
            min_training_events=min_training_events,
            feature_profile_name=feature_profile_name,
            hidden_layers=hidden_layers,
            alpha=alpha,
            random_state=random_state,
        )
        batch_predictions, batch_distributions = _predict_rows(
            test_rows,
            model,
            grouped_markets,
            probability_floor,
        )
        predictions.extend(batch_predictions)
        distributions.extend(batch_distributions)
        diagnostics.extend(
            {
                "target_date": row.target_date.isoformat(),
                "city": row.city,
                "event_ticker": row.event_ticker,
                "snapshot_hour_utc": row.snapshot_hour_utc.isoformat(),
                "mode": model.mode,
                "training_rows": model.training_rows,
                "feature_profile": model.profile.name,
                "train_days": train_days,
                "test_days": test_days,
                "train_start_date": train_dates[0].isoformat() if train_dates else "",
                "train_end_date": train_dates[-1].isoformat() if train_dates else "",
                "test_start_date": test_dates[0].isoformat() if test_dates else "",
                "test_end_date": test_dates[-1].isoformat() if test_dates else "",
                "hidden_layers": ",".join(str(size) for size in model.hidden_layers),
                "alpha": model.alpha,
                "residual_std_f": model.residual_std_f,
            }
            for row in test_rows
        )
        start_index += test_days
    raycaster_evaluate.MODEL_NAME = MODEL_NAME
    result = raycaster_evaluate.write_evaluation_outputs(
        dataset,
        output_dir,
        predictions,
        distributions,
        diagnostics,
        mode=f"rolling_window_{train_days}d_train_{test_days}d_test",
        source_export_id=source_export_id,
    )
    _update_summary(
        Path(output_dir),
        {
            "model_name": MODEL_NAME,
            "model_family": "sklearn_mlp_regressor",
            "feature_profile": feature_profile_name,
            "train_days": train_days,
            "test_days": test_days,
            "min_training_events": min_training_events,
            "hidden_layers": list(hidden_layers),
            "alpha": alpha,
            "random_state": random_state,
            "labeled_target_dates": [value.isoformat() for value in target_dates],
            "independent_city_days": len({(row.city, row.target_date) for row in labeled_rows}),
            "snapshot_rows_with_labels": len(labeled_rows),
        },
    )
    return result


def train_neuralcaster_model(
    rows: list[FeatureRow],
    min_training_events: int = DEFAULT_MIN_TRAINING_EVENTS,
    feature_profile_name: str = DEFAULT_FEATURE_PROFILE,
    hidden_layers: tuple[int, ...] = DEFAULT_HIDDEN_LAYERS,
    alpha: float = DEFAULT_ALPHA,
    random_state: int = 17,
) -> NeuralcasterModel:
    profile = feature_profile(feature_profile_name)
    training_rows = rows_with_temperature(rows)
    residuals = [_residual_target(row, profile.name) for row in training_rows]
    residual_std = max(1.75, pstdev(residuals) if len(residuals) >= 2 else 2.5)
    if len(training_rows) < min_training_events:
        return NeuralcasterModel(
            pipeline=None,
            profile=profile,
            training_rows=len(training_rows),
            residual_std_f=residual_std,
            mode="fallback_source_blend",
            hidden_layers=hidden_layers,
            alpha=alpha,
        )
    x = pd.DataFrame(feature_dicts(training_rows))[profile.feature_columns]
    pipeline = _pipeline(profile, hidden_layers, alpha, random_state)
    pipeline.fit(x, residuals)
    return NeuralcasterModel(
        pipeline=pipeline,
        profile=profile,
        training_rows=len(training_rows),
        residual_std_f=residual_std,
        mode="trained_mlp_residual",
        hidden_layers=hidden_layers,
        alpha=alpha,
    )


def _pipeline(
    profile: FeatureProfile,
    hidden_layers: tuple[int, ...],
    alpha: float,
    random_state: int,
) -> Pipeline:
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
    model = MLPRegressor(
        hidden_layer_sizes=hidden_layers,
        activation="relu",
        solver="adam",
        alpha=alpha,
        learning_rate_init=0.003,
        max_iter=1000,
        early_stopping=True,
        validation_fraction=0.2,
        n_iter_no_change=30,
        random_state=random_state,
    )
    return Pipeline(steps=[("preprocessor", preprocessor), ("model", model)])


def _predict_rows(
    rows: list[FeatureRow],
    model: NeuralcasterModel,
    grouped_markets,
    probability_floor: float,
) -> tuple[list[TemperaturePrediction], list[BracketDistribution]]:
    expected_values = predict_expected_high(model, rows)
    predictions: list[TemperaturePrediction] = []
    distributions: list[BracketDistribution] = []
    for row, expected in zip(rows, expected_values, strict=True):
        quantiles = _quantiles(expected, row, model)
        predictions.append(
            TemperaturePrediction(
                city=row.city,
                event_ticker=row.event_ticker,
                snapshot_hour_utc=row.snapshot_hour_utc,
                model_name=MODEL_NAME,
                expected_high_f=expected,
                quantiles=quantiles,
            )
        )
        brackets = brackets_for_snapshot(
            grouped_markets,
            row.city,
            row.event_ticker,
            row.snapshot_hour_utc,
        )
        if brackets:
            probabilities = bracket_distribution(
                brackets,
                expected_high_f=expected,
                quantiles=quantiles,
                observed_high_so_far_f=_observed(row),
                probability_floor=probability_floor,
            )
            distributions.append(
                BracketDistribution(
                    city=row.city,
                    event_ticker=row.event_ticker,
                    snapshot_hour_utc=row.snapshot_hour_utc,
                    model_name=MODEL_NAME,
                    probabilities=probabilities,
                )
            )
    return predictions, distributions


def predict_expected_high(model: NeuralcasterModel, rows: list[FeatureRow]) -> list[float]:
    if model.pipeline is None:
        return [
            _respect_observed_floor(baseline_prediction(row, model.profile.name), row)
            for row in rows
        ]
    x = pd.DataFrame(feature_dicts(rows))[model.profile.feature_columns]
    residuals = [float(value) for value in model.pipeline.predict(x)]
    return [
        _respect_observed_floor(baseline_prediction(row, model.profile.name) + residual, row)
        for row, residual in zip(rows, residuals, strict=True)
    ]


def _quantiles(
    expected_high_f: float,
    row: FeatureRow,
    model: NeuralcasterModel,
) -> dict[float, float]:
    source_std = _finite_float(row.features.get("source_std_f")) or 0.0
    source_range = _finite_float(row.features.get("source_range_f")) or 0.0
    spread = max(1.75, model.residual_std_f, source_std * 1.15, source_range * 0.35)
    observed = _observed(row)
    raw = {level: expected_high_f + z_score * spread for level, z_score in QUANTILE_Z.items()}
    if observed is not None:
        raw = {level: max(value, observed - 0.75) for level, value in raw.items()}
    return monotonic_quantiles(raw)


def _residual_target(row: FeatureRow, feature_profile_name: str) -> float:
    if row.settlement_temperature_f is None:
        raise ValueError("residual target requires a final temperature")
    return float(row.settlement_temperature_f) - baseline_prediction(row, feature_profile_name)


def _respect_observed_floor(value: float, row: FeatureRow) -> float:
    observed = _observed(row)
    return max(value, observed) if observed is not None else value


def _observed(row: FeatureRow) -> float | None:
    value = row.features.get("observed_high_so_far_f")
    return _finite_float(value)


def _finite_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_hidden_layers(value: str) -> tuple[int, ...]:
    sizes = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    if not sizes or any(size < 1 for size in sizes):
        raise argparse.ArgumentTypeError("hidden layers must be positive integers")
    return sizes


def _update_summary(output_dir: Path, payload: dict[str, Any]) -> None:
    summary_path = output_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.update(payload)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _report(args) -> int:
    summary_path = Path(args.run) / "summary.json"
    if not summary_path.exists():
        raise SystemExit(f"missing summary.json in {args.run}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    print(f"model: {summary.get('model_name')}")
    print(f"mode: {summary.get('mode')}")
    print(f"source_export_id: {summary.get('source_export_id')}")
    print(f"independent_city_days: {summary.get('independent_city_days')}")
    for section in ("temperature_metrics", "bracket_metrics"):
        print(section)
        for row in summary.get(section, []):
            print(f"  {row['metric']}: {float(row['value']):.4f} n={row['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
