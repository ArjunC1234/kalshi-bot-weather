"""Plot how forecast checkpoint timing relates to settled-event performance."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


CHECKPOINTS = (
    ("t_minus_6h", "T-6h\nPrevious 18:00"),
    ("t_plus_6h", "T+6h\n06:00"),
    ("t_plus_10h", "T+10h\n10:00"),
    ("t_plus_14h", "T+14h\n14:00"),
    ("t_plus_18h", "T+18h\n18:00"),
)


def latest_report(root: Path, cohort: str) -> Path:
    reports = root / "cohorts" / cohort / "reports"
    candidates = sorted(
        path for path in reports.iterdir() if (path / "forecast_scores.csv").is_file()
    )
    if not candidates:
        raise FileNotFoundError(f"No evaluation reports found under {reports}")
    return candidates[-1]


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        )
        / denominator
    )
    return center - margin, center + margin


def load_baseline_scores(report: Path) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    with (report / "forecast_scores.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["model"] != "full":
                continue
            grouped[row["checkpoint"]].append(
                {
                    "event": f'{row["target_date"]} {row["city"].upper()}',
                    "probability": float(row["outcome_probability"]),
                    "correct": row["top_one_correct"].lower() == "true",
                }
            )
    return grouped


def plot(grouped: dict[str, list[dict[str, object]]], output: Path) -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.titleweight": "bold",
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    figure, (probability_ax, accuracy_ax) = plt.subplots(
        2,
        1,
        figsize=(11, 9),
        sharex=True,
        gridspec_kw={"height_ratios": [1.25, 1]},
    )

    positions = list(range(len(CHECKPOINTS)))
    means: list[float] = []
    for position, (checkpoint, _) in zip(positions, CHECKPOINTS, strict=True):
        rows = grouped.get(checkpoint, [])
        probabilities = [float(row["probability"]) * 100 for row in rows]
        if probabilities:
            offsets = [
                0.0 if len(rows) == 1 else -0.12 + 0.24 * index / (len(rows) - 1)
                for index in range(len(rows))
            ]
            probability_ax.scatter(
                [position + offset for offset in offsets],
                probabilities,
                s=55,
                color="#247BA0",
                edgecolor="white",
                linewidth=0.8,
                alpha=0.82,
                zorder=3,
            )
            mean = sum(probabilities) / len(probabilities)
            means.append(mean)
            probability_ax.scatter(
                position,
                mean,
                marker="D",
                s=90,
                color="#D1495B",
                edgecolor="white",
                linewidth=0.9,
                zorder=4,
            )
            probability_ax.text(
                position,
                min(98, mean + 7),
                f"mean {mean:.0f}%\nn={len(rows)}",
                ha="center",
                va="bottom",
                fontsize=9,
                color="#7A2635",
            )
        else:
            means.append(float("nan"))
            probability_ax.text(
                position,
                48,
                "No data",
                ha="center",
                color="#777777",
                fontsize=9,
            )

    probability_ax.plot(positions, means, color="#D1495B", linewidth=2, alpha=0.8)
    probability_ax.set_ylim(0, 105)
    probability_ax.set_ylabel("Probability assigned to winner (%)")
    probability_ax.set_title("Probability assigned to the bracket that eventually settled")
    probability_ax.grid(axis="y", color="#D9E2E8", linewidth=0.8)
    probability_ax.text(
        0.01,
        0.02,
        "Blue circles: individual forecasts   Red diamonds: checkpoint mean",
        transform=probability_ax.transAxes,
        fontsize=9,
        color="#44515A",
    )

    accuracies: list[float] = []
    lower_errors: list[float] = []
    upper_errors: list[float] = []
    counts: list[int] = []
    for checkpoint, _ in CHECKPOINTS:
        rows = grouped.get(checkpoint, [])
        total = len(rows)
        successes = sum(bool(row["correct"]) for row in rows)
        accuracy = successes / total if total else 0.0
        lower, upper = wilson_interval(successes, total)
        accuracies.append(accuracy * 100)
        lower_errors.append((accuracy - lower) * 100)
        upper_errors.append((upper - accuracy) * 100)
        counts.append(total)

    colors = ["#A8B6BF" if count == 0 else "#2A9D8F" for count in counts]
    accuracy_ax.bar(positions, accuracies, width=0.62, color=colors, zorder=2)
    for position, accuracy, low, high, count in zip(
        positions,
        accuracies,
        lower_errors,
        upper_errors,
        counts,
        strict=True,
    ):
        if count:
            accuracy_ax.errorbar(
                position,
                accuracy,
                yerr=[[low], [high]],
                fmt="none",
                ecolor="#1D3B45",
                capsize=5,
                linewidth=1.4,
                zorder=3,
            )
            accuracy_ax.text(
                position,
                min(103, accuracy + 5),
                f"{accuracy:.0f}% ({sum(bool(row['correct']) for row in grouped[CHECKPOINTS[position][0]])}/{count})",
                ha="center",
                va="bottom",
                fontsize=9,
                fontweight="bold",
            )

    accuracy_ax.set_ylim(0, 110)
    accuracy_ax.set_ylabel("Top-one accuracy (%)")
    accuracy_ax.set_title("Was the highest-probability bracket correct? (95% Wilson intervals)")
    accuracy_ax.grid(axis="y", color="#D9E2E8", linewidth=0.8, zorder=0)
    accuracy_ax.set_xticks(positions, [label for _, label in CHECKPOINTS])
    accuracy_ax.set_xlabel("Checkpoint relative to fixed-standard-time climate-day start")

    settled_events = len(
        {
            str(row["event"])
            for rows in grouped.values()
            for row in rows
        }
    )
    figure.suptitle(
        "Later snapshots look stronger, but the current sample is very small",
        fontsize=16,
        fontweight="bold",
        color="#19323C",
        y=0.985,
    )
    figure.text(
        0.5,
        0.955,
        f"Baseline model, {settled_events} settled city-days; checkpoint samples overlap and are not independent",
        ha="center",
        fontsize=10,
        color="#52636D",
    )
    figure.tight_layout(rect=(0, 0, 1, 0.92), h_pad=1.25)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=180, facecolor="#F8FAFB")
    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("backtest_data"))
    parser.add_argument("--cohort", default="pilot-v1")
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/time_of_day_accuracy_test.png"),
    )
    args = parser.parse_args()
    report = args.report or latest_report(args.root, args.cohort)
    grouped = load_baseline_scores(report)
    plot(grouped, args.output)
    print(f"Graph written to {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
