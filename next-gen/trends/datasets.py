"""Build GUI-ready trend workbench payloads from local exports and reports."""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from trends.data import METRICS, _load_tables, _metric_series, metric_catalog

EXPORT_TABLES = (
    "events",
    "weather_snapshots",
    "market_snapshots",
    "settlements",
    "final_temperature_labels",
    "provider_errors",
    "collector_runs",
    "raw_payloads",
)

REPORT_TABLES = (
    "predictions",
    "bracket_distributions",
    "errors",
    "by_checkpoint",
    "by_city",
    "temperature_metrics",
    "bracket_metrics",
    "training_diagnostics",
)


def build_workbench_payload(
    data_dir: Path,
    report_dirs: list[Path] | None = None,
    quality_report: Path | None = None,
) -> dict[str, Any]:
    """Return one normalized payload for the local Trends GUI."""

    report_dirs = report_dirs or []
    export_tables = _load_export_tables(data_dir)
    report_tables = _load_report_tables(report_dirs)
    quality = _load_quality_report(quality_report)

    series = {
        metric.key: _metric_series(metric, export_tables)
        for metric in METRICS
        if metric.table in export_tables
    }
    expanded_distributions = _expand_bracket_distributions(report_tables)
    model_error_rows = _model_error_rows(report_tables, export_tables)
    source_disagreement = _source_disagreement_rows(export_tables)
    market_model = _market_model_rows(export_tables, expanded_distributions)
    calibration = _calibration_bins(market_model)
    checkpoint_metrics = _checkpoint_metrics(report_tables)
    event_replays = _event_replays(
        export_tables,
        report_tables,
        expanded_distributions,
        market_model,
    )
    settlement_grid = _settlement_grid(export_tables, report_tables, market_model)
    feature_error_points = _feature_error_points(model_error_rows, source_disagreement)
    series.update(_report_series(report_tables, model_error_rows))

    tables = {
        name: _records(frame)
        for name, frame in {**export_tables, **report_tables}.items()
        if not frame.empty
    }
    tables["expanded_bracket_distributions"] = expanded_distributions
    tables["model_error_rows"] = model_error_rows
    tables["source_disagreement"] = source_disagreement
    tables["market_model_points"] = market_model
    tables["calibration_bins"] = calibration
    tables["checkpoint_metrics"] = checkpoint_metrics
    tables["settlement_grid"] = settlement_grid

    events = _event_catalog(export_tables)
    cities = _cities(export_tables)
    models = _models(report_tables, expanded_distributions)
    date_range = _date_range(export_tables, report_tables)
    has_reports = bool(models or not report_tables.get("errors", pd.DataFrame()).empty)

    return {
        "metadata": {
            "data_dir": str(data_dir),
            "report_dirs": [str(path) for path in report_dirs],
            "quality_report": str(quality_report) if quality_report else None,
            "cities": cities,
            "models": models,
            "date_range": date_range,
            "has_model_reports": has_reports,
            "table_counts": {name: len(rows) for name, rows in tables.items()},
            "metrics": metric_catalog() + _report_metric_catalog(has_reports),
        },
        "catalog": {
            "modes": _mode_catalog(),
            "events": events,
            "cities": cities,
            "models": models,
            "metrics": metric_catalog() + _report_metric_catalog(has_reports),
            "tables": sorted(tables),
            "checkpoints": _checkpoints(export_tables, report_tables),
            "feature_columns": _feature_columns(source_disagreement, model_error_rows),
        },
        "events": events,
        "series": series,
        "tables": tables,
        "analysis": {
            "event_replays": event_replays,
            "checkpoint_metrics": checkpoint_metrics,
            "feature_error_points": feature_error_points,
            "source_disagreement": source_disagreement,
            "market_model_points": market_model,
            "calibration_bins": calibration,
            "settlement_grid": settlement_grid,
            "model_error_rows": model_error_rows,
            "quality": quality,
            "overview": _overview(export_tables, report_tables, quality, event_replays),
        },
    }


