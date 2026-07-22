"""Candidate-set dataset for Edgecaster v2.

V2 intentionally does not overwrite bounded-bracket probabilities from observed
highs. Observed-position features are included, but the neural model must learn
when they matter instead of receiving forced certainty.
"""

from __future__ import annotations

import ast
import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from backtest.data_sources import LocalExportSource
from backtest.load_dataset import load_dataset
from libs.models import BacktestDataset, MarketSnapshot, WeatherSnapshot
from libs.source_families import family_support_count, source_family_features, weather_values

NUMERIC_FEATURES = [
    "yes_probability",
    "outcome_probability",
    "entry_bid",
    "entry_ask",
    "market_midpoint",
    "spread",
    "raw_edge",
    "yes_rank",
    "top_probability",
    "top_margin",
    "entropy",
    "hours_elapsed",
    "hours_remaining",
    "observed_high_so_far_f",
    "distance_to_lower_f",
    "distance_to_upper_f",
    "family_confirmation_count",
    "family_baseline_high_f",
    "family_nws_minus_nbm_f",
    "family_hrrr_minus_nbm_f",
    "family_ensemble_minus_nbm_f",
    "family_numerical_disagreement_f",
    "family_disagreement_range_f",
    "source_std_f",
    "source_range_f",
    "nws_anchor_high_f",
    "hrrr_projected_high_f",
    "nbm_projected_high_f",
    "ensemble_raw_median_high_f",
]
CATEGORICAL_FEATURES = ["city", "side", "checkpoint", "bracket_type"]


@dataclass(frozen=True)
class EdgecasterV2Candidate:
    city: str
    event_ticker: str
    market_ticker: str
    target_date: str
    snapshot_hour_utc: datetime
    side: str
    entry_bid: float
    entry_ask: float
    spread: float
    raw_edge: float
    outcome_probability: float
    reward: float
    win_label: float
    winner_ticker: str
    bracket_type: str
    features: dict[str, Any]


@dataclass(frozen=True)
class CandidateSet:
    key: tuple[str, str, datetime]
    target_date: str
    candidates: list[EdgecasterV2Candidate]


def load_candidate_sets(data_path: str | Path, model_report: str | Path) -> list[CandidateSet]:
    dataset = load_dataset(LocalExportSource(Path(data_path)))
    probabilities = load_probabilities(Path(model_report))
    candidates = build_candidates(dataset, probabilities)
    grouped: dict[tuple[str, str, datetime], list[EdgecasterV2Candidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate.city, candidate.event_ticker, candidate.snapshot_hour_utc)].append(
            candidate
        )
    return [
        CandidateSet(key=key, target_date=items[0].target_date, candidates=items)
        for key, items in sorted(grouped.items(), key=lambda item: item[0][2])
    ]


