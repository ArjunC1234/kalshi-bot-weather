from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import evaluate as raycaster_evaluate
from dataset import brackets_for_snapshot, load_local_dataset, markets_by_snapshot
from distribution import bracket_distribution, monotonic_quantiles
from residual_features import MODEL_NAME, ResidualRow, build_residual_rows
from residual_models import ResidualModel, train_residual_model

from libs.metrics import bias, mean_absolute_error, root_mean_squared_error
from libs.models import BacktestDataset, BracketDistribution, TemperaturePrediction
from libs.settlement_policy import (
    POST_SETTLEMENT_SYSTEM_START_DATE,
    post_settlement_target_dates,
)

QUANTILE_Z = {
    0.05: -1.645,
    0.10: -1.282,
    0.25: -0.674,
    0.50: 0.0,
    0.75: 0.674,
    0.90: 1.282,
    0.95: 1.645,
}


def evaluate_rolling_window(
    data_path: str | Path,
    output_dir: str | Path,
    model_kind: str,
    train_days: int,
    test_days: int,
    min_training_rows: int,
    probability_floor: float,
    seed: int,
    min_target_date=None,
    device: str = "auto",
    source_export_id: str | None = None,
) -> dict[str, Any]:
    dataset = load_local_dataset(Path(data_path))
    return evaluate_dataset_rolling_window(
        dataset,
        output_dir,
        model_kind=model_kind,
        train_days=train_days,
        test_days=test_days,
        min_target_date=min_target_date,
        min_training_rows=min_training_rows,
        probability_floor=probability_floor,
        seed=seed,
        device=device,
        source_export_id=source_export_id,
    )


def evaluate_dataset_rolling_window(
    dataset: BacktestDataset,
    output_dir: str | Path,
    model_kind: str,
    train_days: int,
    test_days: int,
    min_training_rows: int,
    probability_floor: float,
    seed: int,
    min_target_date=None,
    device: str = "auto",
    source_export_id: str | None = None,
) -> dict[str, Any]:
    if train_days < 1:
        raise ValueError("train_days must be at least 1")
    if test_days < 1:
        raise ValueError("test_days must be at least 1")
    output = Path(output_dir)
    rows = build_residual_rows(dataset)
    target_dates = post_settlement_target_dates(
        (row.base.target_date for row in rows),
        min_target_date or POST_SETTLEMENT_SYSTEM_START_DATE,
    )
    markets_grouped = markets_by_snapshot(dataset)
    predictions: list[TemperaturePrediction] = []
    distributions: list[BracketDistribution] = []
    diagnostics: list[dict[str, Any]] = []
    residual_rows: list[dict[str, Any]] = []
    fold_summaries: list[dict[str, Any]] = []
    start_index = train_days
    while start_index < len(target_dates):
        train_dates = target_dates[start_index - train_days : start_index]
        test_dates = target_dates[start_index : start_index + test_days]
        train_set = set(train_dates)
        test_set = set(test_dates)
        train_rows = [row for row in rows if row.base.target_date in train_set]
        test_rows = [row for row in rows if row.base.target_date in test_set]
        model = train_residual_model(
            train_rows,
            kind=model_kind,
            min_training_rows=min_training_rows,
            seed=seed + start_index,
            device=device,
        )
        batch_predictions, batch_distributions, batch_residual_rows = _predict_rows(
            test_rows,
            model,
            markets_grouped,
            probability_floor,
        )
        predictions.extend(batch_predictions)
        distributions.extend(batch_distributions)
        residual_rows.extend(batch_residual_rows)
        fold_summaries.append(
            {
                "model_kind": model.kind,
                "train_start_date": train_dates[0].isoformat(),
                "train_end_date": train_dates[-1].isoformat(),
                "test_start_date": test_dates[0].isoformat(),
                "test_end_date": test_dates[-1].isoformat(),
                "training_rows": model.training_rows,
                "fit_rows": model.fit_rows,
                "validation_rows": model.validation_rows,
                "global_sigma": model.global_sigma,
                "city_checkpoint_sigma_count": len(model.group_sigmas),
                "bucket_sigma_count": len(model.bucket_sigmas),
                "independent_train_city_days": len(
                    {(row.base.city, row.base.target_date) for row in train_rows}
                ),
            }
        )
        diagnostics.extend(
            {
                "target_date": row.base.target_date.isoformat(),
                "city": row.base.city,
                "event_ticker": row.base.event_ticker,
                "snapshot_hour_utc": row.base.snapshot_hour_utc.isoformat(),
                "mode": "nws_residual_distribution",
                "model_kind": model.kind,
                "training_rows": model.training_rows,
                "fit_rows": model.fit_rows,
                "validation_rows": model.validation_rows,
                "train_days": train_days,
                "test_days": test_days,
                "train_start_date": train_dates[0].isoformat(),
                "train_end_date": train_dates[-1].isoformat(),
                "test_start_date": test_dates[0].isoformat(),
                "test_end_date": test_dates[-1].isoformat(),
                "nws_anchor_high_f": row.nws_anchor_high_f,
                "target_offset_f": row.target_offset_f,
                "weight": row.weight,
            }
            for row in test_rows
        )
        start_index += test_days
    raycaster_evaluate.MODEL_NAME = MODEL_NAME
    result = raycaster_evaluate.write_evaluation_outputs(
        dataset,
        output,
        predictions,
        distributions,
        diagnostics,
        mode=f"nws_residual_{model_kind}_rolling_window_{train_days}d_train_{test_days}d_test",
        source_export_id=source_export_id,
    )
    _write_dict_rows(output / "residual_errors.csv", residual_rows)
    _write_dict_rows(output / "residual_metrics.csv", _residual_metric_rows(residual_rows))
    _write_dict_rows(output / "residual_by_city.csv", _group_residual_rows(residual_rows, "city"))
    _write_dict_rows(
        output / "residual_by_checkpoint.csv",
        _group_residual_rows(residual_rows, "checkpoint"),
    )
    _update_summary(
        output,
        {
            "model_name": MODEL_NAME,
            "model_family": "nws_residual_distribution",
            "model_kind": model_kind,
            "train_days": train_days,
            "test_days": test_days,
            "min_target_date": (min_target_date or POST_SETTLEMENT_SYSTEM_START_DATE).isoformat(),
            "min_training_rows": min_training_rows,
            "probability_floor": probability_floor,
            "seed": seed,
            "device": device,
            "labeled_target_dates": [value.isoformat() for value in target_dates],
            "independent_city_days": len({(row.base.city, row.base.target_date) for row in rows}),
            "snapshot_rows_with_labels": len(rows),
            "residual_metrics": _residual_metric_rows(residual_rows),
            "folds": fold_summaries,
        },
    )
    return {**result, "output_dir": str(output)}


