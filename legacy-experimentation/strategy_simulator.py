"""Offline strategy simulator for archived Kalshi weather forecasts."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import matplotlib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from model_improvement_report import load_snapshot, parse_json_list  # noqa: E402
from train_offline_model import (  # noqa: E402
    DEFAULT_CANDIDATES,
    PROBABILITY_FLOOR,
    expanding_window_scores,
    latest_per_event,
    load_examples,
    parse_candidates,
    validate_candidates,
)
from weather_backtest import write_csv  # noqa: E402
from weather_probabilities import DataError  # noqa: E402


DEFAULT_MODELS = (
    "full",
    "hrrr_top3_rerank",
    "soft_floor_weather",
    "soft_floor_family_centered",
    "market_midpoint",
    "trained_blend",
    "checkpoint_trained_blend",
    "trained_weather_blend",
    "checkpoint_trained_weather_blend",
    "trained_weather_hrrr_blend",
    "checkpoint_trained_weather_hrrr_blend",
    "regression_trained_weather_hrrr",
    "anchored_regression_weather_hrrr",
    "calibrated_temperature_error_model",
    "calibrated_temperature_error_market_aware",
)
DEFAULT_MIN_EV = (0.0, 0.02, 0.05, 0.10)
DEFAULT_STRATEGIES = (
    "taker_ev",
    "taker_ev_latest_only",
    "taker_ev_by_checkpoint",
    "weather_market_disagreement",
    "top_one_only",
    "top_gap_ev",
    "regression_trade_filter",
    "grouped_regression_edge_strategy",
)
REGRESSION_TRADE_FILTER = "regression_trade_filter"
GROUPED_REGRESSION_EDGE_STRATEGY = "grouped_regression_edge_strategy"
TOP_GAP_EV_STRATEGY = "top_gap_ev"
DEFAULT_TRADE_MODEL_BASE_MODEL = "regression_trained_weather_hrrr"
TRADE_MODEL_NUMERIC_FEATURES = (
    "model_probability",
    "model_top_probability_gap",
    "candidate_probability_gap_to_leader",
    "yes_ask",
    "fee_per_contract",
    "model_ev",
    "market_midpoint_probability",
    "model_minus_market_probability",
    "ask_minus_market_probability",
    "bracket_index",
    "relative_bracket_index",
    "bracket_count",
    "model_top_distance",
    "abs_model_top_distance",
    "market_top_distance",
    "abs_market_top_distance",
    "hrrr_top_distance",
    "abs_hrrr_top_distance",
    "ask_size",
    "checkpoint_order",
)
TRADE_MODEL_CATEGORICAL_FEATURES = (
    "city",
    "checkpoint",
    "is_model_top",
    "is_market_top",
    "is_hrrr_top_or_adjacent",
    "is_low_ask",
    "is_tiny_ask",
)
GROUPED_STRATEGY_NUMERIC_FEATURES = (
    "weather_logit_probability",
    "ask_cost",
    "weather_edge_after_fee",
    "candidate_gap_to_weather_leader",
    "top_two_weather_gap",
    "hrrr_support_delta",
    "relative_bracket_index",
)
GROUPED_STRATEGY_CATEGORICAL_FEATURES = (
    "city",
    "checkpoint",
    "hrrr_agrees_with_weather_top",
)
DEFAULT_GROUPED_STRATEGY_MIN_EVENTS = 20
DEFAULT_GROUPED_MIN_EV = 0.03
DEFAULT_GROUPED_MIN_FAIR_PROBABILITY = 0.08
DEFAULT_GROUPED_MIN_CANDIDATE_GAP = -0.20
DEFAULT_CONFIDENCE_GAPS = (0.05, 0.10, 0.15, 0.20, 0.25)
CHECKPOINT_ORDER = {
    "t_minus_6h": -6.0,
    "t_plus_6h": 6.0,
    "t_plus_10h": 10.0,
    "t_plus_14h": 14.0,
    "t_plus_18h": 18.0,
}


def ceil_cent(value: float) -> float:
    return math.ceil((value - 1e-12) * 100.0) / 100.0


def kalshi_fee(price: float, contracts: int = 1, mode: str = "taker") -> float:
    if mode == "none":
        return 0.0
    if contracts <= 0:
        raise ValueError("contracts must be positive")
    rate_by_mode = {"taker": 0.07, "maker": 0.0175}
    if mode not in rate_by_mode:
        raise ValueError(f"unknown fee mode {mode!r}")
    raw_fee = rate_by_mode[mode] * contracts * price * (1.0 - price)
    return ceil_cent(raw_fee)


def safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def boolish(value: Any) -> bool:
    return str(value).lower() == "true"


def clipped_logit(value: float, floor: float = 1e-6) -> float:
    probability = min(max(float(value), floor), 1.0 - floor)
    return math.log(probability / (1.0 - probability))


def model_rows(
    root: Path,
    cohort: str,
    candidate_names: tuple[str, ...],
    grid_step: float,
    regularization: float,
    min_train_events: int,
    probability_floor: float,
) -> list[dict[str, Any]]:
    examples = load_examples(root, cohort)
    validate_candidates(examples, candidate_names)
    rows, _ = expanding_window_scores(
        examples,
        candidate_names,
        grid_step,
        regularization,
        min_train_events,
        probability_floor,
    )
    return rows


def latest_keys(rows: list[dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    return {
        (
            str(row["target_date"]),
            str(row["city"]),
            str(row["model"]),
            str(row["checkpoint"]),
        )
        for row in latest_per_event(rows)
    }


def row_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row["target_date"]),
        str(row["city"]),
        str(row["model"]),
        str(row["checkpoint"]),
    )


def market_midpoint_probabilities(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, float]]:
    output: dict[tuple[str, str, str], dict[str, float]] = {}
    for row in rows:
        if row["model"] != "market_midpoint":
            continue
        tickers = [str(value) for value in parse_json_list(row["tickers_json"], "tickers")]
        probabilities = [
            float(value)
            for value in parse_json_list(row["probabilities_json"], "market probabilities")
        ]
        output[(str(row["target_date"]), str(row["city"]), str(row["checkpoint"]))] = {
            ticker: probability
            for ticker, probability in zip(tickers, probabilities, strict=True)
        }
    return output


def model_probability_lookup(
    rows: list[dict[str, Any]],
    model_name: str,
) -> dict[tuple[str, str, str], dict[str, float]]:
    output: dict[tuple[str, str, str], dict[str, float]] = {}
    for row in rows:
        if row["model"] != model_name:
            continue
        tickers = [str(value) for value in parse_json_list(row["tickers_json"], "tickers")]
        probabilities = [
            float(value)
            for value in parse_json_list(row["probabilities_json"], f"{model_name} probabilities")
        ]
        output[(str(row["target_date"]), str(row["city"]), str(row["checkpoint"]))] = {
            ticker: probability
            for ticker, probability in zip(tickers, probabilities, strict=True)
        }
    return output


def top_one_ticker(tickers: list[str], probabilities: list[float]) -> str | None:
    maximum = max(probabilities)
    leaders = [
        ticker
        for ticker, probability in zip(tickers, probabilities, strict=True)
        if math.isclose(probability, maximum, rel_tol=0.0, abs_tol=1e-12)
    ]
    return leaders[0] if len(leaders) == 1 else None


def top_index(probabilities: list[float]) -> int:
    return max(range(len(probabilities)), key=lambda index: probabilities[index])


def top_two_probability_gap(probabilities: list[float]) -> float:
    if not probabilities:
        return 0.0
    ordered = sorted(probabilities, reverse=True)
    if len(ordered) == 1:
        return ordered[0]
    return ordered[0] - ordered[1]


def checkpoint_order(checkpoint: str) -> float:
    return CHECKPOINT_ORDER.get(checkpoint, 0.0)


def market_quotes(snapshot: dict[str, Any], tickers: list[str]) -> dict[str, dict[str, float | int | None]]:
    markets = snapshot.get("event", {}).get("markets")
    if not isinstance(markets, list):
        raise DataError("snapshot markets are malformed")
    by_ticker = {str(market.get("ticker")): market for market in markets}
    quotes: dict[str, dict[str, float | int | None]] = {}
    for ticker in tickers:
        if ticker not in by_ticker:
            raise DataError(f"missing market quote for {ticker}")
        market = by_ticker[ticker]
        ask = safe_float(market.get("yes_ask_dollars"))
        if ask is None:
            raise DataError(f"malformed ask quote for {ticker}")
        ask_size = safe_float(market.get("yes_ask_size"))
        if ask_size is None:
            ask_size = safe_float(market.get("yes_ask_size_fp"))
        quotes[ticker] = {
            "yes_ask": ask,
            "yes_ask_size": max(0, math.floor(ask_size)) if ask_size is not None else None,
        }
    return quotes


def kelly_fraction(probability: float, ask: float, fee_per_contract: float) -> float:
    cost = ask + fee_per_contract
    if cost <= 0.0 or cost >= 1.0:
        return 0.0
    return max(0.0, (probability - cost) / (1.0 - cost))


def choose_contracts(
    probability: float,
    ask: float,
    fee_mode: str,
    fixed_contracts: int,
    sizing: str,
    bankroll: float,
    kelly_multiplier: float,
    max_position_fraction: float,
    max_contracts: int,
    ask_size: int | None,
    use_ask_size: bool,
) -> dict[str, float | int | None]:
    if fixed_contracts <= 0:
        raise ValueError("contracts must be positive")
    if max_contracts <= 0:
        raise ValueError("max_contracts must be positive")
    if sizing == "fixed":
        raw_kelly = None
        used_fraction = None
        contracts = fixed_contracts
    elif sizing == "kelly":
        one_contract_fee = kalshi_fee(ask, 1, fee_mode)
        raw_kelly = kelly_fraction(probability, ask, one_contract_fee)
        used_fraction = min(raw_kelly * kelly_multiplier, max_position_fraction)
        target_stake = bankroll * used_fraction
        one_contract_stake = ask + one_contract_fee
        contracts = math.floor(target_stake / one_contract_stake) if one_contract_stake > 0.0 else 0
    else:
        raise ValueError(f"unknown sizing mode {sizing!r}")

    contracts = min(int(contracts), max_contracts)
    if use_ask_size and ask_size is not None:
        contracts = min(contracts, ask_size)
    return {
        "contracts": max(0, contracts),
        "kelly_fraction_raw": raw_kelly,
        "kelly_fraction_used": used_fraction,
        "ask_size_cap": ask_size if use_ask_size else None,
    }


def trade_model_event_count(records: list[dict[str, Any]]) -> int:
    return len({(str(record["target_date"]), str(record["city"])) for record in records})


def trade_model_feature_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([record["features"] for record in records])


def trade_model_feature_dict(record: dict[str, Any]) -> dict[str, Any]:
    return dict(record["features"])


def fit_trade_decision_model(
    records: list[dict[str, Any]],
    min_events: int,
) -> dict[str, Any]:
    training_events = trade_model_event_count(records)
    if training_events < min_events:
        return {
            "model": None,
            "mode": "fallback_min_train_events",
            "training_candidates": len(records),
            "training_events": training_events,
            "feature_count": len(TRADE_MODEL_NUMERIC_FEATURES)
            + len(TRADE_MODEL_CATEGORICAL_FEATURES),
        }
    labels = [int(record["profitable"]) for record in records]
    if len(set(labels)) < 2:
        return {
            "model": None,
            "mode": "fallback_single_class",
            "training_candidates": len(records),
            "training_events": training_events,
            "feature_count": len(TRADE_MODEL_NUMERIC_FEATURES)
            + len(TRADE_MODEL_CATEGORICAL_FEATURES),
        }
    transformer = ColumnTransformer(
        transformers=[
            ("numeric", StandardScaler(), list(TRADE_MODEL_NUMERIC_FEATURES)),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                list(TRADE_MODEL_CATEGORICAL_FEATURES),
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
    model.fit(trade_model_feature_frame(records), labels)
    return {
        "model": model,
        "mode": "trained",
        "training_candidates": len(records),
        "training_events": training_events,
        "feature_count": len(TRADE_MODEL_NUMERIC_FEATURES)
        + len(TRADE_MODEL_CATEGORICAL_FEATURES),
    }


def predict_trade_score(record: dict[str, Any], fit: dict[str, Any]) -> float | None:
    model = fit.get("model")
    if model is None:
        return None
    probabilities = model.predict_proba(pd.DataFrame([trade_model_feature_dict(record)]))
    classes = list(model.classes_)  # type: ignore[attr-defined]
    if 1 not in classes:
        return None
    positive_index = classes.index(1)
    return float(probabilities[0][positive_index])


def normalize_trade_scores(
    scored_records: list[tuple[dict[str, Any], float | None]],
) -> list[tuple[dict[str, Any], float | None]]:
    valid_scores = [max(float(score), 1e-12) for _, score in scored_records if score is not None]
    total = sum(valid_scores)
    if total <= 0.0:
        return [(record, None) for record, _ in scored_records]
    return [
        (record, max(float(score), 1e-12) / total if score is not None else None)
        for record, score in scored_records
    ]


def build_trade_candidate_records(
    root: Path,
    cohort: str,
    rows: list[dict[str, Any]],
    base_model: str,
    fee_mode: str,
) -> list[dict[str, Any]]:
    market_probs = market_midpoint_probabilities(rows)
    hrrr_probs = model_probability_lookup(rows, "hrrr_top3_rerank")
    records: list[dict[str, Any]] = []
    for row in rows:
        if row["model"] != base_model:
            continue
        tickers = [str(value) for value in parse_json_list(row["tickers_json"], "tickers")]
        probabilities = [
            float(value)
            for value in parse_json_list(row["probabilities_json"], "probabilities")
        ]
        if len(tickers) != len(probabilities):
            raise DataError("tickers and probabilities do not align")
        snapshot = load_snapshot(root, cohort, row)
        quotes = market_quotes(snapshot, tickers)
        lookup_key = (str(row["target_date"]), str(row["city"]), str(row["checkpoint"]))
        market_lookup = market_probs.get(lookup_key, {})
        hrrr_lookup = hrrr_probs.get(lookup_key, {})
        model_top = top_index(probabilities)
        market_values = [float(market_lookup.get(ticker, 0.0)) for ticker in tickers]
        market_top = top_index(market_values) if any(market_values) else model_top
        hrrr_values = [float(hrrr_lookup.get(ticker, 0.0)) for ticker in tickers]
        hrrr_top = top_index(hrrr_values) if any(hrrr_values) else model_top
        hrrr_agrees_with_weather_top = hrrr_top == model_top
        bracket_count = len(tickers)
        sorted_model_probabilities = sorted(probabilities, reverse=True)
        model_top_gap = (
            sorted_model_probabilities[0] - sorted_model_probabilities[1]
            if len(sorted_model_probabilities) >= 2
            else sorted_model_probabilities[0]
        )
        model_leader_probability = sorted_model_probabilities[0]
        for index, (ticker, probability) in enumerate(zip(tickers, probabilities, strict=True)):
            quote = quotes[ticker]
            ask = float(quote["yes_ask"])
            ask_size = quote["yes_ask_size"]
            ask_size_value = float(ask_size) if ask_size is not None else 0.0
            one_contract_fee = kalshi_fee(ask, 1, fee_mode)
            model_ev = probability - ask - one_contract_fee
            market_probability = float(market_lookup.get(ticker, 0.0))
            hrrr_probability = float(hrrr_lookup.get(ticker, 0.0))
            payout = 1.0 if ticker == row["winner_ticker"] else 0.0
            net_pnl_per_contract = payout - ask - one_contract_fee
            hrrr_distance = float(index - hrrr_top)
            features = {
                "model_probability": probability,
                "model_top_probability_gap": model_top_gap,
                "candidate_probability_gap_to_leader": probability - model_leader_probability,
                "yes_ask": ask,
                "fee_per_contract": one_contract_fee,
                "model_ev": model_ev,
                "market_midpoint_probability": market_probability,
                "model_minus_market_probability": probability - market_probability,
                "ask_minus_market_probability": ask - market_probability,
                "bracket_index": float(index),
                "relative_bracket_index": index / max(1.0, bracket_count - 1.0),
                "bracket_count": float(bracket_count),
                "model_top_distance": float(index - model_top),
                "abs_model_top_distance": float(abs(index - model_top)),
                "market_top_distance": float(index - market_top),
                "abs_market_top_distance": float(abs(index - market_top)),
                "hrrr_top_distance": hrrr_distance,
                "abs_hrrr_top_distance": float(abs(index - hrrr_top)),
                "hrrr_support_delta": hrrr_probability - probability,
                "ask_size": ask_size_value,
                "checkpoint_order": checkpoint_order(str(row["checkpoint"])),
                "city": str(row["city"]),
                "checkpoint": str(row["checkpoint"]),
                "is_model_top": str(index == model_top),
                "is_market_top": str(index == market_top),
                "is_hrrr_top_or_adjacent": str(abs(hrrr_distance) <= 1.0),
                "hrrr_agrees_with_weather_top": str(hrrr_agrees_with_weather_top),
                "is_low_ask": str(ask <= 0.10),
                "is_tiny_ask": str(ask <= 0.05),
            }
            records.append(
                {
                    "target_date": str(row["target_date"]),
                    "city": str(row["city"]),
                    "checkpoint": str(row["checkpoint"]),
                    "as_of": str(row["as_of"]),
                    "model": str(row["model"]),
                    "ticker": ticker,
                    "winner_ticker": str(row["winner_ticker"]),
                    "probability": probability,
                    "yes_ask": ask,
                    "yes_ask_size": ask_size,
                    "fee_per_contract": one_contract_fee,
                    "model_ev": model_ev,
                    "market_midpoint_probability": market_probability,
                    "hrrr_probability": hrrr_probability,
                    "hrrr_agrees_with_weather_top": hrrr_agrees_with_weather_top,
                    "bracket_index": index,
                    "model_top_distance": index - model_top,
                    "hrrr_top_distance": index - hrrr_top,
                    "profitable": int(net_pnl_per_contract > 0.0),
                    "won": ticker == row["winner_ticker"],
                    "realized_net_pnl_per_contract": net_pnl_per_contract,
                    "features": features,
                    "row": row,
                }
            )
    return records


def prepare_trade_model_predictions(
    root: Path,
    cohort: str,
    rows: list[dict[str, Any]],
    base_model: str,
    fee_mode: str,
    min_events: int,
) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    records = build_trade_candidate_records(root, cohort, rows, base_model, fee_mode)
    prediction_lookup: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    score_rows: list[dict[str, Any]] = []
    metadata_rows: list[dict[str, Any]] = []
    for target_date in sorted({str(record["target_date"]) for record in records}):
        train = [record for record in records if str(record["target_date"]) < target_date]
        test = [record for record in records if str(record["target_date"]) == target_date]
        fit = fit_trade_decision_model(train, min_events)
        metadata_rows.append(
            {
                "target_date": target_date,
                "base_model": base_model,
                "mode": fit["mode"],
                "training_candidates": fit["training_candidates"],
                "training_events": fit["training_events"],
                "feature_count": fit["feature_count"],
                "min_training_events": min_events,
            }
        )
        test_by_snapshot: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for record in test:
            test_by_snapshot[
                (
                    str(record["target_date"]),
                    str(record["city"]),
                    str(record["checkpoint"]),
                )
            ].append(record)
        for snapshot_records in test_by_snapshot.values():
            scored_records = [
                (record, predict_trade_score(record, fit))
                for record in snapshot_records
            ]
            normalized_records = normalize_trade_scores(scored_records)
            for record, trade_probability in normalized_records:
                raw_score = next(
                    score
                    for candidate, score in scored_records
                    if candidate is record
                )
                used_fallback = trade_probability is None
                key = (
                    str(record["target_date"]),
                    str(record["city"]),
                    str(record["checkpoint"]),
                    str(record["ticker"]),
                )
                prediction = {
                    "trade_model_raw_score": raw_score,
                    "trade_model_probability": trade_probability,
                    "trade_model_training_events": fit["training_events"],
                    "trade_model_training_candidates": fit["training_candidates"],
                    "trade_model_mode": fit["mode"],
                    "trade_model_used_fallback": used_fallback,
                    "record": record,
                }
                prediction_lookup[key] = prediction
                score_rows.append(
                    {
                        "target_date": record["target_date"],
                        "city": record["city"],
                        "checkpoint": record["checkpoint"],
                        "as_of": record["as_of"],
                        "base_model": base_model,
                        "ticker": record["ticker"],
                        "winner_ticker": record["winner_ticker"],
                        "won": record["won"],
                        "profitable": record["profitable"],
                        "realized_net_pnl_per_contract": record[
                            "realized_net_pnl_per_contract"
                        ],
                        "probability": record["probability"],
                        "yes_ask": record["yes_ask"],
                        "fee_per_contract": record["fee_per_contract"],
                        "model_ev": record["model_ev"],
                        "trade_model_ev": (
                            trade_probability - float(record["yes_ask"]) - float(record["fee_per_contract"])
                            if trade_probability is not None
                            else None
                        ),
                        "market_midpoint_probability": record[
                            "market_midpoint_probability"
                        ],
                        "bracket_index": record["bracket_index"],
                        "model_top_distance": record["model_top_distance"],
                        "hrrr_top_distance": record["hrrr_top_distance"],
                        "model_top_probability_gap": record["features"][
                            "model_top_probability_gap"
                        ],
                        "candidate_probability_gap_to_leader": record["features"][
                            "candidate_probability_gap_to_leader"
                        ],
                        "trade_model_raw_score": raw_score,
                        "trade_model_probability": trade_probability,
                        "trade_model_mode": fit["mode"],
                        "trade_model_training_events": fit["training_events"],
                        "trade_model_training_candidates": fit["training_candidates"],
                        "trade_model_used_fallback": used_fallback,
                    }
                )
    return prediction_lookup, score_rows, metadata_rows


def grouped_strategy_feature_dict(record: dict[str, Any]) -> dict[str, Any]:
    probability = float(record["probability"])
    ask_cost = float(record["yes_ask"]) + float(record["fee_per_contract"])
    return {
        "weather_logit_probability": clipped_logit(probability),
        "ask_cost": ask_cost,
        "weather_edge_after_fee": probability - ask_cost,
        "candidate_gap_to_weather_leader": float(
            record["features"]["candidate_probability_gap_to_leader"]
        ),
        "top_two_weather_gap": float(record["features"]["model_top_probability_gap"]),
        "hrrr_support_delta": float(record["hrrr_probability"]) - probability,
        "relative_bracket_index": float(record["features"]["relative_bracket_index"]),
        "city": str(record["city"]),
        "checkpoint": str(record["checkpoint"]),
        "hrrr_agrees_with_weather_top": str(record["hrrr_agrees_with_weather_top"]),
    }


def grouped_strategy_feature_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([grouped_strategy_feature_dict(record) for record in records])


def fit_grouped_regression_edge_model(
    records: list[dict[str, Any]],
    min_events: int,
) -> dict[str, Any]:
    training_events = trade_model_event_count(records)
    feature_count = len(GROUPED_STRATEGY_NUMERIC_FEATURES) + len(
        GROUPED_STRATEGY_CATEGORICAL_FEATURES
    )
    if training_events < min_events:
        return {
            "model": None,
            "mode": "insufficient_training_data",
            "training_candidates": len(records),
            "training_events": training_events,
            "feature_count": feature_count,
        }
    labels = [int(bool(record["won"])) for record in records]
    if len(set(labels)) < 2:
        return {
            "model": None,
            "mode": "single_class_training_data",
            "training_candidates": len(records),
            "training_events": training_events,
            "feature_count": feature_count,
        }
    transformer = ColumnTransformer(
        transformers=[
            ("numeric", StandardScaler(), list(GROUPED_STRATEGY_NUMERIC_FEATURES)),
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore"),
                list(GROUPED_STRATEGY_CATEGORICAL_FEATURES),
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
    model.fit(grouped_strategy_feature_frame(records), labels)
    return {
        "model": model,
        "mode": "trained",
        "training_candidates": len(records),
        "training_events": training_events,
        "feature_count": feature_count,
    }


def predict_grouped_strategy_score(record: dict[str, Any], fit: dict[str, Any]) -> float | None:
    model = fit.get("model")
    if model is None:
        return None
    probabilities = model.predict_proba(pd.DataFrame([grouped_strategy_feature_dict(record)]))
    classes = list(model.classes_)  # type: ignore[attr-defined]
    if 1 not in classes:
        return None
    positive_index = classes.index(1)
    return float(probabilities[0][positive_index])


def prepare_grouped_strategy_predictions(
    root: Path,
    cohort: str,
    rows: list[dict[str, Any]],
    base_model: str,
    fee_mode: str,
    min_events: int,
) -> tuple[dict[tuple[str, str, str], list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    records = build_trade_candidate_records(root, cohort, rows, base_model, fee_mode)
    predictions_by_snapshot: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    score_rows: list[dict[str, Any]] = []
    metadata_rows: list[dict[str, Any]] = []
    for target_date in sorted({str(record["target_date"]) for record in records}):
        train = [record for record in records if str(record["target_date"]) < target_date]
        test = [record for record in records if str(record["target_date"]) == target_date]
        fit = fit_grouped_regression_edge_model(train, min_events)
        metadata_rows.append(
            {
                "target_date": target_date,
                "base_model": base_model,
                "mode": fit["mode"],
                "training_candidates": fit["training_candidates"],
                "training_events": fit["training_events"],
                "feature_count": fit["feature_count"],
                "min_training_events": min_events,
            }
        )
        test_by_snapshot: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for record in test:
            snapshot_key = (
                str(record["target_date"]),
                str(record["city"]),
                str(record["checkpoint"]),
            )
            test_by_snapshot[snapshot_key].append(record)
        for snapshot_key, snapshot_records in test_by_snapshot.items():
            scored_records = [
                (record, predict_grouped_strategy_score(record, fit))
                for record in snapshot_records
            ]
            normalized_records = normalize_trade_scores(scored_records)
            for record, fair_probability in normalized_records:
                raw_score = next(score for candidate, score in scored_records if candidate is record)
                learned_ev = (
                    float(fair_probability)
                    - float(record["yes_ask"])
                    - float(record["fee_per_contract"])
                    if fair_probability is not None
                    else None
                )
                features = grouped_strategy_feature_dict(record)
                prediction = {
                    "record": record,
                    "grouped_raw_score": raw_score,
                    "fair_probability": fair_probability,
                    "learned_ev": learned_ev,
                    "grouped_model_mode": fit["mode"],
                    "grouped_training_events": fit["training_events"],
                    "grouped_training_candidates": fit["training_candidates"],
                    "grouped_used_fallback": fair_probability is None,
                    "features": features,
                }
                predictions_by_snapshot[snapshot_key].append(prediction)
                score_rows.append(
                    {
                        "target_date": record["target_date"],
                        "city": record["city"],
                        "checkpoint": record["checkpoint"],
                        "as_of": record["as_of"],
                        "base_model": base_model,
                        "ticker": record["ticker"],
                        "winner_ticker": record["winner_ticker"],
                        "won": record["won"],
                        "weather_probability": record["probability"],
                        "yes_ask": record["yes_ask"],
                        "fee_per_contract": record["fee_per_contract"],
                        "ask_cost": float(record["yes_ask"]) + float(record["fee_per_contract"]),
                        "weather_edge_after_fee": float(record["probability"])
                        - float(record["yes_ask"])
                        - float(record["fee_per_contract"]),
                        "hrrr_probability": record["hrrr_probability"],
                        "hrrr_support_delta": features["hrrr_support_delta"],
                        "hrrr_agrees_with_weather_top": features[
                            "hrrr_agrees_with_weather_top"
                        ],
                        "candidate_gap_to_weather_leader": features[
                            "candidate_gap_to_weather_leader"
                        ],
                        "top_two_weather_gap": features["top_two_weather_gap"],
                        "relative_bracket_index": features["relative_bracket_index"],
                        "grouped_raw_score": raw_score,
                        "fair_probability": fair_probability,
                        "learned_ev": learned_ev,
                        "grouped_model_mode": fit["mode"],
                        "grouped_training_events": fit["training_events"],
                        "grouped_training_candidates": fit["training_candidates"],
                        "grouped_used_fallback": fair_probability is None,
                    }
                )
    return predictions_by_snapshot, score_rows, metadata_rows


def grouped_no_trade_decision(
    prediction: dict[str, Any] | None,
    strategy: str,
    min_ev: float,
    fee_mode: str,
    reason: str,
) -> dict[str, Any]:
    record = prediction["record"] if prediction is not None else {}
    row = record.get("row", {}) if record else {}
    return {
        "strategy": strategy,
        "model": row.get("model"),
        "min_ev": min_ev,
        "fee_mode": fee_mode,
        "decision": "no_trade",
        "no_trade_reason": reason,
        "city": row.get("city"),
        "target_date": row.get("target_date"),
        "checkpoint": row.get("checkpoint"),
        "as_of": row.get("as_of"),
        "ticker": record.get("ticker") if record else None,
        "winner_ticker": row.get("winner_ticker"),
        "best_rejected_would_have_won": (
            bool(record.get("won")) if record else None
        ),
        "weather_probability": record.get("probability") if record else None,
        "fair_probability": (
            prediction.get("fair_probability") if prediction is not None else None
        ),
        "learned_ev": prediction.get("learned_ev") if prediction is not None else None,
        "yes_ask": record.get("yes_ask") if record else None,
        "fee_per_contract": record.get("fee_per_contract") if record else None,
        "candidate_gap_to_weather_leader": (
            record.get("features", {}).get("candidate_probability_gap_to_leader")
            if record
            else None
        ),
        "grouped_model_mode": (
            prediction.get("grouped_model_mode") if prediction is not None else None
        ),
        "grouped_training_events": (
            prediction.get("grouped_training_events") if prediction is not None else None
        ),
        "grouped_training_candidates": (
            prediction.get("grouped_training_candidates") if prediction is not None else None
        ),
    }


def select_grouped_strategy_trade(
    predictions: list[dict[str, Any]],
    min_ev: float,
    grouped_min_ev: float,
    fee_mode: str,
    contracts: int,
    sizing: str,
    bankroll: float,
    kelly_multiplier: float,
    max_position_fraction: float,
    max_contracts: int,
    use_ask_size: bool,
    min_ask: float,
    longshot_ask_threshold: float,
    longshot_max_contracts: int | None,
    longshot_min_ev: float,
    max_ask: float,
    min_fair_probability: float,
    min_candidate_gap: float,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not predictions:
        return None, grouped_no_trade_decision(
            None,
            GROUPED_REGRESSION_EDGE_STRATEGY,
            min_ev,
            fee_mode,
            "no_valid_quotes",
        )
    if any(prediction["fair_probability"] is None for prediction in predictions):
        best_untrained = max(
            predictions,
            key=lambda item: float(item["record"]["probability"]),
        )
        return None, grouped_no_trade_decision(
            best_untrained,
            GROUPED_REGRESSION_EDGE_STRATEGY,
            min_ev,
            fee_mode,
            "insufficient_training_data",
        )
    ranked = sorted(
        predictions,
        key=lambda item: float(item["learned_ev"]),
        reverse=True,
    )
    best_rejected = ranked[0]
    required_ev = max(min_ev, grouped_min_ev)
    zero_contract_rejection: dict[str, Any] | None = None
    for prediction in ranked:
        record = prediction["record"]
        ask = float(record["yes_ask"])
        fair_probability = float(prediction["fair_probability"])
        candidate_gap = float(record["features"]["candidate_probability_gap_to_leader"])
        learned_ev = float(prediction["learned_ev"])
        if ask < min_ask or ask > max_ask:
            continue
        if fair_probability < min_fair_probability:
            continue
        if candidate_gap < min_candidate_gap:
            continue
        if learned_ev < required_ev:
            continue
        priced_record = dict(record)
        priced_record["probability"] = fair_probability
        trade = trade_from_record(
            priced_record,
            GROUPED_REGRESSION_EDGE_STRATEGY,
            required_ev,
            fee_mode,
            contracts,
            sizing,
            bankroll,
            kelly_multiplier,
            max_position_fraction,
            max_contracts,
            use_ask_size,
            min_ask,
            longshot_ask_threshold,
            longshot_max_contracts,
            longshot_min_ev,
            None,
            fair_probability * max(learned_ev, 0.0),
        )
        if trade is None:
            zero_contract_rejection = prediction
            continue
        trade.update(
            {
                "fair_probability": fair_probability,
                "weather_probability": record["probability"],
                "learned_ev": learned_ev,
                "grouped_raw_score": prediction["grouped_raw_score"],
                "grouped_rank_score": fair_probability * max(learned_ev, 0.0),
                "grouped_model_mode": prediction["grouped_model_mode"],
                "grouped_training_events": prediction["grouped_training_events"],
                "grouped_training_candidates": prediction[
                    "grouped_training_candidates"
                ],
                "candidate_gap_to_weather_leader": candidate_gap,
                "top_two_weather_gap": record["features"]["model_top_probability_gap"],
                "hrrr_support_delta": float(record["hrrr_probability"])
                - float(record["probability"]),
                "hrrr_agrees_with_weather_top": record[
                    "hrrr_agrees_with_weather_top"
                ],
            }
        )
        decision = {
            **grouped_no_trade_decision(
                prediction,
                GROUPED_REGRESSION_EDGE_STRATEGY,
                min_ev,
                fee_mode,
                "",
            ),
            "decision": "trade",
            "no_trade_reason": "",
            "contracts": trade["contracts"],
            "net_pnl": trade["net_pnl"],
            "stake": trade["stake"],
            "won": trade["won"],
            "fair_probability": fair_probability,
            "learned_ev": learned_ev,
            "grouped_rank_score": trade["grouped_rank_score"],
        }
        return trade, decision
    if zero_contract_rejection is not None:
        return None, grouped_no_trade_decision(
            zero_contract_rejection,
            GROUPED_REGRESSION_EDGE_STRATEGY,
            min_ev,
            fee_mode,
            "zero_contract_size",
        )
    reason = "below_min_ev"
    record = best_rejected["record"]
    ask = float(record["yes_ask"])
    fair_probability = float(best_rejected["fair_probability"])
    candidate_gap = float(record["features"]["candidate_probability_gap_to_leader"])
    if ask < min_ask or ask > max_ask:
        reason = "outside_ask_bounds"
    elif fair_probability < min_fair_probability:
        reason = "below_min_probability"
    elif candidate_gap < min_candidate_gap:
        reason = "too_far_from_weather_leader"
    elif float(best_rejected["learned_ev"]) < required_ev:
        reason = "below_min_ev"
    return None, grouped_no_trade_decision(
        best_rejected,
        GROUPED_REGRESSION_EDGE_STRATEGY,
        min_ev,
        fee_mode,
        reason,
    )


def trade_from_record(
    record: dict[str, Any],
    strategy: str,
    min_ev: float,
    fee_mode: str,
    contracts: int,
    sizing: str,
    bankroll: float,
    kelly_multiplier: float,
    max_position_fraction: float,
    max_contracts: int,
    use_ask_size: bool,
    min_ask: float,
    longshot_ask_threshold: float,
    longshot_max_contracts: int | None,
    longshot_min_ev: float,
    trade_model_prediction: dict[str, Any] | None = None,
    rank_score: float | None = None,
) -> dict[str, Any] | None:
    ask = float(record["yes_ask"])
    probability = float(record["probability"])
    required_ev = max(min_ev, longshot_min_ev) if ask <= longshot_ask_threshold else min_ev
    effective_max_contracts = max_contracts
    if ask <= longshot_ask_threshold and longshot_max_contracts is not None:
        effective_max_contracts = min(max_contracts, longshot_max_contracts)
    sizing_result = choose_contracts(
        probability,
        ask,
        fee_mode,
        contracts,
        sizing,
        bankroll,
        kelly_multiplier,
        max_position_fraction,
        effective_max_contracts,
        record["yes_ask_size"] if record["yes_ask_size"] is not None else None,
        use_ask_size,
    )
    sized_contracts = int(sizing_result["contracts"] or 0)
    if sized_contracts <= 0:
        return None
    won = bool(record["won"])
    fee = kalshi_fee(ask, sized_contracts, fee_mode)
    payout = float(sized_contracts) if won else 0.0
    ask_cost = ask * sized_contracts
    net_pnl = payout - ask_cost - fee
    row = record["row"]
    output = {
        "strategy": strategy,
        "model": row["model"],
        "min_ev": min_ev,
        "min_confidence_gap": None,
        "top_two_probability_gap": None,
        "fee_mode": fee_mode,
        "sizing": sizing,
        "contracts": sized_contracts,
        "bankroll": bankroll,
        "kelly_multiplier": kelly_multiplier,
        "kelly_fraction_raw": sizing_result["kelly_fraction_raw"],
        "kelly_fraction_used": sizing_result["kelly_fraction_used"],
        "max_position_fraction": max_position_fraction,
        "max_contracts": max_contracts,
        "effective_max_contracts": effective_max_contracts,
        "min_ask": min_ask,
        "longshot_ask_threshold": longshot_ask_threshold,
        "longshot_max_contracts": longshot_max_contracts,
        "longshot_min_ev": longshot_min_ev,
        "required_ev": required_ev,
        "ask_size_cap": sizing_result["ask_size_cap"],
        "city": row["city"],
        "target_date": row["target_date"],
        "checkpoint": row["checkpoint"],
        "as_of": row["as_of"],
        "ticker": record["ticker"],
        "winner_ticker": row["winner_ticker"],
        "probability": probability,
        "yes_ask": ask,
        "fee": fee,
        "fee_per_contract": fee / sized_contracts,
        "expected_value": probability - ask - kalshi_fee(ask, 1, fee_mode),
        "expected_value_total": probability * sized_contracts - ask_cost - fee,
        "market_midpoint_probability": record["market_midpoint_probability"],
        "won": won,
        "payout": payout,
        "ask_cost": ask_cost,
        "gross_pnl_before_fees": payout - ask_cost,
        "net_pnl": net_pnl,
        "stake": ask_cost + fee,
    }
    if trade_model_prediction is not None:
        output.update(
            {
                "trade_model_probability": trade_model_prediction[
                    "trade_model_probability"
                ],
                "trade_model_raw_score": trade_model_prediction[
                    "trade_model_raw_score"
                ],
                "trade_model_expected_value": (
                    float(trade_model_prediction["trade_model_probability"])
                    - ask
                    - kalshi_fee(ask, 1, fee_mode)
                    if trade_model_prediction["trade_model_probability"] is not None
                    else None
                ),
                "trade_model_rank_score": rank_score,
                "trade_model_training_events": trade_model_prediction[
                    "trade_model_training_events"
                ],
                "trade_model_training_candidates": trade_model_prediction[
                    "trade_model_training_candidates"
                ],
                "trade_model_mode": trade_model_prediction["trade_model_mode"],
                "trade_model_used_fallback": trade_model_prediction[
                    "trade_model_used_fallback"
                ],
            }
        )
    return output


def candidate_trades(
    root: Path,
    cohort: str,
    row: dict[str, Any],
    fee_mode: str,
    contracts: int,
    sizing: str,
    bankroll: float,
    kelly_multiplier: float,
    max_position_fraction: float,
    max_contracts: int,
    use_ask_size: bool,
    min_ask: float,
    longshot_ask_threshold: float,
    longshot_max_contracts: int | None,
    longshot_min_ev: float,
    max_ask: float,
    min_probability: float,
    min_ev: float,
    strategy: str,
    market_probs: dict[tuple[str, str, str], dict[str, float]],
    disagreement_margin: float,
    min_confidence_gap: float = 0.0,
) -> list[dict[str, Any]]:
    tickers = [str(value) for value in parse_json_list(row["tickers_json"], "tickers")]
    probabilities = [
        float(value)
        for value in parse_json_list(row["probabilities_json"], "probabilities")
    ]
    if len(tickers) != len(probabilities):
        raise DataError("tickers and probabilities do not align")
    snapshot = load_snapshot(root, cohort, row)
    quotes = market_quotes(snapshot, tickers)
    top_ticker = top_one_ticker(tickers, probabilities)
    top_gap = top_two_probability_gap(probabilities)
    market_lookup = market_probs.get(
        (str(row["target_date"]), str(row["city"]), str(row["checkpoint"])), {}
    )
    candidates: list[dict[str, Any]] = []
    for ticker, probability in zip(tickers, probabilities, strict=True):
        quote = quotes[ticker]
        ask = float(quote["yes_ask"])
        one_contract_fee = kalshi_fee(ask, 1, fee_mode)
        ev = probability - ask - one_contract_fee
        required_ev = max(min_ev, longshot_min_ev) if ask <= longshot_ask_threshold else min_ev
        if ev < required_ev or probability < min_probability or ask < min_ask or ask > max_ask:
            continue
        if strategy == "top_one_only" and ticker != top_ticker:
            continue
        if strategy == TOP_GAP_EV_STRATEGY:
            if ticker != top_ticker or top_gap < min_confidence_gap:
                continue
        if strategy == "weather_market_disagreement":
            if probability - market_lookup.get(ticker, 0.0) < disagreement_margin:
                continue
        effective_max_contracts = max_contracts
        if ask <= longshot_ask_threshold and longshot_max_contracts is not None:
            effective_max_contracts = min(max_contracts, longshot_max_contracts)
        sizing_result = choose_contracts(
            probability,
            ask,
            fee_mode,
            contracts,
            sizing,
            bankroll,
            kelly_multiplier,
            max_position_fraction,
            effective_max_contracts,
            quote["yes_ask_size"] if quote["yes_ask_size"] is not None else None,
            use_ask_size,
        )
        sized_contracts = int(sizing_result["contracts"] or 0)
        if sized_contracts <= 0:
            continue
        won = ticker == row["winner_ticker"]
        fee = kalshi_fee(ask, sized_contracts, fee_mode)
        payout = float(sized_contracts) if won else 0.0
        ask_cost = ask * sized_contracts
        net_pnl = payout - ask_cost - fee
        candidates.append(
            {
                "strategy": strategy,
                "model": row["model"],
                "min_ev": min_ev,
                "min_confidence_gap": min_confidence_gap
                if strategy == TOP_GAP_EV_STRATEGY
                else None,
                "top_two_probability_gap": top_gap,
                "fee_mode": fee_mode,
                "sizing": sizing,
                "contracts": sized_contracts,
                "bankroll": bankroll,
                "kelly_multiplier": kelly_multiplier,
                "kelly_fraction_raw": sizing_result["kelly_fraction_raw"],
                "kelly_fraction_used": sizing_result["kelly_fraction_used"],
                "max_position_fraction": max_position_fraction,
                "max_contracts": max_contracts,
                "effective_max_contracts": effective_max_contracts,
                "min_ask": min_ask,
                "longshot_ask_threshold": longshot_ask_threshold,
                "longshot_max_contracts": longshot_max_contracts,
                "longshot_min_ev": longshot_min_ev,
                "required_ev": required_ev,
                "ask_size_cap": sizing_result["ask_size_cap"],
                "city": row["city"],
                "target_date": row["target_date"],
                "checkpoint": row["checkpoint"],
                "as_of": row["as_of"],
                "ticker": ticker,
                "winner_ticker": row["winner_ticker"],
                "probability": probability,
                "yes_ask": ask,
                "fee": fee,
                "fee_per_contract": fee / sized_contracts,
                "expected_value": ev,
                "expected_value_total": probability * sized_contracts - ask_cost - fee,
                "market_midpoint_probability": market_lookup.get(ticker),
                "won": won,
                "payout": payout,
                "ask_cost": ask_cost,
                "gross_pnl_before_fees": payout - ask_cost,
                "net_pnl": net_pnl,
                "stake": ask_cost + fee,
            }
        )
    return candidates


def simulate_trades(
    root: Path,
    cohort: str,
    rows: list[dict[str, Any]],
    models: tuple[str, ...],
    min_evs: Iterable[float],
    fee_mode: str,
    contracts: int,
    sizing: str,
    bankroll: float,
    kelly_multiplier: float,
    max_position_fraction: float,
    max_contracts: int,
    use_ask_size: bool,
    min_ask: float,
    longshot_ask_threshold: float,
    longshot_max_contracts: int | None,
    longshot_min_ev: float,
    max_ask: float,
    min_probability: float,
    strategies: tuple[str, ...],
    disagreement_margin: float,
    confidence_gaps: Iterable[float] = DEFAULT_CONFIDENCE_GAPS,
    trade_model_base_model: str = DEFAULT_TRADE_MODEL_BASE_MODEL,
    trade_model_min_events: int = 12,
    trade_probability_threshold: float = 0.55,
    trade_model_min_ev: float = 0.02,
    trade_model_min_probability: float = 0.05,
    trade_model_score_rows: list[dict[str, Any]] | None = None,
    trade_model_metadata_rows: list[dict[str, Any]] | None = None,
    grouped_strategy_base_model: str = DEFAULT_TRADE_MODEL_BASE_MODEL,
    grouped_strategy_min_events: int = DEFAULT_GROUPED_STRATEGY_MIN_EVENTS,
    grouped_strategy_min_ev: float = DEFAULT_GROUPED_MIN_EV,
    grouped_min_fair_probability: float = DEFAULT_GROUPED_MIN_FAIR_PROBABILITY,
    grouped_min_candidate_gap: float = DEFAULT_GROUPED_MIN_CANDIDATE_GAP,
    grouped_strategy_score_rows: list[dict[str, Any]] | None = None,
    grouped_strategy_decision_rows: list[dict[str, Any]] | None = None,
    grouped_strategy_metadata_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    latest = latest_keys(rows)
    market_probs = market_midpoint_probabilities(rows)
    trade_predictions: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    trade_predictions_by_snapshot: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    if REGRESSION_TRADE_FILTER in strategies:
        predictions, scores, metadata = prepare_trade_model_predictions(
            root,
            cohort,
            rows,
            trade_model_base_model,
            fee_mode,
            trade_model_min_events,
        )
        trade_predictions = predictions
        for key, prediction in trade_predictions.items():
            trade_predictions_by_snapshot.setdefault(key[:3], []).append(prediction)
        if trade_model_score_rows is not None:
            trade_model_score_rows.extend(scores)
        if trade_model_metadata_rows is not None:
            trade_model_metadata_rows.extend(metadata)
    grouped_predictions_by_snapshot: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    if GROUPED_REGRESSION_EDGE_STRATEGY in strategies:
        grouped_predictions_by_snapshot, grouped_scores, grouped_metadata = (
            prepare_grouped_strategy_predictions(
                root,
                cohort,
                rows,
                grouped_strategy_base_model,
                fee_mode,
                grouped_strategy_min_events,
            )
        )
        if grouped_strategy_score_rows is not None:
            grouped_strategy_score_rows.extend(grouped_scores)
        if grouped_strategy_metadata_rows is not None:
            grouped_strategy_metadata_rows.extend(grouped_metadata)
    output: list[dict[str, Any]] = []
    seen_grouped_decisions: set[tuple[str, str, str, float]] = set()
    for strategy in strategies:
        for min_ev in min_evs:
            for row in rows:
                if row["model"] not in models:
                    continue
                if strategy == GROUPED_REGRESSION_EDGE_STRATEGY:
                    if row["model"] != grouped_strategy_base_model:
                        continue
                    snapshot_key = (
                        str(row["target_date"]),
                        str(row["city"]),
                        str(row["checkpoint"]),
                    )
                    effective_grouped_min_ev = max(min_ev, grouped_strategy_min_ev)
                    grouped_decision_key = (*snapshot_key, effective_grouped_min_ev)
                    if grouped_decision_key in seen_grouped_decisions:
                        continue
                    seen_grouped_decisions.add(grouped_decision_key)
                    trade, decision = select_grouped_strategy_trade(
                        grouped_predictions_by_snapshot.get(snapshot_key, []),
                        effective_grouped_min_ev,
                        grouped_strategy_min_ev,
                        fee_mode,
                        contracts,
                        sizing,
                        bankroll,
                        kelly_multiplier,
                        max_position_fraction,
                        max_contracts,
                        use_ask_size,
                        min_ask,
                        longshot_ask_threshold,
                        longshot_max_contracts,
                        longshot_min_ev,
                        max_ask,
                        grouped_min_fair_probability,
                        grouped_min_candidate_gap,
                    )
                    if grouped_strategy_decision_rows is not None:
                        decision.update(
                            {
                                "configured_min_ev": grouped_strategy_min_ev,
                                "input_min_ev": min_ev,
                                "configured_min_fair_probability": (
                                    grouped_min_fair_probability
                                ),
                                "configured_min_candidate_gap": grouped_min_candidate_gap,
                            }
                        )
                        if decision.get("target_date") is None:
                            decision.update(
                                {
                                    "model": row["model"],
                                    "city": row["city"],
                                    "target_date": row["target_date"],
                                    "checkpoint": row["checkpoint"],
                                    "as_of": row["as_of"],
                                    "winner_ticker": row["winner_ticker"],
                                }
                            )
                        grouped_strategy_decision_rows.append(decision)
                    if trade is not None:
                        output.append(trade)
                    continue
                if strategy == REGRESSION_TRADE_FILTER:
                    if row["model"] != trade_model_base_model:
                        continue
                    candidates: list[dict[str, Any]] = []
                    for prediction in trade_predictions_by_snapshot.get(
                        (
                            str(row["target_date"]),
                            str(row["city"]),
                            str(row["checkpoint"]),
                        ),
                        [],
                    ):
                        record = prediction["record"]
                        probability = float(record["probability"])
                        ask = float(record["yes_ask"])
                        model_ev = float(record["model_ev"])
                        if model_ev < max(min_ev, trade_model_min_ev):
                            continue
                        if probability < max(min_probability, trade_model_min_probability):
                            continue
                        if ask < min_ask or ask > max_ask:
                            continue
                        if prediction["trade_model_used_fallback"]:
                            fallback = trade_from_record(
                                record,
                                REGRESSION_TRADE_FILTER,
                                max(min_ev, trade_model_min_ev),
                                fee_mode,
                                contracts,
                                sizing,
                                bankroll,
                                kelly_multiplier,
                                max_position_fraction,
                                max_contracts,
                                use_ask_size,
                                min_ask,
                                longshot_ask_threshold,
                                longshot_max_contracts,
                                longshot_min_ev,
                                prediction,
                                None,
                            )
                            if fallback is not None:
                                candidates.append(fallback)
                            continue
                        trade_probability = float(prediction["trade_model_probability"])
                        trade_model_ev = (
                            trade_probability
                            - ask
                            - float(record["fee_per_contract"])
                        )
                        if trade_model_ev < max(min_ev, trade_model_min_ev):
                            continue
                        if trade_probability < trade_probability_threshold:
                            continue
                        if abs(int(record["model_top_distance"])) > 1:
                            continue
                        rank_score = trade_probability * max(trade_model_ev, 0.0)
                        trade = trade_from_record(
                            record,
                            REGRESSION_TRADE_FILTER,
                            max(min_ev, trade_model_min_ev),
                            fee_mode,
                            contracts,
                            sizing,
                            bankroll,
                            kelly_multiplier,
                            max_position_fraction,
                            max_contracts,
                            use_ask_size,
                            min_ask,
                            longshot_ask_threshold,
                            longshot_max_contracts,
                            longshot_min_ev,
                            prediction,
                            rank_score,
                        )
                        if trade is not None:
                            candidates.append(trade)
                    if candidates:
                        best = max(
                            candidates,
                            key=lambda item: (
                                item["trade_model_rank_score"]
                                if item["trade_model_rank_score"] is not None
                                else item["expected_value_total"]
                            ),
                        )
                        output.append(best)
                    continue
                if strategy == "taker_ev_latest_only" and row_key(row) not in latest:
                    continue
                gap_values = (
                    tuple(confidence_gaps)
                    if strategy == TOP_GAP_EV_STRATEGY
                    else (0.0,)
                )
                for min_confidence_gap in gap_values:
                    candidates = candidate_trades(
                        root,
                        cohort,
                        row,
                        fee_mode,
                        contracts,
                        sizing,
                        bankroll,
                        kelly_multiplier,
                        max_position_fraction,
                        max_contracts,
                        use_ask_size,
                        min_ask,
                        longshot_ask_threshold,
                        longshot_max_contracts,
                        longshot_min_ev,
                        max_ask,
                        min_probability,
                        min_ev,
                        strategy,
                        market_probs,
                        disagreement_margin,
                        min_confidence_gap,
                    )
                    if not candidates:
                        continue
                    best = max(candidates, key=lambda item: item["expected_value_total"])
                    output.append(best)
    return output


def aggregate(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row[key] for key in keys)].append(row)
    output: list[dict[str, Any]] = []
    for key_values, group in sorted(grouped.items()):
        stake = sum(float(row["stake"]) for row in group)
        net_pnl = sum(float(row["net_pnl"]) for row in group)
        fees = sum(float(row["fee"]) for row in group)
        gross = sum(float(row["gross_pnl_before_fees"]) for row in group)
        output.append(
            {
                **{key: value for key, value in zip(keys, key_values, strict=True)},
                "trade_count": len(group),
                "hit_rate": statistics.mean(float(boolish(row["won"])) for row in group),
                "average_probability": statistics.mean(float(row["probability"]) for row in group),
                "average_ask": statistics.mean(float(row["yes_ask"]) for row in group),
                "average_expected_value": statistics.mean(
                    float(row["expected_value"]) for row in group
                ),
                "average_contracts": statistics.mean(float(row["contracts"]) for row in group),
                "average_trade_model_probability": statistics.mean(
                    float(row["trade_model_probability"])
                    for row in group
                    if row.get("trade_model_probability") not in (None, "")
                )
                if any(row.get("trade_model_probability") not in (None, "") for row in group)
                else None,
                "gross_pnl_before_fees": gross,
                "fees": fees,
                "net_pnl": net_pnl,
                "stake": stake,
                "roi": net_pnl / stake if stake else None,
            }
        )
    return output


def cumulative_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, float, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                str(row["strategy"]),
                str(row["model"]),
                float(row["min_ev"]),
                row.get("min_confidence_gap"),
            )
        ].append(row)
    for (strategy, model, min_ev, min_confidence_gap), group in sorted(grouped.items()):
        total = 0.0
        for row in sorted(group, key=lambda item: (str(item["target_date"]), str(item["as_of"]))):
            total += float(row["net_pnl"])
            output.append(
                {
                    "strategy": strategy,
                    "model": model,
                    "min_ev": min_ev,
                    "min_confidence_gap": min_confidence_gap,
                    "target_date": row["target_date"],
                    "as_of": row["as_of"],
                    "cumulative_net_pnl": total,
                }
            )
    return output


def grouped_decision_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["strategy"]), str(row["model"]), float(row["min_ev"]))].append(row)
    output: list[dict[str, Any]] = []
    for (strategy, model, min_ev), group in sorted(grouped.items()):
        trades = [row for row in group if row["decision"] == "trade"]
        no_trades = [row for row in group if row["decision"] == "no_trade"]
        stake = sum(float(row.get("stake") or 0.0) for row in trades)
        net_pnl = sum(float(row.get("net_pnl") or 0.0) for row in trades)
        output.append(
            {
                "strategy": strategy,
                "model": model,
                "min_ev": min_ev,
                "evaluated_snapshots": len(group),
                "trade_count": len(trades),
                "no_trade_count": len(no_trades),
                "trade_coverage_rate": len(trades) / len(group) if group else None,
                "hit_rate": (
                    statistics.mean(float(boolish(row["won"])) for row in trades)
                    if trades
                    else None
                ),
                "missed_winners_on_no_trade_snapshots": sum(
                    int(boolish(row.get("best_rejected_would_have_won")))
                    for row in no_trades
                ),
                "average_fair_probability": (
                    statistics.mean(float(row["fair_probability"]) for row in trades)
                    if trades
                    else None
                ),
                "average_learned_ev": (
                    statistics.mean(float(row["learned_ev"]) for row in trades)
                    if trades
                    else None
                ),
                "average_ask": (
                    statistics.mean(float(row["yes_ask"]) for row in trades)
                    if trades
                    else None
                ),
                "net_pnl": net_pnl,
                "stake": stake,
                "roi": net_pnl / stake if stake else None,
            }
        )
    return output


def write_empty_csv(path: Path, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        handle.write(",".join(fields) + "\n")


def plot_strategy(summary_rows: list[dict[str, Any]], cumulative: list[dict[str, Any]], output: Path) -> None:
    figure, axes = plt.subplots(2, 2, figsize=(15, 10), constrained_layout=True)
    primary = [
        row
        for row in summary_rows
        if row["strategy"] == "taker_ev_latest_only"
        and math.isclose(float(row["min_ev"]), 0.05)
    ]
    models = [row["model"] for row in primary]
    positions = list(range(len(models)))
    axes[0, 0].bar(positions, [float(row["net_pnl"]) for row in primary], color="#247BA0")
    axes[0, 0].axhline(0, color="#111827", linewidth=0.8)
    axes[0, 0].set_title("Net PnL by model (latest only, EV >= 5%)")
    axes[0, 0].set_xticks(positions, models, rotation=30, ha="right")

    axes[0, 1].bar(
        positions,
        [
            (float(row["roi"]) if row["roi"] not in (None, "") else 0.0) * 100.0
            for row in primary
        ],
        color="#D97706",
    )
    axes[0, 1].axhline(0, color="#111827", linewidth=0.8)
    axes[0, 1].set_title("ROI by model (latest only, EV >= 5%)")
    axes[0, 1].set_ylabel("Percent")
    axes[0, 1].set_xticks(positions, models, rotation=30, ha="right")

    for model in sorted({row["model"] for row in cumulative}):
        series = [
            row
            for row in cumulative
            if row["strategy"] == "taker_ev_latest_only"
            and row["model"] == model
            and math.isclose(float(row["min_ev"]), 0.05)
        ]
        if series:
            axes[1, 0].plot(
                [row["target_date"] for row in series],
                [float(row["cumulative_net_pnl"]) for row in series],
                marker="o",
                label=model,
            )
    axes[1, 0].axhline(0, color="#111827", linewidth=0.8)
    axes[1, 0].set_title("Cumulative net PnL")
    axes[1, 0].tick_params(axis="x", rotation=30)
    axes[1, 0].legend(fontsize=8)

    axes[1, 1].scatter(
        [float(row["trade_count"]) for row in summary_rows],
        [
            (float(row["roi"]) if row["roi"] not in (None, "") else 0.0) * 100.0
            for row in summary_rows
        ],
        color="#167D8D",
        alpha=0.75,
    )
    axes[1, 1].axhline(0, color="#111827", linewidth=0.8)
    axes[1, 1].set_title("Trade count vs ROI")
    axes[1, 1].set_xlabel("Trades")
    axes[1, 1].set_ylabel("ROI (%)")
    for axis in axes.flat:
        axis.grid(alpha=0.2)
        axis.spines[["top", "right"]].set_visible(False)
    figure.suptitle("Offline Strategy Simulation", fontweight="bold")
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(figure)


def parse_csv_tuple(value: str, cast: Any = str) -> tuple[Any, ...]:
    return tuple(cast(part.strip()) for part in value.split(",") if part.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate offline strategy PnL from archived weather forecasts.")
    parser.add_argument("--root", type=Path, default=Path("backtest_data"))
    parser.add_argument("--cohort", default="pilot-v1")
    parser.add_argument("--output-dir", type=Path, default=Path("output/strategy_simulation"))
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--candidates", default=",".join(DEFAULT_CANDIDATES))
    parser.add_argument("--strategies", default=",".join(DEFAULT_STRATEGIES))
    parser.add_argument("--min-evs", default="0,0.02,0.05,0.10")
    parser.add_argument(
        "--confidence-gaps",
        default=",".join(str(value) for value in DEFAULT_CONFIDENCE_GAPS),
    )
    parser.add_argument("--fee-mode", choices=("taker", "maker", "none"), default="taker")
    parser.add_argument("--contracts", type=int, default=1)
    parser.add_argument("--sizing", choices=("fixed", "kelly"), default="fixed")
    parser.add_argument("--bankroll", type=float, default=100.0)
    parser.add_argument("--kelly-multiplier", type=float, default=0.25)
    parser.add_argument("--max-position-fraction", type=float, default=0.05)
    parser.add_argument("--max-contracts", type=int, default=10)
    parser.add_argument("--ignore-ask-size", action="store_true")
    parser.add_argument("--min-ask", type=float, default=0.05)
    parser.add_argument("--longshot-ask-threshold", type=float, default=0.05)
    parser.add_argument("--longshot-max-contracts", type=int)
    parser.add_argument("--longshot-min-ev", type=float, default=0.0)
    parser.add_argument("--max-ask", type=float, default=0.95)
    parser.add_argument("--min-probability", type=float, default=0.0)
    parser.add_argument("--disagreement-margin", type=float, default=0.05)
    parser.add_argument("--grid-step", type=float, default=0.1)
    parser.add_argument("--regularization", type=float, default=0.01)
    parser.add_argument("--min-train-events", type=int, default=4)
    parser.add_argument("--probability-floor", type=float, default=PROBABILITY_FLOOR)
    parser.add_argument("--trade-model-base-model", default=DEFAULT_TRADE_MODEL_BASE_MODEL)
    parser.add_argument("--trade-model-min-events", type=int, default=12)
    parser.add_argument("--trade-probability-threshold", type=float, default=0.55)
    parser.add_argument("--trade-model-min-ev", type=float, default=0.02)
    parser.add_argument("--trade-model-min-probability", type=float, default=0.05)
    parser.add_argument("--grouped-strategy-base-model", default=DEFAULT_TRADE_MODEL_BASE_MODEL)
    parser.add_argument(
        "--grouped-strategy-min-events",
        type=int,
        default=DEFAULT_GROUPED_STRATEGY_MIN_EVENTS,
    )
    parser.add_argument(
        "--grouped-min-ev",
        type=float,
        default=DEFAULT_GROUPED_MIN_EV,
    )
    parser.add_argument(
        "--grouped-min-fair-probability",
        type=float,
        default=DEFAULT_GROUPED_MIN_FAIR_PROBABILITY,
    )
    parser.add_argument(
        "--grouped-min-candidate-gap",
        type=float,
        default=DEFAULT_GROUPED_MIN_CANDIDATE_GAP,
    )
    args = parser.parse_args()

    models = parse_csv_tuple(args.models)
    rows = model_rows(
        args.root,
        args.cohort,
        parse_candidates(args.candidates),
        args.grid_step,
        args.regularization,
        args.min_train_events,
        args.probability_floor,
    )
    trade_model_score_rows: list[dict[str, Any]] = []
    trade_model_metadata_rows: list[dict[str, Any]] = []
    grouped_strategy_score_rows: list[dict[str, Any]] = []
    grouped_strategy_decision_rows: list[dict[str, Any]] = []
    grouped_strategy_metadata_rows: list[dict[str, Any]] = []
    trades = simulate_trades(
        args.root,
        args.cohort,
        rows,
        models,
        parse_csv_tuple(args.min_evs, float),
        args.fee_mode,
        args.contracts,
        args.sizing,
        args.bankroll,
        args.kelly_multiplier,
        args.max_position_fraction,
        args.max_contracts,
        not args.ignore_ask_size,
        args.min_ask,
        args.longshot_ask_threshold,
        args.longshot_max_contracts,
        args.longshot_min_ev,
        args.max_ask,
        args.min_probability,
        parse_csv_tuple(args.strategies),
        args.disagreement_margin,
        parse_csv_tuple(args.confidence_gaps, float),
        args.trade_model_base_model,
        args.trade_model_min_events,
        args.trade_probability_threshold,
        args.trade_model_min_ev,
        args.trade_model_min_probability,
        trade_model_score_rows,
        trade_model_metadata_rows,
        args.grouped_strategy_base_model,
        args.grouped_strategy_min_events,
        args.grouped_min_ev,
        args.grouped_min_fair_probability,
        args.grouped_min_candidate_gap,
        grouped_strategy_score_rows,
        grouped_strategy_decision_rows,
        grouped_strategy_metadata_rows,
    )
    summary = (
        aggregate(trades, ("strategy", "model", "min_ev", "min_confidence_gap"))
        if trades
        else []
    )
    grouped_summary = grouped_decision_summary(grouped_strategy_decision_rows)
    by_checkpoint = (
        aggregate(
            trades,
            ("strategy", "model", "min_ev", "min_confidence_gap", "checkpoint"),
        )
        if trades
        else []
    )
    by_city = (
        aggregate(trades, ("strategy", "model", "min_ev", "min_confidence_gap", "city"))
        if trades
        else []
    )
    by_model = aggregate(trades, ("model",)) if trades else []
    negative = sorted(
        [row for row in trades if float(row["net_pnl"]) < 0],
        key=lambda row: float(row["net_pnl"]),
    )
    cumulative = cumulative_rows(trades)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if trades:
        write_csv(args.output_dir / "trades.csv", trades)
    else:
        write_empty_csv(args.output_dir / "trades.csv", ["strategy", "model", "net_pnl"])
    write_csv(args.output_dir / "summary.csv", summary) if summary else write_empty_csv(args.output_dir / "summary.csv", ["strategy", "model", "min_ev", "min_confidence_gap"])
    write_csv(args.output_dir / "by_checkpoint.csv", by_checkpoint) if by_checkpoint else write_empty_csv(args.output_dir / "by_checkpoint.csv", ["strategy", "model", "min_ev", "min_confidence_gap", "checkpoint"])
    write_csv(args.output_dir / "by_city.csv", by_city) if by_city else write_empty_csv(args.output_dir / "by_city.csv", ["strategy", "model", "min_ev", "min_confidence_gap", "city"])
    write_csv(args.output_dir / "by_model.csv", by_model) if by_model else write_empty_csv(args.output_dir / "by_model.csv", ["model"])
    write_csv(args.output_dir / "negative_trades.csv", negative) if negative else write_empty_csv(args.output_dir / "negative_trades.csv", ["strategy", "model", "net_pnl"])
    write_csv(args.output_dir / "cumulative_pnl.csv", cumulative) if cumulative else write_empty_csv(args.output_dir / "cumulative_pnl.csv", ["strategy", "model", "min_ev", "min_confidence_gap", "target_date", "cumulative_net_pnl"])
    if trade_model_score_rows:
        write_csv(args.output_dir / "trade_model_scores.csv", trade_model_score_rows)
    else:
        write_empty_csv(
            args.output_dir / "trade_model_scores.csv",
            ["target_date", "city", "checkpoint", "ticker", "trade_model_probability"],
        )
    (args.output_dir / "trade_model_metadata.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_model": args.trade_model_base_model,
                "min_training_events": args.trade_model_min_events,
                "trade_probability_threshold": args.trade_probability_threshold,
                "trade_model_min_ev": args.trade_model_min_ev,
                "trade_model_min_probability": args.trade_model_min_probability,
                "numeric_features": list(TRADE_MODEL_NUMERIC_FEATURES),
                "categorical_features": list(TRADE_MODEL_CATEGORICAL_FEATURES),
                "fits": trade_model_metadata_rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if grouped_strategy_score_rows:
        write_csv(args.output_dir / "grouped_strategy_scores.csv", grouped_strategy_score_rows)
    else:
        write_empty_csv(
            args.output_dir / "grouped_strategy_scores.csv",
            ["target_date", "city", "checkpoint", "ticker", "fair_probability"],
        )
    if grouped_strategy_decision_rows:
        write_csv(
            args.output_dir / "grouped_strategy_decisions.csv",
            grouped_strategy_decision_rows,
        )
    else:
        write_empty_csv(
            args.output_dir / "grouped_strategy_decisions.csv",
            ["target_date", "city", "checkpoint", "decision", "no_trade_reason"],
        )
    grouped_no_trades = [
        row for row in grouped_strategy_decision_rows if row["decision"] == "no_trade"
    ]
    if grouped_no_trades:
        write_csv(args.output_dir / "grouped_strategy_no_trades.csv", grouped_no_trades)
    else:
        write_empty_csv(
            args.output_dir / "grouped_strategy_no_trades.csv",
            ["target_date", "city", "checkpoint", "no_trade_reason"],
        )
    (args.output_dir / "grouped_strategy_metadata.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "strategy": GROUPED_REGRESSION_EDGE_STRATEGY,
                "base_model": args.grouped_strategy_base_model,
                "min_training_events": args.grouped_strategy_min_events,
                "min_ev": args.grouped_min_ev,
                "min_fair_probability": args.grouped_min_fair_probability,
                "min_candidate_gap": args.grouped_min_candidate_gap,
                "numeric_features": list(GROUPED_STRATEGY_NUMERIC_FEATURES),
                "categorical_features": list(GROUPED_STRATEGY_CATEGORICAL_FEATURES),
                "fits": grouped_strategy_metadata_rows,
                "summary": grouped_summary,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if summary:
        plot_strategy(summary, cumulative, args.output_dir / "strategy_dashboard.png")

    payload = {
        "schema_version": 1,
        "cohort": args.cohort,
        "generated_at": datetime.now(UTC).isoformat(),
        "fee_mode": args.fee_mode,
        "contracts": args.contracts,
        "sizing": args.sizing,
        "bankroll": args.bankroll,
        "kelly_multiplier": args.kelly_multiplier,
        "max_position_fraction": args.max_position_fraction,
        "max_contracts": args.max_contracts,
        "uses_ask_size_cap": not args.ignore_ask_size,
        "min_ask": args.min_ask,
        "longshot_ask_threshold": args.longshot_ask_threshold,
        "longshot_max_contracts": args.longshot_max_contracts,
        "longshot_min_ev": args.longshot_min_ev,
        "models": list(models),
        "strategies": list(parse_csv_tuple(args.strategies)),
        "min_evs": list(parse_csv_tuple(args.min_evs, float)),
        "confidence_gaps": list(parse_csv_tuple(args.confidence_gaps, float)),
        "trade_model_base_model": args.trade_model_base_model,
        "trade_model_min_events": args.trade_model_min_events,
        "trade_probability_threshold": args.trade_probability_threshold,
        "trade_model_min_ev": args.trade_model_min_ev,
        "trade_model_min_probability": args.trade_model_min_probability,
        "trade_model_score_count": len(trade_model_score_rows),
        "grouped_strategy_base_model": args.grouped_strategy_base_model,
        "grouped_strategy_min_events": args.grouped_strategy_min_events,
        "grouped_min_ev": args.grouped_min_ev,
        "grouped_min_fair_probability": args.grouped_min_fair_probability,
        "grouped_min_candidate_gap": args.grouped_min_candidate_gap,
        "grouped_strategy_score_count": len(grouped_strategy_score_rows),
        "grouped_strategy_decision_count": len(grouped_strategy_decision_rows),
        "grouped_strategy_summary": grouped_summary,
        "trade_count": len(trades),
        "summary": summary,
        "warnings": [
            "Uses archived YES ask quotes, not guaranteed live fills.",
            "Position sizing is an offline estimate and assumes each archived ask size was fillable.",
            "Does not model slippage, queue position, partial fills, or order cancellations.",
            "Sample size remains small; results are exploratory.",
            "Market midpoint is included as a benchmark only and is not directly executable.",
        ],
    }
    (args.output_dir / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Simulated {len(trades)} trades across {len(summary)} strategy/model/threshold groups.")
    primary = [
        row
        for row in summary
        if row["strategy"] == "taker_ev_latest_only"
        and math.isclose(float(row["min_ev"]), 0.05)
    ]
    for row in primary:
        print(
            f"{row['model']}: trades={row['trade_count']} net={row['net_pnl']:.2f} "
            f"roi={row['roi']:.1%}"
        )
    print(f"Results written to {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
