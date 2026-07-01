"""Generate offline before/after reports for weather-model improvements."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from train_offline_model import (  # noqa: E402
    DEFAULT_CANDIDATES,
    PROBABILITY_FLOOR,
    aggregate_by_checkpoint,
    aggregate_by_model,
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
    "soft_floor_weather_floored",
    "family_centered",
    "soft_floor_family_centered",
    "market_midpoint",
    "trained_blend",
    "checkpoint_trained_blend",
    "trained_weather_blend",
    "checkpoint_trained_weather_blend",
    "trained_weather_hrrr_blend",
    "checkpoint_trained_weather_hrrr_blend",
    "regression_trained_weather_hrrr",
)
DEFAULT_MARGINS = (0.0, 0.02, 0.05, 0.10, 0.15)


def load_snapshot(root: Path, cohort: str, row: dict[str, Any]) -> dict[str, Any]:
    path = (
        root
        / "cohorts"
        / cohort
        / "snapshots"
        / str(row["target_date"])
        / str(row["city"])
        / f"{row['checkpoint']}.json.gz"
    )
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise DataError(f"snapshot is malformed: {path}")
    return payload


def boolish(value: Any) -> bool:
    return str(value).lower() == "true"


def parse_json_list(value: Any, label: str) -> list[Any]:
    parsed = json.loads(str(value))
    if not isinstance(parsed, list):
        raise DataError(f"{label} is not a JSON list")
    return parsed


def latest_model_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return latest_per_event(rows)


def build_before_after(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    scopes = {
        "all_forecasts": rows,
        "latest_per_event": latest_model_rows(rows),
    }
    for scope, scope_rows in scopes.items():
        for model, summary in aggregate_by_model(scope_rows).items():
            if model in DEFAULT_MODELS:
                output.append({"scope": scope, "model": model, **summary})
    for checkpoint, by_model in aggregate_by_checkpoint(rows).items():
        for model, summary in by_model.items():
            if model in DEFAULT_MODELS:
                output.append(
                    {
                        "scope": "checkpoint",
                        "checkpoint": checkpoint,
                        "model": model,
                        **summary,
                    }
                )
    return output


def market_asks(snapshot: dict[str, Any], tickers: list[str]) -> dict[str, float]:
    markets = snapshot.get("event", {}).get("markets")
    if not isinstance(markets, list):
        raise DataError("snapshot markets are malformed")
    by_ticker = {str(market.get("ticker")): market for market in markets}
    asks: dict[str, float] = {}
    for ticker in tickers:
        if ticker not in by_ticker:
            raise DataError(f"missing market quote for {ticker}")
        try:
            asks[ticker] = float(by_ticker[ticker]["yes_ask_dollars"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataError(f"malformed ask quote for {ticker}") from exc
    return asks


def ask_edge_rows(
    root: Path,
    cohort: str,
    score_rows: list[dict[str, Any]],
    margins: Iterable[float],
    fee_per_contract: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in score_rows:
        if row["model"] not in DEFAULT_MODELS:
            continue
        tickers = [str(value) for value in parse_json_list(row["tickers_json"], "tickers")]
        probabilities = [
            float(value)
            for value in parse_json_list(row["probabilities_json"], "probabilities")
        ]
        if len(tickers) != len(probabilities):
            raise DataError("tickers and probabilities do not align")
        snapshot = load_snapshot(root, cohort, row)
        asks = market_asks(snapshot, tickers)
        for margin in margins:
            for ticker, probability in zip(tickers, probabilities, strict=True):
                ask = asks[ticker]
                edge = probability - ask
                if edge < margin:
                    continue
                won = ticker == row["winner_ticker"]
                payout = 1.0 if won else 0.0
                pnl = payout - ask - fee_per_contract
                output.append(
                    {
                        "model": row["model"],
                        "margin": margin,
                        "city": row["city"],
                        "target_date": row["target_date"],
                        "checkpoint": row["checkpoint"],
                        "ticker": ticker,
                        "winner_ticker": row["winner_ticker"],
                        "probability": probability,
                        "yes_ask": ask,
                        "modeled_edge": edge,
                        "won": won,
                        "gross_pnl": pnl,
                        "stake": ask + fee_per_contract,
                    }
                )
    return output


def summarize_edge(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["model"]), float(row["margin"]))].append(row)
    output: list[dict[str, Any]] = []
    for (model, margin), group in sorted(grouped.items()):
        stake = sum(float(row["stake"]) for row in group)
        pnl = sum(float(row["gross_pnl"]) for row in group)
        output.append(
            {
                "model": model,
                "margin": margin,
                "trade_count": len(group),
                "hit_rate": statistics.mean(float(boolish(row["won"])) for row in group),
                "average_ask": statistics.mean(float(row["yes_ask"]) for row in group),
                "average_modeled_edge": statistics.mean(
                    float(row["modeled_edge"]) for row in group
                ),
                "gross_pnl": pnl,
                "stake": stake,
                "gross_roi": pnl / stake if stake else None,
            }
        )
    for model in DEFAULT_MODELS:
        for margin in DEFAULT_MARGINS:
            if not any(row["model"] == model and row["margin"] == margin for row in output):
                output.append(
                    {
                        "model": model,
                        "margin": margin,
                        "trade_count": 0,
                        "hit_rate": None,
                        "average_ask": None,
                        "average_modeled_edge": None,
                        "gross_pnl": 0.0,
                        "stake": 0.0,
                        "gross_roi": None,
                    }
                )
    return sorted(output, key=lambda row: (str(row["model"]), float(row["margin"])))


def leader_index(probabilities: list[float]) -> int | None:
    maximum = max(probabilities)
    leaders = [
        index
        for index, probability in enumerate(probabilities)
        if math.isclose(probability, maximum, rel_tol=0.0, abs_tol=1e-12)
    ]
    return leaders[0] if len(leaders) == 1 else None


def disagreement_rows(
    root: Path, cohort: str, score_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    full_rows = [row for row in score_rows if row["model"] == "full"]
    output: list[dict[str, Any]] = []
    for row in full_rows:
        snapshot = load_snapshot(root, cohort, row)
        distribution = snapshot["distribution"]
        tickers = [str(value) for value in parse_json_list(row["tickers_json"], "tickers")]
        weather = [
            float(value)
            for value in parse_json_list(row["probabilities_json"], "probabilities")
        ]
        market = next(
            (
                candidate
                for candidate in score_rows
                if candidate["model"] == "market_midpoint"
                and candidate["city"] == row["city"]
                and candidate["target_date"] == row["target_date"]
                and candidate["checkpoint"] == row["checkpoint"]
            ),
            None,
        )
        market_probs = (
            [
                float(value)
                for value in parse_json_list(market["probabilities_json"], "market probabilities")
            ]
            if market
            else []
        )
        weather_leader = leader_index(weather)
        market_leader = leader_index(market_probs) if market_probs else None
        winner_index = tickers.index(str(row["winner_ticker"]))
        observed = distribution.get("observed_high_f")
        observed_high = float(observed) if observed is not None else None
        nws_high = float(distribution["nws_high_f"])
        raw_consensus = float(distribution["raw_consensus_high_f"])
        member_highs = [float(value) for value in distribution.get("member_highs_f", [])]
        family_spread = max(member_highs) - min(member_highs) if member_highs else None
        flags = []
        if abs(nws_high - raw_consensus) >= 3.0:
            flags.append("nws_vs_ensemble_3f")
        if observed_high is not None and abs(observed_high - nws_high) >= 3.0:
            flags.append("observation_vs_nws_3f")
        if observed_high is not None and abs(observed_high - raw_consensus) >= 3.0:
            flags.append("observation_vs_ensemble_3f")
        if weather_leader is not None and market_leader is not None and weather_leader != market_leader:
            flags.append("weather_market_top_disagree")
        output.append(
            {
                "city": row["city"],
                "target_date": row["target_date"],
                "checkpoint": row["checkpoint"],
                "winner_ticker": row["winner_ticker"],
                "weather_correct": row["top_one_correct"],
                "weather_winner_probability": row["outcome_probability"],
                "market_winner_probability": (
                    market["outcome_probability"] if market else None
                ),
                "nws_high_f": nws_high,
                "observed_high_f": observed_high,
                "raw_consensus_high_f": raw_consensus,
                "center_shift_f": distribution["center_shift_f"],
                "bandwidth_f": distribution["bandwidth_f"],
                "family_spread_f": family_spread,
                "weather_leader_ticker": (
                    tickers[weather_leader] if weather_leader is not None else None
                ),
                "market_leader_ticker": (
                    tickers[market_leader] if market_leader is not None else None
                ),
                "winner_index": winner_index,
                "weather_leader_index": weather_leader,
                "flags": ",".join(flags),
            }
        )
    return output


def summarize_disagreements(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for flag in str(row["flags"]).split(","):
            if flag:
                grouped[(flag, str(row["checkpoint"]))].append(row)
    output: list[dict[str, Any]] = []
    for (flag, checkpoint), group in sorted(grouped.items()):
        output.append(
            {
                "flag": flag,
                "checkpoint": checkpoint,
                "count": len(group),
                "weather_accuracy": statistics.mean(
                    float(boolish(row["weather_correct"])) for row in group
                ),
                "mean_weather_winner_probability": statistics.mean(
                    float(row["weather_winner_probability"]) for row in group
                ),
            }
        )
    return output


def aggregate_lookup(rows: list[dict[str, Any]], scope: str) -> dict[str, dict[str, Any]]:
    return {
        row["model"]: row
        for row in rows
        if row["scope"] == scope and row.get("checkpoint") in (None, "")
    }


def plot_comparison(before_after: list[dict[str, Any]], edge_summary: list[dict[str, Any]], output: Path) -> None:
    latest = aggregate_lookup(before_after, "latest_per_event")
    models = [model for model in DEFAULT_MODELS if model in latest]
    labels = [model.replace("_", " ") for model in models]
    figure, axes = plt.subplots(1, 3, figsize=(17, 5), constrained_layout=True)
    positions = list(range(len(models)))
    axes[0].bar(positions, [latest[model]["log_loss"] for model in models], color="#247BA0")
    axes[0].set_title("Latest log loss")
    axes[0].set_ylabel("Lower is better")
    axes[0].set_xticks(positions, labels, rotation=35, ha="right")
    axes[0].grid(axis="y", alpha=0.2)

    axes[1].bar(
        positions,
        [(latest[model]["top_one_accuracy"] or 0.0) * 100.0 for model in models],
        color="#167D8D",
    )
    axes[1].set_title("Latest top-one accuracy")
    axes[1].set_ylabel("Percent")
    axes[1].set_ylim(0, 105)
    axes[1].set_xticks(positions, labels, rotation=35, ha="right")
    axes[1].grid(axis="y", alpha=0.2)

    margin_rows = [row for row in edge_summary if math.isclose(float(row["margin"]), 0.05)]
    roi_by_model = {row["model"]: row["gross_roi"] for row in margin_rows}
    axes[2].bar(
        positions,
        [
            (roi_by_model.get(model) if roi_by_model.get(model) is not None else 0.0) * 100
            for model in models
        ],
        color="#D97706",
    )
    axes[2].axhline(0, color="#111827", linewidth=0.8)
    axes[2].set_title("Ask-edge gross ROI at 5% margin")
    axes[2].set_ylabel("Percent")
    axes[2].set_xticks(positions, labels, rotation=35, ha="right")
    axes[2].grid(axis="y", alpha=0.2)
    figure.suptitle("Offline model improvement report", fontweight="bold")
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(figure)


def plot_checkpoint_accuracy(summary: dict[str, Any], output: Path) -> None:
    checkpoints = [
        checkpoint
        for checkpoint in ("t_minus_6h", "t_plus_10h", "t_plus_14h", "t_plus_18h")
        if checkpoint in summary["by_checkpoint"]
    ]
    labels = [
        checkpoint.replace("t_minus_", "T-").replace("t_plus_", "T+").replace("h", "h")
        for checkpoint in checkpoints
    ]
    models = ("full", "soft_floor_weather", "market_midpoint", "checkpoint_trained_blend")
    display = {
        "full": "Weather baseline",
        "soft_floor_weather": "Soft-floor weather",
        "market_midpoint": "Market midpoint",
        "checkpoint_trained_blend": "Checkpoint blend",
    }
    colors = {
        "full": "#247BA0",
        "soft_floor_weather": "#167D8D",
        "market_midpoint": "#E07A5F",
        "checkpoint_trained_blend": "#111827",
    }
    figure, axes = plt.subplots(2, 1, figsize=(12, 9), sharex=True, constrained_layout=True)
    positions = list(range(len(checkpoints)))
    width = 0.18
    offsets = {
        "full": -1.5 * width,
        "soft_floor_weather": -0.5 * width,
        "market_midpoint": 0.5 * width,
        "checkpoint_trained_blend": 1.5 * width,
    }
    for model in models:
        accuracies = [
            (
                summary["by_checkpoint"][checkpoint]
                .get(model, {})
                .get("top_one_accuracy")
            )
            for checkpoint in checkpoints
        ]
        losses = [
            summary["by_checkpoint"][checkpoint].get(model, {}).get("log_loss")
            for checkpoint in checkpoints
        ]
        axes[0].bar(
            [position + offsets[model] for position in positions],
            [(value or 0.0) * 100.0 for value in accuracies],
            width,
            label=display[model],
            color=colors[model],
        )
        axes[1].plot(
            positions,
            losses,
            marker="o",
            linewidth=2,
            label=display[model],
            color=colors[model],
        )
    axes[0].set_title("Top-one accuracy by forecast checkpoint")
    axes[0].set_ylabel("Accuracy (%)")
    axes[0].set_ylim(0, 105)
    axes[0].legend(frameon=False, ncol=2)
    axes[1].set_title("Log loss by forecast checkpoint")
    axes[1].set_ylabel("Lower is better")
    axes[1].set_xlabel("Checkpoint")
    axes[1].set_xticks(positions, labels)
    for axis in axes:
        axis.grid(axis="y", alpha=0.2)
        axis.spines[["top", "right"]].set_visible(False)
    for position, checkpoint in zip(positions, checkpoints, strict=True):
        count = summary["by_checkpoint"][checkpoint]["full"]["forecast_count"]
        axes[0].text(position, 102, f"n={count}", ha="center", va="top", fontsize=9)
    figure.suptitle("Accuracy and probability quality by time of day", fontweight="bold")
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(figure)


def parse_margins(value: str) -> tuple[float, ...]:
    return tuple(float(part.strip()) for part in value.split(",") if part.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("backtest_data"))
    parser.add_argument("--cohort", default="pilot-v1")
    parser.add_argument("--output-dir", type=Path, default=Path("output/model_improvement_report"))
    parser.add_argument("--candidates", default=",".join(DEFAULT_CANDIDATES))
    parser.add_argument("--grid-step", type=float, default=0.05)
    parser.add_argument("--regularization", type=float, default=0.01)
    parser.add_argument("--min-train-events", type=int, default=4)
    parser.add_argument("--probability-floor", type=float, default=PROBABILITY_FLOOR)
    parser.add_argument("--margins", default="0,0.02,0.05,0.10,0.15")
    parser.add_argument("--fee-per-contract", type=float, default=0.0)
    args = parser.parse_args()

    examples = load_examples(args.root, args.cohort)
    candidate_names = parse_candidates(args.candidates)
    validate_candidates(examples, candidate_names)
    score_rows, weight_rows = expanding_window_scores(
        examples,
        candidate_names,
        args.grid_step,
        args.regularization,
        args.min_train_events,
        args.probability_floor,
    )
    before_after = build_before_after(score_rows)
    edge_rows = ask_edge_rows(
        args.root,
        args.cohort,
        score_rows,
        parse_margins(args.margins),
        args.fee_per_contract,
    )
    edge_summary = summarize_edge(edge_rows)
    diagnostics = disagreement_rows(args.root, args.cohort, score_rows)
    diagnostic_summary = summarize_disagreements(diagnostics)

    summary = {
        "schema_version": 1,
        "cohort": args.cohort,
        "generated_at": datetime.now(UTC).isoformat(),
        "settled_forecasts": len(examples),
        "settled_events": len({example.event_key for example in examples}),
        "candidate_distributions": list(candidate_names),
        "probability_floor": args.probability_floor,
        "soft_floor_default_mass": 0.02,
        "all_forecasts": aggregate_by_model(score_rows),
        "latest_per_event": aggregate_by_model(latest_model_rows(score_rows)),
        "by_checkpoint": aggregate_by_checkpoint(score_rows),
        "ask_edge_summary": edge_summary,
        "disagreement_summary": diagnostic_summary,
        "notes": [
            "All variants are offline challengers; active collection is unchanged.",
            "Ask-edge simulation uses archived YES ask prices and gross PnL before fees by default.",
            "Small sample results are directional, not promotion evidence.",
        ],
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "before_after.csv", before_after)
    write_csv(args.output_dir / "weights_by_date.csv", weight_rows)
    write_csv(args.output_dir / "ask_edge_trades.csv", edge_rows)
    write_csv(args.output_dir / "ask_edge_simulation.csv", edge_summary)
    write_csv(args.output_dir / "disagreement_diagnostics.csv", diagnostics)
    write_csv(args.output_dir / "disagreement_summary.csv", diagnostic_summary)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    plot_comparison(before_after, edge_summary, args.output_dir / "comparison.png")
    plot_checkpoint_accuracy(summary, args.output_dir / "checkpoint_accuracy.png")

    latest = summary["latest_per_event"]
    print(
        f"Generated improvement report for {summary['settled_events']} events "
        f"and {summary['settled_forecasts']} forecasts."
    )
    for model in (
        "full",
        "soft_floor_weather",
        "soft_floor_weather_floored",
        "checkpoint_trained_blend",
        "trained_weather_hrrr_blend",
        "checkpoint_trained_weather_hrrr_blend",
    ):
        if model in latest:
            row = latest[model]
            print(
                f"{model}: acc={row['top_one_accuracy']:.1%} "
                f"log={row['log_loss']:.4f} zeros={row['zero_probability_count']}"
            )
    print(f"Results written to {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