def _predict_rows(
    rows: list[ResidualRow],
    model: ResidualModel,
    markets_grouped,
    probability_floor: float,
) -> tuple[list[TemperaturePrediction], list[BracketDistribution], list[dict[str, Any]]]:
    predictions: list[TemperaturePrediction] = []
    distributions: list[BracketDistribution] = []
    residual_rows: list[dict[str, Any]] = []
    for row in rows:
        predicted_offset = model.predict_offset(row)
        sigma = model.sigma_for(row)
        expected_high = _respect_observed_floor(row.nws_anchor_high_f + predicted_offset, row)
        quantiles = _quantiles(expected_high, sigma, row)
        predictions.append(
            TemperaturePrediction(
                city=row.base.city,
                event_ticker=row.base.event_ticker,
                snapshot_hour_utc=row.base.snapshot_hour_utc,
                model_name=MODEL_NAME,
                expected_high_f=expected_high,
                quantiles=quantiles,
            )
        )
        brackets = brackets_for_snapshot(
            markets_grouped,
            row.base.city,
            row.base.event_ticker,
            row.base.snapshot_hour_utc,
        )
        if brackets:
            probabilities = _bracket_probabilities(
                row,
                model,
                brackets,
                expected_high,
                quantiles,
                probability_floor,
            )
            distributions.append(
                BracketDistribution(
                    city=row.base.city,
                    event_ticker=row.base.event_ticker,
                    snapshot_hour_utc=row.base.snapshot_hour_utc,
                    model_name=MODEL_NAME,
                    probabilities=probabilities,
                )
            )
        residual_error = predicted_offset - row.target_offset_f
        residual_rows.append(
            {
                "city": row.base.city,
                "checkpoint": str(row.features.get("checkpoint", "")),
                "target_date": row.base.target_date.isoformat(),
                "event_ticker": row.base.event_ticker,
                "snapshot_hour_utc": row.base.snapshot_hour_utc.isoformat(),
                "nws_anchor_high_f": row.nws_anchor_high_f,
                "actual_high_f": row.nws_anchor_high_f + row.target_offset_f,
                "nws_error_f": -row.target_offset_f,
                "target_offset_f": row.target_offset_f,
                "predicted_offset_f": predicted_offset,
                "residual_error_f": residual_error,
                "absolute_residual_error_f": abs(residual_error),
                "predicted_high_f": expected_high,
                "temperature_error_f": expected_high
                - (row.nws_anchor_high_f + row.target_offset_f),
                "sigma_f": sigma,
                "model_kind": model.kind,
            }
        )
    return predictions, distributions, residual_rows