def _load_export_tables(data_dir: Path) -> dict[str, pd.DataFrame]:
    tables = _load_tables(data_dir)
    for name in EXPORT_TABLES:
        tables.setdefault(name, _read_table(data_dir, name))
    return tables


def _load_report_tables(report_dirs: list[Path]) -> dict[str, pd.DataFrame]:
    output: dict[str, list[pd.DataFrame]] = {name: [] for name in REPORT_TABLES}
    for report_dir in report_dirs:
        report_name = report_dir.name
        for name in REPORT_TABLES:
            frame = _read_table(report_dir, name)
            if frame.empty:
                continue
            frame = frame.copy()
            frame["report_name"] = report_name
            frame["report_dir"] = str(report_dir)
            if "snapshot_hour_utc" in frame.columns and "snapshot_time_utc" not in frame.columns:
                frame["snapshot_time_utc"] = frame["snapshot_hour_utc"]
            output[name].append(frame)
    return {
        name: pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        for name, frames in output.items()
    }


def _read_table(root: Path, name: str) -> pd.DataFrame:
    csv_path = root / f"{name}.csv"
    json_gz_path = root / f"{name}.json.gz"
    json_path = root / f"{name}.json"
    try:
        if csv_path.exists() and csv_path.stat().st_size > 0:
            return pd.read_csv(csv_path)
        if json_gz_path.exists() and json_gz_path.stat().st_size > 0:
            return pd.read_json(json_gz_path, compression="gzip")
        if json_path.exists() and json_path.stat().st_size > 0:
            return pd.read_json(json_path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    return pd.DataFrame()


def _load_quality_report(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"available": False}
    root = path
    if root.is_file():
        try:
            data = json.loads(root.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        return {"available": True, "summary": data, "path": str(root)}
    summary_path = root / "quality_report.json"
    summary: dict[str, Any] = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}
    return {
        "available": True,
        "path": str(root),
        "summary": summary,
        "table_counts": _records(_read_table(root, "table_counts")),
        "missing_city_hours": _records(_read_table(root, "missing_city_hours")),
        "provider_errors": _records(_read_table(root, "provider_errors")),
    }


def _expand_bracket_distributions(report_tables: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    frame = report_tables.get("bracket_distributions", pd.DataFrame())
    if frame.empty or "probabilities" not in frame.columns:
        return []
    rows: list[dict[str, Any]] = []
    for row in frame.to_dict("records"):
        probabilities = _parse_probabilities(row.get("probabilities"))
        for ticker, probability in probabilities.items():
            rows.append(
                {
                    "city": _text(row.get("city")),
                    "event_ticker": _text(row.get("event_ticker")),
                    "event_key": _event_key(row),
                    "snapshot_time_utc": _text(row.get("snapshot_time_utc")),
                    "model_name": _text(row.get("model_name")),
                    "report_name": _text(row.get("report_name")),
                    "market_ticker": str(ticker),
                    "model_probability": _float(probability),
                }
            )
    return rows


def _parse_probabilities(value: Any) -> dict[str, float]:
    if isinstance(value, dict):
        return {str(key): float(val) for key, val in value.items() if _is_number(val)}
    if not isinstance(value, str) or not value.strip():
        return {}
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(value)
        except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return {str(key): float(val) for key, val in parsed.items() if _is_number(val)}
    return {}


def _model_error_rows(
    report_tables: dict[str, pd.DataFrame],
    export_tables: dict[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    errors = report_tables.get("errors", pd.DataFrame())
    if errors.empty:
        return _prediction_error_fallback(report_tables, export_tables)
    rows = _records(errors)
    for row in rows:
        row["event_key"] = _event_key(row)
        if row.get("metric_type") == "temperature":
            row["temperature_error_f"] = row.get("error_f")
            row["absolute_temperature_error_f"] = row.get("absolute_error_f")
        if row.get("metric_type") == "bracket":
            row["bracket_miss_distance"] = _bracket_miss_distance(row)
    return rows


def _prediction_error_fallback(
    report_tables: dict[str, pd.DataFrame],
    export_tables: dict[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    predictions = report_tables.get("predictions", pd.DataFrame())
    labels = export_tables.get("final_temperature_labels", pd.DataFrame())
    if predictions.empty or labels.empty:
        return []
    merged = predictions.merge(
        labels[["city", "event_ticker", "final_high_f"]],
        on=["city", "event_ticker"],
        how="left",
    )
    rows: list[dict[str, Any]] = []
    for row in merged.to_dict("records"):
        predicted = _float(row.get("expected_high_f"))
        actual = _float(row.get("final_high_f"))
        error = predicted - actual if predicted is not None and actual is not None else None
        rows.append(
            {
                "metric_type": "temperature",
                "city": _text(row.get("city")),
                "event_ticker": _text(row.get("event_ticker")),
                "event_key": _event_key(row),
                "snapshot_time_utc": _text(row.get("snapshot_time_utc")),
                "model_name": _text(row.get("model_name")),
                "actual_high_f": actual,
                "predicted_high_f": predicted,
                "error_f": error,
                "absolute_error_f": abs(error) if error is not None else None,
            }
        )
    return rows


def _source_disagreement_rows(export_tables: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    weather = export_tables.get("weather_snapshots", pd.DataFrame())
    if weather.empty:
        return []
    rows: list[dict[str, Any]] = []
    for row in weather.to_dict("records"):
        values = {
            "nws": _float(row.get("nws_anchor_high_f")),
            "observed": _float(row.get("observed_high_so_far_f")),
            "hrrr": _float(row.get("hrrr_projected_high_f")),
            "nbm": _float(row.get("nbm_projected_high_f")),
            "ensemble": _float(row.get("ensemble_raw_median_high_f")),
        }
        numeric_values = [value for value in values.values() if value is not None]
        rows.append(
            {
                "city": _text(row.get("city")),
                "event_ticker": _text(row.get("event_ticker")),
                "event_key": _event_key(row),
                "snapshot_time_utc": _text(row.get("snapshot_time_utc")),
                "checkpoint": _text(row.get("checkpoint_label")),
                "target_date": _text(row.get("target_date")),
                "hours_since_climate_start": _float(row.get("hours_since_climate_start")),
                "hours_until_climate_end": _float(row.get("hours_until_climate_end")),
                "nws_anchor_high_f": values["nws"],
                "observed_high_so_far_f": values["observed"],
                "hrrr_projected_high_f": values["hrrr"],
                "nbm_projected_high_f": values["nbm"],
                "ensemble_raw_median_high_f": values["ensemble"],
                "hrrr_minus_nws": _diff(values["hrrr"], values["nws"]),
                "nbm_minus_nws": _diff(values["nbm"], values["nws"]),
                "ensemble_minus_nws": _diff(values["ensemble"], values["nws"]),
                "observed_minus_nws": _diff(values["observed"], values["nws"]),
                "hrrr_minus_nbm": _diff(values["hrrr"], values["nbm"]),
                "source_range_f": (
                    max(numeric_values) - min(numeric_values) if len(numeric_values) >= 2 else None
                ),
                "observation_age_seconds": _float(row.get("observation_age_seconds")),
                "weather_source_stddev_f": _float(row.get("weather_source_stddev_f")),
                "all_weather_sources_range_f": _float(row.get("all_weather_sources_range_f")),
            }
        )
    return rows


def _market_model_rows(
    export_tables: dict[str, pd.DataFrame],
    expanded_distributions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not expanded_distributions:
        return []
    dist = pd.DataFrame(expanded_distributions)
    markets = export_tables.get("market_snapshots", pd.DataFrame())
    if markets.empty:
        return _records(dist)
    required = ["city", "event_ticker", "snapshot_time_utc", "market_ticker"]
    merged = dist.merge(markets, on=required, how="left", suffixes=("", "_market"))
    settlements = export_tables.get("settlements", pd.DataFrame())
    if not settlements.empty and {"city", "event_ticker", "winner_ticker"}.issubset(
        settlements.columns
    ):
        settlement_columns = [
            column
            for column in ("city", "event_ticker", "winner_ticker", "settlement_bracket_index")
            if column in settlements.columns
        ]
        merged = merged.merge(
            settlements[settlement_columns],
            on=["city", "event_ticker"],
            how="left",
        )
    rows: list[dict[str, Any]] = []
    for row in merged.to_dict("records"):
        probability = _float(row.get("model_probability"))
        ask = _float(row.get("yes_ask_dollars"))
        midpoint = _float(row.get("normalized_market_midpoint_probability"))
        winner = _text(row.get("winner_ticker"))
        ticker = _text(row.get("market_ticker"))
        rows.append(
            {
                "city": _text(row.get("city")),
                "event_ticker": _text(row.get("event_ticker")),
                "event_key": _event_key(row),
                "snapshot_time_utc": _text(row.get("snapshot_time_utc")),
                "checkpoint": _text(row.get("checkpoint_label")),
                "model_name": _text(row.get("model_name")),
                "report_name": _text(row.get("report_name")),
                "market_ticker": ticker,
                "bracket_label": _text(row.get("bracket_label")),
                "bracket_index": _float(row.get("bracket_index")),
                "model_probability": probability,
                "yes_ask_dollars": ask,
                "yes_midpoint": _float(row.get("yes_midpoint")),
                "normalized_market_midpoint_probability": midpoint,
                "market_probability": midpoint,
                "model_minus_market": _diff(probability, midpoint),
                "model_minus_ask": _diff(probability, ask),
                "winner_ticker": winner,
                "is_winner": bool(winner and ticker == winner),
                "settlement_bracket_index": _float(row.get("settlement_bracket_index")),
            }
        )
    return rows


def _calibration_bins(market_model: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in market_model
        if row.get("model_probability") is not None and row.get("winner_ticker")
    ]
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    frame["probability_bin"] = frame["model_probability"].apply(
        lambda value: min(9, int(float(value) * 10)) / 10
    )
    grouped = frame.groupby(["model_name", "probability_bin"], as_index=False).agg(
        count=("is_winner", "size"),
        observed_frequency=("is_winner", "mean"),
        mean_probability=("model_probability", "mean"),
    )
    return _records(grouped)


def _checkpoint_metrics(report_tables: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    checkpoint = report_tables.get("by_checkpoint", pd.DataFrame())
    if checkpoint.empty:
        return []
    rows = _records(checkpoint)
    for row in rows:
        row["checkpoint"] = row.get("group")
    return rows


def _report_series(
    report_tables: dict[str, pd.DataFrame],
    model_error_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    predictions = report_tables.get("predictions", pd.DataFrame())
    output: dict[str, list[dict[str, Any]]] = {}
    if not predictions.empty:
        rows = []
        for row in predictions.to_dict("records"):
            value = _float(row.get("expected_high_f"))
            if value is None:
                continue
            rows.append(
                {
                    "x": _text(row.get("snapshot_time_utc")),
                    "group": _text(row.get("city")),
                    "model_name": _text(row.get("model_name")),
                    "event_key": _event_key(row),
                    "value": value,
                }
            )
        output["model_expected_high_by_snapshot"] = rows
    temp_rows = []
    winner_rows = []
    for row in model_error_rows:
        if row.get("metric_type") == "temperature":
            value = _float(row.get("error_f"))
            if value is not None:
                temp_rows.append(
                    {
                        "x": _text(row.get("snapshot_time_utc")),
                        "group": _text(row.get("city")),
                        "model_name": _text(row.get("model_name")),
                        "event_key": _event_key(row),
                        "value": value,
                    }
                )
        if row.get("metric_type") == "bracket":
            value = _float(row.get("winner_probability"))
            if value is not None:
                winner_rows.append(
                    {
                        "x": _text(row.get("snapshot_time_utc")),
                        "group": _text(row.get("city")),
                        "model_name": _text(row.get("model_name")),
                        "event_key": _event_key(row),
                        "value": value,
                    }
                )
    output["temperature_error_by_snapshot"] = temp_rows
    output["winner_probability_by_snapshot"] = winner_rows
    return output


def _event_replays(
    export_tables: dict[str, pd.DataFrame],
    report_tables: dict[str, pd.DataFrame],
    expanded_distributions: list[dict[str, Any]],
    market_model: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events = export_tables.get("events", pd.DataFrame())
    weather = export_tables.get("weather_snapshots", pd.DataFrame())
    predictions = report_tables.get("predictions", pd.DataFrame())
    settlements = export_tables.get("settlements", pd.DataFrame())
    labels = export_tables.get("final_temperature_labels", pd.DataFrame())
    markets = export_tables.get("market_snapshots", pd.DataFrame())

    event_rows = _event_catalog(export_tables)
    output: list[dict[str, Any]] = []
    for event in event_rows:
        city = event["city"]
        ticker = event["event_ticker"]
        key = event["event_key"]
        timeline = _event_timeline(city, ticker, weather, markets, predictions)
        probabilities = [
            row for row in expanded_distributions if row.get("event_key") == key
        ]
        model_market = [row for row in market_model if row.get("event_key") == key]
        event_frame = events[
            (events.get("city", pd.Series(dtype=str)).astype(str) == city)
            & (events.get("event_ticker", pd.Series(dtype=str)).astype(str) == ticker)
        ]
        settlement = _first_record(settlements, city, ticker)
        label = _first_record(labels, city, ticker)
        output.append(
            {
                **event,
                "target_date": event.get("target_date") or _first_value(event_frame, "target_date"),
                "final_high_f": label.get("final_high_f"),
                "winner_ticker": settlement.get("winner_ticker"),
                "winner_label": settlement.get("winner_label"),
                "settlement_temperature_f": settlement.get("settlement_temperature_f"),
                "timeline": timeline,
                "bracket_probabilities": probabilities,
                "market_model_points": model_market,
            }
        )
    return output


def _event_timeline(
    city: str,
    ticker: str,
    weather: pd.DataFrame,
    markets: pd.DataFrame,
    predictions: pd.DataFrame,
) -> list[dict[str, Any]]:
    if weather.empty:
        base = pd.DataFrame()
    else:
        base = weather[
            (weather["city"].astype(str) == city) & (weather["event_ticker"].astype(str) == ticker)
        ].copy()
    if base.empty:
        return []
    if "snapshot_time_utc" not in base.columns:
        return []
    market_top = _snapshot_market_top(markets, city, ticker)
    pred_rows = _snapshot_predictions(predictions, city, ticker)
    rows: list[dict[str, Any]] = []
    for row in base.sort_values("snapshot_time_utc").to_dict("records"):
        snapshot = _text(row.get("snapshot_time_utc"))
        item = {
            "snapshot_time_utc": snapshot,
            "checkpoint": _text(row.get("checkpoint_label")),
            "hours_since_climate_start": _float(row.get("hours_since_climate_start")),
            "nws_anchor_high_f": _float(row.get("nws_anchor_high_f")),
            "observed_high_so_far_f": _float(row.get("observed_high_so_far_f")),
            "hrrr_projected_high_f": _float(row.get("hrrr_projected_high_f")),
            "nbm_projected_high_f": _float(row.get("nbm_projected_high_f")),
            "ensemble_raw_median_high_f": _float(row.get("ensemble_raw_median_high_f")),
        }
        item.update(market_top.get(snapshot, {}))
        item.update(pred_rows.get(snapshot, {}))
        rows.append(item)
    return rows


def _snapshot_market_top(
    markets: pd.DataFrame,
    city: str,
    ticker: str,
) -> dict[str, dict[str, Any]]:
    if markets.empty:
        return {}
    frame = markets[
        (markets["city"].astype(str) == city) & (markets["event_ticker"].astype(str) == ticker)
    ].copy()
    if frame.empty:
        return {}
    frame["rank_prob"] = pd.to_numeric(
        frame.get("normalized_market_midpoint_probability", frame.get("yes_midpoint")),
        errors="coerce",
    )
    output: dict[str, dict[str, Any]] = {}
    for snapshot, group in frame.groupby("snapshot_time_utc"):
        top = group.sort_values("rank_prob", ascending=False).head(1).to_dict("records")
        if not top:
            continue
        row = top[0]
        output[str(snapshot)] = {
            "market_top_ticker": _text(row.get("market_ticker")),
            "market_top_probability": _float(row.get("rank_prob")),
            "market_top_ask": _float(row.get("yes_ask_dollars")),
        }
    return output


def _snapshot_predictions(
    predictions: pd.DataFrame,
    city: str,
    ticker: str,
) -> dict[str, dict[str, Any]]:
    if predictions.empty:
        return {}
    frame = predictions[
        (predictions["city"].astype(str) == city)
        & (predictions["event_ticker"].astype(str) == ticker)
    ].copy()
    output: dict[str, dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        snapshot = _text(row.get("snapshot_time_utc"))
        output.setdefault(snapshot, {})
        output[snapshot][f"{_text(row.get('model_name'))}_expected_high_f"] = _float(
            row.get("expected_high_f")
        )
    return output


def _settlement_grid(
    export_tables: dict[str, pd.DataFrame],
    report_tables: dict[str, pd.DataFrame],
    market_model: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events = _event_catalog(export_tables)
    settlements = export_tables.get("settlements", pd.DataFrame())
    labels = export_tables.get("final_temperature_labels", pd.DataFrame())
    errors = report_tables.get("errors", pd.DataFrame())
    rows: list[dict[str, Any]] = []
    for event in events:
        city = event["city"]
        ticker = event["event_ticker"]
        key = event["event_key"]
        settlement = _first_record(settlements, city, ticker)
        label = _first_record(labels, city, ticker)
        latest_model = _latest_model_point(market_model, key)
        latest_errors = _latest_error(errors, city, ticker)
        rows.append(
            {
                **event,
                "target_date": event.get("target_date"),
                "winner_ticker": settlement.get("winner_ticker"),
                "winner_label": settlement.get("winner_label"),
                "settlement_temperature_f": settlement.get("settlement_temperature_f"),
                "settlement_bracket_index": settlement.get("settlement_bracket_index"),
                "final_high_f": label.get("final_high_f"),
                "model_top_ticker": latest_model.get("market_ticker"),
                "model_top_probability": latest_model.get("model_probability"),
                "top_one_accuracy": latest_errors.get("top_one_accuracy"),
                "winner_probability": latest_errors.get("winner_probability"),
                "absolute_error_f": latest_errors.get("absolute_error_f"),
                "bracket_miss_distance": _bracket_miss_distance(latest_errors),
            }
        )
    return rows


def _latest_model_point(market_model: list[dict[str, Any]], event_key: str) -> dict[str, Any]:
    rows = [row for row in market_model if row.get("event_key") == event_key]
    if not rows:
        return {}
    rows.sort(
        key=lambda row: (
            _text(row.get("snapshot_time_utc")),
            _float(row.get("model_probability")) or -1.0,
        ),
        reverse=True,
    )
    return rows[0]


def _latest_error(errors: pd.DataFrame, city: str, ticker: str) -> dict[str, Any]:
    if errors.empty:
        return {}
    frame = errors[
        (errors["city"].astype(str) == city) & (errors["event_ticker"].astype(str) == ticker)
    ].copy()
    if frame.empty:
        return {}
    frame = frame.sort_values("snapshot_time_utc")
    return _clean_record(frame.tail(1).to_dict("records")[0])


def _feature_error_points(
    model_error_rows: list[dict[str, Any]],
    source_disagreement: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_by_key = {
        (row.get("city"), row.get("event_ticker"), row.get("snapshot_time_utc")): row
        for row in source_disagreement
    }
    output: list[dict[str, Any]] = []
    for row in model_error_rows:
        if row.get("metric_type") != "temperature":
            continue
        source = source_by_key.get(
            (row.get("city"), row.get("event_ticker"), row.get("snapshot_time_utc")),
            {},
        )
        merged = {**source, **row}
        merged["temperature_error_f"] = row.get("error_f")
        merged["absolute_temperature_error_f"] = row.get("absolute_error_f")
        output.append(merged)
    return output


def _event_catalog(export_tables: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    events = export_tables.get("events", pd.DataFrame())
    if events.empty:
        return []
    columns = [
        "city",
        "event_ticker",
        "target_date",
        "target_date_local",
        "city_timezone",
        "station_id",
        "climate_day_start_utc",
        "climate_day_end_utc",
    ]
    existing = [column for column in columns if column in events.columns]
    frame = events[existing].drop_duplicates(["city", "event_ticker"]).copy()
    rows = _records(frame)
    for row in rows:
        row["event_key"] = _event_key(row)
        row["label"] = (
            f"{row.get('city', '').upper()} "
            f"{row.get('target_date') or row.get('event_ticker')}"
        )
    return sorted(rows, key=lambda row: (str(row.get("target_date")), str(row.get("city"))))


def _overview(
    export_tables: dict[str, pd.DataFrame],
    report_tables: dict[str, pd.DataFrame],
    quality: dict[str, Any],
    event_replays: list[dict[str, Any]],
) -> dict[str, Any]:
    weather = export_tables.get("weather_snapshots", pd.DataFrame())
    latest_snapshot = None
    if not weather.empty and "snapshot_time_utc" in weather.columns:
        latest_snapshot = str(weather["snapshot_time_utc"].dropna().max())
    labels = export_tables.get("final_temperature_labels", pd.DataFrame())
    settlements = export_tables.get("settlements", pd.DataFrame())
    event_count = len(_event_catalog(export_tables))
    settled_events = _dedup_count(settlements, ["city", "event_ticker"])
    labeled_events = _dedup_count(labels, ["city", "event_ticker"])
    return {
        "latest_snapshot_utc": latest_snapshot,
        "event_count": event_count,
        "snapshot_count": len(weather),
        "market_row_count": len(export_tables.get("market_snapshots", pd.DataFrame())),
        "settlement_count": len(settlements),
        "final_high_count": len(labels),
        "pending_settlements": max(0, event_count - settled_events),
        "pending_final_highs": max(0, event_count - labeled_events),
        "report_count": sum(1 for frame in report_tables.values() if not frame.empty),
        "quality_available": bool(quality.get("available")),
        "events_with_replay": len([event for event in event_replays if event.get("timeline")]),
    }


def _report_metric_catalog(has_reports: bool) -> list[dict[str, str]]:
    if not has_reports:
        return []
    return [
        {
            "key": "model_expected_high_by_snapshot",
            "label": "Model Expected High By Snapshot",
            "description": "Raycaster or report-provided expected final high over time.",
        },
        {
            "key": "temperature_error_by_snapshot",
            "label": "Temperature Error By Snapshot",
            "description": "Predicted final high minus final NWS high.",
        },
        {
            "key": "winner_probability_by_snapshot",
            "label": "Winner Probability By Snapshot",
            "description": "Model probability assigned to the settled winning bracket.",
        },
    ]


def _mode_catalog() -> list[dict[str, Any]]:
    return [
        {"key": "overview", "label": "Overview", "requires_report": False},
        {"key": "trends", "label": "Trend Explorer", "requires_report": False},
        {"key": "replay", "label": "Event Replay", "requires_report": False},
        {"key": "performance", "label": "Checkpoint Performance", "requires_report": True},
        {"key": "features", "label": "Feature vs Error", "requires_report": True},
        {"key": "disagreement", "label": "Source Disagreement", "requires_report": False},
        {"key": "market-model", "label": "Market vs Model", "requires_report": True},
        {"key": "calibration", "label": "Calibration", "requires_report": True},
        {"key": "settlements", "label": "Settlement Grid", "requires_report": False},
        {"key": "quality", "label": "Data Quality", "requires_report": False},
    ]


def _feature_columns(
    source_disagreement: list[dict[str, Any]],
    model_error_rows: list[dict[str, Any]],
) -> list[str]:
    keys: set[str] = set()
    for row in source_disagreement[:10] + model_error_rows[:10]:
        for key, value in row.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                keys.add(key)
    return sorted(keys)


def _cities(tables: dict[str, pd.DataFrame]) -> list[str]:
    values: set[str] = set()
    for frame in tables.values():
        if "city" in frame.columns:
            values.update(str(value) for value in frame["city"].dropna().unique())
    return sorted(values)


def _models(report_tables: dict[str, pd.DataFrame], expanded: list[dict[str, Any]]) -> list[str]:
    values: set[str] = set()
    for frame in report_tables.values():
        if "model_name" in frame.columns:
            values.update(str(value) for value in frame["model_name"].dropna().unique())
    values.update(str(row["model_name"]) for row in expanded if row.get("model_name"))
    return sorted(values)


def _checkpoints(
    export_tables: dict[str, pd.DataFrame],
    report_tables: dict[str, pd.DataFrame],
) -> list[str]:
    values: set[str] = set()
    for frame in [*export_tables.values(), *report_tables.values()]:
        for column in ("checkpoint", "checkpoint_label"):
            if column in frame.columns:
                values.update(str(value) for value in frame[column].dropna().unique())
    return sorted(values)


def _date_range(
    export_tables: dict[str, pd.DataFrame],
    report_tables: dict[str, pd.DataFrame],
) -> dict[str, str | None]:
    values: list[str] = []
    for frame in [*export_tables.values(), *report_tables.values()]:
        for column in ("snapshot_time_utc", "target_date"):
            if column in frame.columns:
                values.extend(str(value) for value in frame[column].dropna().unique())
    values = sorted(values)
    return {"start": values[0] if values else None, "end": values[-1] if values else None}


def _first_record(frame: pd.DataFrame, city: str, ticker: str) -> dict[str, Any]:
    if frame.empty or not {"city", "event_ticker"}.issubset(frame.columns):
        return {}
    subset = frame[
        (frame["city"].astype(str) == city)
        & (frame["event_ticker"].astype(str) == ticker)
    ]
    if subset.empty:
        return {}
    return _clean_record(subset.head(1).to_dict("records")[0])


def _first_value(frame: pd.DataFrame, column: str) -> Any:
    if frame.empty or column not in frame.columns:
        return None
    values = frame[column].dropna()
    return _clean_value(values.iloc[0]) if not values.empty else None


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return [_clean_record(row) for row in frame.to_dict("records")]


def _clean_record(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _clean_value(value) for key, value in row.items()}


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _clean_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    if not isinstance(value, tuple) and pd.isna(value):
        return None
    return value


def _dedup_count(frame: pd.DataFrame, columns: list[str]) -> int:
    if frame.empty or not set(columns).issubset(frame.columns):
        return 0
    return len(frame.drop_duplicates(columns))


def _event_key(row: dict[str, Any]) -> str:
    return f"{_text(row.get('city'))}|{_text(row.get('event_ticker'))}"


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value)


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_number(value: Any) -> bool:
    return _float(value) is not None


def _diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _bracket_miss_distance(row: dict[str, Any]) -> float | None:
    winner = _text(row.get("winner_ticker"))
    top = _text(row.get("top_ticker") or row.get("model_top_ticker"))
    if not winner or not top:
        return None
    if winner == top:
        return 0.0
    # Exact distance needs bracket indexes for both tickers. Fall back to a one-miss marker.
    return 1.0