def load_probabilities(path: Path) -> dict[tuple[str, str, datetime], dict[str, float]]:
    output: dict[tuple[str, str, datetime], dict[str, float]] = {}
    with (path / "bracket_distributions.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            snapshot = datetime.fromisoformat(row["snapshot_hour_utc"])
            output[(row["city"], row["event_ticker"], snapshot)] = {
                str(key): float(value)
                for key, value in ast.literal_eval(row["probabilities"]).items()
            }
    return output


def build_candidates(
    dataset: BacktestDataset,
    probabilities: dict[tuple[str, str, datetime], dict[str, float]],
) -> list[EdgecasterV2Candidate]:
    weather_by_key = {
        (row.city, row.event_ticker, row.snapshot_hour_utc): row for row in dataset.weather
    }
    settlements = {
        row.event_ticker: row for row in dataset.settlements if row.validation_status == "valid"
    }
    output: list[EdgecasterV2Candidate] = []
    for market in sorted(dataset.markets, key=lambda row: row.snapshot_hour_utc):
        settlement = settlements.get(market.event_ticker)
        if settlement is None or settlement.settled_at_utc <= market.snapshot_hour_utc:
            continue
        snapshot_probabilities = probabilities.get(
            (market.city, market.event_ticker, market.snapshot_hour_utc)
        )
        if not snapshot_probabilities or market.market_ticker not in snapshot_probabilities:
            continue
        weather = weather_by_key.get((market.city, market.event_ticker, market.snapshot_hour_utc))
        yes_probability = float(snapshot_probabilities[market.market_ticker])
        stats = _distribution_stats(snapshot_probabilities, market.market_ticker)
        for side, outcome_probability in (("yes", yes_probability), ("no", 1.0 - yes_probability)):
            quote = _quote(market, side)
            if quote is None:
                continue
            bid, ask = quote
            if ask <= 0.0 or ask >= 1.0:
                continue
            yes_won = settlement.winner_ticker == market.market_ticker
            hit = yes_won if side == "yes" else not yes_won
            reward = (1.0 if hit else 0.0) - ask
            features = _features(
                market,
                weather,
                side,
                yes_probability,
                outcome_probability,
                bid,
                ask,
                stats,
            )
            output.append(
                EdgecasterV2Candidate(
                    city=market.city,
                    event_ticker=market.event_ticker,
                    market_ticker=market.market_ticker,
                    target_date=market.target_date.isoformat(),
                    snapshot_hour_utc=market.snapshot_hour_utc,
                    side=side,
                    entry_bid=bid,
                    entry_ask=ask,
                    spread=ask - bid,
                    raw_edge=outcome_probability - ask,
                    outcome_probability=outcome_probability,
                    reward=reward,
                    win_label=1.0 if hit else 0.0,
                    winner_ticker=settlement.winner_ticker,
                    bracket_type=_bracket_type(market),
                    features=features,
                )
            )
    return output


def split_sets(
    sets: list[CandidateSet],
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
) -> tuple[list[CandidateSet], list[CandidateSet]]:
    train = [item for item in sets if train_start <= item.target_date <= train_end]
    test = [item for item in sets if test_start <= item.target_date <= test_end]
    return train, test


def market_history(
    dataset: BacktestDataset,
) -> dict[tuple[str, str], list[MarketSnapshot]]:
    output: dict[tuple[str, str], list[MarketSnapshot]] = defaultdict(list)
    for market in dataset.markets:
        output[(market.event_ticker, market.market_ticker)].append(market)
    return {
        key: sorted(value, key=lambda item: item.snapshot_hour_utc) for key, value in output.items()
    }


def load_dataset_for_trades(data_path: str | Path) -> BacktestDataset:
    return load_dataset(LocalExportSource(Path(data_path)))


def feature_means(candidates: list[EdgecasterV2Candidate]) -> dict[str, float]:
    output = {}
    for name in NUMERIC_FEATURES:
        values = [_finite_float(row.features.get(name)) for row in candidates]
        parsed = [value for value in values if value is not None]
        output[name] = mean(parsed) if parsed else 0.0
    return output


def _features(
    market: MarketSnapshot,
    weather: WeatherSnapshot | None,
    side: str,
    yes_probability: float,
    outcome_probability: float,
    bid: float,
    ask: float,
    stats: dict[str, float],
) -> dict[str, Any]:
    lower = float(market.bracket.lower_f) if market.bracket.lower_f is not None else None
    upper = float(market.bracket.upper_f) if market.bracket.upper_f is not None else None
    observed = weather.observed_high_so_far_f if weather is not None else None
    return {
        "city": market.city,
        "side": side,
        "checkpoint": f"utc_{market.snapshot_hour_utc.hour:02d}",
        "bracket_type": _bracket_type(market),
        "yes_probability": yes_probability,
        "outcome_probability": outcome_probability,
        "entry_bid": bid,
        "entry_ask": ask,
        "market_midpoint": (bid + ask) / 2.0,
        "spread": ask - bid,
        "raw_edge": outcome_probability - ask,
        "yes_rank": stats["rank"],
        "top_probability": stats["top_probability"],
        "top_margin": stats["top_margin"],
        "entropy": stats["entropy"],
        "hours_elapsed": _feature_number(weather, "hours_elapsed"),
        "hours_remaining": _feature_number(weather, "hours_remaining"),
        "observed_high_so_far_f": observed,
        "distance_to_lower_f": (
            None if observed is None or lower is None else float(observed) - lower
        ),
        "distance_to_upper_f": (
            None if observed is None or upper is None else float(observed) - upper
        ),
        **(source_family_features(weather_values(weather)) if weather is not None else {}),
        "family_confirmation_count": _source_confirmation_count(market, weather),
        "source_std_f": _feature_number(weather, "source_std_f"),
        "source_range_f": _feature_number(weather, "source_range_f"),
        "nws_anchor_high_f": weather.nws_anchor_high_f if weather is not None else None,
        "hrrr_projected_high_f": weather.hrrr_projected_high_f if weather is not None else None,
        "nbm_projected_high_f": weather.nbm_projected_high_f if weather is not None else None,
        "ensemble_raw_median_high_f": (
            weather.ensemble_raw_median_high_f if weather is not None else None
        ),
    }


def _quote(market: MarketSnapshot, side: str) -> tuple[float, float] | None:
    if side == "yes":
        if market.yes_bid is None or market.yes_ask is None:
            return None
        return float(market.yes_bid), float(market.yes_ask)
    no_bid = market.no_bid
    no_ask = market.no_ask
    if no_bid is None and market.yes_ask is not None:
        no_bid = max(0.0, 1.0 - float(market.yes_ask))
    if no_ask is None and market.yes_bid is not None:
        no_ask = max(0.0, 1.0 - float(market.yes_bid))
    if no_bid is None or no_ask is None:
        return None
    return float(no_bid), float(no_ask)


def _distribution_stats(probabilities: dict[str, float], market_ticker: str) -> dict[str, float]:
    ordered = sorted(probabilities.values(), reverse=True)
    top = ordered[0] if ordered else 0.0
    second = ordered[1] if len(ordered) > 1 else 0.0
    probability = probabilities.get(market_ticker, 0.0)
    entropy = -sum(value * math.log(max(value, 1e-12)) for value in probabilities.values())
    return {
        "rank": float(1 + sum(1 for value in probabilities.values() if value > probability)),
        "top_probability": float(top),
        "top_margin": float(top - second),
        "entropy": float(entropy),
    }


def _source_confirmation_count(market: MarketSnapshot, weather: WeatherSnapshot | None) -> int:
    if weather is None:
        return 0
    return family_support_count(market.bracket, weather_values(weather))


def _feature_number(weather: WeatherSnapshot | None, key: str) -> float | None:
    if weather is None:
        return None
    value = weather.features.get(key)
    if value in (None, ""):
        return None
    return _finite_float(value)


def _finite_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _bracket_type(market: MarketSnapshot) -> str:
    if market.bracket.lower_f is None:
        return "lower_tail"
    if market.bracket.upper_f is None:
        return "upper_tail"
    return "bounded"

