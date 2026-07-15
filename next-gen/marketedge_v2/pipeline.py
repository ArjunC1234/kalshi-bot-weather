"""Leakage-safe market-aware weather distribution and execution replay."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any

import matplotlib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from backtest.data_sources import LocalExportSource

matplotlib.use("Agg")
import matplotlib.pyplot as plt

EPSILON = 1e-5
WEATHER_NUMERIC = [
    "nws_anchor_high_f",
    "nws_daily_daytime_high_f",
    "nws_hourly_window_max_f",
    "nws_remaining_day_max_f",
    "observed_high_so_far_f",
    "latest_observation_temp_f",
    "observation_age_seconds",
    "warming_rate_last_1h_f_per_hour",
    "warming_rate_last_3h_f_per_hour",
    "hrrr_projected_high_f",
    "nbm_projected_high_f",
    "ensemble_raw_median_high_f",
    "ensemble_raw_mean_high_f",
    "ensemble_member_stddev_f",
    "weather_source_stddev_f",
    "all_weather_sources_range_f",
    "nws_hrrr_disagreement_f",
    "nws_nbm_disagreement_f",
    "hrrr_nbm_disagreement_f",
    "hours_since_climate_start",
    "hours_until_climate_end",
]
WEATHER_CATEGORICAL = ["city"]
FAMILY_WEATHER_NUMERIC = [
    "family_baseline_high_f",
    "family_numerical_anchor_high_f",
    "family_nws_minus_nbm_f",
    "family_hrrr_minus_nbm_f",
    "family_ensemble_minus_nbm_f",
    "family_numerical_disagreement_f",
    "family_disagreement_range_f",
    "family_disagreement_std_f",
    "observed_high_so_far_f",
    "latest_observation_temp_f",
    "observation_age_seconds",
    "warming_rate_last_1h_f_per_hour",
    "warming_rate_last_3h_f_per_hour",
    "hours_since_climate_start",
    "hours_until_climate_end",
]
WEATHER_FEATURE_PROFILES = ("legacy", "family_v2")
FEATURE_CATALOG = [
    ("nws_anchor_high_f", "weather", "forecast level", "primary deterministic forecast anchor"),
    ("nws_hourly_window_max_f", "weather", "forecast level", "short-horizon NWS peak"),
    ("nws_remaining_day_max_f", "weather", "forecast level", "remaining-day NWS maximum"),
    ("hrrr_projected_high_f", "weather", "forecast level", "high-resolution model peak"),
    ("nbm_projected_high_f", "weather", "forecast level", "blended numerical model peak"),
    ("ensemble_raw_median_high_f", "weather", "forecast level", "ensemble central estimate"),
    ("observed_high_so_far_f", "weather", "hard constraint", "final high cannot be lower"),
    ("warming_rate_last_1h_f_per_hour", "weather", "trajectory", "short-term observed trend"),
    ("hours_until_climate_end", "weather", "lead time", "remaining opportunity to warm"),
    ("weather_source_stddev_f", "weather", "uncertainty", "widens conformal forecast scale"),
    ("all_weather_sources_range_f", "weather", "uncertainty", "source disagreement diagnostic"),
    ("city", "weather", "regularized intercept", "shrunk station-level bias only"),
    (
        "normalized_market_midpoint_probability",
        "probability",
        "market prior",
        "base contract probability",
    ),
    (
        "bracket_lower_f/bracket_upper_f",
        "probability",
        "geometry",
        "integrates temperature distribution",
    ),
    ("yes_ask_dollars/no_ask_dollars", "execution", "cost", "executable entry price"),
    ("yes_spread", "execution", "liquidity", "blocks expensive execution"),
    ("yes_ask_size/no_ask_size", "execution", "capacity", "caps simulated fill size"),
    (
        "liquidity_dollars/volume/open_interest",
        "execution",
        "diagnostic",
        "reported but not fitted",
    ),
]


@dataclass(frozen=True)
class MarketEdgeConfig:
    min_training_dates: int = 5
    ridge_alpha: float = 20.0
    blend_penalty: float = 0.05
    minimum_ev: float = 0.03
    max_spread: float = 0.10
    daily_budget: float = 40.0
    max_order_cost: float = 3.0
    max_contracts_per_order: int = 20
    min_quote_size: float = 1.0
    required_confirmations: int = 2
    fee_mode: str = "taker"
    weather_feature_profile: str = "legacy"


def run_marketedge_backtest(
    data_path: str | Path,
    output_dir: str | Path,
    config: MarketEdgeConfig | None = None,
) -> dict[str, Any]:
    config = config or MarketEdgeConfig()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    weather, market = _load_frames(Path(data_path))
    _write_rows(
        output / "feature_catalog.csv",
        [
            dict(zip(("feature", "layer", "role", "use"), row, strict=True))
            for row in FEATURE_CATALOG
        ],
    )
    weather_predictions, market_predictions, combined_predictions, diagnostics = _walk_forward(
        weather, market, config
    )
    temperature_metrics = _temperature_metrics(weather_predictions)
    probability_metrics = {
        "market": _probability_metrics(market_predictions),
        "weather": _probability_metrics(_weather_distributions(combined_predictions)),
        "marketedge": _probability_metrics(combined_predictions),
    }
    trades, decisions = _replay(combined_predictions, market, config)
    strategy = _strategy_summary(trades)
    summary = {
        "mode": "marketedge_v2_expanding_walk_forward",
        "data_path": str(data_path),
        "config": asdict(config),
        "independent_city_events": int(
            weather[["city", "event_ticker"]].drop_duplicates().shape[0]
        ),
        "target_dates": int(weather["target_date"].nunique()),
        "weather_snapshot_rows": int(len(weather)),
        "market_snapshot_rows": int(len(market)),
        "temperature_metrics": temperature_metrics,
        "probability_metrics": probability_metrics,
        "strategy": strategy,
    }
    _write_rows(output / "weather_predictions.csv", weather_predictions.to_dict("records"))
    _write_rows(output / "market_distributions.csv", market_predictions.to_dict("records"))
    _write_rows(output / "marketedge_distributions.csv", combined_predictions.to_dict("records"))
    _write_rows(output / "training_diagnostics.csv", diagnostics)
    _write_rows(output / "trade_decisions.csv", decisions)
    _write_rows(output / "trades.csv", trades)
    _write_rows(output / "temperature_metrics.csv", temperature_metrics)
    _write_rows(
        output / "probability_metrics.csv", _flatten_probability_metrics(probability_metrics)
    )
    _write_rows(output / "daily_pnl.csv", _daily_pnl(trades))
    _write_rows(output / "trade_metrics_by_side.csv", _trade_groups(trades, "side"))
    _write_rows(output / "trade_metrics_by_city.csv", _trade_groups(trades, "city"))
    _write_rows(output / "trade_metrics_by_checkpoint.csv", _trade_groups(trades, "checkpoint"))
    _write_rows(output / "trade_metrics_by_edge.csv", _trade_groups(trades, "edge_bucket"))
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    _write_report(output / "marketedge_report.md", summary)
    _plot_metrics(output, weather_predictions, combined_predictions, trades)
    return {**summary, "output_dir": str(output)}


def _load_frames(data_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    source = LocalExportSource(data_path)
    weather = pd.DataFrame(source.load_table("weather_snapshots"))
    market = pd.DataFrame(source.load_table("market_snapshots"))
    labels = pd.DataFrame(source.load_table("final_temperature_labels"))
    settlements = pd.DataFrame(source.load_table("settlements"))
    if weather.empty or market.empty or labels.empty:
        raise ValueError("export requires weather snapshots, market snapshots, and final labels")
    labels = labels[labels.get("validation_status", "valid").fillna("valid") == "valid"].copy()
    labels = labels[["city", "event_ticker", "target_date", "final_high_f"]].drop_duplicates(
        ["city", "event_ticker"], keep="last"
    )
    settlement_columns = ["city", "event_ticker", "target_date", "winner_ticker", "settled_at_utc"]
    settlement = settlements[
        [column for column in settlement_columns if column in settlements]
    ].copy()
    weather = weather.merge(labels, on=["city", "event_ticker", "target_date"], how="inner")
    market = market.merge(labels, on=["city", "event_ticker", "target_date"], how="inner")
    market = market.merge(settlement, on=["city", "event_ticker", "target_date"], how="left")
    for frame in (weather, market):
        frame["snapshot_time_utc"] = pd.to_datetime(frame["snapshot_time_utc"], utc=True)
        frame["target_date"] = frame["target_date"].astype(str)
    numeric_columns = set(WEATHER_NUMERIC) | {
        "bracket_lower_f",
        "bracket_upper_f",
        "yes_bid_dollars",
        "yes_ask_dollars",
        "no_bid_dollars",
        "no_ask_dollars",
        "yes_bid_size",
        "yes_ask_size",
        "no_bid_size",
        "no_ask_size",
        "normalized_market_midpoint_probability",
        "last_price_dollars",
        "previous_price_dollars",
        "yes_spread",
        "liquidity_dollars",
        "volume",
        "open_interest",
    }
    for frame in (weather, market):
        for column in numeric_columns.intersection(frame.columns):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    weather = _add_family_features(weather)
    return weather.sort_values("snapshot_time_utc").reset_index(drop=True), market.sort_values(
        "snapshot_time_utc"
    ).reset_index(drop=True)


def _walk_forward(
    weather: pd.DataFrame,
    market: pd.DataFrame,
    config: MarketEdgeConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    weather_outputs: list[pd.DataFrame] = []
    market_outputs: list[pd.DataFrame] = []
    combined_outputs: list[pd.DataFrame] = []
    diagnostics: list[dict[str, Any]] = []
    dates = sorted(weather["target_date"].unique())
    for target_date in dates:
        train_weather = weather[weather["target_date"] < target_date]
        test_weather = weather[weather["target_date"] == target_date].copy()
        train_dates = int(train_weather["target_date"].nunique())
        model, mode, scale = _fit_weather_model(train_weather, config)
        test_weather["weather_mean_f"] = _predict_weather(model, test_weather, config)
        test_weather["weather_scale_f"] = _prediction_scale(test_weather, scale)
        test_weather["weather_mode"] = mode
        weather_outputs.append(test_weather)
        day_market = market[market["target_date"] == target_date].copy()
        if day_market.empty:
            diagnostics.append(
                {
                    "target_date": target_date,
                    "weather_training_dates": train_dates,
                    "weather_training_events": int(
                        train_weather[["city", "event_ticker"]].drop_duplicates().shape[0]
                    ),
                    "weather_mode": mode,
                    "conformal_scale_f": scale,
                    "probability_training_dates": 0,
                    "market_residual_weight": 0.0,
                    "warning": "no_market_rows_for_labeled_target_date",
                }
            )
            continue
        day = day_market.merge(
            test_weather[
                [
                    "city",
                    "event_ticker",
                    "snapshot_time_utc",
                    "weather_mean_f",
                    "weather_scale_f",
                    "weather_mode",
                    "observed_high_so_far_f",
                    "hours_until_climate_end",
                    "weather_source_stddev_f",
                ]
            ],
            on=["city", "event_ticker", "snapshot_time_utc"],
            how="inner",
        )
        day["market_probability"] = _market_probability(day)
        day["weather_probability"] = _weather_probability(day)
        market_outputs.append(_distribution_rows(day, "market_probability", "market"))
        prior = (
            pd.concat(combined_outputs, ignore_index=True) if combined_outputs else pd.DataFrame()
        )
        blend_weight = _fit_blend_weight(prior, config)
        day["marketedge_probability"] = _blend_probability(day, blend_weight)
        day = _normalize_probabilities(day, "marketedge_probability")
        combined_outputs.append(day)
        diagnostics.append(
            {
                "target_date": target_date,
                "weather_training_dates": train_dates,
                "weather_training_events": int(
                    train_weather[["city", "event_ticker"]].drop_duplicates().shape[0]
                ),
                "weather_mode": mode,
                "conformal_scale_f": scale,
                "probability_training_dates": int(prior["target_date"].nunique())
                if not prior.empty
                else 0,
                "market_residual_weight": blend_weight,
            }
        )
    weather_predictions = pd.concat(weather_outputs, ignore_index=True)
    combined = pd.concat(combined_outputs, ignore_index=True)
    combined["model_name"] = "marketedge"
    combined["model_probability"] = combined["marketedge_probability"]
    market_distributions = pd.concat(market_outputs, ignore_index=True)
    return weather_predictions, market_distributions, combined, diagnostics


def _fit_weather_model(
    train: pd.DataFrame,
    config: MarketEdgeConfig,
) -> tuple[Pipeline | None, str, float]:
    train_dates = train["target_date"].nunique()
    baseline = _source_blend(train, config.weather_feature_profile)
    if train_dates < config.min_training_dates:
        residuals = train["final_high_f"] - baseline if not train.empty else pd.Series([2.0])
        return None, f"{config.weather_feature_profile}_blend_fallback", _conformal_scale(residuals)
    numeric = [
        column
        for column in _weather_numeric_features(config.weather_feature_profile)
        if column in train
    ]
    categorical = [column for column in WEATHER_CATEGORICAL if column in train]
    preprocessor = ColumnTransformer(
        [
            (
                "numeric",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                        ("scale", StandardScaler()),
                    ]
                ),
                numeric,
            ),
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("encode", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                    ]
                ),
                categorical,
            ),
        ]
    )
    model = Pipeline([("features", preprocessor), ("ridge", Ridge(alpha=config.ridge_alpha))])
    weights = _event_weights(train)
    model.fit(train[numeric + categorical], train["final_high_f"], ridge__sample_weight=weights)
    residuals = train["final_high_f"] - model.predict(train[numeric + categorical])
    return model, "regularized_weather_residual", _conformal_scale(residuals)


def _predict_weather(
    model: Pipeline | None, rows: pd.DataFrame, config: MarketEdgeConfig
) -> np.ndarray:
    baseline = _source_blend(rows, config.weather_feature_profile).to_numpy()
    if model is None:
        return baseline
    numeric = [
        column
        for column in _weather_numeric_features(config.weather_feature_profile)
        if column in rows
    ]
    categorical = [column for column in WEATHER_CATEGORICAL if column in rows]
    predicted = np.asarray(model.predict(rows[numeric + categorical]), dtype=float)
    observed = pd.to_numeric(rows.get("observed_high_so_far_f"), errors="coerce").to_numpy()
    return np.maximum(predicted, np.where(np.isfinite(observed), observed, -np.inf))


def _source_blend(rows: pd.DataFrame, profile_name: str = "legacy") -> pd.Series:
    if profile_name == "family_v2" and "family_baseline_high_f" in rows:
        result = pd.to_numeric(rows["family_baseline_high_f"], errors="coerce").fillna(0.0)
        observed = pd.to_numeric(rows.get("observed_high_so_far_f"), errors="coerce")
        return pd.Series(
            np.maximum(result.to_numpy(), observed.fillna(-np.inf).to_numpy()), index=rows.index
        )
    columns = [
        column
        for column in (
            "nws_anchor_high_f",
            "hrrr_projected_high_f",
            "nbm_projected_high_f",
            "ensemble_raw_median_high_f",
        )
        if column in rows
    ]
    values = rows[columns].apply(pd.to_numeric, errors="coerce")
    result = values.mean(axis=1).fillna(0.0)
    observed = pd.to_numeric(rows.get("observed_high_so_far_f"), errors="coerce")
    return pd.Series(
        np.maximum(result.to_numpy(), observed.fillna(-np.inf).to_numpy()),
        index=rows.index,
    )


def _weather_numeric_features(profile_name: str) -> list[str]:
    if profile_name == "legacy":
        return WEATHER_NUMERIC
    if profile_name == "family_v2":
        return FAMILY_WEATHER_NUMERIC
    raise ValueError(f"unknown MarketEdge weather feature profile: {profile_name}")


def _add_family_features(rows: pd.DataFrame) -> pd.DataFrame:
    output = rows.copy()
    nws = pd.to_numeric(output.get("nws_anchor_high_f"), errors="coerce")
    hrrr = pd.to_numeric(output.get("hrrr_projected_high_f"), errors="coerce")
    nbm = pd.to_numeric(output.get("nbm_projected_high_f"), errors="coerce")
    ensemble = pd.to_numeric(output.get("ensemble_raw_median_high_f"), errors="coerce")
    observed = pd.to_numeric(output.get("observed_high_so_far_f"), errors="coerce")
    anchor = nbm.combine_first(hrrr)
    output["family_numerical_anchor_high_f"] = anchor
    output["family_nws_minus_nbm_f"] = nws - nbm
    output["family_hrrr_minus_nbm_f"] = hrrr - nbm
    output["family_ensemble_minus_nbm_f"] = ensemble - nbm
    output["family_numerical_disagreement_f"] = (hrrr - nbm).abs()
    output["family_baseline_high_f"] = pd.concat([nws, anchor], axis=1).median(axis=1)
    output["family_baseline_high_f"] = output["family_baseline_high_f"].combine(observed, max)
    output["family_disagreement_range_f"] = pd.concat([nws, anchor], axis=1).max(
        axis=1
    ) - pd.concat([nws, anchor], axis=1).min(axis=1)
    output["family_disagreement_std_f"] = pd.concat([nws, anchor], axis=1).std(axis=1, ddof=0)
    return output


def _conformal_scale(residuals: pd.Series | np.ndarray) -> float:
    values = np.asarray(residuals, dtype=float)
    values = np.abs(values[np.isfinite(values)])
    return float(max(0.75, np.quantile(values, 0.68) if len(values) else 2.0))


def _prediction_scale(rows: pd.DataFrame, base_scale: float) -> np.ndarray:
    source_std = pd.to_numeric(rows.get("weather_source_stddev_f"), errors="coerce").fillna(0.0)
    remaining = pd.to_numeric(rows.get("hours_until_climate_end"), errors="coerce").fillna(12.0)
    return np.maximum(0.5, base_scale * (1.0 + 0.12 * source_std + 0.01 * remaining))


def _market_probability(rows: pd.DataFrame) -> np.ndarray:
    normalized = pd.to_numeric(rows.get("normalized_market_midpoint_probability"), errors="coerce")
    midpoint = (
        pd.to_numeric(rows.get("yes_bid_dollars"), errors="coerce")
        + pd.to_numeric(rows.get("yes_ask_dollars"), errors="coerce")
    ) / 2.0
    values = normalized.fillna(midpoint).fillna(0.0).to_numpy(dtype=float)
    return _normalize_by_snapshot(rows, values)


def _weather_probability(rows: pd.DataFrame) -> np.ndarray:
    lower = pd.to_numeric(rows.get("bracket_lower_f"), errors="coerce").to_numpy()
    upper = pd.to_numeric(rows.get("bracket_upper_f"), errors="coerce").to_numpy()
    mean_f = rows["weather_mean_f"].to_numpy(dtype=float)
    scale_f = rows["weather_scale_f"].to_numpy(dtype=float)
    low_cdf = np.where(np.isfinite(lower), _normal_cdf((lower - 0.5 - mean_f) / scale_f), 0.0)
    high_cdf = np.where(np.isfinite(upper), _normal_cdf((upper + 0.5 - mean_f) / scale_f), 1.0)
    values = np.maximum(0.0, high_cdf - low_cdf)
    observed = pd.to_numeric(rows.get("observed_high_so_far_f"), errors="coerce").to_numpy()
    impossible_yes = np.isfinite(observed) & np.isfinite(upper) & (observed >= upper)
    guaranteed_upper = np.isfinite(observed) & ~np.isfinite(upper) & (observed >= lower)
    values[impossible_yes] = 0.0
    values[guaranteed_upper] = 1.0
    return _normalize_by_snapshot(rows, values)


def _normal_cdf(values: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.vectorize(math.erf)(values / math.sqrt(2.0)))


def _normalize_by_snapshot(rows: pd.DataFrame, values: np.ndarray) -> np.ndarray:
    output = np.zeros(len(rows), dtype=float)
    keys = [rows["city"], rows["event_ticker"], rows["snapshot_time_utc"]]
    for _, indexes in pd.Series(range(len(rows))).groupby(keys).groups.items():
        indices = np.asarray(list(indexes), dtype=int)
        total = float(values[indices].sum())
        output[indices] = values[indices] / total if total > EPSILON else 1.0 / len(indices)
    return output


def _fit_blend_weight(prior: pd.DataFrame, config: MarketEdgeConfig) -> float:
    if prior.empty or prior["target_date"].nunique() < config.min_training_dates:
        return 0.0
    weights = _event_weights(prior)
    winner = (prior["market_ticker"] == prior["winner_ticker"]).astype(float).to_numpy()
    market = np.clip(prior["market_probability"].to_numpy(dtype=float), EPSILON, 1.0 - EPSILON)
    weather = np.clip(prior["weather_probability"].to_numpy(dtype=float), EPSILON, 1.0 - EPSILON)
    best_weight, best_loss = 0.0, float("inf")
    for candidate in np.linspace(0.0, 0.5, 11):
        raw = _sigmoid(_logit(market) + candidate * (_logit(weather) - _logit(market)))
        blended = _normalize_by_snapshot(prior, raw)
        loss = (
            -np.average(
                winner * np.log(np.clip(blended, EPSILON, 1.0))
                + (1.0 - winner) * np.log(np.clip(1.0 - blended, EPSILON, 1.0)),
                weights=weights,
            )
            + config.blend_penalty * candidate * candidate
        )
        if loss < best_loss:
            best_weight, best_loss = float(candidate), float(loss)
    return best_weight


def _blend_probability(rows: pd.DataFrame, weight: float) -> np.ndarray:
    market = np.clip(rows["market_probability"].to_numpy(dtype=float), EPSILON, 1.0 - EPSILON)
    weather = np.clip(rows["weather_probability"].to_numpy(dtype=float), EPSILON, 1.0 - EPSILON)
    return _sigmoid(_logit(market) + weight * (_logit(weather) - _logit(market)))


def _normalize_probabilities(rows: pd.DataFrame, column: str) -> pd.DataFrame:
    rows[column] = _normalize_by_snapshot(rows, rows[column].to_numpy(dtype=float))
    return rows


def _logit(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, EPSILON, 1.0 - EPSILON)
    return np.log(clipped / (1.0 - clipped))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -30.0, 30.0)))


def _distribution_rows(
    rows: pd.DataFrame, probability_column: str, model_name: str
) -> pd.DataFrame:
    output = rows.copy()
    output["model_name"] = model_name
    output["model_probability"] = output[probability_column]
    return output


def _weather_distributions(rows: pd.DataFrame) -> pd.DataFrame:
    output = rows.copy()
    output["model_name"] = "weather"
    output["model_probability"] = output["weather_probability"]
    return output


def _temperature_metrics(rows: pd.DataFrame) -> list[dict[str, Any]]:
    error = rows["weather_mean_f"] - rows["final_high_f"]
    weights = _event_weights(rows)
    return [
        {
            "metric": "mae",
            "value": float(np.average(np.abs(error), weights=weights)),
            "count": len(rows),
        },
        {
            "metric": "rmse",
            "value": float(np.sqrt(np.average(error * error, weights=weights))),
            "count": len(rows),
        },
        {"metric": "bias", "value": float(np.average(error, weights=weights)), "count": len(rows)},
        {
            "metric": "within_1f",
            "value": float(np.average(np.abs(error) <= 1.0, weights=weights)),
            "count": len(rows),
        },
        {
            "metric": "within_2f",
            "value": float(np.average(np.abs(error) <= 2.0, weights=weights)),
            "count": len(rows),
        },
    ]


def _probability_metrics(rows: pd.DataFrame) -> dict[str, float | int]:
    snapshot_rows = []
    for _, group in rows.groupby(["city", "event_ticker", "snapshot_time_utc"], sort=False):
        winner = group["winner_ticker"].iloc[0]
        probabilities = group["model_probability"].to_numpy(dtype=float)
        won = (group["market_ticker"] == winner).to_numpy()
        winner_probability = float(probabilities[won][0]) if won.any() else 0.0
        top = str(group.iloc[int(np.argmax(probabilities))]["market_ticker"])
        snapshot_rows.append(
            {
                "city": group["city"].iloc[0],
                "event_ticker": group["event_ticker"].iloc[0],
                "winner_probability": winner_probability,
                "log_loss": -math.log(max(EPSILON, winner_probability)),
                "brier": float(np.sum((probabilities - won.astype(float)) ** 2)),
                "top_one_accuracy": float(top == winner),
            }
        )
    frame = pd.DataFrame(snapshot_rows)
    weights = _event_weights(frame)
    return {
        "count": len(frame),
        "log_loss": float(np.average(frame["log_loss"], weights=weights)),
        "brier": float(np.average(frame["brier"], weights=weights)),
        "top_one_accuracy": float(np.average(frame["top_one_accuracy"], weights=weights)),
        "winner_probability": float(np.average(frame["winner_probability"], weights=weights)),
    }


def _replay(
    predictions: pd.DataFrame,
    market: pd.DataFrame,
    config: MarketEdgeConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    budget: dict[str, float] = defaultdict(lambda: config.daily_budget)
    confirmations: Counter[tuple[str, str, str]] = Counter()
    held_events: set[str] = set()
    for _, group in predictions.sort_values("snapshot_time_utc").groupby(
        ["city", "event_ticker", "snapshot_time_utc"], sort=True
    ):
        candidates = []
        for _, row in group.iterrows():
            for side, probability, ask, bid, size in _quotes(row):
                decision = _decision(row, side, probability, ask, bid, size, config)
                key = (str(row["event_ticker"]), str(row["market_ticker"]), side)
                confirmations[key] = confirmations[key] + 1 if decision["eligible"] else 0
                decision["confirmations"] = confirmations[key]
                decisions.append(decision)
                if decision["eligible"] and confirmations[key] >= config.required_confirmations:
                    candidates.append(decision)
        if not candidates:
            continue
        candidate = max(candidates, key=lambda item: item["expected_value"])
        event = candidate["event_ticker"]
        if event in held_events:
            candidate["decision"] = "blocked_existing_event_position"
            continue
        remaining = budget[candidate["target_date"]]
        contracts = min(
            config.max_contracts_per_order,
            int(config.max_order_cost / candidate["ask"]),
            int(remaining / candidate["ask"]),
            int(candidate["quote_size"]),
        )
        if contracts < 1:
            candidate["decision"] = "blocked_budget_or_depth"
            continue
        entry_fee = kalshi_fee(candidate["ask"], contracts, config.fee_mode)
        won = candidate["market_ticker"] == candidate["winner_ticker"]
        payout = float(contracts if (won if candidate["side"] == "yes" else not won) else 0.0)
        pnl = payout - candidate["ask"] * contracts - entry_fee
        budget[candidate["target_date"]] -= candidate["ask"] * contracts + entry_fee
        held_events.add(event)
        trades.append(
            {
                **candidate,
                "contracts": contracts,
                "entry_fee": entry_fee,
                "payout": payout,
                "pnl": pnl,
                "roi": pnl / max(EPSILON, candidate["ask"] * contracts + entry_fee),
                "hit": float(payout > 0),
                "decision": "entered_hold_to_settlement",
                "edge_bucket": _edge_bucket(candidate["expected_value"]),
            }
        )
    return trades, decisions


def _quotes(row: pd.Series) -> list[tuple[str, float, float, float, float]]:
    yes_bid = _number(row.get("yes_bid_dollars"))
    yes_ask = _number(row.get("yes_ask_dollars"))
    no_bid = _number(row.get("no_bid_dollars"))
    no_ask = _number(row.get("no_ask_dollars"))
    yes_size = _number(row.get("yes_ask_size")) or 0.0
    no_size = _number(row.get("no_ask_size")) or _number(row.get("yes_bid_size")) or 0.0
    if no_ask is None and yes_bid is not None:
        no_ask = 1.0 - yes_bid
    if no_bid is None and yes_ask is not None:
        no_bid = 1.0 - yes_ask
    output = []
    if yes_bid is not None and yes_ask is not None:
        output.append(("yes", float(row["marketedge_probability"]), yes_ask, yes_bid, yes_size))
    if no_bid is not None and no_ask is not None:
        output.append(("no", 1.0 - float(row["marketedge_probability"]), no_ask, no_bid, no_size))
    return output


def _decision(
    row: pd.Series,
    side: str,
    probability: float,
    ask: float,
    bid: float,
    quote_size: float,
    config: MarketEdgeConfig,
) -> dict[str, Any]:
    fee = kalshi_fee(ask, 1, config.fee_mode)
    expected_value = probability - ask - fee
    spread = ask - bid
    reasons = []
    if ask <= 0.0 or ask >= 1.0:
        reasons.append("invalid_ask")
    if quote_size < config.min_quote_size:
        reasons.append("insufficient_quote_size")
    if spread > config.max_spread:
        reasons.append("spread_too_wide")
    if expected_value < config.minimum_ev:
        reasons.append("insufficient_fee_adjusted_ev")
    return {
        "target_date": str(row["target_date"]),
        "snapshot_time_utc": str(row["snapshot_time_utc"]),
        "city": str(row["city"]),
        "event_ticker": str(row["event_ticker"]),
        "market_ticker": str(row["market_ticker"]),
        "winner_ticker": str(row["winner_ticker"]),
        "checkpoint": str(row.get("checkpoint_label") or "unknown"),
        "side": side,
        "probability": probability,
        "ask": ask,
        "bid": bid,
        "quote_size": quote_size,
        "spread": spread,
        "fee_per_contract": fee,
        "expected_value": expected_value,
        "eligible": not reasons,
        "reason": ";".join(reasons) if reasons else "eligible",
    }


def kalshi_fee(price: float, contracts: int, mode: str) -> float:
    if mode == "none":
        return 0.0
    rate = {"taker": 0.07, "maker": 0.0175}.get(mode)
    if rate is None:
        raise ValueError(f"unknown fee mode {mode!r}")
    return math.ceil((rate * contracts * price * (1.0 - price) - 1e-12) * 100.0) / 100.0


def _strategy_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0, "total_pnl": 0.0, "roi": 0.0, "hit_rate": 0.0, "max_drawdown": 0.0}
    risk = sum(trade["ask"] * trade["contracts"] + trade["entry_fee"] for trade in trades)
    pnls = [trade["pnl"] for trade in sorted(trades, key=lambda item: item["snapshot_time_utc"])]
    equity = peak = drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return {
        "trades": len(trades),
        "total_pnl": sum(pnls),
        "total_fees": sum(trade["entry_fee"] for trade in trades),
        "roi": sum(pnls) / max(EPSILON, risk),
        "hit_rate": mean(trade["hit"] for trade in trades),
        "max_drawdown": drawdown,
        "mean_expected_value": mean(trade["expected_value"] for trade in trades),
    }


def _daily_pnl(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[trade["target_date"]].append(trade)
    cumulative = 0.0
    output = []
    for target_date, values in sorted(grouped.items()):
        pnl = sum(value["pnl"] for value in values)
        cumulative += pnl
        output.append(
            {
                "target_date": target_date,
                "trades": len(values),
                "pnl": pnl,
                "cumulative_pnl": cumulative,
            }
        )
    return output


def _trade_groups(trades: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade[key])].append(trade)
    return [
        {
            "group": group,
            "trades": len(values),
            "pnl": sum(value["pnl"] for value in values),
            "hit_rate": mean(value["hit"] for value in values),
            "mean_ev": mean(value["expected_value"] for value in values),
        }
        for group, values in sorted(grouped.items())
    ]


def _event_weights(rows: pd.DataFrame) -> np.ndarray:
    counts = rows.groupby(["city", "event_ticker"])["event_ticker"].transform("count")
    return (1.0 / counts).to_numpy(dtype=float)


def _flatten_probability_metrics(
    metrics: dict[str, dict[str, float | int]],
) -> list[dict[str, Any]]:
    return [{"model": model, **values} for model, values in metrics.items()]


def _edge_bucket(value: float) -> str:
    if value < 0.05:
        return "0.03-0.05"
    if value < 0.08:
        return "0.05-0.08"
    return "0.08+"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    weather = {row["metric"]: row["value"] for row in summary["temperature_metrics"]}
    probability = summary["probability_metrics"]
    strategy = summary["strategy"]
    path.write_text(
        "# MarketEdge v2 Walk-Forward Backtest\n\n"
        f"- Independent city-events: {summary['independent_city_events']}\n"
        f"- Target dates: {summary['target_dates']}\n"
        f"- Temperature MAE: {weather['mae']:.4f}\n"
        f"- Temperature RMSE: {weather['rmse']:.4f}\n"
        f"- Market log loss: {probability['market']['log_loss']:.4f}\n"
        f"- Weather log loss: {probability['weather']['log_loss']:.4f}\n"
        f"- MarketEdge log loss: {probability['marketedge']['log_loss']:.4f}\n"
        f"- Trades: {strategy['trades']}\n"
        f"- Fee-adjusted PnL: {strategy['total_pnl']:.4f}\n"
        f"- ROI: {strategy['roi']:.4f}\n"
        f"- Max drawdown: {strategy['max_drawdown']:.4f}\n\n"
        "This is a research-only, hold-to-settlement replay. It does not authorize deployment.\n",
        encoding="utf-8",
    )


def _plot_metrics(
    output: Path,
    weather: pd.DataFrame,
    predictions: pd.DataFrame,
    trades: list[dict[str, Any]],
) -> None:
    by_date = weather.groupby("target_date", as_index=False).apply(
        lambda group: pd.Series(
            {
                "mae": np.average(
                    np.abs(group["weather_mean_f"] - group["final_high_f"]),
                    weights=_event_weights(group),
                ),
                "rmse": np.sqrt(
                    np.average(
                        (group["weather_mean_f"] - group["final_high_f"]) ** 2,
                        weights=_event_weights(group),
                    )
                ),
            }
        ),
        include_groups=False,
    )
    probability_rows = []
    for target_date, group in predictions.groupby("target_date"):
        metrics = _probability_metrics(
            group.assign(model_probability=group["marketedge_probability"])
        )
        probability_rows.append({"target_date": target_date, **metrics})
    probability = pd.DataFrame(probability_rows)
    figure, axes = plt.subplots(2, 2, figsize=(12, 8))
    axes[0, 0].plot(by_date["target_date"], by_date["mae"], marker="o")
    axes[0, 0].set_title("Walk-forward MAE")
    axes[0, 1].plot(by_date["target_date"], by_date["rmse"], marker="o")
    axes[0, 1].set_title("Walk-forward RMSE")
    axes[1, 0].plot(probability["target_date"], probability["log_loss"], marker="o")
    axes[1, 0].set_title("MarketEdge log loss")
    daily = pd.DataFrame(_daily_pnl(trades))
    if not daily.empty:
        axes[1, 1].plot(daily["target_date"], daily["cumulative_pnl"], marker="o")
    axes[1, 1].axhline(0.0, color="black", linewidth=0.8)
    axes[1, 1].set_title("Fee-adjusted cumulative PnL")
    for axis in axes.flat:
        axis.tick_params(axis="x", rotation=35)
        axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(output / "walk_forward_metrics.png", dpi=160)
    plt.close(figure)
