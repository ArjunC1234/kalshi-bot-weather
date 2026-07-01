"""Explore whether archived NWS features are associated with forecast quality."""

from __future__ import annotations

import argparse
import csv
import gzip
import itertools
import json
import math
import random
import re
import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


FEATURE_LABELS = {
    "current_temp_f": "Current temperature",
    "temp_trend_f_per_hour": "Recent temperature trend",
    "dewpoint_f": "Dew point",
    "relative_humidity_pct": "Relative humidity",
    "wind_speed_mph": "Wind speed",
    "pressure_hpa": "Air pressure",
    "remaining_precip_pct": "Remaining forecast rain chance",
    "remaining_humidity_pct": "Remaining forecast humidity",
    "remaining_dewpoint_f": "Remaining forecast dew point",
    "remaining_wind_mph": "Remaining forecast wind",
}


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def c_to_f(value: float) -> float:
    return value * 9 / 5 + 32


def measurement(properties: dict[str, Any], key: str) -> float | None:
    item = properties.get(key)
    if not isinstance(item, dict) or item.get("value") is None:
        return None
    return float(item["value"])


def latest_report(root: Path, cohort: str) -> Path:
    reports = root / "cohorts" / cohort / "reports"
    candidates = sorted(
        path for path in reports.iterdir() if (path / "forecast_scores.csv").is_file()
    )
    if not candidates:
        raise FileNotFoundError(f"No evaluation reports found under {reports}")
    return candidates[-1]


def latest_baseline_rows(report: Path) -> list[dict[str, str]]:
    with (report / "forecast_scores.csv").open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["model"] == "full"]
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["target_date"], row["city"])].append(row)
    return [
        max(event_rows, key=lambda row: parse_datetime(row["as_of"]))
        for event_rows in grouped.values()
    ]


def trace_record(snapshot: dict[str, Any], predicate: Any) -> dict[str, Any] | None:
    return next((record for record in snapshot["http_trace"] if predicate(record["url"])), None)


def numeric_wind(value: object) -> float | None:
    numbers = re.findall(r"\d+(?:\.\d+)?", str(value))
    return max(map(float, numbers)) if numbers else None


def extract_observation_features(
    snapshot: dict[str, Any], as_of: datetime
) -> dict[str, float | None]:
    record = trace_record(snapshot, lambda url: "/observations" in url)
    if record is None:
        return {}
    observations: list[tuple[datetime, dict[str, Any]]] = []
    for feature in record["payload"].get("features", []):
        properties = feature.get("properties", {})
        try:
            timestamp = parse_datetime(properties["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if timestamp <= as_of and measurement(properties, "temperature") is not None:
            observations.append((timestamp, properties))
    if not observations:
        return {}
    observations.sort(key=lambda item: item[0])
    latest_at, latest = observations[-1]
    current_c = measurement(latest, "temperature")
    earliest = next(
        (
            item
            for item in observations
            if item[0] >= latest_at - timedelta(hours=2)
            and item[0] < latest_at
        ),
        None,
    )
    trend: float | None = None
    if earliest is not None and current_c is not None:
        earlier_c = measurement(earliest[1], "temperature")
        hours = (latest_at - earliest[0]).total_seconds() / 3600
        if earlier_c is not None and hours > 0:
            trend = (c_to_f(current_c) - c_to_f(earlier_c)) / hours
    wind_kmh = measurement(latest, "windSpeed")
    pressure_pa = measurement(latest, "barometricPressure")
    dewpoint_c = measurement(latest, "dewpoint")
    return {
        "current_temp_f": c_to_f(current_c) if current_c is not None else None,
        "temp_trend_f_per_hour": trend,
        "dewpoint_f": c_to_f(dewpoint_c) if dewpoint_c is not None else None,
        "relative_humidity_pct": measurement(latest, "relativeHumidity"),
        "wind_speed_mph": wind_kmh * 0.621371 if wind_kmh is not None else None,
        "pressure_hpa": pressure_pa / 100 if pressure_pa is not None else None,
    }


def extract_hourly_features(
    snapshot: dict[str, Any], as_of: datetime
) -> dict[str, float | None]:
    record = trace_record(snapshot, lambda url: url.endswith("/forecast/hourly"))
    if record is None:
        return {}
    window_end = parse_datetime(snapshot["event"]["window_end"])
    precipitation: list[float] = []
    humidity: list[float] = []
    dewpoints: list[float] = []
    winds: list[float] = []
    for period in record["payload"].get("properties", {}).get("periods", []):
        try:
            timestamp = parse_datetime(period["startTime"])
        except (KeyError, TypeError, ValueError):
            continue
        if not as_of <= timestamp < window_end:
            continue
        pop = period.get("probabilityOfPrecipitation", {}).get("value")
        rh = period.get("relativeHumidity", {}).get("value")
        dewpoint = period.get("dewpoint", {}).get("value")
        wind = numeric_wind(period.get("windSpeed"))
        if pop is not None:
            precipitation.append(float(pop))
        if rh is not None:
            humidity.append(float(rh))
        if dewpoint is not None:
            dewpoints.append(c_to_f(float(dewpoint)))
        if wind is not None:
            winds.append(wind)
    return {
        "remaining_precip_pct": max(precipitation) if precipitation else None,
        "remaining_humidity_pct": statistics.mean(humidity) if humidity else None,
        "remaining_dewpoint_f": statistics.mean(dewpoints) if dewpoints else None,
        "remaining_wind_mph": statistics.mean(winds) if winds else None,
    }


def rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index + 1
        while end < len(order) and values[order[end]] == values[order[index]]:
            end += 1
        average_rank = (index + end - 1) / 2
        for position in order[index:end]:
            result[position] = average_rank
        index = end
    return result


def pearson(left: list[float], right: list[float]) -> float:
    left_mean = statistics.mean(left)
    right_mean = statistics.mean(right)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right, strict=True)
    )
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left)
        * sum((value - right_mean) ** 2 for value in right)
    )
    return numerator / denominator if denominator else 0.0


