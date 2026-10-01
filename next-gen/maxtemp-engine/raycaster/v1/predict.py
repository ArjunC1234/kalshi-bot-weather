"""Prediction helpers for Raycaster v1."""

from __future__ import annotations

from dataset import brackets_for_snapshot, markets_by_snapshot
from distribution import bracket_distribution, monotonic_quantiles
from features import MODEL_NAME, FeatureRow, build_feature_rows
from train import RaycasterModel, predict_expected_high, predict_quantiles

from libs.models import BracketDistribution, TemperaturePrediction


def predict_dataset(
    dataset,
    model: RaycasterModel,
    probability_floor: float = 0.001,
) -> tuple[list[TemperaturePrediction], list[BracketDistribution]]:
    rows = build_feature_rows(dataset)
    grouped_markets = markets_by_snapshot(dataset)
    expected_values = predict_expected_high(model, rows)
    quantile_values = predict_quantiles(model, rows)
    temperature_predictions: list[TemperaturePrediction] = []
    bracket_distributions: list[BracketDistribution] = []
    for row, expected, quantiles in zip(rows, expected_values, quantile_values, strict=True):
        cleaned_quantiles = monotonic_quantiles(quantiles)
        temperature_predictions.append(
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
                brackets=brackets,
                expected_high_f=expected,
                quantiles=cleaned_quantiles,
                observed_high_so_far_f=_observed(row),
                probability_floor=probability_floor,
            )
            bracket_distributions.append(
                BracketDistribution(
                    city=row.city,
                    event_ticker=row.event_ticker,
                    snapshot_hour_utc=row.snapshot_hour_utc,
                    model_name=MODEL_NAME,
                    probabilities=probabilities,
                )
            )
    return temperature_predictions, bracket_distributions


def _observed(row: FeatureRow) -> float | None:
    value = row.features.get("settlement_observed_high_so_far_f")
    if value is None:
        value = row.features.get("observed_high_so_far_f")
    return float(value) if value is not None else None
