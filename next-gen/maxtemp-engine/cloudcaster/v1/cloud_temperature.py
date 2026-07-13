"""Temperature scaling for Cloudcaster per-snapshot bracket scores."""

from __future__ import annotations

from collections.abc import Iterable

from cloud_features import CloudcasterRow
from cloud_train import CloudcasterModel, predict_distributions

from libs.metrics import log_loss
from libs.probabilities import normalize

TEMPERATURE_GRID = (0.15, 0.20, 0.25, 0.35, 0.50, 0.65, 0.80, 1.00, 1.25, 1.50, 2.00, 2.50)


def apply_temperature(scores: dict[str, float], temperature: float) -> dict[str, float]:
    power = 1.0 / max(float(temperature), 1e-6)
    scaled = {ticker: max(float(score), 1e-12) ** power for ticker, score in scores.items()}
    return normalize(scaled, "temperature-scaled scores")


def fit_temperature(
    model: CloudcasterModel,
    rows: list[CloudcasterRow],
    fallback_probabilities: dict[tuple[str, str, object], dict[str, float]],
    probability_floor: float,
    candidates: Iterable[float] = TEMPERATURE_GRID,
) -> dict[str, float]:
    rows = [row for row in rows if row.winner_ticker]
    if not rows:
        return _temperature_result(1.0, 0.0, 0.0, 0)
    baseline_loss = _mean_log_loss(
        model,
        rows,
        fallback_probabilities,
        temperature=1.0,
        probability_floor=probability_floor,
    )
    best_temperature = 1.0
    best_loss = baseline_loss
    for temperature in candidates:
        candidate_loss = _mean_log_loss(
            model,
            rows,
            fallback_probabilities,
            temperature=temperature,
            probability_floor=probability_floor,
        )
        if candidate_loss < best_loss:
            best_temperature = float(temperature)
            best_loss = candidate_loss
    return _temperature_result(best_temperature, best_loss, baseline_loss, len(rows))


def _mean_log_loss(
    model: CloudcasterModel,
    rows: list[CloudcasterRow],
    fallback_probabilities: dict[tuple[str, str, object], dict[str, float]],
    temperature: float,
    probability_floor: float,
) -> float:
    distributions = predict_distributions(
        model,
        rows,
        fallback_probabilities,
        probability_floor=probability_floor,
        temperature=temperature,
    )
    winners = _winner_by_snapshot(rows)
    distribution_keys = [
        (distribution.city, distribution.event_ticker, distribution.snapshot_hour_utc)
        for distribution in distributions
    ]
    losses = [
        log_loss(distribution.probabilities[winners[key]])
        for distribution, key in zip(distributions, distribution_keys, strict=True)
        if key in winners and winners[key] in distribution.probabilities
    ]
    if not losses:
        return float("inf")
    return sum(losses) / len(losses)


def _winner_by_snapshot(rows: list[CloudcasterRow]) -> dict[tuple[str, str, object], str]:
    output = {}
    for row in rows:
        if row.winner_ticker:
            output[row.snapshot_key] = row.winner_ticker
    return output


def _temperature_result(
    temperature: float,
    loss: float,
    baseline_loss: float,
    rows: int,
) -> dict[str, float]:
    return {
        "temperature": temperature,
        "calibration_log_loss": loss,
        "uncalibrated_log_loss": baseline_loss,
        "calibration_rows": float(rows),
    }
