"""Score an observation-aligned ensemble trajectory challenger offline."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from weather_backtest import _stored_brackets, score_probabilities
from weather_probabilities import (
    ENSEMBLE_MODELS,
    DataError,
    bracket_probability,
    kernel_bandwidth,
    parse_datetime,
    point_mass_bracket_probability,
)


def trace_record(snapshot: dict[str, Any], predicate: Any) -> dict[str, Any] | None:
    return next(
        (record for record in snapshot.get("http_trace", []) if predicate(record.get("url", ""))),
        None,
    )


def latest_report(root: Path, cohort: str) -> Path:
    reports = root / "cohorts" / cohort / "reports"
    candidates = sorted(
        path for path in reports.iterdir() if (path / "forecast_scores.csv").is_file()
    )
    if not candidates:
        raise FileNotFoundError(f"No evaluation reports found under {reports}")
    return candidates[-1]


def load_baseline_rows(report: Path) -> list[dict[str, str]]:
    with (report / "forecast_scores.csv").open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row["model"] == "full"]


def observed_temperature(snapshot: dict[str, Any]) -> tuple[datetime, float] | None:
    observed_at_raw = snapshot["distribution"].get("observed_at")
    if observed_at_raw is None:
        return None
    observed_at = parse_datetime(observed_at_raw).astimezone(UTC)
    record = trace_record(snapshot, lambda url: "/observations" in url)
    if record is None:
        raise DataError("snapshot has normalized observation metadata but no observation trace")
    candidates: list[tuple[datetime, float]] = []
    for feature in record.get("payload", {}).get("features", []):
        properties = feature.get("properties", {})
        try:
            timestamp = parse_datetime(properties["timestamp"]).astimezone(UTC)
            value = properties["temperature"]["value"]
            if value is not None and timestamp <= observed_at:
                candidates.append((timestamp, float(value) * 9 / 5 + 32))
        except (KeyError, TypeError, ValueError):
            continue
    if not candidates:
        raise DataError("snapshot observation trace has no usable temperature")
    return max(candidates, key=lambda item: item[0])


def interpolate_temperature(
    timestamps: list[datetime], values: list[Any], target: datetime
) -> float | None:
    usable = [
        (timestamp, float(value))
        for timestamp, value in zip(timestamps, values, strict=True)
        if value is not None
    ]
    before = [item for item in usable if item[0] <= target]
    after = [item for item in usable if item[0] >= target]
    if not before or not after:
        nearest_time, nearest_value = min(
            usable, key=lambda item: abs((item[0] - target).total_seconds())
        )
        if abs((nearest_time - target).total_seconds()) <= 3600:
            return nearest_value
        return None
    left_time, left_value = before[-1]
    right_time, right_value = after[0]
    if left_time == right_time:
        return left_value
    span = (right_time - left_time).total_seconds()
    if span <= 0 or span > 2 * 3600:
        return None
    fraction = (target - left_time).total_seconds() / span
    return left_value + fraction * (right_value - left_value)


def corrected_member_highs(
    snapshot: dict[str, Any]
) -> tuple[list[float], list[float], list[float], dict[str, int]]:
    observation = observed_temperature(snapshot)
    if observation is None:
        raise DataError("trajectory correction requires an observation")
    observed_at, current_temperature = observation
    observed_high = float(snapshot["distribution"]["observed_high_f"])
    window_end = parse_datetime(snapshot["event"]["window_end"]).astimezone(UTC)
    ensemble_record = trace_record(
        snapshot, lambda url: "ensemble-api.open-meteo.com" in url
    )
    if ensemble_record is None:
        raise DataError("snapshot has no ensemble trace")
    hourly = ensemble_record.get("payload", {}).get("hourly")
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise DataError("snapshot ensemble trace is malformed")
    timestamps = [
        parse_datetime(str(value)).astimezone(UTC) for value in hourly["time"]
    ]
    remaining_indexes = [
        index
        for index, timestamp in enumerate(timestamps)
        if observed_at < timestamp < window_end
    ]
    if not remaining_indexes:
        return [observed_high], [1.0], [0.0], {"fully_observed": 1}

    by_family: dict[str, list[tuple[float, float]]] = {}
    for family, (_, suffix) in ENSEMBLE_MODELS.items():
        family_rows: list[tuple[float, float]] = []
        fields = sorted(
            field
            for field in hourly
            if field.startswith("temperature_2m") and field.endswith(f"_{suffix}")
        )
        for field in fields:
            values = hourly.get(field)
            if not isinstance(values, list) or len(values) != len(timestamps):
                continue
            predicted_now = interpolate_temperature(timestamps, values, observed_at)
            remaining = [
                float(values[index])
                for index in remaining_indexes
                if values[index] is not None
            ]
            if predicted_now is None or not remaining:
                continue
            correction = current_temperature - predicted_now
            corrected_high = max(observed_high, max(remaining) + correction)
            family_rows.append((corrected_high, correction))
        if len(family_rows) >= 10:
            by_family[family] = family_rows
    if len(by_family) < 3:
        counts = ", ".join(f"{family}={len(rows)}" for family, rows in by_family.items())
        raise DataError(f"fewer than three usable corrected families ({counts or 'none'})")

    highs: list[float] = []
    weights: list[float] = []
    corrections: list[float] = []
    family_weight = 1.0 / len(by_family)
    for rows in by_family.values():
        highs.extend(high for high, _ in rows)
        corrections.extend(correction for _, correction in rows)
        weights.extend([family_weight / len(rows)] * len(rows))
    return highs, weights, corrections, {
        family: len(rows) for family, rows in by_family.items()
    }


def challenger_probabilities(
    snapshot: dict[str, Any]
) -> tuple[list[float], dict[str, Any]]:
    brackets = _stored_brackets(snapshot)
    observed_high = snapshot["distribution"].get("observed_high_f")
    if observed_high is None:
        raise DataError("trajectory correction requires an observed temperature")
    observed_floor = float(observed_high)
    highs, weights, corrections, family_counts = corrected_member_highs(snapshot)
    if family_counts == {"fully_observed": 1}:
        probabilities = [
            point_mass_bracket_probability(bracket, observed_floor) for bracket in brackets
        ]
        bandwidth = 1.0
    else:
        bandwidth = kernel_bandwidth(highs, weights)
        probabilities = [
            bracket_probability(bracket, highs, bandwidth, weights, observed_floor)
            for bracket in brackets
        ]
    if not math.isclose(sum(probabilities), 1.0, abs_tol=1e-9):
        raise DataError("trajectory-corrected probabilities do not sum to one")
    return probabilities, {
        "bandwidth_f": bandwidth,
        "member_count": len(highs),
        "family_counts": family_counts,
        "mean_member_correction_f": statistics.mean(corrections),
        "median_member_correction_f": statistics.median(corrections),
        "minimum_member_correction_f": min(corrections),
        "maximum_member_correction_f": max(corrections),
    }


def score_snapshot(
    snapshot: dict[str, Any], score_row: dict[str, str]
) -> dict[str, Any]:
    brackets = _stored_brackets(snapshot)
    tickers = [bracket.ticker for bracket in brackets]
    baseline_probabilities = [
        float(value) for value in snapshot["distribution"]["probabilities"]
    ]
    challenger, diagnostics = challenger_probabilities(snapshot)
    baseline_score = score_probabilities(
        tickers, baseline_probabilities, score_row["winner_ticker"]
    )
    challenger_score = score_probabilities(
        tickers, challenger, score_row["winner_ticker"]
    )
    return {
        "target_date": score_row["target_date"],
        "city": score_row["city"],
        "checkpoint": score_row["checkpoint"],
        "as_of": score_row["as_of"],
        "winner_ticker": score_row["winner_ticker"],
        **{f"baseline_{key}": value for key, value in baseline_score.items()},
        **{f"challenger_{key}": value for key, value in challenger_score.items()},
        "log_loss_delta": challenger_score["log_loss"] - baseline_score["log_loss"],
        "brier_delta": challenger_score["brier"] - baseline_score["brier"],
        "rps_delta": challenger_score["ranked_probability_score"]
        - baseline_score["ranked_probability_score"],
        **diagnostics,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"forecast_count": len(rows)}
    for model in ("baseline", "challenger"):
        covered = [row for row in rows if row[f"{model}_top_one_covered"]]
        result[f"{model}_log_loss"] = statistics.mean(
            row[f"{model}_log_loss"] for row in rows
        )
        result[f"{model}_brier"] = statistics.mean(
            row[f"{model}_brier"] for row in rows
        )
        result[f"{model}_rps"] = statistics.mean(
            row[f"{model}_ranked_probability_score"] for row in rows
        )
        result[f"{model}_winner_probability"] = statistics.mean(
            row[f"{model}_outcome_probability"] for row in rows
        )
        result[f"{model}_top_one_accuracy"] = (
            statistics.mean(float(row[f"{model}_top_one_correct"]) for row in covered)
            if covered
            else None
        )
    for metric in ("log_loss", "brier", "rps"):
        result[f"{metric}_delta"] = statistics.mean(
            row[f"{metric}_delta"] for row in rows
        )
    return result


def latest_per_event(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["target_date"], row["city"])].append(row)
    return [
        max(event_rows, key=lambda row: parse_datetime(row["as_of"]))
        for event_rows in grouped.values()
    ]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("backtest_data"))
    parser.add_argument("--cohort", default="pilot-v1")
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output/trajectory_correction")
    )
    args = parser.parse_args()
    report = args.report or latest_report(args.root, args.cohort)
    score_rows = load_baseline_rows(report)
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for score_row in score_rows:
        snapshot_path = (
            args.root
            / "cohorts"
            / args.cohort
            / "snapshots"
            / score_row["target_date"]
            / score_row["city"]
            / f'{score_row["checkpoint"]}.json.gz'
        )
        with gzip.open(snapshot_path, "rt", encoding="utf-8") as handle:
            snapshot = json.load(handle)
        try:
            rows.append(score_snapshot(snapshot, score_row))
        except DataError as exc:
            skipped.append({"snapshot": str(snapshot_path), "reason": str(exc)})
    if not rows:
        raise DataError("no observed snapshots could be scored")

    latest = latest_per_event(rows)
    by_checkpoint = {
        checkpoint: aggregate([row for row in rows if row["checkpoint"] == checkpoint])
        for checkpoint in sorted({row["checkpoint"] for row in rows})
    }
    summary = {
        "source_report": str(report),
        "method": (
            "Each ensemble member's future temperatures receive the full difference "
            "between the latest station temperature and that member's interpolated "
            "temperature at the same time."
        ),
        "all_observed_forecasts": aggregate(rows),
        "latest_per_event": aggregate(latest),
        "by_checkpoint": by_checkpoint,
        "calendar_dates": sorted({row["target_date"] for row in latest}),
        "skipped": skipped,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "forecast_scores.csv", rows)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    latest_summary = summary["latest_per_event"]
    print(
        f"Scored {len(rows)} observed forecasts across {len(latest)} independent events "
        f"and {len(summary['calendar_dates'])} dates."
    )
    print(
        "Latest per event: "
        f"baseline accuracy={latest_summary['baseline_top_one_accuracy']:.1%}, "
        f"challenger accuracy={latest_summary['challenger_top_one_accuracy']:.1%}"
    )
    print(
        "Paired challenger-minus-baseline: "
        f"log loss={latest_summary['log_loss_delta']:+.4f}, "
        f"Brier={latest_summary['brier_delta']:+.4f}, "
        f"RPS={latest_summary['rps_delta']:+.4f}"
    )
    print(f"Results written to {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
