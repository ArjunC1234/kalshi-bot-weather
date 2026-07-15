"""Expanding-window and fixed-artifact evaluation for Raycaster v1."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from dataset import brackets_for_snapshot, markets_by_snapshot
from distribution import bracket_distribution, monotonic_quantiles
from features import MODEL_NAME, FeatureRow, build_feature_rows
from predict import predict_dataset
from train import (
    DEFAULT_MIN_TRAINING_EVENTS,
    RaycasterModel,
    predict_expected_high,
    predict_quantiles,
    train_raycaster_model,
)

from libs.metrics import (
    bias,
    log_loss,
    mean_absolute_error,
    multiclass_brier,
    ranked_probability_score,
    root_mean_squared_error,
    top_one_accuracy,
    within_one_bracket_accuracy,
)
from libs.models import BacktestDataset, BracketDistribution, TemperaturePrediction, to_dict
from libs.probabilities import bracket_for_temperature, top_ticker


def evaluate_expanding_window(
    dataset: BacktestDataset,
    output_dir: str | Path,
    min_training_events: int = DEFAULT_MIN_TRAINING_EVENTS,
    probability_floor: float = 0.001,
    source_export_id: str | None = None,
    feature_profile_name: str = "weather_only",
) -> dict[str, Any]:
    rows = build_feature_rows(dataset)
    grouped_markets = markets_by_snapshot(dataset)
    predictions: list[TemperaturePrediction] = []
    distributions: list[BracketDistribution] = []
    diagnostics: list[dict[str, Any]] = []
    for target_date in sorted({row.target_date for row in rows}):
        train_rows = [row for row in rows if row.target_date < target_date]
        test_rows = [row for row in rows if row.target_date == target_date]
        model = train_raycaster_model(
            train_rows,
            min_training_events=min_training_events,
            feature_profile_name=feature_profile_name,
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
                "feature_profile": model.feature_profile_name,
            }
            for row in test_rows
        )
    return write_evaluation_outputs(
        dataset,
        output_dir,
        predictions,
        distributions,
        diagnostics,
        mode="expanding_window",
        source_export_id=source_export_id,
    )


def evaluate_rolling_window(
    dataset: BacktestDataset,
    output_dir: str | Path,
    train_days: int,
    test_days: int = 1,
    min_training_events: int = DEFAULT_MIN_TRAINING_EVENTS,
    probability_floor: float = 0.001,
    source_export_id: str | None = None,
    feature_profile_name: str = "weather_only",
) -> dict[str, Any]:
    if train_days < 1:
        raise ValueError("train_days must be at least 1")
    if test_days < 1:
        raise ValueError("test_days must be at least 1")
    rows = build_feature_rows(dataset)
    target_dates = sorted({row.target_date for row in rows})
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
        train_rows = [row for row in rows if row.target_date in train_set]
        test_rows = [row for row in rows if row.target_date in test_set]
        model = train_raycaster_model(
            train_rows,
            min_training_events=min_training_events,
            feature_profile_name=feature_profile_name,
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
                "feature_profile": model.feature_profile_name,
                "train_days": train_days,
                "test_days": test_days,
                "train_start_date": train_dates[0].isoformat() if train_dates else "",
                "train_end_date": train_dates[-1].isoformat() if train_dates else "",
                "test_start_date": test_dates[0].isoformat() if test_dates else "",
                "test_end_date": test_dates[-1].isoformat() if test_dates else "",
            }
            for row in test_rows
        )
        start_index += test_days
    return write_evaluation_outputs(
        dataset,
        output_dir,
        predictions,
        distributions,
        diagnostics,
        mode=f"rolling_window_{train_days}d_train_{test_days}d_test",
        source_export_id=source_export_id,
    )


def evaluate_fixed_model(
    dataset: BacktestDataset,
    model: RaycasterModel,
    output_dir: str | Path,
    probability_floor: float = 0.001,
    source_export_id: str | None = None,
) -> dict[str, Any]:
    predictions, distributions = predict_dataset(dataset, model, probability_floor)
    diagnostics = [
        {
            "target_date": row.target_date.isoformat(),
            "city": row.city,
            "event_ticker": row.event_ticker,
            "snapshot_hour_utc": row.snapshot_hour_utc.isoformat(),
            "mode": model.mode,
            "training_rows": model.training_rows,
        }
        for row in build_feature_rows(dataset)
    ]
    return write_evaluation_outputs(
        dataset,
        output_dir,
        predictions,
        distributions,
        diagnostics,
        mode="fixed_artifact",
        source_export_id=source_export_id,
    )


def write_evaluation_outputs(
    dataset: BacktestDataset,
    output_dir: str | Path,
    predictions: list[TemperaturePrediction],
    distributions: list[BracketDistribution],
    diagnostics: list[dict[str, Any]],
    mode: str,
    source_export_id: str | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_dict_rows(output / "predictions.csv", [_prediction_row(item) for item in predictions])
    _write_dict_rows(
        output / "bracket_distributions.csv",
        [_distribution_row(item) for item in distributions],
    )
    _write_dict_rows(output / "training_diagnostics.csv", diagnostics)
    temp_rows = _temperature_score_rows(dataset, predictions)
    bracket_rows = _bracket_score_rows(dataset, distributions)
    _write_dict_rows(output / "temperature_metrics.csv", _metric_rows(temp_rows, "temperature"))
    _write_dict_rows(output / "bracket_metrics.csv", _metric_rows(bracket_rows, "bracket"))
    _write_dict_rows(output / "by_city.csv", _group_metric_rows(temp_rows, bracket_rows, "city"))
    _write_dict_rows(
        output / "daily_metrics.csv",
        _group_metric_rows(temp_rows, bracket_rows, "target_date"),
    )
    _write_dict_rows(
        output / "city_day_metrics.csv",
        _group_metric_rows(temp_rows, bracket_rows, "city_day"),
    )
    _write_dict_rows(
        output / "by_checkpoint.csv",
        _group_metric_rows(temp_rows, bracket_rows, "checkpoint"),
    )
    _write_dict_rows(output / "calibration_bins.csv", _calibration_rows(bracket_rows))
    _write_dict_rows(output / "errors.csv", temp_rows + bracket_rows)
    _write_summary_json(output, mode, temp_rows, bracket_rows, source_export_id=source_export_id)
    _write_charts(output, temp_rows, bracket_rows)
    return {
        "mode": mode,
        "temperature_rows": len(temp_rows),
        "bracket_rows": len(bracket_rows),
        "output_dir": str(output),
    }


def _predict_rows(
    rows: list[FeatureRow],
    model: RaycasterModel,
    grouped_markets,
    probability_floor: float,
) -> tuple[list[TemperaturePrediction], list[BracketDistribution]]:
    expected_values = predict_expected_high(model, rows)
    quantile_values = predict_quantiles(model, rows)
    predictions: list[TemperaturePrediction] = []
    distributions: list[BracketDistribution] = []
    for row, expected, quantiles in zip(rows, expected_values, quantile_values, strict=True):
        cleaned_quantiles = monotonic_quantiles(quantiles)
        predictions.append(
            TemperaturePrediction(
                city=row.city,
                event_ticker=row.event_ticker,
                snapshot_hour_utc=row.snapshot_hour_utc,
                model_name=MODEL_NAME,
                expected_high_f=expected,
                quantiles=cleaned_quantiles,
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
                quantiles=cleaned_quantiles,
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


def _temperature_score_rows(
    dataset: BacktestDataset,
    predictions: list[TemperaturePrediction],
) -> list[dict[str, Any]]:
    settlements = {settlement.event_ticker: settlement for settlement in dataset.settlements}
    final_labels = {label.event_ticker: label for label in dataset.final_temperature_labels}
    events = {event.event_ticker: event for event in dataset.events}
    rows: list[dict[str, Any]] = []
    for prediction in predictions:
        settlement = settlements.get(prediction.event_ticker)
        final_label = final_labels.get(prediction.event_ticker)
        actual_high = (
            final_label.final_high_f
            if final_label is not None
            else settlement.settlement_temperature_f
            if settlement is not None
            else None
        )
        if actual_high is None:
            continue
        actual = float(actual_high)
        error = prediction.expected_high_f - actual
        event = events.get(prediction.event_ticker)
        target_date = event.target_date.isoformat() if event else ""
        rows.append(
            {
                "metric_type": "temperature",
                "model_name": prediction.model_name,
                "city": prediction.city,
                "target_date": target_date,
                "city_day": _city_day(prediction.city, target_date),
                "checkpoint": _checkpoint(prediction, event),
                "event_ticker": prediction.event_ticker,
                "snapshot_hour_utc": prediction.snapshot_hour_utc.isoformat(),
                "actual_high_f": actual,
                "predicted_high_f": prediction.expected_high_f,
                "error_f": error,
                "absolute_error_f": abs(error),
                "within_1f": 1.0 if abs(error) <= 1.0 else 0.0,
                "within_2f": 1.0 if abs(error) <= 2.0 else 0.0,
            }
        )
    return rows


def _bracket_score_rows(
    dataset: BacktestDataset,
    distributions: list[BracketDistribution],
) -> list[dict[str, Any]]:
    settlements = {settlement.event_ticker: settlement for settlement in dataset.settlements}
    grouped_markets = markets_by_snapshot(dataset)
    events = {event.event_ticker: event for event in dataset.events}
    rows: list[dict[str, Any]] = []
    for distribution in distributions:
        settlement = settlements.get(distribution.event_ticker)
        if settlement is None:
            continue
        markets = grouped_markets.get(
            (distribution.city, distribution.event_ticker, distribution.snapshot_hour_utc),
            [],
        )
        brackets = [market.bracket for market in markets]
        ordered_tickers = [bracket.ticker for bracket in brackets]
        if settlement.winner_ticker not in distribution.probabilities or not ordered_tickers:
            continue
        winner_index = settlement.settlement_bracket_index
        if winner_index is None and settlement.settlement_temperature_f is not None:
            winner_index = bracket_for_temperature(
                brackets,
                settlement.settlement_temperature_f,
            ).index
        top = top_ticker(distribution.probabilities)
        top_index = next((bracket.index for bracket in brackets if bracket.ticker == top), None)
        winner_bracket = next(
            (bracket for bracket in brackets if bracket.ticker == settlement.winner_ticker),
            None,
        )
        top_bracket = next((bracket for bracket in brackets if bracket.ticker == top), None)
        event = events.get(distribution.event_ticker)
        target_date = event.target_date.isoformat() if event else ""
        rows.append(
            {
                "metric_type": "bracket",
                "model_name": distribution.model_name,
                "city": distribution.city,
                "target_date": target_date,
                "city_day": _city_day(distribution.city, target_date),
                "checkpoint": _checkpoint(distribution, event),
                "event_ticker": distribution.event_ticker,
                "snapshot_hour_utc": distribution.snapshot_hour_utc.isoformat(),
                "winner_ticker": settlement.winner_ticker,
                "winner_bracket_type": _bracket_type(winner_bracket),
                "winner_probability": distribution.probabilities[settlement.winner_ticker],
                "top_ticker": top,
                "top_bracket_type": _bracket_type(top_bracket),
                "top_probability": distribution.probabilities[top],
                "top_one_accuracy": top_one_accuracy(
                    distribution.probabilities,
                    settlement.winner_ticker,
                ),
                "log_loss": log_loss(distribution.probabilities[settlement.winner_ticker]),
                "brier": multiclass_brier(
                    distribution.probabilities,
                    settlement.winner_ticker,
                ),
                "rps": ranked_probability_score(
                    ordered_tickers,
                    distribution.probabilities,
                    settlement.winner_ticker,
                ),
                "within_one_bracket": (
                    within_one_bracket_accuracy(int(top_index), int(winner_index))
                    if top_index is not None and winner_index is not None
                    else ""
                ),
            }
        )
    return rows


def _metric_rows(rows: list[dict[str, Any]], metric_type: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    if metric_type == "temperature":
        actual = [float(row["actual_high_f"]) for row in rows]
        predicted = [float(row["predicted_high_f"]) for row in rows]
        return [
            {
                "metric": "mae",
                "value": mean_absolute_error(actual, predicted),
                "count": len(rows),
            },
            {
                "metric": "rmse",
                "value": root_mean_squared_error(actual, predicted),
                "count": len(rows),
            },
            {"metric": "bias", "value": bias(actual, predicted), "count": len(rows)},
            {
                "metric": "within_1f",
                "value": mean(float(row["within_1f"]) for row in rows),
                "count": len(rows),
            },
            {
                "metric": "within_2f",
                "value": mean(float(row["within_2f"]) for row in rows),
                "count": len(rows),
            },
        ]
    return [
        {
            "metric": "log_loss",
            "value": mean(float(row["log_loss"]) for row in rows),
            "count": len(rows),
        },
        {"metric": "brier", "value": mean(float(row["brier"]) for row in rows), "count": len(rows)},
        {"metric": "rps", "value": mean(float(row["rps"]) for row in rows), "count": len(rows)},
        {
            "metric": "top_one_accuracy",
            "value": mean(float(row["top_one_accuracy"]) for row in rows),
            "count": len(rows),
        },
        {
            "metric": "winner_probability",
            "value": mean(float(row["winner_probability"]) for row in rows),
            "count": len(rows),
        },
    ]


def _group_metric_rows(
    temp_rows: list[dict[str, Any]],
    bracket_rows: list[dict[str, Any]],
    group_column: str,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for metric_type, rows in (("temperature", temp_rows), ("bracket", bracket_rows)):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row[group_column])].append(row)
        for group, group_rows in sorted(grouped.items()):
            for metric in _metric_rows(group_rows, metric_type):
                output.append({"group": group, "metric_type": metric_type, **metric})
    return output


def _calibration_rows(bracket_rows: list[dict[str, Any]], bins: int = 10) -> list[dict[str, Any]]:
    if not bracket_rows:
        return []
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in bracket_rows:
        probability = float(row["top_probability"])
        index = min(bins - 1, max(0, int(probability * bins)))
        grouped[index].append(row)
    output = []
    for index in range(bins):
        rows = grouped.get(index, [])
        lower = index / bins
        upper = (index + 1) / bins
        if not rows:
            output.append(
                {
                    "bin": f"{lower:.1f}-{upper:.1f}",
                    "lower": lower,
                    "upper": upper,
                    "count": 0,
                    "mean_top_probability": "",
                    "mean_winner_probability": "",
                    "empirical_win_rate": "",
                    "log_loss": "",
                    "brier": "",
                    "top_one_accuracy": "",
                }
            )
            continue
        output.append(
            {
                "bin": f"{lower:.1f}-{upper:.1f}",
                "lower": lower,
                "upper": upper,
                "count": len(rows),
                "mean_top_probability": mean(float(row["top_probability"]) for row in rows),
                "mean_winner_probability": mean(float(row["winner_probability"]) for row in rows),
                "empirical_win_rate": mean(float(row["top_one_accuracy"]) for row in rows),
                "log_loss": mean(float(row["log_loss"]) for row in rows),
                "brier": mean(float(row["brier"]) for row in rows),
                "top_one_accuracy": mean(float(row["top_one_accuracy"]) for row in rows),
            }
        )
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


def _write_summary_json(
    output: Path,
    mode: str,
    temp_rows: list[dict[str, Any]],
    bracket_rows: list[dict[str, Any]],
    source_export_id: str | None = None,
) -> None:
    import json
    from datetime import UTC, datetime

    summary = {
        "model_name": MODEL_NAME,
        "mode": mode,
        "source_export_id": source_export_id,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "temperature_prediction_count": len(temp_rows),
        "bracket_prediction_count": len(bracket_rows),
        "temperature_metrics": _metric_rows(temp_rows, "temperature"),
        "bracket_metrics": _metric_rows(bracket_rows, "bracket"),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _write_charts(
    output: Path,
    temp_rows: list[dict[str, Any]],
    bracket_rows: list[dict[str, Any]],
) -> None:
    if not temp_rows and not bracket_rows:
        return
    try:
        import matplotlib
    except ImportError:
        return
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    charts = output / "charts"
    charts.mkdir(exist_ok=True)
    if temp_rows:
        grouped = defaultdict(list)
        for row in temp_rows:
            grouped[row["checkpoint"]].append(float(row["absolute_error_f"]))
        labels = list(sorted(grouped))
        values = [mean(grouped[label]) for label in labels]
        plt.figure(figsize=(8, 4))
        plt.bar(labels, values)
        plt.ylabel("MAE (F)")
        plt.title("Raycaster v1 Temperature Error By Checkpoint")
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        plt.savefig(charts / "temperature_mae_by_checkpoint.png")
        plt.close()
    if bracket_rows:
        grouped = defaultdict(list)
        for row in bracket_rows:
            grouped[row["checkpoint"]].append(float(row["top_one_accuracy"]))
        labels = list(sorted(grouped))
        values = [mean(grouped[label]) for label in labels]
        plt.figure(figsize=(8, 4))
        plt.bar(labels, values)
        plt.ylabel("Top-one accuracy")
        plt.ylim(0, 1)
        plt.title("Raycaster v1 Bracket Accuracy By Checkpoint")
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        plt.savefig(charts / "bracket_accuracy_by_checkpoint.png")
        plt.close()


def _prediction_row(prediction: TemperaturePrediction) -> dict[str, Any]:
    row = to_dict(prediction)
    row["snapshot_hour_utc"] = prediction.snapshot_hour_utc.isoformat()
    for level, value in prediction.quantiles.items():
        row[f"q{int(float(level) * 100):02d}"] = value
    row.pop("quantiles", None)
    return row


def _distribution_row(distribution: BracketDistribution) -> dict[str, Any]:
    return {
        "city": distribution.city,
        "event_ticker": distribution.event_ticker,
        "snapshot_hour_utc": distribution.snapshot_hour_utc.isoformat(),
        "model_name": distribution.model_name,
        "probabilities": distribution.probabilities,
    }


def _observed(row: FeatureRow) -> float | None:
    value = row.features.get("observed_high_so_far_f")
    return float(value) if value is not None else None


def _city_day(city: str, target_date: str) -> str:
    return f"{city}:{target_date}" if target_date else city


def _bracket_type(bracket) -> str:
    if bracket is None:
        return "unknown"
    if bracket.lower_f is None:
        return "lower_tail"
    if bracket.upper_f is None:
        return "upper_tail"
    return "bounded"


def _checkpoint(item, event) -> str:
    if event is None:
        return "unknown"
    from libs.time_utils import checkpoint_label

    return checkpoint_label(item.snapshot_hour_utc, event.climate_window_start_utc)
