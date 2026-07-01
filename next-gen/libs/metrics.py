"""Weather and bracket probability metrics."""

from __future__ import annotations

import math
from collections.abc import Sequence

from libs.probabilities import normalize, top_ticker


def mean_absolute_error(actual: Sequence[float], predicted: Sequence[float]) -> float:
    _check_lengths(actual, predicted)
    return sum(abs(a - p) for a, p in zip(actual, predicted, strict=True)) / len(actual)


def root_mean_squared_error(actual: Sequence[float], predicted: Sequence[float]) -> float:
    _check_lengths(actual, predicted)
    squared_error = sum((a - p) ** 2 for a, p in zip(actual, predicted, strict=True))
    return math.sqrt(squared_error / len(actual))


def bias(actual: Sequence[float], predicted: Sequence[float]) -> float:
    _check_lengths(actual, predicted)
    return sum(p - a for a, p in zip(actual, predicted, strict=True)) / len(actual)


def log_loss(probability: float, epsilon: float = 1e-12) -> float:
    return -math.log(min(1.0 - epsilon, max(epsilon, probability)))


def multiclass_brier(probabilities: dict[str, float], winner_ticker: str) -> float:
    normalized = normalize(probabilities)
    return sum(
        (prob - (1.0 if ticker == winner_ticker else 0.0)) ** 2
        for ticker, prob in normalized.items()
    )


def ranked_probability_score(
    ordered_tickers: Sequence[str],
    probabilities: dict[str, float],
    winner_ticker: str,
) -> float:
    normalized = normalize(probabilities)
    if winner_ticker not in ordered_tickers:
        raise ValueError("winner_ticker is not in ordered_tickers")
    score = 0.0
    forecast_cdf = 0.0
    observed_cdf = 0.0
    winner_seen = False
    for ticker in ordered_tickers[:-1]:
        forecast_cdf += normalized.get(ticker, 0.0)
        if ticker == winner_ticker:
            winner_seen = True
        observed_cdf = 1.0 if winner_seen else 0.0
        score += (forecast_cdf - observed_cdf) ** 2
    return score / max(1, len(ordered_tickers) - 1)


def top_one_accuracy(probabilities: dict[str, float], winner_ticker: str) -> float:
    return 1.0 if top_ticker(probabilities) == winner_ticker else 0.0


def within_one_bracket_accuracy(predicted_index: int, winner_index: int) -> float:
    return 1.0 if abs(predicted_index - winner_index) <= 1 else 0.0


def _check_lengths(actual: Sequence[float], predicted: Sequence[float]) -> None:
    if not actual or len(actual) != len(predicted):
        raise ValueError("actual and predicted must be non-empty and equal length")
