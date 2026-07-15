"""Offline Edgecaster tradeability evaluation.

Edgecaster is a second-stage model: it consumes Cloudcaster probabilities plus
market microstructure and learns whether a YES/NO action is worth taking.
"""

from __future__ import annotations

import ast
import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from backtest.data_sources import LocalExportSource
from backtest.load_dataset import load_dataset
from libs.models import BacktestDataset, MarketSnapshot, WeatherSnapshot
from libs.source_families import family_support_count, source_family_features, weather_values
from strategy.metrics import (
    daily_pnl_rows,
    grouped_metric_rows,
    max_drawdown,
    summary_metrics,
)
from strategy.orders import PaperTrade

NUMERIC_FEATURES = [
    "yes_probability",
    "outcome_probability",
    "entry_price",
    "market_midpoint",
    "spread",
    "cloud_edge",
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
FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES


@dataclass(frozen=True)
class EdgecasterConfig:
    model_type: str = "ridge"
    min_training_days: int = 5
    min_training_examples: int = 400
    min_predicted_reward: float = 0.03
    min_cloud_edge: float = 0.0
    max_spread: float = 0.15
    min_entry_price: float = 0.02
    max_entry_price: float = 0.80
    daily_budget: float = 40.0
    max_order_cost: float = 3.0
    budget_fraction: float = 0.10
    max_contracts_per_order: int = 20
    max_no_contracts_per_order: int = 10
    max_positions_per_event: int = 1
    allow_fallback_trades: bool = False


@dataclass(frozen=True)
class EdgecasterExample:
    city: str
    event_ticker: str
    market_ticker: str
    target_date: str
    snapshot_hour_utc: datetime
    side: str
    entry_bid: float
    entry_ask: float
    spread: float
    cloud_probability: float
    cloud_edge: float
    reward: float
    winner_ticker: str
    features: dict[str, Any]


@dataclass(frozen=True)
class EdgecasterPrediction:
    example: EdgecasterExample
    predicted_reward: float
    model_mode: str
    training_examples: int
    training_days: int


def run_edgecaster_evaluation(
    data_path: str | Path,
    model_report: str | Path,
    output_dir: str | Path,
    config: EdgecasterConfig,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset(LocalExportSource(Path(data_path)))
    probabilities = load_probabilities(Path(model_report))
    examples = build_examples(dataset, probabilities)
    predictions = walk_forward_predictions(examples, config)
    trades = select_trades(predictions, dataset, config)
    summary = {
        "mode": "edgecaster_walk_forward",
        "data_path": str(data_path),
        "model_report": str(model_report),
        "config": asdict(config),
        "candidate_examples": len(examples),
        "prediction_examples": len(predictions),
        "model_predictions": len(
            [row for row in predictions if row.model_mode.startswith("trained_")]
        ),
        "fallback_predictions": len(
            [row for row in predictions if row.model_mode == "fallback_cloud_edge"]
        ),
        **summary_metrics(trades),
    }
    write_outputs(output, summary, examples, predictions, trades)
    return {**summary, "output_dir": str(output)}


def build_examples(
    dataset: BacktestDataset,
    probabilities: dict[tuple[str, str, datetime], dict[str, float]],
) -> list[EdgecasterExample]:
    weather_by_key = {
        (row.city, row.event_ticker, row.snapshot_hour_utc): row for row in dataset.weather
    }
    settlements = {
        row.event_ticker: row for row in dataset.settlements if row.validation_status == "valid"
    }
    examples: list[EdgecasterExample] = []
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
        yes_probability = _effective_yes_probability(
            market,
            weather,
            float(snapshot_probabilities[market.market_ticker]),
        )
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
            examples.append(
                EdgecasterExample(
                    city=market.city,
                    event_ticker=market.event_ticker,
                    market_ticker=market.market_ticker,
                    target_date=market.target_date.isoformat(),
                    snapshot_hour_utc=market.snapshot_hour_utc,
                    side=side,
                    entry_bid=bid,
                    entry_ask=ask,
                    spread=ask - bid,
                    cloud_probability=outcome_probability,
                    cloud_edge=outcome_probability - ask,
                    reward=reward,
                    winner_ticker=settlement.winner_ticker,
                    features=features,
                )
            )
    return examples


def walk_forward_predictions(
    examples: list[EdgecasterExample],
    config: EdgecasterConfig,
) -> list[EdgecasterPrediction]:
    output: list[EdgecasterPrediction] = []
    for target_date in sorted({example.target_date for example in examples}):
        train = [example for example in examples if example.target_date < target_date]
        test = [example for example in examples if example.target_date == target_date]
        training_days = len({example.target_date for example in train})
        if training_days < config.min_training_days or len(train) < config.min_training_examples:
            output.extend(
                EdgecasterPrediction(
                    example=example,
                    predicted_reward=example.cloud_edge,
                    model_mode="fallback_cloud_edge",
                    training_examples=len(train),
                    training_days=training_days,
                )
                for example in test
            )
            continue
        model = _train_model(train, config)
        predicted = model.predict(_frame(test))
        output.extend(
            EdgecasterPrediction(
                example=example,
                predicted_reward=float(score),
                model_mode=f"trained_{config.model_type}_reward",
                training_examples=len(train),
                training_days=training_days,
            )
            for example, score in zip(test, predicted, strict=True)
        )
    return output


def select_trades(
    predictions: list[EdgecasterPrediction],
    dataset: BacktestDataset,
    config: EdgecasterConfig,
) -> list[PaperTrade]:
    market_history = _market_history(dataset.markets)
    open_events: Counter[str] = Counter()
    budget_remaining: dict[str, float] = {}
    trades: list[PaperTrade] = []
    sequence = 1
    grouped = _group_predictions_by_snapshot(predictions)
    for _, snapshot_predictions in sorted(grouped.items()):
        candidates = [
            row
            for row in snapshot_predictions
            if _passes_policy(row, config)
            and open_events[row.example.event_ticker] < config.max_positions_per_event
        ]
        by_event: dict[str, list[EdgecasterPrediction]] = defaultdict(list)
        for candidate in candidates:
            by_event[candidate.example.event_ticker].append(candidate)
        selected = [
            sorted(
                event_candidates,
                key=lambda item: (item.predicted_reward, item.example.cloud_edge),
                reverse=True,
            )[0]
            for event_candidates in by_event.values()
        ]
        for prediction in sorted(selected, key=lambda item: item.predicted_reward, reverse=True):
            example = prediction.example
            budget = budget_remaining.get(example.target_date, config.daily_budget)
            contracts = _contracts(example, budget, config)
            if contracts <= 0:
                continue
            budget_remaining[example.target_date] = budget - contracts * example.entry_ask
            open_events[example.event_ticker] += 1
            trades.append(_trade(prediction, contracts, market_history, sequence))
            sequence += 1
    return trades


def load_probabilities(path: Path) -> dict[tuple[str, str, datetime], dict[str, float]]:
    distributions_path = path / "bracket_distributions.csv"
    output: dict[tuple[str, str, datetime], dict[str, float]] = {}
    with distributions_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            snapshot = datetime.fromisoformat(row["snapshot_hour_utc"])
            output[(row["city"], row["event_ticker"], snapshot)] = {
                str(key): float(value)
                for key, value in ast.literal_eval(row["probabilities"]).items()
            }
    return output


def write_outputs(
    output: Path,
    summary: dict[str, Any],
    examples: list[EdgecasterExample],
    predictions: list[EdgecasterPrediction],
    trades: list[PaperTrade],
) -> None:
    _write_dict_rows(output / "candidates.csv", [_example_row(row) for row in examples])
    _write_dict_rows(output / "predictions.csv", [_prediction_row(row) for row in predictions])
    _write_dict_rows(output / "trades.csv", [asdict(row) for row in trades])
    _write_dict_rows(output / "daily_pnl.csv", daily_pnl_rows(trades))
    _write_dict_rows(output / "city_metrics.csv", grouped_metric_rows(trades, "city"))
    _write_dict_rows(output / "side_metrics.csv", grouped_metric_rows(trades, "side"))
    _write_dict_rows(output / "predicted_reward_buckets.csv", _reward_bucket_rows(trades))
    _write_dict_rows(output / "policy_calibration.csv", _policy_calibration_rows(predictions))
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    _write_report(output / "edgecaster_report.md", summary)


def _train_model(examples: list[EdgecasterExample], config: EdgecasterConfig) -> Pipeline:
    model = _pipeline(config.model_type)
    model.fit(
        _frame(examples),
        [example.reward for example in examples],
        model__sample_weight=_sample_weights(examples),
    )
    return model


def _pipeline(model_type: str) -> Pipeline:
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
                NUMERIC_FEATURES,
            ),
            (
                "categorical",
                Pipeline(
                    steps=[
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("encoder", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                    ]
                ),
                CATEGORICAL_FEATURES,
            ),
        ],
        remainder="drop",
    )
    if model_type == "ridge":
        estimator = Ridge(alpha=5.0)
    elif model_type == "hgb":
        estimator = HistGradientBoostingRegressor(
            learning_rate=0.04,
            max_leaf_nodes=12,
            min_samples_leaf=40,
            l2_regularization=0.3,
            random_state=29,
        )
    else:
        raise ValueError(f"unsupported Edgecaster model_type {model_type}")
    return Pipeline(steps=[("preprocessor", preprocessor), ("model", estimator)])