def _bracket_probabilities(
    row: ResidualRow,
    model: ResidualModel,
    brackets,
    expected_high: float,
    quantiles: dict[float, float],
    probability_floor: float,
) -> dict[str, float]:
    direct = model.predict_bracket_probabilities(row)
    if direct is None:
        return bracket_distribution(
            brackets,
            expected_high_f=expected_high,
            quantiles=quantiles,
            observed_high_so_far_f=_observed(row),
            probability_floor=probability_floor,
        )
    raw = {bracket.ticker: direct.get(int(bracket.index), 0.0) for bracket in brackets}
    return _normalize_possible_probabilities(raw, brackets, _observed(row), probability_floor)


def _normalize_possible_probabilities(
    raw: dict[str, float],
    brackets,
    observed: float | None,
    probability_floor: float,
) -> dict[str, float]:
    possible = {bracket.ticker for bracket in brackets if _bracket_possible(bracket, observed)}
    if not possible:
        possible = {bracket.ticker for bracket in brackets}
    constrained = {
        ticker: max(0.0, float(value)) if ticker in possible else 0.0
        for ticker, value in raw.items()
    }
    total = sum(constrained.values())
    if total <= 0:
        constrained = {ticker: 1.0 if ticker in possible else 0.0 for ticker in raw}
        total = sum(constrained.values())
    normalized = {ticker: value / total for ticker, value in constrained.items()}
    if probability_floor <= 0:
        return normalized
    floored = {
        ticker: max(value, probability_floor) if ticker in possible else 0.0
        for ticker, value in normalized.items()
    }
    floor_total = sum(floored.values())
    return {ticker: value / floor_total for ticker, value in floored.items()}


def _bracket_possible(bracket, observed: float | None) -> bool:
    if observed is None or bracket.upper_f is None:
        return True
    rounded_observed = int(observed + 0.5)
    return int(bracket.upper_f) >= rounded_observed


def _quantiles(expected_high_f: float, sigma: float, row: ResidualRow) -> dict[float, float]:
    sigma = max(0.75, min(8.0, sigma))
    raw = {level: expected_high_f + z_score * sigma for level, z_score in QUANTILE_Z.items()}
    observed = _observed(row)
    if observed is not None:
        raw = {level: max(value, observed) for level, value in raw.items()}
    return monotonic_quantiles(raw)


def _respect_observed_floor(value: float, row: ResidualRow) -> float:
    observed = _observed(row)
    return max(value, observed) if observed is not None else value


def _observed(row: ResidualRow) -> float | None:
    value = row.base.features.get("settlement_observed_high_so_far_f")
    if value in (None, ""):
        value = row.base.features.get("observed_high_so_far_f")
    if value in (None, ""):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _residual_metric_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    actual_offsets = [float(row["target_offset_f"]) for row in rows]
    predicted_offsets = [float(row["predicted_offset_f"]) for row in rows]
    nws_predictions = [float(row["nws_anchor_high_f"]) for row in rows]
    actual_highs = [float(row["actual_high_f"]) for row in rows]
    predicted_highs = [float(row["predicted_high_f"]) for row in rows]
    nws_mae = mean_absolute_error(actual_highs, nws_predictions)
    model_mae = mean_absolute_error(actual_highs, predicted_highs)
    return [
        {
            "metric": "offset_mae",
            "value": mean_absolute_error(actual_offsets, predicted_offsets),
            "count": len(rows),
        },
        {
            "metric": "offset_rmse",
            "value": root_mean_squared_error(actual_offsets, predicted_offsets),
            "count": len(rows),
        },
        {
            "metric": "offset_bias",
            "value": bias(actual_offsets, predicted_offsets),
            "count": len(rows),
        },
        {"metric": "nws_baseline_mae", "value": nws_mae, "count": len(rows)},
        {"metric": "model_temperature_mae", "value": model_mae, "count": len(rows)},
        {"metric": "mae_improvement_vs_nws", "value": nws_mae - model_mae, "count": len(rows)},
    ]


def _group_residual_rows(rows: list[dict[str, Any]], group_key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[group_key])].append(row)
    output = []
    for group, group_rows in sorted(grouped.items()):
        for metric in _residual_metric_rows(group_rows):
            output.append({"group": group, **metric})
    return output


def _write_dict_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _update_summary(path: Path, updates: dict[str, Any]) -> None:
    summary_path = path / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    summary.update({"generated_at_utc": datetime.now(UTC).isoformat(), **updates})
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
