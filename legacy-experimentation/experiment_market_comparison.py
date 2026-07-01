"""Compare archived weather forecasts with point-in-time Kalshi market prices."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from weather_backtest import _stored_brackets, score_probabilities  # noqa: E402
from weather_probabilities import DataError, parse_datetime  # noqa: E402


MODEL_NAMES = ("weather_model", "market_midpoint", "market_ask", "market_last")
CHECKPOINT_ORDER = ("t_minus_6h", "t_plus_6h", "t_plus_10h", "t_plus_14h", "t_plus_18h")


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


def normalize(values: list[float], label: str) -> list[float]:
    total = sum(values)
    if total <= 0:
        raise DataError(f"{label} quotes have no positive probability mass")
    return [value / total for value in values]


def quote_distributions(
    snapshot: dict[str, Any], tickers: list[str]
) -> tuple[dict[str, list[float]], dict[str, float]]:
    markets = snapshot.get("event", {}).get("markets")
    if not isinstance(markets, list):
        raise DataError("snapshot event markets are malformed")
    by_ticker = {str(market.get("ticker")): market for market in markets}
    if set(tickers) != set(by_ticker):
        raise DataError("snapshot quote tickers do not match forecast brackets")

    bids: list[float] = []
    asks: list[float] = []
    lasts: list[float] = []
    for ticker in tickers:
        market = by_ticker[ticker]
        try:
            bids.append(float(market["yes_bid_dollars"]))
            asks.append(float(market["yes_ask_dollars"]))
            lasts.append(float(market["last_price_dollars"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise DataError(f"snapshot has malformed quotes for {ticker}") from exc
    midpoints = [(bid + ask) / 2 for bid, ask in zip(bids, asks, strict=True)]
    return (
        {
            "market_midpoint": normalize(midpoints, "midpoint"),
            "market_ask": normalize(asks, "ask"),
            "market_last": normalize(lasts, "last trade"),
        },
        {
            "raw_midpoint_sum": sum(midpoints),
            "raw_ask_sum": sum(asks),
            "mean_bid_ask_spread": statistics.mean(
                ask - bid for bid, ask in zip(bids, asks, strict=True)
            ),
            "maximum_bid_ask_spread": max(
                ask - bid for bid, ask in zip(bids, asks, strict=True)
            ),
        },
    )


def score_snapshot(snapshot: dict[str, Any], row: dict[str, str]) -> dict[str, Any]:
    brackets = _stored_brackets(snapshot)
    tickers = [bracket.ticker for bracket in brackets]
    weather_probabilities = [
        float(value) for value in snapshot["distribution"]["probabilities"]
    ]
    quote_probabilities, diagnostics = quote_distributions(snapshot, tickers)
    probabilities = {"weather_model": weather_probabilities, **quote_probabilities}
    scores = {
        name: score_probabilities(tickers, values, row["winner_ticker"])
        for name, values in probabilities.items()
    }
    output: dict[str, Any] = {
        "target_date": row["target_date"],
        "city": row["city"],
        "checkpoint": row["checkpoint"],
        "as_of": row["as_of"],
        "winner_ticker": row["winner_ticker"],
        **diagnostics,
    }
    for name, score in scores.items():
        output.update({f"{name}_{key}": value for key, value in score.items()})
    for market_name in MODEL_NAMES[1:]:
        market_log_loss = (
            scores[market_name]["log_loss"]
            if scores[market_name]["log_loss"] is not None
            else -math.log(max(float(scores[market_name]["outcome_probability"]), 1e-12))
        )
        weather_log_loss = (
            scores["weather_model"]["log_loss"]
            if scores["weather_model"]["log_loss"] is not None
            else -math.log(max(float(scores["weather_model"]["outcome_probability"]), 1e-12))
        )
        output[f"{market_name}_log_loss_delta"] = (
            market_log_loss - weather_log_loss
        )
        output[f"{market_name}_brier_delta"] = (
            scores[market_name]["brier"] - scores["weather_model"]["brier"]
        )
        output[f"{market_name}_rps_delta"] = (
            scores[market_name]["ranked_probability_score"]
            - scores["weather_model"]["ranked_probability_score"]
        )
    return output


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "forecast_count": len(rows),
        "mean_bid_ask_spread": statistics.mean(
            row["mean_bid_ask_spread"] for row in rows
        ),
        "mean_raw_midpoint_sum": statistics.mean(row["raw_midpoint_sum"] for row in rows),
        "mean_raw_ask_sum": statistics.mean(row["raw_ask_sum"] for row in rows),
    }
    for name in MODEL_NAMES:
        covered = [row for row in rows if row[f"{name}_top_one_covered"]]
        capped_log_losses = [
            (
                row[f"{name}_log_loss"]
                if row[f"{name}_log_loss"] is not None
                else -math.log(max(float(row[f"{name}_outcome_probability"]), 1e-12))
            )
            for row in rows
        ]
        result[name] = {
            "log_loss": statistics.mean(capped_log_losses),
            "zero_probability_count": sum(
                bool(row[f"{name}_zero_probability"]) for row in rows
            ),
            "brier": statistics.mean(row[f"{name}_brier"] for row in rows),
            "rps": statistics.mean(row[f"{name}_ranked_probability_score"] for row in rows),
            "winner_probability": statistics.mean(
                row[f"{name}_outcome_probability"] for row in rows
            ),
            "top_one_accuracy": (
                statistics.mean(float(row[f"{name}_top_one_correct"]) for row in covered)
                if covered
                else None
            ),
            "top_one_coverage": len(covered) / len(rows),
        }
    for market_name in MODEL_NAMES[1:]:
        result[f"{market_name}_minus_weather"] = {
            metric: statistics.mean(row[f"{market_name}_{metric}_delta"] for row in rows)
            for metric in ("log_loss", "brier", "rps")
        }
    return result


def latest_per_event(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["target_date"], row["city"])].append(row)
    return [
        max(event_rows, key=lambda row: parse_datetime(row["as_of"]))
        for event_rows in grouped.values()
    ]


def plot_checkpoint_comparison(
    by_checkpoint: dict[str, dict[str, Any]], output: Path
) -> None:
    checkpoints = [name for name in CHECKPOINT_ORDER if name in by_checkpoint]
    labels = [name.replace("t_minus_", "T-").replace("t_plus_", "T+").replace("h", "h") for name in checkpoints]
    positions = list(range(len(checkpoints)))
    width = 0.36
    figure, (loss_ax, accuracy_ax) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    colors = {"weather_model": "#247BA0", "market_midpoint": "#E07A5F"}
    display = {"weather_model": "Weather model", "market_midpoint": "Kalshi midpoint"}
    for offset, name in ((-width / 2, "weather_model"), (width / 2, "market_midpoint")):
        losses = [by_checkpoint[checkpoint][name]["log_loss"] for checkpoint in checkpoints]
        accuracies = [
            by_checkpoint[checkpoint][name]["top_one_accuracy"] * 100
            for checkpoint in checkpoints
        ]
        loss_ax.bar(
            [position + offset for position in positions],
            losses,
            width,
            color=colors[name],
            label=display[name],
        )
        accuracy_ax.bar(
            [position + offset for position in positions],
            accuracies,
            width,
            color=colors[name],
        )
    loss_ax.set_ylabel("Log loss (lower is better)")
    loss_ax.set_title("Probability quality by collection checkpoint")
    loss_ax.legend(frameon=False)
    accuracy_ax.set_ylabel("Top-one accuracy (%)")
    accuracy_ax.set_ylim(0, 105)
    accuracy_ax.set_title("Highest-probability bracket accuracy")
    accuracy_ax.set_xticks(positions, labels)
    accuracy_ax.set_xlabel("Checkpoint")
    for axis in (loss_ax, accuracy_ax):
        axis.grid(axis="y", color="#D9E2E8", linewidth=0.8, zorder=0)
        axis.spines[["top", "right"]].set_visible(False)
    for position, checkpoint in zip(positions, checkpoints, strict=True):
        accuracy_ax.text(
            position,
            102,
            f"n={by_checkpoint[checkpoint]['forecast_count']}",
            ha="center",
            va="top",
            fontsize=9,
            color="#56636B",
        )
    figure.suptitle(
        "Weather model versus point-in-time Kalshi market consensus",
        fontsize=15,
        fontweight="bold",
    )
    figure.tight_layout(rect=(0, 0, 1, 0.96))
    figure.savefig(output, dpi=180, facecolor="white")
    plt.close(figure)


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
    parser.add_argument("--output-dir", type=Path, default=Path("output/market_comparison"))
    args = parser.parse_args()
    report = args.report or latest_report(args.root, args.cohort)
    rows: list[dict[str, Any]] = []
    for score_row in load_baseline_rows(report):
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
        rows.append(score_snapshot(snapshot, score_row))
    latest = latest_per_event(rows)
    by_checkpoint = {
        checkpoint: aggregate([row for row in rows if row["checkpoint"] == checkpoint])
        for checkpoint in CHECKPOINT_ORDER
        if any(row["checkpoint"] == checkpoint for row in rows)
    }
    summary = {
        "source_report": str(report),
        "primary_market_method": (
            "Normalize the midpoint of each archived YES bid and YES ask across the "
            "mutually exclusive brackets."
        ),
        "all_forecasts": aggregate(rows),
        "latest_per_event": aggregate(latest),
        "by_checkpoint": by_checkpoint,
        "calendar_dates": sorted({row["target_date"] for row in latest}),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "forecast_scores.csv", rows)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    plot_checkpoint_comparison(by_checkpoint, args.output_dir / "comparison.png")

    latest_summary = summary["latest_per_event"]
    weather = latest_summary["weather_model"]
    market = latest_summary["market_midpoint"]
    print(
        f"Compared {len(rows)} forecasts across {len(latest)} independent events "
        f"and {len(summary['calendar_dates'])} dates."
    )
    print(
        f"Latest per event accuracy: weather={weather['top_one_accuracy']:.1%}, "
        f"market={market['top_one_accuracy']:.1%}"
    )
    print(
        f"Latest per event log loss: weather={weather['log_loss']:.4f}, "
        f"market={market['log_loss']:.4f}"
    )
    print(f"Results written to {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