def _frame(examples: list[EdgecasterExample]) -> pd.DataFrame:
    return pd.DataFrame([example.features for example in examples], columns=FEATURE_COLUMNS)


def _sample_weights(examples: list[EdgecasterExample]) -> list[float]:
    day_counts = Counter(example.target_date for example in examples)
    event_counts = Counter(example.event_ticker for example in examples)
    return [
        (1.0 / day_counts[example.target_date]) * (1.0 / event_counts[example.event_ticker])
        for example in examples
    ]


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
        "entry_price": ask,
        "market_midpoint": (bid + ask) / 2.0,
        "spread": ask - bid,
        "cloud_edge": outcome_probability - ask,
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


def _effective_yes_probability(
    market: MarketSnapshot,
    weather: WeatherSnapshot | None,
    probability: float,
) -> float:
    observed = weather.observed_high_so_far_f if weather is not None else None
    if observed is None:
        return probability
    if market.bracket.upper_f is not None and float(observed) >= float(market.bracket.upper_f):
        return 0.0
    if market.bracket.lower_f is not None and market.bracket.upper_f is None:
        if int(float(observed) + 0.5) >= int(market.bracket.lower_f):
            return 1.0
    return probability


def _distribution_stats(
    probabilities: dict[str, float],
    market_ticker: str,
) -> dict[str, float]:
    ordered = sorted(probabilities.values(), reverse=True)
    top = ordered[0] if ordered else 0.0
    second = ordered[1] if len(ordered) > 1 else 0.0
    probability = probabilities.get(market_ticker, 0.0)
    rank = 1 + sum(1 for value in probabilities.values() if value > probability)
    entropy = -sum(value * math.log(max(value, 1e-12)) for value in probabilities.values())
    return {
        "rank": float(rank),
        "top_probability": float(top),
        "top_margin": float(top - second),
        "entropy": float(entropy),
    }


