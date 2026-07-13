"""Benchmark Raycaster candidates and weather-source baselines."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from baselines import DEFAULT_ESTIMATORS, TemperatureEstimator, create_estimator
from dataset import brackets_for_snapshot, markets_by_snapshot
from distribution import bracket_distribution, monotonic_quantiles
from evaluate import (
    _bracket_score_rows,
    _calibration_rows,
    _group_metric_rows,
    _metric_rows,
    _temperature_score_rows,
    _write_dict_rows,
)
from features import FeatureRow, build_feature_rows

from libs.models import BacktestDataset, BracketDistribution, TemperaturePrediction


def benchmark_expanding_window(
    dataset: BacktestDataset,
    output_dir: str | Path,
    estimator_names: list[str] | None = None,
    min_training_events: int = 60,
    probability_floor: float = 0.001,
    source_export_id: str | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    names = estimator_names or list(DEFAULT_ESTIMATORS)
    rows = build_feature_rows(dataset)
    grouped_markets = markets_by_snapshot(dataset)
    all_temp_rows: list[dict[str, Any]] = []
    all_bracket_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for name in names:
        predictions, distributions, model_diagnostics = _evaluate_estimator(
            name,
            rows,
            grouped_markets,
            min_training_events=min_training_events,
            probability_floor=probability_floor,
        )
        temp_rows = _temperature_score_rows(dataset, predictions)
        bracket_rows = _bracket_score_rows(dataset, distributions)
        all_temp_rows.extend(temp_rows)
        all_bracket_rows.extend(bracket_rows)
        diagnostics.extend(model_diagnostics)

    comparison = _model_comparison_rows(names, all_temp_rows, all_bracket_rows)
    weighted_comparison = _weighted_model_comparison_rows(names, all_temp_rows, all_bracket_rows)
    confidence_intervals = _bootstrap_confidence_rows(names, all_temp_rows, all_bracket_rows)
    daily = _grouped_by_model(names, all_temp_rows, all_bracket_rows, "target_date")
    city_day = _grouped_by_model(names, all_temp_rows, all_bracket_rows, "city_day")
    calibration = _calibration_by_model(names, all_bracket_rows)
    calibration_summary = _calibration_summary_rows(names, calibration)
    _write_dict_rows(output / "model_comparison.csv", comparison)
    _write_dict_rows(output / "weighted_model_comparison.csv", weighted_comparison)
    _write_dict_rows(output / "bootstrap_confidence_intervals.csv", confidence_intervals)
    _write_dict_rows(output / "daily_metrics.csv", daily)
    _write_dict_rows(output / "city_day_metrics.csv", city_day)
    _write_dict_rows(output / "calibration_bins.csv", calibration)
    _write_dict_rows(output / "calibration_summary.csv", calibration_summary)
    _write_dict_rows(output / "training_diagnostics.csv", diagnostics)
    _write_dict_rows(output / "errors.csv", all_temp_rows + all_bracket_rows)
    _write_model_decision(output, weighted_comparison, calibration_summary)
    _write_summary(output, source_export_id, names, comparison, weighted_comparison)
    _write_benchmark_charts(output, names, comparison, daily, calibration)
    return {
        "mode": "benchmark_expanding_window",
        "models": len(names),
        "temperature_rows": len(all_temp_rows),
        "bracket_rows": len(all_bracket_rows),
        "headline_weighting": "city_day_weighted",
        "output_dir": str(output),
    }


def _evaluate_estimator(
    name: str,
    rows: list[FeatureRow],
    grouped_markets,
    min_training_events: int,
    probability_floor: float,
) -> tuple[list[TemperaturePrediction], list[BracketDistribution], list[dict[str, Any]]]:
    predictions: list[TemperaturePrediction] = []
    distributions: list[BracketDistribution] = []
    diagnostics: list[dict[str, Any]] = []
    for target_date in sorted({row.target_date for row in rows}):
        train_rows = [row for row in rows if row.target_date < target_date]
        test_rows = [row for row in rows if row.target_date == target_date]
        estimator = create_estimator(name, min_training_events=min_training_events)
        estimator.fit(train_rows)
        batch_predictions, batch_distributions = _predict_rows(
            test_rows,
            estimator,
            grouped_markets,
            probability_floor,
        )
        predictions.extend(batch_predictions)
        distributions.extend(batch_distributions)
        diagnostics.extend(
            {
                "model_name": name,
                "target_date": row.target_date.isoformat(),
                "city": row.city,
                "event_ticker": row.event_ticker,
                "snapshot_hour_utc": row.snapshot_hour_utc.isoformat(),
                "mode": estimator.mode,
                "training_rows": estimator.training_rows,
            }
            for row in test_rows
        )
    return predictions, distributions, diagnostics


def _predict_rows(
    rows: list[FeatureRow],
    estimator: TemperatureEstimator,
    grouped_markets,
    probability_floor: float,
) -> tuple[list[TemperaturePrediction], list[BracketDistribution]]:
    expected_values = estimator.predict_expected_high(rows)
    quantile_values = estimator.predict_quantiles(rows)
    distribution_expected_values = _distribution_expected_high(estimator, rows, expected_values)
    distribution_quantile_values = _distribution_quantiles(estimator, rows, quantile_values)
    predictions: list[TemperaturePrediction] = []
    distributions: list[BracketDistribution] = []
    for row, expected, quantiles, distribution_expected, distribution_quantiles in zip(
        rows,
        expected_values,
        quantile_values,
        distribution_expected_values,
        distribution_quantile_values,
        strict=True,
    ):
        cleaned_quantiles = monotonic_quantiles(quantiles)
        cleaned_distribution_quantiles = monotonic_quantiles(distribution_quantiles)
        predictions.append(
            TemperaturePrediction(
                city=row.city,
                event_ticker=row.event_ticker,
                snapshot_hour_utc=row.snapshot_hour_utc,
                model_name=estimator.name,
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
                brackets,
                expected_high_f=distribution_expected,
                quantiles=cleaned_distribution_quantiles,
                observed_high_so_far_f=_observed(row),
                probability_floor=probability_floor,
            )
            distributions.append(
                BracketDistribution(
                    city=row.city,
                    event_ticker=row.event_ticker,
                    snapshot_hour_utc=row.snapshot_hour_utc,
                    model_name=estimator.name,
                    probabilities=probabilities,
                )
            )
    return predictions, distributions


def _distribution_expected_high(
    estimator: TemperatureEstimator,
    rows: list[FeatureRow],
    fallback: list[float],
) -> list[float]:
    method = getattr(estimator, "predict_distribution_expected_high", None)
    if method is None:
        return fallback
    return method(rows)


def _distribution_quantiles(
    estimator: TemperatureEstimator,
    rows: list[FeatureRow],
    fallback: list[dict[float, float]],
) -> list[dict[float, float]]:
    method = getattr(estimator, "predict_distribution_quantiles", None)
    if method is None:
        return fallback
    return method(rows)


def _model_comparison_rows(
    names: list[str],
    temp_rows: list[dict[str, Any]],
    bracket_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    for name in names:
        model_temp = [row for row in temp_rows if row["model_name"] == name]
        model_bracket = [row for row in bracket_rows if row["model_name"] == name]
        row: dict[str, Any] = {
            "model_name": name,
            "temperature_rows": len(model_temp),
            "bracket_rows": len(model_bracket),
        }
        for metric in _metric_rows(model_temp, "temperature"):
            row[metric["metric"]] = metric["value"]
        for metric in _metric_rows(model_bracket, "bracket"):
            row[metric["metric"]] = metric["value"]
        output.append(row)
    return sorted(output, key=lambda row: (float(row.get("mae") or 999), row["model_name"]))


def _weighted_model_comparison_rows(
    names: list[str],
    temp_rows: list[dict[str, Any]],
    bracket_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for weighting, group_column in (
        ("row_weighted", None),
        ("city_day_weighted", "city_day"),
        ("target_day_weighted", "target_date"),
    ):
        for name in names:
            model_temp = [row for row in temp_rows if row["model_name"] == name]
            model_bracket = [row for row in bracket_rows if row["model_name"] == name]
            temp_metrics, temp_groups = _weighted_metric_values(
                model_temp,
                "temperature",
                group_column,
            )
            bracket_metrics, bracket_groups = _weighted_metric_values(
                model_bracket,
                "bracket",
                group_column,
            )
            output.append(
                {
                    "model_name": name,
                    "weighting": weighting,
                    "temperature_rows": len(model_temp),
                    "bracket_rows": len(model_bracket),
                    "temperature_groups": temp_groups,
                    "bracket_groups": bracket_groups,
                    **temp_metrics,
                    **bracket_metrics,
                }
            )
    weight_order = {"row_weighted": 0, "city_day_weighted": 1, "target_day_weighted": 2}
    return sorted(
        output,
        key=lambda row: (
            weight_order[str(row["weighting"])],
            float(row.get("mae") or 999),
            row["model_name"],
        ),
    )


def _weighted_metric_values(
    rows: list[dict[str, Any]],
    metric_type: str,
    group_column: str | None,
) -> tuple[dict[str, float], int]:
    if not rows:
        return {}, 0
    if group_column is None:
        return _metric_values(rows, metric_type), len(rows)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[group_column])].append(row)
    by_metric: dict[str, list[float]] = defaultdict(list)
    for group_rows in grouped.values():
        for metric in _metric_rows(group_rows, metric_type):
            by_metric[str(metric["metric"])].append(float(metric["value"]))
    return {metric: mean(values) for metric, values in by_metric.items()}, len(grouped)


def _metric_values(rows: list[dict[str, Any]], metric_type: str) -> dict[str, float]:
    return {
        str(metric["metric"]): float(metric["value"])
        for metric in _metric_rows(rows, metric_type)
    }


def _bootstrap_confidence_rows(
    names: list[str],
    temp_rows: list[dict[str, Any]],
    bracket_rows: list[dict[str, Any]],
    samples: int = 500,
    seed: int = 17,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    output: list[dict[str, Any]] = []
    for name in names:
        model_temp = [row for row in temp_rows if row["model_name"] == name]
        model_bracket = [row for row in bracket_rows if row["model_name"] == name]
        per_date = _per_target_day_metrics(model_temp, model_bracket)
        metric_names = sorted({metric for metrics in per_date.values() for metric in metrics})
        for metric in metric_names:
            date_values = {
                target_date: values[metric]
                for target_date, values in per_date.items()
                if metric in values
            }
            dates = sorted(date_values)
            if not dates:
                continue
            estimate = mean(date_values[date] for date in dates)
            boot = []
            for _ in range(samples):
                sampled_dates = [rng.choice(dates) for _ in dates]
                boot.append(mean(date_values[date] for date in sampled_dates))
            low, high = _percentile_interval(boot, 0.025, 0.975)
            output.append(
                {
                    "model_name": name,
                    "metric": metric,
                    "weighting": "target_day_bootstrap",
                    "estimate": estimate,
                    "ci_low": low,
                    "ci_high": high,
                    "samples": samples,
                    "groups": len(dates),
                    "seed": seed,
                }
            )
    return output


def _per_target_day_metrics(
    temp_rows: list[dict[str, Any]],
    bracket_rows: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    target_dates = sorted(
        {str(row["target_date"]) for row in temp_rows}
        | {str(row["target_date"]) for row in bracket_rows}
    )
    for target_date in target_dates:
        temp_metrics = _metric_values(
            [row for row in temp_rows if row["target_date"] == target_date],
            "temperature",
        )
        bracket_metrics = _metric_values(
            [row for row in bracket_rows if row["target_date"] == target_date],
            "bracket",
        )
        output[target_date] = {**temp_metrics, **bracket_metrics}
    return output


def _percentile_interval(values: list[float], low: float, high: float) -> tuple[float, float]:
    ordered = sorted(values)
    if not ordered:
        return float("nan"), float("nan")
    low_index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * low)))
    high_index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * high)))
    return ordered[low_index], ordered[high_index]


def _grouped_by_model(
    names: list[str],
    temp_rows: list[dict[str, Any]],
    bracket_rows: list[dict[str, Any]],
    group_column: str,
) -> list[dict[str, Any]]:
    output = []
    for name in names:
        rows = _group_metric_rows(
            [row for row in temp_rows if row["model_name"] == name],
            [row for row in bracket_rows if row["model_name"] == name],
            group_column,
        )
        output.extend({"model_name": name, **row} for row in rows)
    return output


def _calibration_by_model(
    names: list[str],
    bracket_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    for name in names:
        rows = _calibration_rows([row for row in bracket_rows if row["model_name"] == name])
        output.extend({"model_name": name, **row} for row in rows)
    return output


def _calibration_summary_rows(
    names: list[str],
    calibration_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for name in names:
        rows = [
            row
            for row in calibration_rows
            if row["model_name"] == name and int(row.get("count") or 0) > 0
        ]
        total = sum(int(row["count"]) for row in rows)
        if total == 0:
            continue
        weighted_diffs = [
            (
                int(row["count"]),
                float(row["mean_top_probability"]) - float(row["empirical_win_rate"]),
            )
            for row in rows
        ]
        output.append(
            {
                "model_name": name,
                "count": total,
                "calibration_mae": sum(count * abs(diff) for count, diff in weighted_diffs)
                / total,
                "calibration_bias": sum(count * diff for count, diff in weighted_diffs) / total,
                "mean_top_probability": _weighted_average(rows, "mean_top_probability"),
                "empirical_win_rate": _weighted_average(rows, "empirical_win_rate"),
                "log_loss": _weighted_average(rows, "log_loss"),
                "brier": _weighted_average(rows, "brier"),
                "top_one_accuracy": _weighted_average(rows, "top_one_accuracy"),
            }
        )
    return sorted(output, key=lambda row: (float(row["calibration_mae"]), row["model_name"]))


def _weighted_average(rows: list[dict[str, Any]], column: str) -> float:
    total = sum(int(row["count"]) for row in rows)
    return sum(int(row["count"]) * float(row[column]) for row in rows) / total


def _write_summary(
    output: Path,
    source_export_id: str | None,
    names: list[str],
    comparison: list[dict[str, Any]],
    weighted_comparison: list[dict[str, Any]],
) -> None:
    headline = [
        row for row in weighted_comparison if row["weighting"] == "city_day_weighted"
    ]
    summary = {
        "mode": "benchmark_expanding_window",
        "source_export_id": source_export_id,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "models": names,
        "model_comparison": comparison,
        "headline_weighting": "city_day_weighted",
        "headline_model_comparison": headline,
        "weighted_model_comparison": weighted_comparison,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _write_model_decision(
    output: Path,
    weighted_comparison: list[dict[str, Any]],
    calibration_summary: list[dict[str, Any]],
) -> None:
    city_rows = [row for row in weighted_comparison if row["weighting"] == "city_day_weighted"]
    if not city_rows:
        (output / "model_decision.md").write_text("No scored model rows.\n", encoding="utf-8")
        return
    best_point = min(city_rows, key=lambda row: float(row.get("mae") or 999))
    best_probability = min(city_rows, key=lambda row: float(row.get("log_loss") or 999))
    best_calibration = (
        min(calibration_summary, key=lambda row: float(row["calibration_mae"]))
        if calibration_summary
        else None
    )
    raycaster = _row_by_model(city_rows, "raycaster")
    source_blend = _row_by_model(city_rows, "source_blend")
    hybrid = _row_by_model(city_rows, "raycaster_source_blend_hybrid")
    lines = [
        "# Raycaster Benchmark Decision",
        "",
        "Headline weighting: equal weight per city-day.",
        "",
        f"- Best point forecast: {best_point['model_name']} "
        f"(MAE {float(best_point['mae']):.4f}, RMSE {float(best_point['rmse']):.4f}).",
        f"- Best bracket probability: {best_probability['model_name']} "
        f"(log loss {float(best_probability['log_loss']):.4f}).",
    ]
    if best_calibration is not None:
        lines.append(
            f"- Best top-pick calibration: {best_calibration['model_name']} "
            f"(calibration MAE {float(best_calibration['calibration_mae']):.4f})."
        )
    if raycaster and source_blend:
        lines.extend(
            [
                "",
                "## Raycaster vs Source Blend",
                "",
                f"- MAE delta: {float(raycaster['mae']) - float(source_blend['mae']):+.4f} F.",
                f"- RMSE delta: {float(raycaster['rmse']) - float(source_blend['rmse']):+.4f} F.",
                f"- Log-loss delta: "
                f"{float(raycaster['log_loss']) - float(source_blend['log_loss']):+.4f}.",
                f"- Top-one accuracy delta: "
                f"{_metric_delta(raycaster, source_blend, 'top_one_accuracy'):+.4f}.",
            ]
        )
    if hybrid and source_blend:
        lines.extend(
            [
                "",
                "## Hybrid vs Source Blend",
                "",
                f"- MAE delta: {float(hybrid['mae']) - float(source_blend['mae']):+.4f} F.",
                f"- RMSE delta: {float(hybrid['rmse']) - float(source_blend['rmse']):+.4f} F.",
                f"- Log-loss delta: "
                f"{float(hybrid['log_loss']) - float(source_blend['log_loss']):+.4f}.",
                f"- Top-one accuracy delta: "
                f"{_metric_delta(hybrid, source_blend, 'top_one_accuracy'):+.4f}.",
            ]
        )
    lines.extend(
        [
            "",
            "## Caveat",
            "",
            "Treat this as a small-sample benchmark until more fully settled target days exist. "
            "Use bootstrap intervals before declaring small deltas meaningful.",
            "",
        ]
    )
    (output / "model_decision.md").write_text("\n".join(lines), encoding="utf-8")


def _row_by_model(rows: list[dict[str, Any]], model_name: str) -> dict[str, Any] | None:
    return next((row for row in rows if row["model_name"] == model_name), None)


def _metric_delta(
    first: dict[str, Any],
    second: dict[str, Any],
    metric: str,
) -> float:
    return float(first[metric]) - float(second[metric])


def _write_benchmark_charts(
    output: Path,
    names: list[str],
    comparison: list[dict[str, Any]],
    daily_rows: list[dict[str, Any]],
    calibration_rows: list[dict[str, Any]],
) -> None:
    try:
        import matplotlib
    except ImportError:
        return
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    charts = output / "charts"
    charts.mkdir(exist_ok=True)
    mae_ordered = sorted(comparison, key=lambda row: float(row["mae"]), reverse=True)
    log_loss_ordered = sorted(comparison, key=lambda row: float(row["log_loss"]), reverse=True)
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].barh(
        [row["model_name"] for row in mae_ordered],
        [float(row["mae"]) for row in mae_ordered],
    )
    axes[0].set_title("Temperature MAE")
    axes[0].set_xlabel("Degrees F")
    axes[1].barh(
        [row["model_name"] for row in log_loss_ordered],
        [float(row["log_loss"]) for row in log_loss_ordered],
    )
    axes[1].set_title("Bracket log loss")
    axes[1].set_xlabel("Loss")
    fig.suptitle("Raycaster benchmark leaderboard")
    fig.tight_layout()
    fig.savefig(charts / "model_leaderboard.png", dpi=180)
    plt.close(fig)

    daily_index = _daily_metric_index(daily_rows)
    dates = sorted({key[1] for key in daily_index if key[2] == "mae"})
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True)
    daily_panels = (
        ("mae", "Daily MAE by model", "Degrees F"),
        ("rmse", "Daily RMSE by model", "Degrees F"),
        ("log_loss", "Daily log loss by model", "Loss"),
        ("top_one_accuracy", "Daily top-one accuracy by model", "Accuracy"),
    )
    flat_axes = axes.ravel()
    for axis, (metric, title, ylabel) in zip(flat_axes, daily_panels, strict=True):
        for name in names:
            axis.plot(
                dates,
                [daily_index.get((name, date, metric), float("nan")) for date in dates],
                marker="o",
                linewidth=1.5,
                label=name,
            )
        axis.set_title(title)
        axis.set_ylabel(ylabel)
        axis.tick_params(axis="x", rotation=35)
    flat_axes[0].legend(ncols=4, fontsize=8)
    fig.tight_layout()
    fig.savefig(charts / "daily_model_trends.png", dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.plot([0, 1], [0, 1], color="black", linestyle="--", linewidth=1, label="perfect")
    for name in names:
        rows = [
            row
            for row in calibration_rows
            if row["model_name"] == name and int(row.get("count") or 0) > 0
        ]
        if not rows:
            continue
        ax.plot(
            [float(row["mean_top_probability"]) for row in rows],
            [float(row["empirical_win_rate"]) for row in rows],
            marker="o",
            linewidth=1.5,
            label=name,
        )
    ax.set_title("Top-pick calibration")
    ax.set_xlabel("Mean top probability")
    ax.set_ylabel("Empirical win rate")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(ncols=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(charts / "calibration_reliability.png", dpi=180)
    plt.close(fig)


def _daily_metric_index(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], float]:
    output = {}
    for row in rows:
        output[(str(row["model_name"]), str(row["group"]), str(row["metric"]))] = float(
            row["value"]
        )
    return output


def _observed(row: FeatureRow) -> float | None:
    value = row.features.get("observed_high_so_far_f")
    return float(value) if value is not None else None
