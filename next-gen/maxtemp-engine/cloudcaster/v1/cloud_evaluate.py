"""Leakage-safe Cloudcaster evaluation."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from baselines import SourceBaseline
from cloud_features import build_cloudcaster_rows, row_snapshot_key
from cloud_temperature import fit_temperature
from cloud_train import (
    CHAIN_MODEL_NAME,
    DEFAULT_MIN_TRAINING_ROWS,
    predict_distributions,
    train_cloudcaster_model,
)
from dataset import brackets_for_snapshot, markets_by_snapshot
from distribution import bracket_distribution, monotonic_quantiles
from evaluate import (
    _bracket_score_rows,
    _calibration_rows,
    _group_metric_rows,
    _metric_rows,
    _temperature_score_rows,
    _write_charts,
    _write_dict_rows,
)
from features import FeatureRow, build_feature_rows
from train import (
    DEFAULT_MIN_TRAINING_EVENTS,
    predict_expected_high,
    predict_quantiles,
    train_raycaster_model,
)

from libs.models import BacktestDataset, BracketDistribution, TemperaturePrediction


def evaluate_expanding_window(
    dataset: BacktestDataset,
    output_dir: str | Path,
    min_raycaster_training_events: int = DEFAULT_MIN_TRAINING_EVENTS,
    min_cloudcaster_training_rows: int = DEFAULT_MIN_TRAINING_ROWS,
    probability_floor: float = 0.001,
    source_export_id: str | None = None,
    raycaster_feature_profile: str = "city_residual",
    cloudcaster_feature_profile: str = "legacy",
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows = build_feature_rows(dataset)
    grouped_markets = markets_by_snapshot(dataset)
    predictions: list[TemperaturePrediction] = []
    distributions: list[BracketDistribution] = []
    diagnostics: list[dict[str, Any]] = []
    for target_date in sorted({row.target_date for row in rows}):
        train_rows = [row for row in rows if row.target_date < target_date]
        test_rows = [row for row in rows if row.target_date == target_date]
        raycaster = train_raycaster_model(
            train_rows,
            min_training_events=min_raycaster_training_events,
            feature_profile_name=raycaster_feature_profile,
        )
        source = SourceBaseline(name="source_blend")
        source.fit(train_rows)
        train_expected = predict_expected_high(raycaster, train_rows)
        train_quantiles = predict_quantiles(raycaster, train_rows)
        train_source_probabilities = _source_probabilities(
            train_rows,
            grouped_markets,
            source,
            probability_floor,
        )
        train_cloud_rows = build_cloudcaster_rows(
            train_rows,
            grouped_markets,
            train_expected,
            train_quantiles,
            train_source_probabilities,
        )
        cloudcaster = train_cloudcaster_model(
            train_cloud_rows,
            min_training_rows=min_cloudcaster_training_rows,
            feature_profile_name=cloudcaster_feature_profile,
        )
        temperature_result = _fit_fold_temperature(
            train_rows,
            grouped_markets,
            min_raycaster_training_events,
            min_cloudcaster_training_rows,
            probability_floor,
            raycaster_feature_profile,
        )
        test_expected = predict_expected_high(raycaster, test_rows)
        test_quantiles = predict_quantiles(raycaster, test_rows)
        test_source_probabilities = _source_probabilities(
            test_rows,
            grouped_markets,
            source,
            probability_floor,
        )
        test_cloud_rows = build_cloudcaster_rows(
            test_rows,
            grouped_markets,
            test_expected,
            test_quantiles,
            test_source_probabilities,
        )
        predictions.extend(_temperature_predictions(test_rows, test_expected, test_quantiles))
        distributions.extend(
            predict_distributions(
                cloudcaster,
                test_cloud_rows,
                test_source_probabilities,
                probability_floor=probability_floor,
                temperature=temperature_result["temperature"],
            )
        )
        diagnostics.extend(
            {
                "target_date": row.target_date.isoformat(),
                "city": row.city,
                "event_ticker": row.event_ticker,
                "snapshot_hour_utc": row.snapshot_hour_utc.isoformat(),
                "raycaster_mode": raycaster.mode,
                "raycaster_training_rows": raycaster.training_rows,
                "raycaster_feature_profile": raycaster.feature_profile_name,
                "cloudcaster_mode": cloudcaster.mode,
                "cloudcaster_training_rows": cloudcaster.training_rows,
                "cloudcaster_feature_profile": cloudcaster.feature_profile_name,
                "cloudcaster_temperature": temperature_result["temperature"],
                "temperature_calibration_rows": temperature_result["calibration_rows"],
                "temperature_calibration_log_loss": temperature_result["calibration_log_loss"],
                "temperature_uncalibrated_log_loss": temperature_result["uncalibrated_log_loss"],
            }
            for row in test_rows
        )
    return write_outputs(
        dataset,
        output,
        predictions,
        distributions,
        diagnostics,
        source_export_id=source_export_id,
    )


def write_outputs(
    dataset: BacktestDataset,
    output: Path,
    predictions: list[TemperaturePrediction],
    distributions: list[BracketDistribution],
    diagnostics: list[dict[str, Any]],
    source_export_id: str | None,
) -> dict[str, Any]:
    temp_rows = _temperature_score_rows(dataset, predictions)
    bracket_rows = _bracket_score_rows(dataset, distributions)
    market_distributions = _market_midpoint_distributions(dataset)
    market_bracket_rows = _bracket_score_rows(dataset, market_distributions)
    relative_rows = _market_relative_rows(bracket_rows, market_bracket_rows)
    calibration = _calibration_rows(bracket_rows)
    _write_dict_rows(output / "predictions.csv", [_prediction_row(item) for item in predictions])
    _write_dict_rows(
        output / "bracket_distributions.csv",
        [_distribution_row(item) for item in distributions],
    )
    _write_dict_rows(
        output / "market_midpoint_distributions.csv",
        [_distribution_row(item) for item in market_distributions],
    )
    _write_dict_rows(output / "temperature_metrics.csv", _metric_rows(temp_rows, "temperature"))
    _write_dict_rows(output / "bracket_metrics.csv", _metric_rows(bracket_rows, "bracket"))
    _write_dict_rows(
        output / "market_bracket_metrics.csv",
        _metric_rows(market_bracket_rows, "bracket"),
    )
    _write_dict_rows(output / "market_relative_metrics.csv", relative_rows)
    _write_dict_rows(
        output / "market_relative_by_city.csv",
        _market_relative_group_rows(relative_rows, "city"),
    )
    _write_dict_rows(
        output / "market_relative_by_checkpoint.csv",
        _market_relative_group_rows(relative_rows, "checkpoint"),
    )
    _write_dict_rows(
        output / "market_relative_by_bracket_type.csv",
        _market_relative_group_rows(relative_rows, "winner_bracket_type"),
    )
    _write_dict_rows(
        output / "market_relative_by_slice.csv",
        _market_relative_group_rows(relative_rows, "slice_key"),
    )
    _write_dict_rows(
        output / "daily_metrics.csv",
        _group_metric_rows(temp_rows, bracket_rows, "target_date"),
    )
    _write_dict_rows(
        output / "city_day_metrics.csv",
        _group_metric_rows(temp_rows, bracket_rows, "city_day"),
    )
    _write_dict_rows(output / "calibration_bins.csv", calibration)
    _write_dict_rows(output / "calibration_summary.csv", _calibration_summary_rows(calibration))
    _write_dict_rows(output / "training_diagnostics.csv", diagnostics)
    _write_dict_rows(output / "errors.csv", temp_rows + bracket_rows + market_bracket_rows)
    _write_summary(
        output,
        source_export_id,
        temp_rows,
        bracket_rows,
        market_bracket_rows,
        relative_rows,
        diagnostics,
    )
    _write_charts(output, temp_rows, bracket_rows)
    return {
        "mode": "cloudcaster_expanding_window",
        "temperature_rows": len(temp_rows),
        "bracket_rows": len(bracket_rows),
        "output_dir": str(output),
    }


def _fit_fold_temperature(
    train_rows: list[FeatureRow],
    grouped_markets,
    min_raycaster_training_events: int,
    min_cloudcaster_training_rows: int,
    probability_floor: float,
    raycaster_feature_profile: str,
) -> dict[str, float]:
    target_dates = sorted({row.target_date for row in train_rows})
    if len(target_dates) < 4:
        return _default_temperature_result()
    calibration_day_count = min(3, max(1, len(target_dates) // 4))
    calibration_dates = set(target_dates[-calibration_day_count:])
    model_rows = [row for row in train_rows if row.target_date not in calibration_dates]
    calibration_rows = [row for row in train_rows if row.target_date in calibration_dates]
    if not model_rows or not calibration_rows:
        return _default_temperature_result()

    raycaster = train_raycaster_model(
        model_rows,
        min_training_events=min_raycaster_training_events,
        feature_profile_name=raycaster_feature_profile,
    )
    source = SourceBaseline(name="source_blend")
    source.fit(model_rows)
    model_expected = predict_expected_high(raycaster, model_rows)
    model_quantiles = predict_quantiles(raycaster, model_rows)
    model_source_probabilities = _source_probabilities(
        model_rows,
        grouped_markets,
        source,
        probability_floor,
    )
    model_cloud_rows = build_cloudcaster_rows(
        model_rows,
        grouped_markets,
        model_expected,
        model_quantiles,
        model_source_probabilities,
    )
    calibration_model = train_cloudcaster_model(
        model_cloud_rows,
        min_training_rows=min_cloudcaster_training_rows,
    )
    calibration_expected = predict_expected_high(raycaster, calibration_rows)
    calibration_quantiles = predict_quantiles(raycaster, calibration_rows)
    calibration_source_probabilities = _source_probabilities(
        calibration_rows,
        grouped_markets,
        source,
        probability_floor,
    )
    calibration_cloud_rows = build_cloudcaster_rows(
        calibration_rows,
        grouped_markets,
        calibration_expected,
        calibration_quantiles,
        calibration_source_probabilities,
    )
    if not calibration_cloud_rows:
        return _default_temperature_result()
    return fit_temperature(
        calibration_model,
        calibration_cloud_rows,
        calibration_source_probabilities,
        probability_floor=probability_floor,
    )


def _default_temperature_result() -> dict[str, float]:
    return {
        "temperature": 1.0,
        "calibration_log_loss": 0.0,
        "uncalibrated_log_loss": 0.0,
        "calibration_rows": 0.0,
    }


def _source_probabilities(
    rows: list[FeatureRow],
    grouped_markets,
    source: SourceBaseline,
    probability_floor: float,
) -> dict[tuple[str, str, object], dict[str, float]]:
    expected_values = source.predict_expected_high(rows)
    quantile_values = source.predict_quantiles(rows)
    output: dict[tuple[str, str, object], dict[str, float]] = {}
    for row, expected, quantiles in zip(rows, expected_values, quantile_values, strict=True):
        brackets = brackets_for_snapshot(
            grouped_markets,
            row.city,
            row.event_ticker,
            row.snapshot_hour_utc,
        )
        if not brackets:
            continue
        output[row_snapshot_key(row)] = bracket_distribution(
            brackets,
            expected_high_f=expected,
            quantiles=monotonic_quantiles(quantiles),
            observed_high_so_far_f=_observed(row),
            probability_floor=probability_floor,
        )
    return output


def _calibration_summary_rows(calibration_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [row for row in calibration_rows if int(row.get("count") or 0) > 0]
    total = sum(int(row["count"]) for row in rows)
    if total == 0:
        return []
    diffs = [
        (
            int(row["count"]),
            float(row["mean_top_probability"]) - float(row["empirical_win_rate"]),
        )
        for row in rows
    ]
    return [
        {
            "model_name": CHAIN_MODEL_NAME,
            "count": total,
            "calibration_mae": sum(count * abs(diff) for count, diff in diffs) / total,
            "calibration_bias": sum(count * diff for count, diff in diffs) / total,
            "mean_top_probability": _weighted_average(rows, "mean_top_probability"),
            "empirical_win_rate": _weighted_average(rows, "empirical_win_rate"),
            "log_loss": _weighted_average(rows, "log_loss"),
            "brier": _weighted_average(rows, "brier"),
            "top_one_accuracy": _weighted_average(rows, "top_one_accuracy"),
        }
    ]


def _market_midpoint_distributions(dataset: BacktestDataset) -> list[BracketDistribution]:
    grouped: dict[tuple[str, str, object], list[Any]] = defaultdict(list)
    for market in dataset.markets:
        grouped[(market.city, market.event_ticker, market.snapshot_hour_utc)].append(market)
    distributions: list[BracketDistribution] = []
    for (city, event_ticker, snapshot), markets in sorted(grouped.items()):
        raw = {}
        for market in markets:
            probability = market.normalized_market_midpoint_probability
            if probability is None and market.yes_bid is not None and market.yes_ask is not None:
                probability = (float(market.yes_bid) + float(market.yes_ask)) / 2.0
            if probability is not None:
                raw[market.market_ticker] = max(0.0, float(probability))
        total = sum(raw.values())
        if total <= 0:
            continue
        distributions.append(
            BracketDistribution(
                city=city,
                event_ticker=event_ticker,
                snapshot_hour_utc=snapshot,
                model_name="market_midpoint",
                probabilities={ticker: value / total for ticker, value in raw.items()},
            )
        )
    return distributions


def _market_relative_rows(
    model_rows: list[dict[str, Any]],
    market_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    market_by_key = {_score_key(row): row for row in market_rows}
    output = []
    for row in model_rows:
        market = market_by_key.get(_score_key(row))
        if market is None:
            continue
        output.append(
            {
                "city": row["city"],
                "checkpoint": row["checkpoint"],
                "winner_bracket_type": row.get("winner_bracket_type", "unknown"),
                "top_bracket_type": row.get("top_bracket_type", "unknown"),
                "target_date": row["target_date"],
                "event_ticker": row["event_ticker"],
                "snapshot_hour_utc": row["snapshot_hour_utc"],
                "winner_ticker": row["winner_ticker"],
                "slice_key": _relative_slice_key(row),
                "model_log_loss": row["log_loss"],
                "market_log_loss": market["log_loss"],
                "log_loss_delta": float(row["log_loss"]) - float(market["log_loss"]),
                "model_brier": row["brier"],
                "market_brier": market["brier"],
                "brier_delta": float(row["brier"]) - float(market["brier"]),
                "model_winner_probability": row["winner_probability"],
                "market_winner_probability": market["winner_probability"],
                "winner_probability_delta": float(row["winner_probability"])
                - float(market["winner_probability"]),
                "model_top_one_accuracy": row["top_one_accuracy"],
                "market_top_one_accuracy": market["top_one_accuracy"],
                "top_one_accuracy_delta": float(row["top_one_accuracy"])
                - float(market["top_one_accuracy"]),
            }
        )
    return output


def _score_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row["city"]),
        str(row["event_ticker"]),
        str(row["snapshot_hour_utc"]),
    )


def _relative_slice_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            str(row["city"]),
            str(row["checkpoint"]),
            str(row.get("winner_bracket_type", "unknown")),
        ]
    )


def _market_relative_group_rows(
    rows: list[dict[str, Any]],
    group_column: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[group_column])].append(row)
    output = []
    for group, group_rows in sorted(grouped.items()):
        log_loss_delta = _mean(group_rows, "log_loss_delta")
        output.append(
            {
                "group": group,
                "count": len(group_rows),
                "model_log_loss": _mean(group_rows, "model_log_loss"),
                "market_log_loss": _mean(group_rows, "market_log_loss"),
                "log_loss_delta": log_loss_delta,
                "model_brier": _mean(group_rows, "model_brier"),
                "market_brier": _mean(group_rows, "market_brier"),
                "brier_delta": _mean(group_rows, "brier_delta"),
                "model_winner_probability": _mean(group_rows, "model_winner_probability"),
                "market_winner_probability": _mean(group_rows, "market_winner_probability"),
                "winner_probability_delta": _mean(group_rows, "winner_probability_delta"),
                "model_top_one_accuracy": _mean(group_rows, "model_top_one_accuracy"),
                "market_top_one_accuracy": _mean(group_rows, "market_top_one_accuracy"),
                "top_one_accuracy_delta": _mean(group_rows, "top_one_accuracy_delta"),
                "cloudcaster_beats_market_log_loss": log_loss_delta < 0,
            }
        )
    return output


def _market_relative_overall(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    return _market_relative_group_rows([{**row, "overall": "overall"} for row in rows], "overall")


def _mean(rows: list[dict[str, Any]], column: str) -> float:
    return sum(float(row[column]) for row in rows) / len(rows)


def _weighted_average(rows: list[dict[str, Any]], column: str) -> float:
    total = sum(int(row["count"]) for row in rows)
    return sum(int(row["count"]) * float(row[column]) for row in rows) / total


def _temperature_predictions(
    rows: list[FeatureRow],
    expected_values: list[float],
    quantile_values: list[dict[float, float]],
) -> list[TemperaturePrediction]:
    return [
        TemperaturePrediction(
            city=row.city,
            event_ticker=row.event_ticker,
            snapshot_hour_utc=row.snapshot_hour_utc,
            model_name=CHAIN_MODEL_NAME,
            expected_high_f=expected,
            quantiles=monotonic_quantiles(quantiles),
        )
        for row, expected, quantiles in zip(rows, expected_values, quantile_values, strict=True)
    ]


def _write_summary(
    output: Path,
    source_export_id: str | None,
    temp_rows: list[dict[str, Any]],
    bracket_rows: list[dict[str, Any]],
    market_bracket_rows: list[dict[str, Any]],
    relative_rows: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
) -> None:
    summary = {
        "model_name": CHAIN_MODEL_NAME,
        "mode": "cloudcaster_expanding_window",
        "source_export_id": source_export_id,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "temperature_prediction_count": len(temp_rows),
        "bracket_prediction_count": len(bracket_rows),
        "temperature_metrics": _metric_rows(temp_rows, "temperature"),
        "bracket_metrics": _metric_rows(bracket_rows, "bracket"),
        "market_bracket_metrics": _metric_rows(market_bracket_rows, "bracket"),
        "market_relative_metrics": _market_relative_overall(relative_rows),
        "latest_diagnostics": diagnostics[-6:],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _prediction_row(prediction: TemperaturePrediction) -> dict[str, Any]:
    row: dict[str, Any] = {
        "city": prediction.city,
        "event_ticker": prediction.event_ticker,
        "snapshot_hour_utc": prediction.snapshot_hour_utc.isoformat(),
        "model_name": prediction.model_name,
        "expected_high_f": prediction.expected_high_f,
    }
    for level, value in prediction.quantiles.items():
        row[f"q{int(float(level) * 100):02d}"] = value
    return row


def _distribution_row(distribution: BracketDistribution) -> dict[str, Any]:
    return {
        "city": distribution.city,
        "event_ticker": distribution.event_ticker,
        "snapshot_hour_utc": distribution.snapshot_hour_utc.isoformat(),
        "model_name": distribution.model_name,
        "probabilities": distribution.probabilities,
    }


def _observed(row: FeatureRow) -> float | None:
    value = row.features.get("observed_high_so_far_f")
    return float(value) if value is not None else None