def _source_confirmation_count(
    market: MarketSnapshot,
    weather: WeatherSnapshot | None,
) -> int:
    if weather is None:
        return 0
    return family_support_count(market.bracket, weather_values(weather))


def _feature_number(weather: WeatherSnapshot | None, key: str) -> float | None:
    if weather is None:
        return None
    value = weather.features.get(key)
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


def _group_predictions_by_snapshot(
    predictions: list[EdgecasterPrediction],
) -> dict[datetime, list[EdgecasterPrediction]]:
    output: dict[datetime, list[EdgecasterPrediction]] = defaultdict(list)
    for prediction in predictions:
        output[prediction.example.snapshot_hour_utc].append(prediction)
    return output


def _passes_policy(prediction: EdgecasterPrediction, config: EdgecasterConfig) -> bool:
    example = prediction.example
    if not prediction.model_mode.startswith("trained_") and not config.allow_fallback_trades:
        return False
    return (
        prediction.predicted_reward >= config.min_predicted_reward
        and example.cloud_edge >= config.min_cloud_edge
        and example.spread <= config.max_spread
        and config.min_entry_price <= example.entry_ask <= config.max_entry_price
    )


def _contracts(
    example: EdgecasterExample,
    budget_remaining: float,
    config: EdgecasterConfig,
) -> int:
    per_order_budget = min(
        config.max_order_cost,
        budget_remaining,
        config.daily_budget * config.budget_fraction,
    )
    cap = (
        config.max_no_contracts_per_order
        if example.side == "no"
        else config.max_contracts_per_order
    )
    return max(0, min(cap, int(per_order_budget // max(0.01, example.entry_ask))))


def _trade(
    prediction: EdgecasterPrediction,
    contracts: int,
    market_history: dict[tuple[str, str], list[MarketSnapshot]],
    sequence: int,
) -> PaperTrade:
    example = prediction.example
    settlement_value = 1.0 if example.reward > 0 else 0.0
    pnl = example.reward * contracts
    closing_mid = _closing_mid(example, market_history)
    return PaperTrade(
        order_id=f"edgecaster-{sequence:06d}",
        city=example.city,
        event_ticker=example.event_ticker,
        market_ticker=example.market_ticker,
        target_date=example.target_date,
        entry_time_utc=example.snapshot_hour_utc,
        model_probability=prediction.predicted_reward,
        entry_price=example.entry_ask,
        edge=prediction.predicted_reward,
        contracts=float(contracts),
        winner_ticker=example.winner_ticker,
        settlement_value=settlement_value,
        pnl=pnl,
        roi=pnl / max(1e-9, example.entry_ask * contracts),
        hit=1.0 if example.reward > 0 else 0.0,
        closing_mid=closing_mid,
        clv=(closing_mid - example.entry_ask) if closing_mid is not None else None,
        checkpoint=f"utc_{example.snapshot_hour_utc.hour:02d}",
        side=example.side,
    )


def _closing_mid(
    example: EdgecasterExample,
    market_history: dict[tuple[str, str], list[MarketSnapshot]],
) -> float | None:
    history = market_history.get((example.event_ticker, example.market_ticker), [])
    prior = [row for row in history if row.snapshot_hour_utc >= example.snapshot_hour_utc]
    if not prior:
        return None
    quote = _quote(prior[-1], example.side)
    if quote is None:
        return None
    bid, ask = quote
    return (bid + ask) / 2.0


def _market_history(
    markets: list[MarketSnapshot],
) -> dict[tuple[str, str], list[MarketSnapshot]]:
    output: dict[tuple[str, str], list[MarketSnapshot]] = defaultdict(list)
    for market in markets:
        output[(market.event_ticker, market.market_ticker)].append(market)
    return {
        key: sorted(value, key=lambda item: item.snapshot_hour_utc) for key, value in output.items()
    }


def _example_row(example: EdgecasterExample) -> dict[str, Any]:
    return {
        "city": example.city,
        "event_ticker": example.event_ticker,
        "market_ticker": example.market_ticker,
        "target_date": example.target_date,
        "snapshot_hour_utc": example.snapshot_hour_utc.isoformat(),
        "side": example.side,
        "entry_bid": example.entry_bid,
        "entry_ask": example.entry_ask,
        "spread": example.spread,
        "cloud_probability": example.cloud_probability,
        "cloud_edge": example.cloud_edge,
        "reward": example.reward,
        "winner_ticker": example.winner_ticker,
        **example.features,
    }


def _prediction_row(prediction: EdgecasterPrediction) -> dict[str, Any]:
    return {
        **_example_row(prediction.example),
        "predicted_reward": prediction.predicted_reward,
        "model_mode": prediction.model_mode,
        "training_examples": prediction.training_examples,
        "training_days": prediction.training_days,
    }


REWARD_BUCKETS = (
    (-1.0, 0.00, "<0.00"),
    (0.00, 0.03, "0.00-0.03"),
    (0.03, 0.06, "0.03-0.06"),
    (0.06, 0.10, "0.06-0.10"),
    (0.10, 1.00, "0.10+"),
)


def _reward_bucket_rows(trades: list[PaperTrade]) -> list[dict[str, Any]]:
    grouped: dict[str, list[PaperTrade]] = {label: [] for _, _, label in REWARD_BUCKETS}
    for trade in trades:
        for low, high, label in REWARD_BUCKETS:
            if low <= trade.edge < high:
                grouped[label].append(trade)
                break
    return [_trade_group_row(label, rows) for label, rows in grouped.items()]


def _policy_calibration_rows(predictions: list[EdgecasterPrediction]) -> list[dict[str, Any]]:
    grouped: dict[str, list[EdgecasterPrediction]] = {label: [] for _, _, label in REWARD_BUCKETS}
    for prediction in predictions:
        for low, high, label in REWARD_BUCKETS:
            if low <= prediction.predicted_reward < high:
                grouped[label].append(prediction)
                break
    rows = []
    for label, bucket in grouped.items():
        rows.append(
            {
                "group": label,
                "examples": len(bucket),
                "avg_predicted_reward": (
                    mean(row.predicted_reward for row in bucket) if bucket else ""
                ),
                "avg_realized_reward": mean(row.example.reward for row in bucket) if bucket else "",
                "positive_reward_rate": (
                    mean(1.0 if row.example.reward > 0 else 0.0 for row in bucket) if bucket else ""
                ),
            }
        )
    return rows


def _trade_group_row(group: str, trades: list[PaperTrade]) -> dict[str, Any]:
    if not trades:
        return {
            "group": group,
            "trades": 0,
            "pnl": 0.0,
            "roi": 0.0,
            "hit_rate": "",
            "avg_predicted_reward": "",
            "avg_clv": "",
        }
    risk = sum(trade.entry_price * trade.contracts for trade in trades)
    clv_values = [trade.clv for trade in trades if trade.clv is not None]
    return {
        "group": group,
        "trades": len(trades),
        "pnl": sum(trade.pnl for trade in trades),
        "roi": sum(trade.pnl for trade in trades) / max(1e-9, risk),
        "hit_rate": mean(trade.hit for trade in trades),
        "avg_predicted_reward": mean(trade.edge for trade in trades),
        "avg_entry_price": mean(trade.entry_price for trade in trades),
        "avg_clv": mean(clv_values) if clv_values else "",
    }


def _write_dict_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Edgecaster Walk-Forward Evaluation",
        "",
        f"- Candidate examples: {summary['candidate_examples']}",
        f"- Prediction examples: {summary['prediction_examples']}",
        f"- Model predictions: {summary['model_predictions']}",
        f"- Fallback predictions: {summary['fallback_predictions']}",
        f"- Trades: {summary['trades']}",
        f"- Total PnL: {float(summary['total_pnl']):.4f}",
        f"- ROI: {float(summary['roi']):.4f}",
        f"- Hit rate: {float(summary['hit_rate']):.4f}",
        f"- Max drawdown: {float(summary['max_drawdown']):.4f}",
        f"- Mean CLV: {summary['mean_clv']}",
        "",
        "Edgecaster predicts per-contract reward for YES/NO actions and abstains unless "
        "predicted reward clears the policy threshold. This first version is hold-to-settlement "
        "and should be treated as an offline selection diagnostic, not a live deployment policy.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def edgecaster_diagnostics(trades: list[PaperTrade]) -> dict[str, float]:
    return {
        "trades": float(len(trades)),
        "total_pnl": sum(trade.pnl for trade in trades),
        "max_drawdown": max_drawdown(trades),
    }
