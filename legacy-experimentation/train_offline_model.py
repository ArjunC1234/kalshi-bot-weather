"""Train an offline probability blender from archived weather backtest snapshots.

This is intentionally a calibration layer, not a new weather data collector.
It reads immutable snapshots and settlements, trains only from already-settled
historical rows, and writes reports without calling live APIs.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.compose import ColumnTransformer  # noqa: E402
from sklearn.linear_model import LogisticRegression, Ridge  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # noqa: E402

from experiment_market_comparison import quote_distributions  # noqa: E402
from weather_backtest import (  # noqa: E402
    challenger_distributions,
    read_json_gz,
    score_probabilities,
    settlement_path,
    write_csv,
)
from weather_probabilities import DataError, parse_datetime  # noqa: E402


PROBABILITY_FLOOR = 0.001
SOFT_FLOOR_MASS = 0.02
DEFAULT_CANDIDATES = (
    "full",
    "family_centered",
    "soft_floor_weather",
    "soft_floor_family_centered",
    "market_midpoint",
    "uniform",
)
WEATHER_CANDIDATES = (
    "full",
    "family_centered",
    "soft_floor_weather",
    "soft_floor_family_centered",
    "soft_floor_weather_floored",
)
STAGED_HRRR_CANDIDATES = (
    "trained_weather_blend",
    "hrrr_top3_rerank",
)
ANCHORED_REGRESSION_MODEL = "anchored_regression_weather_hrrr"
ANCHORED_REGRESSION_CANDIDATES = (
    "checkpoint_anchor",
    "regression_hrrr",
)
ANCHORED_MAX_REGRESSION_WEIGHT = 0.5
COMPARISON_MODELS = (
    "trained_blend",
    "checkpoint_trained_blend",
    "trained_weather_blend",
    "checkpoint_trained_weather_blend",
    "trained_weather_hrrr_blend",
    "checkpoint_trained_weather_hrrr_blend",
    "regression_trained_weather_hrrr",
    ANCHORED_REGRESSION_MODEL,
    "calibrated_temperature_error_model",
    "calibrated_temperature_error_market_aware",
    "full",
    "hrrr_top3_rerank",
    "family_centered",
    "soft_floor_weather",
    "soft_floor_family_centered",
    "soft_floor_weather_floored",
    "market_midpoint",
    "uniform",
)


@dataclass(frozen=True)
class ForecastExample:
    city: str
    target_date: str
    event_ticker: str
    checkpoint: str
    scheduled_at: str
    as_of: str
    winner_ticker: str
    tickers: tuple[str, ...]
    distributions: dict[str, tuple[float, ...]]
    feature_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def event_key(self) -> tuple[str, str]:
        return self.target_date, self.city


def normalize(values: Iterable[float], label: str) -> tuple[float, ...]:
    row = [float(value) for value in values]
    total = sum(row)
    if total <= 0:
        raise DataError(f"{label} distribution has no positive mass")
    normalized = tuple(value / total for value in row)
    if not math.isclose(sum(normalized), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise DataError(f"{label} distribution cannot be normalized")
    return normalized


def bracket_index_for_temperature(brackets: list[dict[str, Any]], value: float) -> int:
    for index, bracket in enumerate(brackets):
        lower = bracket.get("lower")
        upper = bracket.get("upper")
        if (lower is None or value >= int(lower) - 0.5) and (
            upper is None or value < int(upper) + 0.5
        ):
            return index
    raise DataError(f"temperature {value} does not map to a bracket")


def apply_probability_floor(
    probabilities: Iterable[float], floor: float = PROBABILITY_FLOOR
) -> tuple[float, ...]:
    values = normalize(probabilities, "probability floor input")
    if floor < 0:
        raise ValueError("probability floor must be nonnegative")
    reserved = floor * len(values)
    if reserved >= 1.0:
        raise ValueError("probability floor is too large for bracket count")
    return tuple(floor + value * (1.0 - reserved) for value in values)


def soft_observation_floor_probabilities(
    brackets: list[dict[str, Any]],
    probabilities: Iterable[float],
    observed_high_f: float | None,
    mass: float = SOFT_FLOOR_MASS,
) -> tuple[float, ...]:
    values = list(normalize(probabilities, "soft floor input"))
    if observed_high_f is None:
        return tuple(values)
    if not 0 <= mass < 1:
        raise ValueError("soft floor mass must be in [0, 1)")

    observed_index = bracket_index_for_temperature(brackets, observed_high_f)
    lower_allowed_index = max(0, observed_index - 1)
    relaxed = [
        value if index >= lower_allowed_index else 0.0
        for index, value in enumerate(values)
    ]
    relaxed = list(normalize(relaxed, "soft floor relaxed probabilities"))
    if lower_allowed_index < observed_index:
        relaxed = [value * (1.0 - mass) for value in relaxed]
        relaxed[lower_allowed_index] += mass
    return normalize(relaxed, "soft floor probabilities")


def blend_probabilities(
    distributions: dict[str, tuple[float, ...]],
    weights: dict[str, float],
    probability_floor: float | None = PROBABILITY_FLOOR,
) -> tuple[float, ...]:
    if not weights:
        raise DataError("trained blend has no weights")
    names = list(weights)
    length = len(distributions[names[0]])
    blended = [0.0] * length
    for name, weight in weights.items():
        values = distributions[name]
        if len(values) != length:
            raise DataError(f"{name} has a different bracket count")
        for index, value in enumerate(values):
            blended[index] += weight * value
    normalized = normalize(blended, "trained blend")
    return (
        apply_probability_floor(normalized, probability_floor)
        if probability_floor is not None and probability_floor > 0
        else normalized
    )


def simplex_weight_grid(names: tuple[str, ...], step: float) -> Iterable[dict[str, float]]:
    if not 0 < step <= 1:
        raise ValueError("step must be in (0, 1]")
    units = round(1.0 / step)
    if not math.isclose(units * step, 1.0, abs_tol=1e-9):
        raise ValueError("step must evenly divide 1.0")

    counts = [0] * len(names)

    def rec(index: int, remaining: int) -> Iterable[dict[str, float]]:
        if index == len(names) - 1:
            counts[index] = remaining
            yield {name: count / units for name, count in zip(names, counts, strict=True)}
            return
        for value in range(remaining + 1):
            counts[index] = value
            yield from rec(index + 1, remaining - value)

    yield from rec(0, units)


def prior_weights(names: tuple[str, ...]) -> dict[str, float]:
    return {name: 1.0 / len(names) for name in names}


def event_balanced_weights(examples: list[ForecastExample]) -> list[float]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for example in examples:
        counts[example.event_key] += 1
    return [1.0 / counts[example.event_key] for example in examples]


def weighted_log_loss(
    examples: list[ForecastExample],
    weights: dict[str, float],
    example_weights: list[float],
    probability_floor: float,
) -> float:
    total_weight = sum(example_weights)
    if total_weight <= 0:
        raise DataError("training examples have no weight")
    losses: list[float] = []
    for example, example_weight in zip(examples, example_weights, strict=True):
        probabilities = blend_probabilities(
            example.distributions, weights, probability_floor
        )
        score = score_probabilities(list(example.tickers), list(probabilities), example.winner_ticker)
        outcome_probability = float(score["outcome_probability"])
        losses.append(example_weight * -math.log(max(outcome_probability, 1e-12)))
    return sum(losses) / total_weight


def fit_blend_weights(
    examples: list[ForecastExample],
    candidate_names: tuple[str, ...],
    grid_step: float,
    regularization: float,
    probability_floor: float = PROBABILITY_FLOOR,
) -> dict[str, Any]:
    if not examples:
        return {
            "weights": prior_weights(candidate_names),
            "training_forecasts": 0,
            "training_events": 0,
            "objective_log_loss": None,
            "mode": "fallback_no_training_data",
        }

    defaults = prior_weights(candidate_names)
    example_weights = event_balanced_weights(examples)
    best_weights: dict[str, float] | None = None
    best_objective = float("inf")
    best_log_loss = float("inf")
    for weights in simplex_weight_grid(candidate_names, grid_step):
        log_loss = weighted_log_loss(
            examples, weights, example_weights, probability_floor
        )
        penalty = regularization * sum(
            (weights[name] - defaults[name]) ** 2 for name in candidate_names
        )
        objective = log_loss + penalty
        if objective < best_objective:
            best_weights = weights
            best_objective = objective
            best_log_loss = log_loss
    if best_weights is None:
        raise DataError("no trained weights were generated")
    return {
        "weights": best_weights,
        "training_forecasts": len(examples),
        "training_events": len({example.event_key for example in examples}),
        "objective_log_loss": best_log_loss,
        "regularized_objective": best_objective,
        "mode": "trained",
    }


def fit_checkpoint_models(
    examples: list[ForecastExample],
    candidate_names: tuple[str, ...],
    grid_step: float,
    regularization: float,
    min_train_events: int,
    probability_floor: float = PROBABILITY_FLOOR,
) -> dict[str, dict[str, Any]]:
    checkpoint_models: dict[str, dict[str, Any]] = {}
    for checkpoint in sorted({example.checkpoint for example in examples}):
        checkpoint_examples = [
            example for example in examples if example.checkpoint == checkpoint
        ]
        event_count = len({example.event_key for example in checkpoint_examples})
        if event_count >= min_train_events:
            fit = fit_blend_weights(
                checkpoint_examples,
                candidate_names,
                grid_step,
                regularization,
                probability_floor,
            )
        else:
            fit = {
                "weights": prior_weights(candidate_names),
                "training_forecasts": len(checkpoint_examples),
                "training_events": event_count,
                "objective_log_loss": None,
                "regularized_objective": None,
                "mode": "fallback_min_train_events",
            }
        checkpoint_models[checkpoint] = {
            **fit,
            "checkpoint": checkpoint,
            "model_name": f"checkpoint_model_{checkpoint}",
        }
    return checkpoint_models


def _fit_or_fallback(
    examples: list[ForecastExample],
    candidate_names: tuple[str, ...],
    grid_step: float,
    regularization: float,
    min_train_events: int,
    probability_floor: float,
) -> dict[str, Any]:
    event_count = len({example.event_key for example in examples})
    if event_count >= min_train_events:
        return fit_blend_weights(
            examples,
            candidate_names,
            grid_step,
            regularization,
            probability_floor,
        )
    return {
        "weights": prior_weights(candidate_names),
        "training_forecasts": len(examples),
        "training_events": event_count,
        "objective_log_loss": None,
        "regularized_objective": None,
        "mode": "fallback_min_train_events",
    }


def _hrrr_stage_examples(
    examples: list[ForecastExample],
    base_weights: dict[str, float],
    probability_floor: float,
) -> list[ForecastExample]:
    stage_examples: list[ForecastExample] = []
    for example in examples:
        if "hrrr_top3_rerank" not in example.distributions:
            continue
        base_probabilities = blend_probabilities(
            example.distributions,
            base_weights,
            probability_floor,
        )
        stage_examples.append(
            ForecastExample(
                city=example.city,
                target_date=example.target_date,
                event_ticker=example.event_ticker,
                checkpoint=example.checkpoint,
                scheduled_at=example.scheduled_at,
                as_of=example.as_of,
                winner_ticker=example.winner_ticker,
                tickers=example.tickers,
                distributions={
                    "trained_weather_blend": base_probabilities,
                    "hrrr_top3_rerank": example.distributions["hrrr_top3_rerank"],
                },
            )
        )
    return stage_examples


def _hrrr_stage_fit(
    train: list[ForecastExample],
    base_weights: dict[str, float],
    grid_step: float,
    regularization: float,
    min_train_events: int,
    probability_floor: float,
) -> dict[str, Any]:
    stage_train = _hrrr_stage_examples(train, base_weights, probability_floor)
    event_count = len({example.event_key for example in stage_train})
    if event_count >= min_train_events:
        fit = fit_blend_weights(
            stage_train,
            STAGED_HRRR_CANDIDATES,
            grid_step,
            regularization,
            probability_floor,
        )
        return {**fit, "hrrr_training_events": event_count}
    return {
        "weights": {"trained_weather_blend": 1.0, "hrrr_top3_rerank": 0.0},
        "training_forecasts": len(stage_train),
        "training_events": event_count,
        "hrrr_training_events": event_count,
        "objective_log_loss": None,
        "regularized_objective": None,
        "mode": "fallback_no_prior_hrrr_training_data",
    }


def staged_hrrr_probabilities(
    example: ForecastExample,
    weather_weights: dict[str, float],
    hrrr_weights: dict[str, float],
    probability_floor: float = PROBABILITY_FLOOR,
) -> tuple[float, ...]:
    weather_probabilities = blend_probabilities(
        example.distributions,
        weather_weights,
        probability_floor,
    )
    if "hrrr_top3_rerank" not in example.distributions:
        return weather_probabilities
    return blend_probabilities(
        {
            "trained_weather_blend": weather_probabilities,
            "hrrr_top3_rerank": example.distributions["hrrr_top3_rerank"],
        },
        hrrr_weights,
        probability_floor,
    )


def score_checkpoint_models(
    examples: list[ForecastExample],
    checkpoint_models: dict[str, dict[str, Any]],
    probability_floor: float = PROBABILITY_FLOOR,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for example in examples:
        fit = checkpoint_models.get(example.checkpoint)
        if fit is None:
            continue
        weights = {str(name): float(value) for name, value in fit["weights"].items()}
        row = score_model(
            example,
            str(fit["model_name"]),
            blend_probabilities(example.distributions, weights, probability_floor),
        )
        row.update(
            {
                "training_mode": fit["mode"],
                "training_forecasts": fit["training_forecasts"],
                "training_events": fit["training_events"],
                "weights_json": json.dumps(weights, sort_keys=True),
            }
        )
        rows.append(row)
    return rows


def load_examples(root: Path, cohort: str) -> list[ForecastExample]:
    base = root / "cohorts" / cohort
    examples: list[ForecastExample] = []
    for path in sorted((base / "snapshots").glob("*/*/*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            snapshot = json.load(handle)
        event = snapshot["event"]
        target_date = str(event["target_date"])
        city = str(event["city"])
        settlement_file = settlement_path(base, parse_datetime(f"{target_date}T00:00:00Z").date(), city)
        if not settlement_file.exists():
            continue
        settlement = read_json_gz(settlement_file)
        winner = str(settlement["winner_ticker"])
        brackets = snapshot["distribution"]["brackets"]
        tickers = tuple(str(bracket["ticker"]) for bracket in brackets)
        full = normalize(snapshot["distribution"]["probabilities"], "full")
        variants, _ = challenger_distributions(snapshot)
        family_centered = normalize(
            variants["family_centered"]["probabilities"], "family_centered"
        )
        observed_high = snapshot["distribution"].get("observed_high_f")
        observed_high_f = float(observed_high) if observed_high is not None else None
        soft_floor_weather = soft_observation_floor_probabilities(
            brackets, full, observed_high_f
        )
        soft_floor_family_centered = soft_observation_floor_probabilities(
            brackets, family_centered, observed_high_f
        )
        quote_probs, _ = quote_distributions(snapshot, list(tickers))
        distributions: dict[str, tuple[float, ...]] = {
            "full": full,
            "family_centered": family_centered,
            "soft_floor_weather": soft_floor_weather,
            "soft_floor_family_centered": soft_floor_family_centered,
            "soft_floor_weather_floored": apply_probability_floor(
                soft_floor_weather
            ),
            "uniform": tuple(1.0 / len(tickers) for _ in tickers),
        }
        if "hrrr_top3_rerank" in variants:
            distributions["hrrr_top3_rerank"] = normalize(
                variants["hrrr_top3_rerank"]["probabilities"],
                "hrrr_top3_rerank",
            )
        distributions.update(
            {name: normalize(values, name) for name, values in quote_probs.items()}
        )
        checkpoint = snapshot["checkpoint"]
        examples.append(
            ForecastExample(
                city=city,
                target_date=target_date,
                event_ticker=str(event["event_ticker"]),
                checkpoint=str(checkpoint["name"]),
                scheduled_at=str(checkpoint["scheduled_at"]),
                as_of=str(checkpoint["as_of"]),
                winner_ticker=winner,
                tickers=tickers,
                distributions=distributions,
                feature_metadata=snapshot_feature_metadata(snapshot, variants),
            )
        )
    return sorted(
        examples,
        key=lambda item: (
            item.target_date,
            parse_datetime(item.as_of),
            item.city,
            item.checkpoint,
        ),
    )


def validate_candidates(
    examples: list[ForecastExample], candidate_names: tuple[str, ...]
) -> None:
    if not candidate_names:
        raise DataError("at least one training candidate is required")
    for example in examples:
        missing = [name for name in candidate_names if name not in example.distributions]
        if missing:
            raise DataError(
                f"{example.event_ticker} {example.checkpoint} is missing candidates: "
                f"{', '.join(missing)}"
            )


def examples_have_candidates(
    examples: list[ForecastExample], candidate_names: tuple[str, ...]
) -> bool:
    return all(
        all(name in example.distributions for name in candidate_names)
        for example in examples
    )


def _safe_bracket_index(
    brackets: list[dict[str, Any]], value: Any
) -> float | None:
    if value is None:
        return None
    try:
        return float(bracket_index_for_temperature(brackets, float(value)))
    except (TypeError, ValueError, DataError):
        return None


def _age_hours(as_of: str, observed_at: Any) -> float | None:
    if not observed_at:
        return None
    try:
        return max(
            0.0,
            (
                parse_datetime(as_of).astimezone(UTC)
                - parse_datetime(str(observed_at)).astimezone(UTC)
            ).total_seconds()
            / 3600.0,
        )
    except (TypeError, ValueError):
        return None


def snapshot_feature_metadata(
    snapshot: dict[str, Any],
    variants: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    distribution = snapshot.get("distribution", {})
    checkpoint = snapshot.get("checkpoint", {})
    brackets = distribution.get("brackets", [])
    if not isinstance(brackets, list):
        brackets = []
    hrrr = variants.get("hrrr_top3_rerank", {})
    member_highs = [
        float(value)
        for value in distribution.get("member_highs_f", [])
        if value is not None
    ]
    family_spread = max(member_highs) - min(member_highs) if member_highs else None
    nws_high = distribution.get("nws_high_f")
    raw_consensus = distribution.get("raw_consensus_high_f")
    return {
        "nws_index": _safe_bracket_index(brackets, nws_high),
        "observed_index": _safe_bracket_index(
            brackets, distribution.get("observed_high_f")
        ),
        "observed_age_hours": _age_hours(
            str(checkpoint.get("as_of", "")), distribution.get("observed_at")
        ),
        "hrrr_index": (
            float(hrrr["hrrr_bracket_index"])
            if hrrr.get("hrrr_bracket_index") is not None
            else _safe_bracket_index(brackets, hrrr.get("hrrr_projected_high_f"))
        ),
        "family_spread_f": family_spread,
        "raw_consensus_minus_nws_f": (
            float(raw_consensus) - float(nws_high)
            if raw_consensus is not None and nws_high is not None
            else None
        ),
        "center_shift_f": (
            float(distribution["center_shift_f"])
            if distribution.get("center_shift_f") is not None
            else None
        ),
        "bandwidth_f": (
            float(distribution["bandwidth_f"])
            if distribution.get("bandwidth_f") is not None
            else None
        ),
    }


def score_model(
    example: ForecastExample, model_name: str, probabilities: tuple[float, ...]
) -> dict[str, Any]:
    row = {
        "city": example.city,
        "target_date": example.target_date,
        "event_ticker": example.event_ticker,
        "checkpoint": example.checkpoint,
        "scheduled_at": example.scheduled_at,
        "as_of": example.as_of,
        "winner_ticker": example.winner_ticker,
        "model": model_name,
        "tickers_json": json.dumps(list(example.tickers)),
        "probabilities_json": json.dumps(list(probabilities)),
    }
    row.update(
        score_probabilities(list(example.tickers), list(probabilities), example.winner_ticker)
    )
    return row


REGRESSION_NUMERIC_FEATURES = (
    "bracket_index",
    "bracket_count",
    "relative_bracket_index",
    "weather_probability",
    "family_centered_probability",
    "soft_floor_family_centered_probability",
    "soft_floor_weather_probability",
    "soft_floor_weather_floored_probability",
    "hrrr_probability",
    "market_probability",
    "uniform_probability",
    "weather_logit",
    "hrrr_logit",
    "market_logit",
    "hrrr_distance",
    "abs_hrrr_distance",
    "weather_top_distance",
    "abs_weather_top_distance",
    "family_minus_weather_probability",
    "soft_family_minus_family_probability",
    "hrrr_minus_weather_probability",
    "market_minus_weather_probability",
)
REGRESSION_CATEGORICAL_FEATURES = (
    "city",
    "checkpoint",
    "is_hrrr_top",
    "is_one_below_hrrr",
    "is_one_above_hrrr",
    "is_weather_top",
    "has_hrrr",
)
CALIBRATED_BASE_MODEL = "soft_floor_family_centered"
CALIBRATED_FALLBACK_MODEL = "checkpoint_trained_weather_blend"
CALIBRATED_MIN_TRAIN_EVENTS = 12
CALIBRATED_MIN_CHECKPOINT_RESIDUAL_EVENTS = 4
CALIBRATED_MIN_SPREAD_BRACKETS = 0.75
CALIBRATED_MAX_SPREAD_BRACKETS = 2.5
CALIBRATED_NUMERIC_FEATURES = (
    "base_expected_index",
    "base_top_index",
    "base_top_probability",
    "base_top_two_gap",
    "base_entropy",
    "base_variance",
    "nws_index",
    "observed_index",
    "observed_age_hours",
    "hrrr_index",
    "hrrr_minus_base_expected",
    "nws_minus_base_expected",
    "observed_minus_base_expected",
    "family_expected_index",
    "family_minus_base_expected",
    "family_spread_f",
    "raw_consensus_minus_nws_f",
    "center_shift_f",
    "bandwidth_f",
    "has_nws_index",
    "has_observed_index",
    "has_hrrr_index",
)
CALIBRATED_MARKET_NUMERIC_FEATURES = (
    *CALIBRATED_NUMERIC_FEATURES,
    "market_expected_index",
    "market_minus_base_expected",
)
CALIBRATED_CATEGORICAL_FEATURES = ("city", "checkpoint")


def _safe_probability(value: float | None, floor: float = 1e-6) -> float:
    if value is None or not math.isfinite(value):
        value = floor
    return min(1.0 - floor, max(floor, float(value)))


def _logit(value: float | None) -> float:
    probability = _safe_probability(value)
    return math.log(probability / (1.0 - probability))


def _top_index(probabilities: tuple[float, ...]) -> int:
    return max(range(len(probabilities)), key=lambda index: probabilities[index])


def _regression_feature_rows(
    examples: list[ForecastExample],
) -> tuple[list[dict[str, Any]], list[int], list[tuple[int, int]]]:
    features: list[dict[str, Any]] = []
    labels: list[int] = []
    groups: list[tuple[int, int]] = []
    for example in examples:
        length = len(example.tickers)
        start = len(features)
        family = example.distributions.get("family_centered")
        soft_family = example.distributions.get("soft_floor_family_centered")
        soft_weather = example.distributions.get("soft_floor_weather")
        soft_weather_floored = example.distributions.get("soft_floor_weather_floored")
        full = example.distributions.get("full")
        hrrr = example.distributions.get("hrrr_top3_rerank")
        market = example.distributions.get("market_midpoint")
        uniform = example.distributions.get("uniform")
        weather_reference = soft_family or family or full
        if full is None or family is None or soft_family is None or weather_reference is None:
            raise DataError("regression training requires weather candidate distributions")
        weather_top = _top_index(weather_reference)
        hrrr_top = _top_index(hrrr) if hrrr is not None else weather_top
        for index, ticker in enumerate(example.tickers):
            weather_probability = _safe_probability(weather_reference[index])
            hrrr_probability = _safe_probability(hrrr[index] if hrrr is not None else None)
            market_probability = _safe_probability(market[index] if market is not None else None)
            row = {
                "city": example.city,
                "checkpoint": example.checkpoint,
                "bracket_index": float(index),
                "bracket_count": float(length),
                "relative_bracket_index": index / max(1.0, length - 1.0),
                "weather_probability": weather_probability,
                "family_centered_probability": _safe_probability(family[index]),
                "soft_floor_family_centered_probability": _safe_probability(soft_family[index]),
                "soft_floor_weather_probability": _safe_probability(
                    soft_weather[index] if soft_weather is not None else None
                ),
                "soft_floor_weather_floored_probability": _safe_probability(
                    soft_weather_floored[index] if soft_weather_floored is not None else None
                ),
                "hrrr_probability": hrrr_probability,
                "market_probability": market_probability,
                "uniform_probability": _safe_probability(
                    uniform[index] if uniform is not None else 1.0 / length
                ),
                "weather_logit": _logit(weather_probability),
                "hrrr_logit": _logit(hrrr_probability),
                "market_logit": _logit(market_probability),
                "hrrr_distance": float(index - hrrr_top),
                "abs_hrrr_distance": float(abs(index - hrrr_top)),
                "weather_top_distance": float(index - weather_top),
                "abs_weather_top_distance": float(abs(index - weather_top)),
                "family_minus_weather_probability": _safe_probability(family[index])
                - weather_probability,
                "soft_family_minus_family_probability": _safe_probability(soft_family[index])
                - _safe_probability(family[index]),
                "hrrr_minus_weather_probability": hrrr_probability - weather_probability,
                "market_minus_weather_probability": market_probability - weather_probability,
                "is_hrrr_top": str(hrrr is not None and index == hrrr_top),
                "is_one_below_hrrr": str(hrrr is not None and index == hrrr_top - 1),
                "is_one_above_hrrr": str(hrrr is not None and index == hrrr_top + 1),
                "is_weather_top": str(index == weather_top),
                "has_hrrr": str(hrrr is not None),
            }
            features.append(row)
            labels.append(int(ticker == example.winner_ticker))
        groups.append((start, len(features)))
    return features, labels, groups


def fit_regression_weather_hrrr(
    examples: list[ForecastExample],
    min_train_events: int,
) -> dict[str, Any]:
    event_count = len({example.event_key for example in examples})
    if event_count < min_train_events:
        return {
            "model": None,
            "mode": "fallback_min_train_events",
            "training_forecasts": len(examples),
            "training_events": event_count,
            "feature_count": 0,
        }
    features, labels, _ = _regression_feature_rows(examples)
    if len(set(labels)) < 2:
        return {
            "model": None,
            "mode": "fallback_single_class",
            "training_forecasts": len(examples),
            "training_events": event_count,
            "feature_count": len(REGRESSION_NUMERIC_FEATURES)
            + len(REGRESSION_CATEGORICAL_FEATURES),
        }
    transformer = ColumnTransformer(
        transformers=[
            (
                "numeric",
                StandardScaler(),
                list(REGRESSION_NUMERIC_FEATURES),
            ),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                list(REGRESSION_CATEGORICAL_FEATURES),
            ),
        ],
        remainder="drop",
    )
    model = Pipeline(
        steps=[
            ("features", transformer),
            (
                "classifier",
                LogisticRegression(
                    C=0.5,
                    class_weight="balanced",
                    max_iter=2000,
                    solver="lbfgs",
                ),
            ),
        ]
    )
    model.fit(pd.DataFrame(features), labels)
    return {
        "model": model,
        "mode": "trained",
        "training_forecasts": len(examples),
        "training_events": event_count,
        "feature_count": len(REGRESSION_NUMERIC_FEATURES)
        + len(REGRESSION_CATEGORICAL_FEATURES),
    }


def regression_weather_hrrr_probabilities(
    example: ForecastExample,
    fit: dict[str, Any],
    fallback_probabilities: tuple[float, ...],
    probability_floor: float = PROBABILITY_FLOOR,
) -> tuple[float, ...]:
    model = fit.get("model")
    if model is None:
        return fallback_probabilities
    features, _, groups = _regression_feature_rows([example])
    probabilities = model.predict_proba(pd.DataFrame(features))
    classes = list(model.classes_)  # type: ignore[attr-defined]
    positive_index = classes.index(1)
    start, end = groups[0]
    scores = [_safe_probability(float(probabilities[index][positive_index])) for index in range(start, end)]
    normalized = normalize(scores, "regression_trained_weather_hrrr")
    return apply_probability_floor(normalized, probability_floor)


def anchored_regression_examples(
    examples: list[ForecastExample],
    regression_fit: dict[str, Any],
    anchor_probabilities_by_key: dict[tuple[str, str, str, str], tuple[float, ...]],
    probability_floor: float,
) -> list[ForecastExample]:
    anchored: list[ForecastExample] = []
    for example in examples:
        key = (example.target_date, example.city, example.checkpoint, example.as_of)
        anchor = anchor_probabilities_by_key[key]
        regression = regression_weather_hrrr_probabilities(
            example,
            regression_fit,
            anchor,
            probability_floor,
        )
        anchored.append(
            ForecastExample(
                city=example.city,
                target_date=example.target_date,
                event_ticker=example.event_ticker,
                checkpoint=example.checkpoint,
                scheduled_at=example.scheduled_at,
                as_of=example.as_of,
                winner_ticker=example.winner_ticker,
                tickers=example.tickers,
                distributions={
                    "checkpoint_anchor": anchor,
                    "regression_hrrr": regression,
                },
            )
        )
    return anchored


def fit_anchored_regression_blend(
    examples: list[ForecastExample],
    regression_fit: dict[str, Any],
    anchor_probabilities_by_key: dict[tuple[str, str, str, str], tuple[float, ...]],
    grid_step: float,
    regularization: float,
    min_train_events: int,
    probability_floor: float,
) -> dict[str, Any]:
    event_count = len({example.event_key for example in examples})
    if event_count < min_train_events:
        return {
            "weights": {"checkpoint_anchor": 1.0, "regression_hrrr": 0.0},
            "training_forecasts": len(examples),
            "training_events": event_count,
            "objective_log_loss": None,
            "regularized_objective": None,
            "mode": "fallback_min_train_events",
        }
    anchored = anchored_regression_examples(
        examples,
        regression_fit,
        anchor_probabilities_by_key,
        probability_floor,
    )
    fit = fit_blend_weights(
        anchored,
        ANCHORED_REGRESSION_CANDIDATES,
        grid_step,
        regularization,
        probability_floor,
    )
    raw_weights = {
        str(name): float(value) for name, value in fit["weights"].items()
    }
    regression_weight = min(
        raw_weights.get("regression_hrrr", 0.0),
        ANCHORED_MAX_REGRESSION_WEIGHT,
    )
    capped_weights = {
        "checkpoint_anchor": 1.0 - regression_weight,
        "regression_hrrr": regression_weight,
    }
    return {
        **fit,
        "weights": capped_weights,
        "raw_weights": raw_weights,
        "max_regression_weight": ANCHORED_MAX_REGRESSION_WEIGHT,
        "anchor_training_events": event_count,
        "regression_mode": regression_fit["mode"],
    }


def anchored_regression_probabilities(
    example: ForecastExample,
    regression_fit: dict[str, Any],
    anchor_probabilities: tuple[float, ...],
    anchor_weights: dict[str, float],
    probability_floor: float = PROBABILITY_FLOOR,
) -> tuple[float, ...]:
    regression = regression_weather_hrrr_probabilities(
        example,
        regression_fit,
        anchor_probabilities,
        probability_floor,
    )
    return blend_probabilities(
        {
            "checkpoint_anchor": anchor_probabilities,
            "regression_hrrr": regression,
        },
        anchor_weights,
        probability_floor,
    )


def expected_bracket_index(probabilities: Iterable[float]) -> float:
    values = normalize(probabilities, "expected bracket index")
    return sum(index * probability for index, probability in enumerate(values))


def distribution_variance(probabilities: Iterable[float]) -> float:
    values = normalize(probabilities, "distribution variance")
    expected = expected_bracket_index(values)
    return sum(((index - expected) ** 2) * probability for index, probability in enumerate(values))


def distribution_entropy(probabilities: Iterable[float]) -> float:
    values = normalize(probabilities, "distribution entropy")
    return -sum(probability * math.log(max(probability, 1e-12)) for probability in values)


def top_probability_gap(probabilities: Iterable[float]) -> float:
    values = sorted(normalize(probabilities, "top probability gap"), reverse=True)
    if len(values) < 2:
        return values[0]
    return values[0] - values[1]


def _metadata_float(metadata: dict[str, Any], key: str, default: float) -> float:
    value = metadata.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _calibrated_base_probabilities(example: ForecastExample) -> tuple[float, ...]:
    if CALIBRATED_BASE_MODEL in example.distributions:
        return example.distributions[CALIBRATED_BASE_MODEL]
    if "family_centered" in example.distributions:
        return example.distributions["family_centered"]
    return example.distributions["full"]


def calibrated_feature_row(
    example: ForecastExample,
    include_market: bool,
) -> dict[str, Any]:
    base = _calibrated_base_probabilities(example)
    family = example.distributions.get("family_centered", base)
    market = example.distributions.get("market_midpoint")
    metadata = example.feature_metadata
    base_expected = expected_bracket_index(base)
    family_expected = expected_bracket_index(family)
    top_index = float(_top_index(tuple(base)))
    row: dict[str, Any] = {
        "city": example.city,
        "checkpoint": example.checkpoint,
        "base_expected_index": base_expected,
        "base_top_index": top_index,
        "base_top_probability": max(base),
        "base_top_two_gap": top_probability_gap(base),
        "base_entropy": distribution_entropy(base),
        "base_variance": distribution_variance(base),
        "nws_index": _metadata_float(metadata, "nws_index", base_expected),
        "observed_index": _metadata_float(metadata, "observed_index", base_expected),
        "observed_age_hours": _metadata_float(metadata, "observed_age_hours", 999.0),
        "hrrr_index": _metadata_float(metadata, "hrrr_index", base_expected),
        "hrrr_minus_base_expected": _metadata_float(metadata, "hrrr_index", base_expected)
        - base_expected,
        "nws_minus_base_expected": _metadata_float(metadata, "nws_index", base_expected)
        - base_expected,
        "observed_minus_base_expected": _metadata_float(
            metadata, "observed_index", base_expected
        )
        - base_expected,
        "family_expected_index": family_expected,
        "family_minus_base_expected": family_expected - base_expected,
        "family_spread_f": _metadata_float(metadata, "family_spread_f", 0.0),
        "raw_consensus_minus_nws_f": _metadata_float(
            metadata, "raw_consensus_minus_nws_f", 0.0
        ),
        "center_shift_f": _metadata_float(metadata, "center_shift_f", 0.0),
        "bandwidth_f": _metadata_float(metadata, "bandwidth_f", 1.0),
        "has_nws_index": float(metadata.get("nws_index") is not None),
        "has_observed_index": float(metadata.get("observed_index") is not None),
        "has_hrrr_index": float(metadata.get("hrrr_index") is not None),
    }
    if include_market:
        market_expected = expected_bracket_index(market) if market is not None else base_expected
        row["market_expected_index"] = market_expected
        row["market_minus_base_expected"] = market_expected - base_expected
    return row


def calibrated_target_error(example: ForecastExample) -> float:
    winner_index = example.tickers.index(example.winner_ticker)
    return float(winner_index) - expected_bracket_index(_calibrated_base_probabilities(example))


def fit_calibrated_temperature_error_model(
    examples: list[ForecastExample],
    min_train_events: int,
    include_market: bool = False,
) -> dict[str, Any]:
    event_count = len({example.event_key for example in examples})
    feature_names = (
        CALIBRATED_MARKET_NUMERIC_FEATURES
        if include_market
        else CALIBRATED_NUMERIC_FEATURES
    )
    if event_count < min_train_events:
        return {
            "model": None,
            "mode": "fallback_min_train_events",
            "training_forecasts": len(examples),
            "training_events": event_count,
            "feature_count": len(feature_names) + len(CALIBRATED_CATEGORICAL_FEATURES),
            "global_residual_spread": CALIBRATED_MAX_SPREAD_BRACKETS,
            "checkpoint_residual_spreads": {},
        }
    features = [calibrated_feature_row(example, include_market) for example in examples]
    labels = [calibrated_target_error(example) for example in examples]
    transformer = ColumnTransformer(
        transformers=[
            ("numeric", StandardScaler(), list(feature_names)),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                list(CALIBRATED_CATEGORICAL_FEATURES),
            ),
        ],
        remainder="drop",
    )
    model = Pipeline(
        steps=[
            ("features", transformer),
            ("regressor", Ridge(alpha=1.0)),
        ]
    )
    model.fit(pd.DataFrame(features), labels)
    predictions = [float(value) for value in model.predict(pd.DataFrame(features))]
    residuals = [
        label - prediction
        for label, prediction in zip(labels, predictions, strict=True)
    ]
    global_spread = residual_spread(residuals)
    by_checkpoint: dict[str, list[float]] = defaultdict(list)
    for example, residual in zip(examples, residuals, strict=True):
        by_checkpoint[example.checkpoint].append(residual)
    checkpoint_spreads = {
        checkpoint: residual_spread(values)
        for checkpoint, values in by_checkpoint.items()
        if len({example.event_key for example in examples if example.checkpoint == checkpoint})
        >= CALIBRATED_MIN_CHECKPOINT_RESIDUAL_EVENTS
    }
    return {
        "model": model,
        "mode": "trained",
        "training_forecasts": len(examples),
        "training_events": event_count,
        "feature_count": len(feature_names) + len(CALIBRATED_CATEGORICAL_FEATURES),
        "global_residual_spread": global_spread,
        "checkpoint_residual_spreads": checkpoint_spreads,
    }


def residual_spread(residuals: list[float]) -> float:
    if len(residuals) < 2:
        spread = CALIBRATED_MAX_SPREAD_BRACKETS
    else:
        spread = statistics.pstdev(residuals)
    return min(
        CALIBRATED_MAX_SPREAD_BRACKETS,
        max(CALIBRATED_MIN_SPREAD_BRACKETS, spread),
    )


def finite_normal_index_probabilities(
    center: float,
    spread: float,
    count: int,
    probability_floor: float = PROBABILITY_FLOOR,
) -> tuple[float, ...]:
    spread = min(
        CALIBRATED_MAX_SPREAD_BRACKETS,
        max(CALIBRATED_MIN_SPREAD_BRACKETS, spread),
    )

    def cdf(value: float) -> float:
        return 0.5 * (1.0 + math.erf((value - center) / (spread * math.sqrt(2.0))))

    probabilities: list[float] = []
    for index in range(count):
        lower = float("-inf") if index == 0 else index - 0.5
        upper = float("inf") if index == count - 1 else index + 0.5
        lower_cdf = 0.0 if math.isinf(lower) else cdf(lower)
        upper_cdf = 1.0 if math.isinf(upper) else cdf(upper)
        probabilities.append(max(0.0, upper_cdf - lower_cdf))
    return apply_probability_floor(normalize(probabilities, "calibrated temperature error"), probability_floor)


def calibrated_temperature_error_probabilities(
    example: ForecastExample,
    fit: dict[str, Any],
    fallback_probabilities: tuple[float, ...],
    include_market: bool = False,
    probability_floor: float = PROBABILITY_FLOOR,
) -> tuple[float, ...]:
    model = fit.get("model")
    if model is None:
        return fallback_probabilities
    feature = calibrated_feature_row(example, include_market)
    predicted_error = float(model.predict(pd.DataFrame([feature]))[0])
    base_expected = expected_bracket_index(_calibrated_base_probabilities(example))
    center = base_expected + predicted_error
    spread = float(
        fit.get("checkpoint_residual_spreads", {}).get(
            example.checkpoint, fit.get("global_residual_spread", CALIBRATED_MAX_SPREAD_BRACKETS)
        )
    )
    return finite_normal_index_probabilities(
        center,
        spread,
        len(example.tickers),
        probability_floor,
    )


def calibrated_diagnostic_row(
    example: ForecastExample,
    fit: dict[str, Any],
    include_market: bool,
) -> dict[str, Any]:
    feature = calibrated_feature_row(example, include_market)
    model = fit.get("model")
    predicted_error = (
        float(model.predict(pd.DataFrame([feature]))[0])
        if model is not None
        else None
    )
    base_expected = expected_bracket_index(_calibrated_base_probabilities(example))
    winner_index = example.tickers.index(example.winner_ticker)
    spread = float(
        fit.get("checkpoint_residual_spreads", {}).get(
            example.checkpoint, fit.get("global_residual_spread", CALIBRATED_MAX_SPREAD_BRACKETS)
        )
    )
    return {
        "city": example.city,
        "target_date": example.target_date,
        "checkpoint": example.checkpoint,
        "winner_ticker": example.winner_ticker,
        "winner_index": winner_index,
        "base_expected_index": base_expected,
        "base_top_index": feature["base_top_index"],
        "predicted_correction": predicted_error,
        "actual_correction": winner_index - base_expected,
        "residual_error": (
            winner_index - base_expected - predicted_error
            if predicted_error is not None
            else None
        ),
        "predicted_spread": spread,
        "calibrated_expected_index": (
            base_expected + predicted_error if predicted_error is not None else None
        ),
        "top_two_probability_gap": feature["base_top_two_gap"],
        "hrrr_minus_base_expected": feature["hrrr_minus_base_expected"],
        "nws_minus_base_expected": feature["nws_minus_base_expected"],
        "observed_minus_base_expected": feature["observed_minus_base_expected"],
        "include_market": include_market,
        "mode": fit["mode"],
        "training_events": fit["training_events"],
    }


def expanding_window_scores(
    examples: list[ForecastExample],
    candidate_names: tuple[str, ...],
    grid_step: float,
    regularization: float,
    min_train_events: int,
    probability_floor: float = PROBABILITY_FLOOR,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    weight_rows: list[dict[str, Any]] = []
    dates = sorted({example.target_date for example in examples})
    for target_date in dates:
        train = [example for example in examples if example.target_date < target_date]
        test = [example for example in examples if example.target_date == target_date]
        stage_weather_models = examples_have_candidates(train + test, WEATHER_CANDIDATES)
        unique_train_events = len({example.event_key for example in train})
        if unique_train_events >= min_train_events:
            fit = fit_blend_weights(
                train, candidate_names, grid_step, regularization, probability_floor
            )
        else:
            fit = {
                "weights": prior_weights(candidate_names),
                "training_forecasts": len(train),
                "training_events": unique_train_events,
                "objective_log_loss": None,
                "regularized_objective": None,
                "mode": "fallback_min_train_events",
            }
        weights = {str(name): float(value) for name, value in fit["weights"].items()}
        weather_fit: dict[str, Any] | None = None
        weather_weights: dict[str, float] = {}
        hrrr_fit: dict[str, Any] | None = None
        hrrr_weights: dict[str, float] = {}
        regression_fit: dict[str, Any] | None = None
        anchored_fit: dict[str, Any] | None = None
        calibrated_fit: dict[str, Any] | None = None
        calibrated_market_fit: dict[str, Any] | None = None
        if stage_weather_models:
            weather_fit = _fit_or_fallback(
                train,
                WEATHER_CANDIDATES,
                grid_step,
                regularization,
                min_train_events,
                probability_floor,
            )
            weather_weights = {
                str(name): float(value) for name, value in weather_fit["weights"].items()
            }
            hrrr_fit = _hrrr_stage_fit(
                train,
                weather_weights,
                grid_step,
                regularization,
                min_train_events,
                probability_floor,
            )
            hrrr_weights = {
                str(name): float(value) for name, value in hrrr_fit["weights"].items()
            }
            regression_fit = fit_regression_weather_hrrr(train, min_train_events)
            global_anchor_map = {
                (
                    example.target_date,
                    example.city,
                    example.checkpoint,
                    example.as_of,
                ): staged_hrrr_probabilities(
                    example,
                    weather_weights,
                    hrrr_weights,
                    probability_floor,
                )
                for example in train
            }
            anchored_fit = fit_anchored_regression_blend(
                train,
                regression_fit,
                global_anchor_map,
                grid_step,
                regularization,
                min_train_events,
                probability_floor,
            )
            calibrated_fit = fit_calibrated_temperature_error_model(
                train, CALIBRATED_MIN_TRAIN_EVENTS, include_market=False
            )
            calibrated_market_fit = fit_calibrated_temperature_error_model(
                train, CALIBRATED_MIN_TRAIN_EVENTS, include_market=True
            )
        weight_rows.append(
            {
                "target_date": target_date,
                "mode": fit["mode"],
                "training_forecasts": fit["training_forecasts"],
                "training_events": fit["training_events"],
                "objective_log_loss": fit["objective_log_loss"],
                "regularized_objective": fit.get("regularized_objective"),
                "weights_json": json.dumps(weights, sort_keys=True),
                "checkpoint": "all",
                "model": "trained_blend",
            }
        )
        if weather_fit is not None and hrrr_fit is not None:
            weight_rows.append(
                {
                    "target_date": target_date,
                    "checkpoint": "all",
                    "model": "trained_weather_blend",
                    "mode": weather_fit["mode"],
                    "training_forecasts": weather_fit["training_forecasts"],
                    "training_events": weather_fit["training_events"],
                    "objective_log_loss": weather_fit["objective_log_loss"],
                    "regularized_objective": weather_fit.get("regularized_objective"),
                    "weights_json": json.dumps(weather_weights, sort_keys=True),
                }
            )
            weight_rows.append(
                {
                    "target_date": target_date,
                    "checkpoint": "all",
                    "model": "trained_weather_hrrr_blend",
                    "mode": hrrr_fit["mode"],
                    "training_forecasts": hrrr_fit["training_forecasts"],
                    "training_events": hrrr_fit["training_events"],
                    "hrrr_training_events": hrrr_fit.get("hrrr_training_events"),
                    "objective_log_loss": hrrr_fit["objective_log_loss"],
                    "regularized_objective": hrrr_fit.get("regularized_objective"),
                    "weights_json": json.dumps(hrrr_weights, sort_keys=True),
                }
            )
            if regression_fit is not None:
                weight_rows.append(
                    {
                        "target_date": target_date,
                        "checkpoint": "all",
                        "model": "regression_trained_weather_hrrr",
                        "mode": regression_fit["mode"],
                        "training_forecasts": regression_fit["training_forecasts"],
                        "training_events": regression_fit["training_events"],
                        "feature_count": regression_fit["feature_count"],
                        "weights_json": None,
                    }
                )
            if anchored_fit is not None:
                weight_rows.append(
                    {
                        "target_date": target_date,
                        "checkpoint": "all",
                        "model": ANCHORED_REGRESSION_MODEL,
                        "mode": anchored_fit["mode"],
                        "training_forecasts": anchored_fit["training_forecasts"],
                        "training_events": anchored_fit["training_events"],
                        "feature_count": regression_fit["feature_count"]
                        if regression_fit is not None
                        else None,
                        "anchor_training_events": anchored_fit.get(
                            "anchor_training_events"
                        ),
                        "regression_mode": anchored_fit.get("regression_mode"),
                        "objective_log_loss": anchored_fit["objective_log_loss"],
                        "regularized_objective": anchored_fit.get(
                            "regularized_objective"
                        ),
                        "weights_json": json.dumps(
                            {
                                str(name): float(value)
                                for name, value in anchored_fit["weights"].items()
                            },
                            sort_keys=True,
                        ),
                    }
                )
            if calibrated_fit is not None and calibrated_market_fit is not None:
                for model_name, calibrated in (
                    ("calibrated_temperature_error_model", calibrated_fit),
                    (
                        "calibrated_temperature_error_market_aware",
                        calibrated_market_fit,
                    ),
                ):
                    weight_rows.append(
                        {
                            "target_date": target_date,
                            "checkpoint": "all",
                            "model": model_name,
                            "mode": calibrated["mode"],
                            "training_forecasts": calibrated["training_forecasts"],
                            "training_events": calibrated["training_events"],
                            "feature_count": calibrated["feature_count"],
                            "global_residual_spread": calibrated[
                                "global_residual_spread"
                            ],
                            "checkpoint_residual_spreads_json": json.dumps(
                                calibrated["checkpoint_residual_spreads"],
                                sort_keys=True,
                            ),
                            "weights_json": None,
                        }
                    )
        checkpoint_fits: dict[str, dict[str, Any]] = {}
        checkpoint_weather_fits: dict[str, dict[str, Any]] = {}
        checkpoint_hrrr_fits: dict[str, dict[str, Any]] = {}
        checkpoint_anchored_fits: dict[str, dict[str, Any]] = {}
        for checkpoint in sorted({example.checkpoint for example in test}):
            checkpoint_train = [
                example for example in train if example.checkpoint == checkpoint
            ]
            checkpoint_events = len({example.event_key for example in checkpoint_train})
            if checkpoint_events >= min_train_events:
                checkpoint_fit = fit_blend_weights(
                    checkpoint_train,
                    candidate_names,
                    grid_step,
                    regularization,
                    probability_floor,
                )
            elif fit["mode"] == "trained":
                checkpoint_fit = {
                    **fit,
                    "mode": "fallback_global_weights",
                    "checkpoint_training_forecasts": len(checkpoint_train),
                    "checkpoint_training_events": checkpoint_events,
                }
            else:
                checkpoint_fit = {
                    "weights": prior_weights(candidate_names),
                    "training_forecasts": len(train),
                    "training_events": unique_train_events,
                    "objective_log_loss": None,
                    "regularized_objective": None,
                    "mode": "fallback_min_train_events",
                    "checkpoint_training_forecasts": len(checkpoint_train),
                    "checkpoint_training_events": checkpoint_events,
                }
            checkpoint_fits[checkpoint] = checkpoint_fit
            if weather_fit is not None and hrrr_fit is not None:
                if checkpoint_events >= min_train_events:
                    checkpoint_weather_fit = fit_blend_weights(
                        checkpoint_train,
                        WEATHER_CANDIDATES,
                        grid_step,
                        regularization,
                        probability_floor,
                    )
                elif weather_fit["mode"] == "trained":
                    checkpoint_weather_fit = {
                        **weather_fit,
                        "mode": "fallback_global_weather_weights",
                        "checkpoint_training_forecasts": len(checkpoint_train),
                        "checkpoint_training_events": checkpoint_events,
                    }
                else:
                    checkpoint_weather_fit = {
                        "weights": prior_weights(WEATHER_CANDIDATES),
                        "training_forecasts": len(train),
                        "training_events": unique_train_events,
                        "objective_log_loss": None,
                        "regularized_objective": None,
                        "mode": "fallback_min_train_events",
                        "checkpoint_training_forecasts": len(checkpoint_train),
                        "checkpoint_training_events": checkpoint_events,
                    }
                checkpoint_weather_weights = {
                    str(name): float(value)
                    for name, value in checkpoint_weather_fit["weights"].items()
                }
                checkpoint_hrrr_train = [
                    example
                    for example in checkpoint_train
                    if "hrrr_top3_rerank" in example.distributions
                ]
                checkpoint_hrrr_events = len(
                    {example.event_key for example in checkpoint_hrrr_train}
                )
                if checkpoint_hrrr_events >= min_train_events:
                    checkpoint_hrrr_fit = _hrrr_stage_fit(
                        checkpoint_train,
                        checkpoint_weather_weights,
                        grid_step,
                        regularization,
                        min_train_events,
                        probability_floor,
                    )
                elif hrrr_fit["mode"] == "trained":
                    checkpoint_hrrr_fit = {
                        **hrrr_fit,
                        "mode": "fallback_global_hrrr_weights",
                        "checkpoint_training_forecasts": len(checkpoint_hrrr_train),
                        "checkpoint_training_events": checkpoint_hrrr_events,
                    }
                else:
                    checkpoint_hrrr_fit = {
                        "weights": {
                            "trained_weather_blend": 1.0,
                            "hrrr_top3_rerank": 0.0,
                        },
                        "training_forecasts": len(checkpoint_hrrr_train),
                        "training_events": checkpoint_hrrr_events,
                        "hrrr_training_events": checkpoint_hrrr_events,
                        "objective_log_loss": None,
                        "regularized_objective": None,
                        "mode": "fallback_no_prior_hrrr_training_data",
                        "checkpoint_training_forecasts": len(checkpoint_hrrr_train),
                        "checkpoint_training_events": checkpoint_hrrr_events,
                    }
                checkpoint_weather_fits[checkpoint] = checkpoint_weather_fit
                checkpoint_hrrr_fits[checkpoint] = checkpoint_hrrr_fit
                if regression_fit is not None and anchored_fit is not None:
                    checkpoint_anchor_map = {
                        (
                            example.target_date,
                            example.city,
                            example.checkpoint,
                            example.as_of,
                        ): staged_hrrr_probabilities(
                            example,
                            checkpoint_weather_weights,
                            {
                                str(name): float(value)
                                for name, value in checkpoint_hrrr_fit[
                                    "weights"
                                ].items()
                            },
                            probability_floor,
                        )
                        for example in checkpoint_train
                    }
                    if checkpoint_hrrr_events >= min_train_events:
                        checkpoint_anchored_fit = fit_anchored_regression_blend(
                            checkpoint_train,
                            regression_fit,
                            checkpoint_anchor_map,
                            grid_step,
                            regularization,
                            min_train_events,
                            probability_floor,
                        )
                    elif anchored_fit["mode"] == "trained":
                        checkpoint_anchored_fit = {
                            **anchored_fit,
                            "mode": "fallback_global_anchored_regression_weights",
                            "checkpoint_training_forecasts": len(checkpoint_train),
                            "checkpoint_training_events": checkpoint_events,
                        }
                    else:
                        checkpoint_anchored_fit = {
                            "weights": {
                                "checkpoint_anchor": 1.0,
                                "regression_hrrr": 0.0,
                            },
                            "training_forecasts": len(checkpoint_train),
                            "training_events": checkpoint_events,
                            "objective_log_loss": None,
                            "regularized_objective": None,
                            "mode": "fallback_min_train_events",
                            "checkpoint_training_forecasts": len(checkpoint_train),
                            "checkpoint_training_events": checkpoint_events,
                        }
                    checkpoint_anchored_fits[checkpoint] = checkpoint_anchored_fit
            weight_rows.append(
                {
                    "target_date": target_date,
                    "checkpoint": checkpoint,
                    "model": "checkpoint_trained_blend",
                    "mode": checkpoint_fit["mode"],
                    "training_forecasts": checkpoint_fit["training_forecasts"],
                    "training_events": checkpoint_fit["training_events"],
                    "checkpoint_training_forecasts": checkpoint_fit.get(
                        "checkpoint_training_forecasts",
                        checkpoint_fit["training_forecasts"],
                    ),
                    "checkpoint_training_events": checkpoint_fit.get(
                        "checkpoint_training_events",
                        checkpoint_fit["training_events"],
                    ),
                    "objective_log_loss": checkpoint_fit["objective_log_loss"],
                    "regularized_objective": checkpoint_fit.get(
                        "regularized_objective"
                    ),
                    "weights_json": json.dumps(
                        {
                            str(name): float(value)
                            for name, value in checkpoint_fit["weights"].items()
                        },
                        sort_keys=True,
                    ),
                }
            )
            if weather_fit is not None and hrrr_fit is not None:
                checkpoint_weather_fit = checkpoint_weather_fits[checkpoint]
                checkpoint_hrrr_fit = checkpoint_hrrr_fits[checkpoint]
                checkpoint_anchored_fit = checkpoint_anchored_fits.get(checkpoint)
                weight_rows.append(
                    {
                        "target_date": target_date,
                        "checkpoint": checkpoint,
                        "model": "checkpoint_trained_weather_blend",
                        "mode": checkpoint_weather_fit["mode"],
                        "training_forecasts": checkpoint_weather_fit[
                            "training_forecasts"
                        ],
                        "training_events": checkpoint_weather_fit["training_events"],
                        "checkpoint_training_forecasts": checkpoint_weather_fit.get(
                            "checkpoint_training_forecasts",
                            checkpoint_weather_fit["training_forecasts"],
                        ),
                        "checkpoint_training_events": checkpoint_weather_fit.get(
                            "checkpoint_training_events",
                            checkpoint_weather_fit["training_events"],
                        ),
                        "objective_log_loss": checkpoint_weather_fit[
                            "objective_log_loss"
                        ],
                        "regularized_objective": checkpoint_weather_fit.get(
                            "regularized_objective"
                        ),
                        "weights_json": json.dumps(
                            {
                                str(name): float(value)
                                for name, value in checkpoint_weather_fit[
                                    "weights"
                                ].items()
                            },
                            sort_keys=True,
                        ),
                    }
                )
                if checkpoint_anchored_fit is not None:
                    weight_rows.append(
                        {
                            "target_date": target_date,
                            "checkpoint": checkpoint,
                            "model": ANCHORED_REGRESSION_MODEL,
                            "mode": checkpoint_anchored_fit["mode"],
                            "training_forecasts": checkpoint_anchored_fit[
                                "training_forecasts"
                            ],
                            "training_events": checkpoint_anchored_fit[
                                "training_events"
                            ],
                            "checkpoint_training_forecasts": checkpoint_anchored_fit.get(
                                "checkpoint_training_forecasts",
                                checkpoint_anchored_fit["training_forecasts"],
                            ),
                            "checkpoint_training_events": checkpoint_anchored_fit.get(
                                "checkpoint_training_events",
                                checkpoint_anchored_fit["training_events"],
                            ),
                            "anchor_training_events": checkpoint_anchored_fit.get(
                                "anchor_training_events"
                            ),
                            "regression_mode": checkpoint_anchored_fit.get(
                                "regression_mode"
                            ),
                            "objective_log_loss": checkpoint_anchored_fit[
                                "objective_log_loss"
                            ],
                            "regularized_objective": checkpoint_anchored_fit.get(
                                "regularized_objective"
                            ),
                            "weights_json": json.dumps(
                                {
                                    str(name): float(value)
                                    for name, value in checkpoint_anchored_fit[
                                        "weights"
                                    ].items()
                                },
                                sort_keys=True,
                            ),
                        }
                    )
                weight_rows.append(
                    {
                        "target_date": target_date,
                        "checkpoint": checkpoint,
                        "model": "checkpoint_trained_weather_hrrr_blend",
                        "mode": checkpoint_hrrr_fit["mode"],
                        "training_forecasts": checkpoint_hrrr_fit[
                            "training_forecasts"
                        ],
                        "training_events": checkpoint_hrrr_fit["training_events"],
                        "hrrr_training_events": checkpoint_hrrr_fit.get(
                            "hrrr_training_events"
                        ),
                        "checkpoint_training_forecasts": checkpoint_hrrr_fit.get(
                            "checkpoint_training_forecasts",
                            checkpoint_hrrr_fit["training_forecasts"],
                        ),
                        "checkpoint_training_events": checkpoint_hrrr_fit.get(
                            "checkpoint_training_events",
                            checkpoint_hrrr_fit["training_events"],
                        ),
                        "objective_log_loss": checkpoint_hrrr_fit["objective_log_loss"],
                        "regularized_objective": checkpoint_hrrr_fit.get(
                            "regularized_objective"
                        ),
                        "weights_json": json.dumps(
                            {
                                str(name): float(value)
                                for name, value in checkpoint_hrrr_fit[
                                    "weights"
                                ].items()
                            },
                            sort_keys=True,
                        ),
                    }
                )
        for example in test:
            trained = score_model(
                example,
                "trained_blend",
                blend_probabilities(example.distributions, weights, probability_floor),
            )
            trained.update(
                {
                    "training_mode": fit["mode"],
                    "training_forecasts": fit["training_forecasts"],
                    "training_events": fit["training_events"],
                    "weights_json": json.dumps(weights, sort_keys=True),
                }
            )
            rows.append(trained)
            if weather_fit is not None and hrrr_fit is not None:
                weather_trained = score_model(
                    example,
                    "trained_weather_blend",
                    blend_probabilities(
                        example.distributions, weather_weights, probability_floor
                    ),
                )
                weather_trained.update(
                    {
                        "training_mode": weather_fit["mode"],
                        "training_forecasts": weather_fit["training_forecasts"],
                        "training_events": weather_fit["training_events"],
                        "weights_json": json.dumps(weather_weights, sort_keys=True),
                    }
                )
                rows.append(weather_trained)
                hrrr_trained = score_model(
                    example,
                    "trained_weather_hrrr_blend",
                    staged_hrrr_probabilities(
                        example,
                        weather_weights,
                        hrrr_weights,
                        probability_floor,
                    ),
                )
                hrrr_trained.update(
                    {
                        "training_mode": hrrr_fit["mode"],
                        "training_forecasts": hrrr_fit["training_forecasts"],
                        "training_events": hrrr_fit["training_events"],
                        "hrrr_training_events": hrrr_fit.get("hrrr_training_events"),
                        "weights_json": json.dumps(hrrr_weights, sort_keys=True),
                        "has_hrrr": "hrrr_top3_rerank" in example.distributions,
                    }
                )
                rows.append(hrrr_trained)
                if regression_fit is not None:
                    regression_trained = score_model(
                        example,
                        "regression_trained_weather_hrrr",
                        regression_weather_hrrr_probabilities(
                            example,
                            regression_fit,
                            staged_hrrr_probabilities(
                                example,
                                weather_weights,
                                hrrr_weights,
                                probability_floor,
                            ),
                            probability_floor,
                        ),
                    )
                    regression_trained.update(
                        {
                            "training_mode": regression_fit["mode"],
                            "training_forecasts": regression_fit["training_forecasts"],
                            "training_events": regression_fit["training_events"],
                            "feature_count": regression_fit["feature_count"],
                            "has_hrrr": "hrrr_top3_rerank" in example.distributions,
                            "weights_json": None,
                        }
                    )
                    rows.append(regression_trained)
            checkpoint_fit = checkpoint_fits[example.checkpoint]
            checkpoint_weights = {
                str(name): float(value)
                for name, value in checkpoint_fit["weights"].items()
            }
            checkpoint_trained = score_model(
                example,
                "checkpoint_trained_blend",
                blend_probabilities(
                    example.distributions, checkpoint_weights, probability_floor
                ),
            )
            checkpoint_trained.update(
                {
                    "training_mode": checkpoint_fit["mode"],
                    "training_forecasts": checkpoint_fit["training_forecasts"],
                    "training_events": checkpoint_fit["training_events"],
                    "checkpoint_training_forecasts": checkpoint_fit.get(
                        "checkpoint_training_forecasts",
                        checkpoint_fit["training_forecasts"],
                    ),
                    "checkpoint_training_events": checkpoint_fit.get(
                        "checkpoint_training_events",
                        checkpoint_fit["training_events"],
                    ),
                    "weights_json": json.dumps(checkpoint_weights, sort_keys=True),
                }
            )
            rows.append(checkpoint_trained)
            if weather_fit is not None and hrrr_fit is not None:
                checkpoint_weather_fit = checkpoint_weather_fits[example.checkpoint]
                checkpoint_weather_weights = {
                    str(name): float(value)
                    for name, value in checkpoint_weather_fit["weights"].items()
                }
                checkpoint_weather_trained = score_model(
                    example,
                    "checkpoint_trained_weather_blend",
                    blend_probabilities(
                        example.distributions,
                        checkpoint_weather_weights,
                        probability_floor,
                    ),
                )
                checkpoint_weather_trained.update(
                    {
                        "training_mode": checkpoint_weather_fit["mode"],
                        "training_forecasts": checkpoint_weather_fit[
                            "training_forecasts"
                        ],
                        "training_events": checkpoint_weather_fit["training_events"],
                        "checkpoint_training_forecasts": checkpoint_weather_fit.get(
                            "checkpoint_training_forecasts",
                            checkpoint_weather_fit["training_forecasts"],
                        ),
                        "checkpoint_training_events": checkpoint_weather_fit.get(
                            "checkpoint_training_events",
                            checkpoint_weather_fit["training_events"],
                        ),
                        "weights_json": json.dumps(
                            checkpoint_weather_weights, sort_keys=True
                        ),
                    }
                )
                rows.append(checkpoint_weather_trained)
                if calibrated_fit is not None and calibrated_market_fit is not None:
                    fallback_probabilities = blend_probabilities(
                        example.distributions,
                        checkpoint_weather_weights,
                        probability_floor,
                    )
                    for model_name, calibrated, include_market in (
                        (
                            "calibrated_temperature_error_model",
                            calibrated_fit,
                            False,
                        ),
                        (
                            "calibrated_temperature_error_market_aware",
                            calibrated_market_fit,
                            True,
                        ),
                    ):
                        calibrated_row = score_model(
                            example,
                            model_name,
                            calibrated_temperature_error_probabilities(
                                example,
                                calibrated,
                                fallback_probabilities,
                                include_market,
                                probability_floor,
                            ),
                        )
                        calibrated_row.update(
                            {
                                "training_mode": calibrated["mode"],
                                "training_forecasts": calibrated[
                                    "training_forecasts"
                                ],
                                "training_events": calibrated["training_events"],
                                "feature_count": calibrated["feature_count"],
                                "global_residual_spread": calibrated[
                                    "global_residual_spread"
                                ],
                                "checkpoint_residual_spreads_json": json.dumps(
                                    calibrated["checkpoint_residual_spreads"],
                                    sort_keys=True,
                                ),
                                "weights_json": None,
                            }
                        )
                        rows.append(calibrated_row)
                checkpoint_hrrr_fit = checkpoint_hrrr_fits[example.checkpoint]
                checkpoint_hrrr_weights = {
                    str(name): float(value)
                    for name, value in checkpoint_hrrr_fit["weights"].items()
                }
                checkpoint_hrrr_trained = score_model(
                    example,
                    "checkpoint_trained_weather_hrrr_blend",
                    staged_hrrr_probabilities(
                        example,
                        checkpoint_weather_weights,
                        checkpoint_hrrr_weights,
                        probability_floor,
                    ),
                )
                checkpoint_hrrr_trained.update(
                    {
                        "training_mode": checkpoint_hrrr_fit["mode"],
                        "training_forecasts": checkpoint_hrrr_fit[
                            "training_forecasts"
                        ],
                        "training_events": checkpoint_hrrr_fit["training_events"],
                        "hrrr_training_events": checkpoint_hrrr_fit.get(
                            "hrrr_training_events"
                        ),
                        "checkpoint_training_forecasts": checkpoint_hrrr_fit.get(
                            "checkpoint_training_forecasts",
                            checkpoint_hrrr_fit["training_forecasts"],
                        ),
                        "checkpoint_training_events": checkpoint_hrrr_fit.get(
                            "checkpoint_training_events",
                            checkpoint_hrrr_fit["training_events"],
                        ),
                        "weights_json": json.dumps(
                            checkpoint_hrrr_weights, sort_keys=True
                        ),
                        "has_hrrr": "hrrr_top3_rerank" in example.distributions,
                    }
                )
                rows.append(checkpoint_hrrr_trained)
                checkpoint_anchored_fit = checkpoint_anchored_fits.get(
                    example.checkpoint
                )
                if regression_fit is not None and checkpoint_anchored_fit is not None:
                    checkpoint_anchor_probabilities = staged_hrrr_probabilities(
                        example,
                        checkpoint_weather_weights,
                        checkpoint_hrrr_weights,
                        probability_floor,
                    )
                    checkpoint_anchored_weights = {
                        str(name): float(value)
                        for name, value in checkpoint_anchored_fit["weights"].items()
                    }
                    anchored_regression = score_model(
                        example,
                        ANCHORED_REGRESSION_MODEL,
                        anchored_regression_probabilities(
                            example,
                            regression_fit,
                            checkpoint_anchor_probabilities,
                            checkpoint_anchored_weights,
                            probability_floor,
                        ),
                    )
                    anchored_regression.update(
                        {
                            "training_mode": checkpoint_anchored_fit["mode"],
                            "training_forecasts": checkpoint_anchored_fit[
                                "training_forecasts"
                            ],
                            "training_events": checkpoint_anchored_fit[
                                "training_events"
                            ],
                            "checkpoint_training_forecasts": checkpoint_anchored_fit.get(
                                "checkpoint_training_forecasts",
                                checkpoint_anchored_fit["training_forecasts"],
                            ),
                            "checkpoint_training_events": checkpoint_anchored_fit.get(
                                "checkpoint_training_events",
                                checkpoint_anchored_fit["training_events"],
                            ),
                            "feature_count": regression_fit["feature_count"],
                            "anchor_training_events": checkpoint_anchored_fit.get(
                                "anchor_training_events"
                            ),
                            "regression_mode": checkpoint_anchored_fit.get(
                                "regression_mode",
                                regression_fit["mode"],
                            ),
                            "has_hrrr": "hrrr_top3_rerank" in example.distributions,
                            "weights_json": json.dumps(
                                checkpoint_anchored_weights,
                                sort_keys=True,
                            ),
                        }
                    )
                    rows.append(anchored_regression)
            for model_name in COMPARISON_MODELS[1:]:
                if (
                    model_name != "checkpoint_trained_blend"
                    and model_name != "trained_weather_blend"
                    and model_name != "checkpoint_trained_weather_blend"
                    and model_name != "trained_weather_hrrr_blend"
                    and model_name != "checkpoint_trained_weather_hrrr_blend"
                    and model_name != "regression_trained_weather_hrrr"
                    and model_name != ANCHORED_REGRESSION_MODEL
                    and model_name != "calibrated_temperature_error_model"
                    and model_name != "calibrated_temperature_error_market_aware"
                    and model_name in example.distributions
                ):
                    comparison = score_model(
                        example, model_name, example.distributions[model_name]
                    )
                    comparison.update(
                        {
                            "training_mode": "comparison",
                            "training_forecasts": None,
                            "training_events": None,
                            "weights_json": None,
                        }
                    )
                    rows.append(comparison)
    return rows, weight_rows


def latest_per_event(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["target_date"]), str(row["city"]), str(row["model"]))].append(row)
    return [
        max(event_rows, key=lambda row: parse_datetime(str(row["as_of"])))
        for event_rows in grouped.values()
    ]


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"forecast_count": len(rows)}
    if not rows:
        return {
            **result,
            "event_count": 0,
            "date_count": 0,
            "log_loss": None,
            "brier": None,
            "ranked_probability_score": None,
            "winner_probability": None,
            "top_one_accuracy": None,
            "top_one_coverage": None,
        }
    covered = [row for row in rows if row["top_one_covered"]]
    capped_log_losses = [
        (
            float(row["log_loss"])
            if row["log_loss"] is not None
            else -math.log(max(float(row["outcome_probability"]), 1e-12))
        )
        for row in rows
    ]
    return {
        **result,
        "event_count": len({(row["target_date"], row["city"]) for row in rows}),
        "date_count": len({row["target_date"] for row in rows}),
        "log_loss": statistics.mean(capped_log_losses),
        "zero_probability_count": sum(bool(row["zero_probability"]) for row in rows),
        "brier": statistics.mean(float(row["brier"]) for row in rows),
        "ranked_probability_score": statistics.mean(
            float(row["ranked_probability_score"]) for row in rows
        ),
        "winner_probability": statistics.mean(
            float(row["outcome_probability"]) for row in rows
        ),
        "top_one_accuracy": (
            statistics.mean(float(row["top_one_correct"]) for row in covered)
            if covered
            else None
        ),
        "top_one_coverage": len(covered) / len(rows),
    }


def aggregate_by_model(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        model: aggregate([row for row in rows if row["model"] == model])
        for model in sorted({str(row["model"]) for row in rows})
    }


def aggregate_by_checkpoint(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    checkpoints = sorted({str(row["checkpoint"]) for row in rows})
    return {
        checkpoint: aggregate_by_model(
            [row for row in rows if row["checkpoint"] == checkpoint]
        )
        for checkpoint in checkpoints
    }


def plot_summary(summary: dict[str, Any], output: Path) -> None:
    latest = summary["latest_per_event"]
    models = [model for model in COMPARISON_MODELS if model in latest]
    labels = [model.replace("_", " ") for model in models]
    positions = list(range(len(models)))
    figure, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
    axes[0].bar(
        positions,
        [latest[model]["log_loss"] for model in models],
        color=["#111827", "#247BA0", "#167D8D", "#E07A5F", "#6B7280"][: len(models)],
    )
    axes[0].set_title("Latest forecast log loss")
    axes[0].set_ylabel("Lower is better")
    axes[0].set_xticks(positions, labels, rotation=25, ha="right")
    axes[0].grid(axis="y", alpha=0.2)

    axes[1].bar(
        positions,
        [
            (latest[model]["top_one_accuracy"] or 0.0) * 100.0
            for model in models
        ],
        color=["#111827", "#247BA0", "#167D8D", "#E07A5F", "#6B7280"][: len(models)],
    )
    axes[1].set_title("Latest forecast top-one accuracy")
    axes[1].set_ylabel("Percent")
    axes[1].set_ylim(0, 105)
    axes[1].set_xticks(positions, labels, rotation=25, ha="right")
    axes[1].grid(axis="y", alpha=0.2)
    figure.suptitle("Offline trained probability blend", fontweight="bold")
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(figure)


def plot_calibrated_comparison(summary: dict[str, Any], output: Path) -> None:
    models = [
        model
        for model in (
            "full",
            "checkpoint_trained_weather_blend",
            "regression_trained_weather_hrrr",
            "calibrated_temperature_error_model",
            "calibrated_temperature_error_market_aware",
            "market_midpoint",
        )
        if model in summary
    ]
    labels = [model.replace("_", " ") for model in models]
    positions = list(range(len(models)))
    figure, axes = plt.subplots(1, 2, figsize=(13, 5), constrained_layout=True)
    axes[0].bar(positions, [summary[model]["log_loss"] for model in models], color="#247BA0")
    axes[0].set_title("Log loss")
    axes[0].set_ylabel("Lower is better")
    axes[0].set_xticks(positions, labels, rotation=25, ha="right")
    axes[0].grid(axis="y", alpha=0.2)
    axes[1].bar(
        positions,
        [(summary[model]["top_one_accuracy"] or 0.0) * 100.0 for model in models],
        color="#D97706",
    )
    axes[1].set_title("Top-one accuracy")
    axes[1].set_ylabel("Percent")
    axes[1].set_ylim(0, 105)
    axes[1].set_xticks(positions, labels, rotation=25, ha="right")
    axes[1].grid(axis="y", alpha=0.2)
    figure.suptitle("Calibrated Temperature Error Model", fontweight="bold")
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(figure)


def write_calibrated_temperature_error_report(
    output_dir: Path,
    examples: list[ForecastExample],
    score_rows: list[dict[str, Any]],
    probability_floor: float,
) -> None:
    feature_rows: list[dict[str, Any]] = []
    residual_rows: list[dict[str, Any]] = []
    by_key = {
        (example.target_date, example.city, example.checkpoint): example
        for example in examples
    }
    for example in examples:
        feature = calibrated_feature_row(example, include_market=False)
        feature_rows.append(
            {
                "target_date": example.target_date,
                "city": example.city,
                "checkpoint": example.checkpoint,
                "winner_ticker": example.winner_ticker,
                "winner_index": example.tickers.index(example.winner_ticker),
                "actual_correction": calibrated_target_error(example),
                **feature,
            }
        )
    for row in score_rows:
        if row["model"] not in (
            "calibrated_temperature_error_model",
            "calibrated_temperature_error_market_aware",
        ):
            continue
        example = by_key[
            (
                str(row["target_date"]),
                str(row["city"]),
                str(row["checkpoint"]),
            )
        ]
        probabilities = tuple(float(value) for value in json.loads(row["probabilities_json"]))
        base_expected = expected_bracket_index(_calibrated_base_probabilities(example))
        calibrated_expected = expected_bracket_index(probabilities)
        winner_index = example.tickers.index(example.winner_ticker)
        residual_rows.append(
            {
                "city": example.city,
                "target_date": example.target_date,
                "checkpoint": example.checkpoint,
                "model": row["model"],
                "winner_ticker": example.winner_ticker,
                "winner_index": winner_index,
                "base_expected_index": base_expected,
                "base_top_index": _top_index(_calibrated_base_probabilities(example)),
                "predicted_correction": calibrated_expected - base_expected,
                "actual_correction": winner_index - base_expected,
                "residual_error": winner_index - calibrated_expected,
                "predicted_spread": row.get("global_residual_spread"),
                "calibrated_expected_index": calibrated_expected,
                "top_two_probability_gap": top_probability_gap(
                    _calibrated_base_probabilities(example)
                ),
                "hrrr_minus_base_expected": calibrated_feature_row(
                    example, include_market=False
                )["hrrr_minus_base_expected"],
                "nws_minus_base_expected": calibrated_feature_row(
                    example, include_market=False
                )["nws_minus_base_expected"],
                "observed_minus_base_expected": calibrated_feature_row(
                    example, include_market=False
                )["observed_minus_base_expected"],
                "include_market": row["model"]
                == "calibrated_temperature_error_market_aware",
                "mode": row.get("training_mode"),
                "training_events": row.get("training_events"),
            }
        )
    calibrated_scores = [
        row
        for row in score_rows
        if row["model"]
        in (
            "full",
            "family_centered",
            "soft_floor_weather",
            "hrrr_top3_rerank",
            "trained_weather_blend",
            "checkpoint_trained_weather_blend",
            "regression_trained_weather_hrrr",
            "calibrated_temperature_error_model",
            "calibrated_temperature_error_market_aware",
            "market_midpoint",
        )
    ]
    latest_summary = aggregate_by_model(latest_per_event(calibrated_scores))
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "feature_rows.csv", feature_rows)
    write_csv(output_dir / "residuals.csv", residual_rows)
    (output_dir / "summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": datetime.now(UTC).isoformat(),
                "model_type": "calibrated_temperature_error_model",
                "probability_floor": probability_floor,
                "base_model": CALIBRATED_BASE_MODEL,
                "fallback_model": CALIBRATED_FALLBACK_MODEL,
                "min_train_events": CALIBRATED_MIN_TRAIN_EVENTS,
                "min_spread_brackets": CALIBRATED_MIN_SPREAD_BRACKETS,
                "max_spread_brackets": CALIBRATED_MAX_SPREAD_BRACKETS,
                "latest_per_event": latest_summary,
                "by_checkpoint": aggregate_by_checkpoint(calibrated_scores),
                "limits": [
                    "This is an offline challenger only.",
                    "Expanding-window rows train only on prior target dates.",
                    "The market-aware variant is diagnostic and must not replace the weather-only model automatically.",
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    plot_calibrated_comparison(latest_summary, output_dir / "comparison.png")


def write_model_card(
    output: Path,
    cohort: str,
    examples: list[ForecastExample],
    candidate_names: tuple[str, ...],
    fit: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    payload = {
        "schema_version": 1,
        "model_type": "offline_probability_blend",
        "status": "trained_from_settled_archived_snapshots_only",
        "cohort": cohort,
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate_distributions": list(candidate_names),
        "weights": fit["weights"],
        "training_forecasts": fit["training_forecasts"],
        "training_events": fit["training_events"],
        "training_dates": sorted({example.target_date for example in examples}),
        "objective_log_loss": fit["objective_log_loss"],
        "regularized_objective": fit.get("regularized_objective"),
        "grid_step": args.grid_step,
        "regularization": args.regularization,
        "probability_floor": args.probability_floor,
        "min_train_events_for_expanding_score": args.min_train_events,
        "important_limits": [
            "This is not a new meteorological model.",
            "It cannot score a forecast unless the same candidate distributions were archived.",
            "Expanding-window scores train only on prior target dates.",
            "Current sample size may be too small for a stable edge estimate.",
        ],
    }
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_candidates(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train an offline probability blend from settled archived snapshots."
    )
    parser.add_argument("--root", type=Path, default=Path("backtest_data"))
    parser.add_argument("--cohort", default="pilot-v1")
    parser.add_argument(
        "--candidates",
        default=",".join(DEFAULT_CANDIDATES),
        help="Comma-separated source distributions to blend.",
    )
    parser.add_argument("--grid-step", type=float, default=0.05)
    parser.add_argument("--regularization", type=float, default=0.01)
    parser.add_argument("--min-train-events", type=int, default=4)
    parser.add_argument("--probability-floor", type=float, default=PROBABILITY_FLOOR)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output/offline_trained_model")
    )
    parser.add_argument(
        "--calibrated-output-dir",
        type=Path,
        default=Path("output/calibrated_temperature_error_model"),
    )
    args = parser.parse_args()

    examples = load_examples(args.root, args.cohort)
    if not examples:
        raise DataError("no settled archived snapshots are available for training")
    candidate_names = parse_candidates(args.candidates)
    validate_candidates(examples, candidate_names)
    fit = fit_blend_weights(
        examples,
        candidate_names,
        args.grid_step,
        args.regularization,
        args.probability_floor,
    )
    checkpoint_models = fit_checkpoint_models(
        examples,
        candidate_names,
        args.grid_step,
        args.regularization,
        args.min_train_events,
        args.probability_floor,
    )
    checkpoint_model_rows = score_checkpoint_models(
        examples, checkpoint_models, args.probability_floor
    )
    score_rows, weight_rows = expanding_window_scores(
        examples,
        candidate_names,
        args.grid_step,
        args.regularization,
        args.min_train_events,
        args.probability_floor,
    )
    latest_rows = latest_per_event(score_rows)
    summary = {
        "schema_version": 1,
        "cohort": args.cohort,
        "generated_at": datetime.now(UTC).isoformat(),
        "settled_forecasts": len(examples),
        "settled_events": len({example.event_key for example in examples}),
        "settled_dates": sorted({example.target_date for example in examples}),
        "candidate_distributions": list(candidate_names),
        "trained_on_all_settled_data": fit,
        "trained_on_all_settled_data_by_checkpoint": checkpoint_models,
        "checkpoint_model_in_sample": aggregate_by_checkpoint(checkpoint_model_rows),
        "expanding_window_method": (
            "For each target date, fit weights on earlier target dates only; "
            "fallback to equal weights until min_train_events is reached."
        ),
        "all_forecasts": aggregate_by_model(score_rows),
        "latest_per_event": aggregate_by_model(latest_rows),
        "by_checkpoint": aggregate_by_checkpoint(score_rows),
        "limits": [
            "This is a trained calibration layer, not a trained weather model.",
            "The sample is still small; do not promote on pilot results alone.",
            "Market distributions are archived quote consensus, not guaranteed executable fills.",
        ],
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "expanding_scores.csv", score_rows)
    write_csv(args.output_dir / "checkpoint_model_scores.csv", checkpoint_model_rows)
    write_csv(args.output_dir / "weights_by_date.csv", weight_rows)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (args.output_dir / "checkpoint_models.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_type": "offline_probability_blends_by_checkpoint",
                "status": "trained_from_settled_archived_snapshots_only",
                "cohort": args.cohort,
                "generated_at": datetime.now(UTC).isoformat(),
                "candidate_distributions": list(candidate_names),
                "grid_step": args.grid_step,
                "regularization": args.regularization,
                "probability_floor": args.probability_floor,
                "min_train_events": args.min_train_events,
                "models": checkpoint_models,
                "important_limits": [
                    "These are in-sample trained weights for each checkpoint.",
                    "Use expanding-window checkpoint_trained_blend scores for leakage-safe evaluation.",
                    "Do not promote a checkpoint model until it improves out-of-sample proper scores.",
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    write_model_card(
        args.output_dir / "trained_model.json",
        args.cohort,
        examples,
        candidate_names,
        fit,
        args,
    )
    plot_summary(summary, args.output_dir / "comparison.png")
    write_calibrated_temperature_error_report(
        args.calibrated_output_dir,
        examples,
        score_rows,
        args.probability_floor,
    )

    latest = summary["latest_per_event"]
    trained = latest["trained_blend"]
    full = latest.get("full")
    market = latest.get("market_midpoint")
    print(
        f"Trained on {fit['training_events']} settled events "
        f"({fit['training_forecasts']} forecasts)."
    )
    print(f"Weights: {json.dumps(fit['weights'], sort_keys=True)}")
    print(
        "Latest per event accuracy: "
        f"trained={trained['top_one_accuracy']:.1%}, "
        f"full={full['top_one_accuracy']:.1%}" if full else ""
    )
    if market:
        print(f"Latest per event market accuracy: {market['top_one_accuracy']:.1%}")
    calibrated = latest.get("calibrated_temperature_error_model")
    if calibrated:
        print(
            "Latest calibrated weather accuracy: "
            f"{calibrated['top_one_accuracy']:.1%}"
        )
    print("Checkpoint models:")
    for checkpoint, checkpoint_fit in checkpoint_models.items():
        print(
            f"  {checkpoint}: events={checkpoint_fit['training_events']} "
            f"weights={json.dumps(checkpoint_fit['weights'], sort_keys=True)}"
        )
    print(f"Results written to {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
