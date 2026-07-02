"""Template prediction helpers for TheTemp v1."""

from __future__ import annotations

from config import DEFAULT_PROBABILITY_FLOOR, MODEL_NAME
from dataset import brackets_for_snapshot, markets_by_snapshot
from distribution import bracket_distribution
from features import build_feature_rows
from train import TheTempModel, predict_expected_high, predict_quantiles

from libs.models import BracketDistribution, TemperaturePrediction


def predict_dataset(
    dataset,
    model: TheTempModel,
    probability_floor: float = DEFAULT_PROBABILITY_FLOOR,
) -> tuple[list[TemperaturePrediction], list[BracketDistribution]]:
    rows = build_feature_rows(dataset)
    grouped_markets = markets_by_snapshot(dataset)
    expected_values = predict_expected_high(model, rows)
    quantile_values = predict_quantiles(model, rows)
    predictions: list[TemperaturePrediction] = []
    distributions: list[BracketDistribution] = []
    for row, expected, quantiles in zip(rows, expected_values, quantile_values, strict=True):
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
            distributions.append(
                BracketDistribution(
                    city=row.city,
                    event_ticker=row.event_ticker,
                    snapshot_hour_utc=row.snapshot_hour_utc,
                    model_name=MODEL_NAME,
                    probabilities=bracket_distribution(brackets, quantiles, probability_floor),
                )
            )
    return predictions, distributions
