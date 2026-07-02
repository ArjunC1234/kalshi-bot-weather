"""Template evaluation helpers for TheTemp v1."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any

from config import MODEL_NAME
from features import build_feature_rows
from predict import predict_dataset
from train import train_model

from libs.metrics import log_loss, mean_absolute_error, top_one_accuracy
from libs.probabilities import top_ticker


def evaluate_dataset(dataset, output_dir: str | Path) -> dict[str, Any]:
    model = train_model(build_feature_rows(dataset))
    predictions, distributions = predict_dataset(dataset, model)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    temperature_rows = _temperature_rows(dataset, predictions)
    bracket_rows = _bracket_rows(dataset, distributions)
    _write_rows(output / "predictions.csv", [_prediction_row(row) for row in predictions])
    _write_rows(
        output / "bracket_distributions.csv",
        [_distribution_row(row) for row in distributions],
    )
    _write_rows(output / "temperature_metrics.csv", _temperature_metrics(temperature_rows))
    _write_rows(output / "bracket_metrics.csv", _bracket_metrics(bracket_rows))
    summary = {
        "model_name": MODEL_NAME,
        "mode": model.mode,
        "temperature_prediction_count": len(temperature_rows),
        "bracket_prediction_count": len(bracket_rows),
        "temperature_metrics": _temperature_metrics(temperature_rows),
        "bracket_metrics": _bracket_metrics(bracket_rows),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def _temperature_rows(dataset, predictions) -> list[dict[str, Any]]:
    settlements = {settlement.event_ticker: settlement for settlement in dataset.settlements}
    rows = []
    for prediction in predictions:
        settlement = settlements.get(prediction.event_ticker)
        if settlement is None or settlement.settlement_temperature_f is None:
            continue
        actual = float(settlement.settlement_temperature_f)
        rows.append(
            {
                "actual_high_f": actual,
                "predicted_high_f": prediction.expected_high_f,
                "absolute_error_f": abs(prediction.expected_high_f - actual),
            }
        )
    return rows


def _bracket_rows(dataset, distributions) -> list[dict[str, Any]]:
    settlements = {settlement.event_ticker: settlement for settlement in dataset.settlements}
    rows = []
    for distribution in distributions:
        settlement = settlements.get(distribution.event_ticker)
        if settlement is None or settlement.winner_ticker not in distribution.probabilities:
            continue
        rows.append(
            {
                "winner_probability": distribution.probabilities[settlement.winner_ticker],
                "top_one_accuracy": top_one_accuracy(
                    distribution.probabilities,
                    settlement.winner_ticker,
                ),
                "top_ticker": top_ticker(distribution.probabilities),
            }
        )
    return rows


def _temperature_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    actual = [float(row["actual_high_f"]) for row in rows]
    predicted = [float(row["predicted_high_f"]) for row in rows]
    return [{"metric": "mae", "value": mean_absolute_error(actual, predicted), "count": len(rows)}]


def _bracket_metrics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    return [
        {
            "metric": "log_loss",
            "value": mean(log_loss(float(row["winner_probability"])) for row in rows),
            "count": len(rows),
        },
        {
            "metric": "top_one_accuracy",
            "value": mean(float(row["top_one_accuracy"]) for row in rows),
            "count": len(rows),
        },
    ]


def _prediction_row(prediction) -> dict[str, Any]:
    return {
        "city": prediction.city,
        "event_ticker": prediction.event_ticker,
        "snapshot_hour_utc": prediction.snapshot_hour_utc.isoformat(),
        "model_name": prediction.model_name,
        "expected_high_f": prediction.expected_high_f,
        "quantiles": prediction.quantiles,
    }


def _distribution_row(distribution) -> dict[str, Any]:
    return {
        "city": distribution.city,
        "event_ticker": distribution.event_ticker,
        "snapshot_hour_utc": distribution.snapshot_hour_utc.isoformat(),
        "model_name": distribution.model_name,
        "probabilities": distribution.probabilities,
    }


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({key for row in rows for key in row}))
        writer.writeheader()
        writer.writerows(rows)