def spearman(left: list[float], right: list[float]) -> float:
    return pearson(rank(left), rank(right))


def correlation_permutation_p(
    values: list[float], outcomes: list[float], observed: float, seed: int
) -> float:
    randomizer = random.Random(seed)
    extreme = 0
    trials = 20_000
    shuffled = outcomes.copy()
    for _ in range(trials):
        randomizer.shuffle(shuffled)
        if abs(spearman(values, shuffled)) >= abs(observed) - 1e-12:
            extreme += 1
    return (extreme + 1) / (trials + 1)


def correct_miss_test(values: list[float], correct: list[bool]) -> tuple[float, float]:
    correct_count = sum(correct)
    if correct_count in (0, len(correct)):
        return 0.0, 1.0
    observed = statistics.mean(
        value for value, is_correct in zip(values, correct, strict=True) if is_correct
    ) - statistics.mean(
        value for value, is_correct in zip(values, correct, strict=True) if not is_correct
    )
    standard_deviation = statistics.stdev(values)
    standardized = observed / standard_deviation if standard_deviation else 0.0
    absolute_observed = abs(observed)
    extreme = 0
    combinations = 0
    indexes = range(len(values))
    for selected in itertools.combinations(indexes, correct_count):
        selected_set = set(selected)
        difference = statistics.mean(values[index] for index in selected_set) - statistics.mean(
            values[index] for index in indexes if index not in selected_set
        )
        combinations += 1
        if abs(difference) >= absolute_observed - 1e-12:
            extreme += 1
    return standardized, extreme / combinations


def benjamini_hochberg(rows: list[dict[str, Any]], p_key: str, q_key: str) -> None:
    ordered = sorted(enumerate(rows), key=lambda item: item[1][p_key])
    running = 1.0
    for reverse_index in range(len(ordered) - 1, -1, -1):
        original_index, row = ordered[reverse_index]
        rank_value = reverse_index + 1
        running = min(running, row[p_key] * len(rows) / rank_value)
        rows[original_index][q_key] = running


def analyze_scope(records: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for feature, label in FEATURE_LABELS.items():
        usable = [record for record in records if record.get(feature) is not None]
        if len(usable) < 5:
            continue
        values = [float(record[feature]) for record in usable]
        probabilities = [float(record["winner_probability"]) for record in usable]
        correct = [bool(record["correct"]) for record in usable]
        rho = spearman(values, probabilities)
        standardized_difference, difference_p = correct_miss_test(values, correct)
        results.append(
            {
                "scope": scope,
                "feature": feature,
                "label": label,
                "n": len(usable),
                "spearman_with_winner_probability": rho,
                "spearman_p": correlation_permutation_p(values, probabilities, rho, len(results)),
                "correct_minus_miss_standardized": standardized_difference,
                "correct_miss_p": difference_p,
            }
        )
    benjamini_hochberg(results, "spearman_p", "spearman_q")
    benjamini_hochberg(results, "correct_miss_p", "correct_miss_q")
    return results


def load_records(root: Path, cohort: str, report: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for score in latest_baseline_rows(report):
        snapshot_path = (
            root
            / "cohorts"
            / cohort
            / "snapshots"
            / score["target_date"]
            / score["city"]
            / f'{score["checkpoint"]}.json.gz'
        )
        with gzip.open(snapshot_path, "rt", encoding="utf-8") as handle:
            snapshot = json.load(handle)
        as_of = parse_datetime(score["as_of"])
        record: dict[str, Any] = {
            "target_date": score["target_date"],
            "city": score["city"],
            "checkpoint": score["checkpoint"],
            "winner_probability": float(score["outcome_probability"]),
            "correct": score["top_one_correct"].lower() == "true",
        }
        record.update(extract_observation_features(snapshot, as_of))
        record.update(extract_hourly_features(snapshot, as_of))
        records.append(record)
    return records


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
        "--output",
        type=Path,
        default=Path("output/weather_feature_analysis.csv"),
    )
    args = parser.parse_args()
    report = args.report or latest_report(args.root, args.cohort)
    records = load_records(args.root, args.cohort, report)
    results = analyze_scope(records, "latest_per_event")
    results.extend(
        analyze_scope(
            [record for record in records if record["checkpoint"] == "t_plus_18h"],
            "t_plus_18h_only",
        )
    )
    write_csv(args.output, results)
    print(f"Analyzed {len(records)} independent city-day forecasts from {report.name}")
    for scope in ("latest_per_event", "t_plus_18h_only"):
        print(f"\n{scope}")
        scoped = [row for row in results if row["scope"] == scope]
        for row in sorted(
            scoped,
            key=lambda item: abs(item["spearman_with_winner_probability"]),
            reverse=True,
        )[:5]:
            print(
                f'{row["label"]}: n={row["n"]} '
                f'rho={row["spearman_with_winner_probability"]:+.2f} '
                f'q={row["spearman_q"]:.3f}; '
                f'correct-vs-miss={row["correct_minus_miss_standardized"]:+.2f} SD '
                f'q={row["correct_miss_q"]:.3f}'
            )
    print(f"\nFull results written to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
