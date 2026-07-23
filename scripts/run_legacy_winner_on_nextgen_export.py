"""Run the best legacy-experimentation strategy on a next-gen local export.

This is an adapter, not a new model implementation. It preserves the legacy
winner's semantics:

* model: checkpoint_trained_weather_hrrr_blend
* strategy: top_one_only
* min EV: 0.02 after taker fee
* sizing: Kelly with longshot cap from the winning legacy run

The next-gen export does not contain the old immutable legacy snapshot object or
the raw ensemble member highs. To bridge that gap, the adapter reconstructs the
legacy source-family distributions from normalized weather fields in the export.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = REPO_ROOT / "legacy-experimentation"
if str(LEGACY_ROOT) not in sys.path:
    sys.path.insert(0, str(LEGACY_ROOT))

from strategy_simulator import (  # noqa: E402
    choose_contracts,
    kalshi_fee,
    top_one_ticker,
)
from train_offline_model import (  # noqa: E402
    PROBABILITY_FLOOR,
    WEATHER_CANDIDATES,
    ForecastExample,
    apply_probability_floor,
    blend_probabilities,
    fit_blend_weights,
    load_examples,
    normalize,
    soft_observation_floor_probabilities,
    staged_hrrr_probabilities,
)


MODEL_NAME = "checkpoint_trained_weather_hrrr_blend"
STRATEGY = "top_one_only"


def read_table(export: Path, table: str) -> list[dict[str, Any]]:
    gz = export / f"{table}.json.gz"
    js = export / f"{table}.json"
    csv_path = export / f"{table}.csv"
    if gz.exists():
        with gzip.open(gz, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    if js.exists():
        return json.loads(js.read_text(encoding="utf-8-sig"))
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    return []


def optional_float(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_time(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def bracket_dicts(event: dict[str, Any], market_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw = event.get("metadata", {}).get("brackets") if isinstance(event.get("metadata"), dict) else None
    if isinstance(raw, list) and raw:
        rows = raw
    else:
        rows = market_rows
    output = []
    indexed_rows = list(enumerate(rows))
    for index, row in sorted(
        indexed_rows,
        key=lambda item: int(float(item[1].get("bracket_index", item[0]) or item[0])),
    ):
        output.append(
            {
                "ticker": str(row.get("ticker") or row.get("market_ticker")),
                "label": str(row.get("label") or row.get("bracket_label") or ""),
                "lower": (
                    None
                    if row.get("lower_f") in (None, "")
                    else int(float(row.get("lower_f")))
                )
                if "lower_f" in row
                else (
                    None
                    if row.get("bracket_lower_f") in (None, "")
                    else int(float(row.get("bracket_lower_f")))
                ),
                "upper": (
                    None
                    if row.get("upper_f") in (None, "")
                    else int(float(row.get("upper_f")))
                )
                if "upper_f" in row
                else (
                    None
                    if row.get("bracket_upper_f") in (None, "")
                    else int(float(row.get("bracket_upper_f")))
                ),
            }
        )
    return output


def normal_cdf(value: float, center: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((value - center) / (sigma * math.sqrt(2.0))))


def temperature_distribution(
    brackets: list[dict[str, Any]],
    center: float,
    sigma: float,
    label: str,
) -> tuple[float, ...]:
    sigma = max(0.75, sigma)
    values = []
    for bracket in brackets:
        lower = bracket.get("lower")
        upper = bracket.get("upper")
        lower_edge = float("-inf") if lower is None else float(lower) - 0.5
        upper_edge = float("inf") if upper is None else float(upper) + 0.5
        low = 0.0 if math.isinf(lower_edge) else normal_cdf(lower_edge, center, sigma)
        high = 1.0 if math.isinf(upper_edge) else normal_cdf(upper_edge, center, sigma)
        values.append(max(0.0, high - low))
    return apply_probability_floor(normalize(values, label), PROBABILITY_FLOOR)


def hrrr_top3_distribution(
    brackets: list[dict[str, Any]],
    hrrr_high_f: float | None,
    fallback: tuple[float, ...],
) -> tuple[float, ...]:
    if hrrr_high_f is None:
        return fallback
    center_probs = temperature_distribution(brackets, hrrr_high_f, 0.9, "hrrr")
    top = max(range(len(center_probs)), key=lambda index: center_probs[index])
    weights = [0.001] * len(center_probs)
    for index, mass in ((top, 0.70), (top - 1, 0.15), (top + 1, 0.15)):
        if 0 <= index < len(weights):
            weights[index] += mass
    return apply_probability_floor(normalize(weights, "hrrr_top3_rerank"), PROBABILITY_FLOOR)


def market_distribution(market_rows: list[dict[str, Any]]) -> tuple[float, ...]:
    values = [
        optional_float(row.get("normalized_market_midpoint_probability"))
        for row in sorted(market_rows, key=lambda r: int(float(r.get("bracket_index") or 0)))
    ]
    if not values or any(value is None for value in values):
        values = [
            optional_float(row.get("yes_midpoint"))
            for row in sorted(market_rows, key=lambda r: int(float(r.get("bracket_index") or 0)))
        ]
    if not values or any(value is None for value in values):
        return tuple(1.0 / len(market_rows) for _ in market_rows)
    return normalize([float(value) for value in values], "market_midpoint")


def build_new_examples(
    export: Path,
    start: str,
    end: str,
    checkpoints: set[str] | None,
) -> list[ForecastExample]:
    events = read_table(export, "events")
    weather = read_table(export, "weather_snapshots")
    markets = read_table(export, "market_snapshots")
    settlements = read_table(export, "settlements")
    settlement_by_event = {str(row["event_ticker"]): row for row in settlements}
    events_by_key = {
        (str(row["event_ticker"]), str(row.get("snapshot_time_utc"))): row for row in events
    }
    market_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in markets:
        target = str(row.get("target_date", ""))[:10]
        if start <= target <= end:
            market_by_key[(str(row["event_ticker"]), str(row["snapshot_time_utc"]))].append(row)

    examples: list[ForecastExample] = []
    for row in weather:
        target = str(row.get("target_date", ""))[:10]
        if not (start <= target <= end):
            continue
        checkpoint_label = str(row.get("checkpoint_label") or "")
        if checkpoints is not None and checkpoint_label not in checkpoints:
            continue
        event_ticker = str(row["event_ticker"])
        snapshot_time = str(row["snapshot_time_utc"])
        settlement = settlement_by_event.get(event_ticker)
        market_rows = market_by_key.get((event_ticker, snapshot_time), [])
        if settlement is None or not market_rows:
            continue
        event = events_by_key.get((event_ticker, snapshot_time), {})
        brackets = bracket_dicts(event, market_rows)
        tickers = tuple(bracket["ticker"] for bracket in brackets)
        full_center = (
            optional_float(row.get("ensemble_raw_median_high_f"))
            or optional_float(row.get("nws_anchor_high_f"))
            or optional_float(row.get("hrrr_projected_high_f"))
        )
        if full_center is None:
            continue
        spread = (
            optional_float(row.get("ensemble_member_stddev_f"))
            or optional_float(row.get("weather_source_stddev_f"))
            or 2.5
        )
        full = temperature_distribution(brackets, full_center, spread, "full")
        family_center = statistics.mean(
            value
            for value in (
                optional_float(row.get("ensemble_raw_median_high_f")),
                optional_float(row.get("nws_anchor_high_f")),
                optional_float(row.get("nbm_projected_high_f")),
            )
            if value is not None
        )
        family_centered = temperature_distribution(
            brackets,
            family_center,
            max(1.0, spread * 0.85),
            "family_centered",
        )
        observed_high = optional_float(row.get("observed_high_so_far_f"))
        soft_floor_weather = soft_observation_floor_probabilities(brackets, full, observed_high)
        soft_floor_family_centered = soft_observation_floor_probabilities(
            brackets,
            family_centered,
            observed_high,
        )
        distributions = {
            "full": full,
            "family_centered": family_centered,
            "soft_floor_weather": soft_floor_weather,
            "soft_floor_family_centered": soft_floor_family_centered,
            "soft_floor_weather_floored": apply_probability_floor(soft_floor_weather),
            "uniform": tuple(1.0 / len(tickers) for _ in tickers),
            "market_midpoint": market_distribution(market_rows),
            "hrrr_top3_rerank": hrrr_top3_distribution(
                brackets,
                optional_float(row.get("hrrr_projected_high_f")),
                soft_floor_family_centered,
            ),
        }
        examples.append(
            ForecastExample(
                city=str(row["city"]),
                target_date=target,
                event_ticker=event_ticker,
                checkpoint=checkpoint_label or f"utc_{parse_time(snapshot_time).hour:02d}",
                scheduled_at=snapshot_time,
                as_of=snapshot_time,
                winner_ticker=str(settlement["winner_ticker"]),
                tickers=tickers,
                distributions=distributions,
                feature_metadata={
                    "source": "next-gen-adapter",
                    "weather_snapshot_id": row.get("weather_snapshot_id"),
                },
            )
        )
    return sorted(examples, key=lambda item: (item.target_date, item.as_of, item.city, item.checkpoint))


def fit_legacy_checkpoint_model(
    root: Path,
    cohort: str,
    grid_step: float,
    regularization: float,
    min_train_events: int,
) -> dict[str, dict[str, Any]]:
    examples = load_examples(root, cohort)
    checkpoint_fits: dict[str, dict[str, Any]] = {}
    global_weather_fit = fit_blend_weights(
        examples,
        WEATHER_CANDIDATES,
        grid_step,
        regularization,
        PROBABILITY_FLOOR,
    )
    global_weather_weights = {
        str(name): float(value) for name, value in global_weather_fit["weights"].items()
    }
    global_hrrr_fit = fit_blend_weights(
        [
            ForecastExample(
                city=example.city,
                target_date=example.target_date,
                event_ticker=example.event_ticker,
                checkpoint=example.checkpoint,
                scheduled_at=example.scheduled_at,
                as_of=example.as_of,
                winner_ticker=example.winner_ticker,
                tickers=example.tickers,
                distributions={
                    "trained_weather_blend": blend_probabilities(
                        example.distributions,
                        global_weather_weights,
                        PROBABILITY_FLOOR,
                    ),
                    "hrrr_top3_rerank": example.distributions.get(
                        "hrrr_top3_rerank",
                        blend_probabilities(example.distributions, global_weather_weights, PROBABILITY_FLOOR),
                    ),
                },
                feature_metadata=example.feature_metadata,
            )
            for example in examples
        ],
        ("trained_weather_blend", "hrrr_top3_rerank"),
        grid_step,
        regularization,
        PROBABILITY_FLOOR,
    )
    for checkpoint in sorted({example.checkpoint for example in examples}):
        checkpoint_examples = [example for example in examples if example.checkpoint == checkpoint]
        if len({example.event_key for example in checkpoint_examples}) >= min_train_events:
            weather_fit = fit_blend_weights(
                checkpoint_examples,
                WEATHER_CANDIDATES,
                grid_step,
                regularization,
                PROBABILITY_FLOOR,
            )
            weather_weights = {
                str(name): float(value) for name, value in weather_fit["weights"].items()
            }
            hrrr_examples = []
            for example in checkpoint_examples:
                hrrr_examples.append(
                    ForecastExample(
                        city=example.city,
                        target_date=example.target_date,
                        event_ticker=example.event_ticker,
                        checkpoint=example.checkpoint,
                        scheduled_at=example.scheduled_at,
                        as_of=example.as_of,
                        winner_ticker=example.winner_ticker,
                        tickers=example.tickers,
                        distributions={
                            "trained_weather_blend": blend_probabilities(
                                example.distributions,
                                weather_weights,
                                PROBABILITY_FLOOR,
                            ),
                            "hrrr_top3_rerank": example.distributions.get(
                                "hrrr_top3_rerank",
                                blend_probabilities(example.distributions, weather_weights, PROBABILITY_FLOOR),
                            ),
                        },
                        feature_metadata=example.feature_metadata,
                    )
                )
            hrrr_fit = fit_blend_weights(
                hrrr_examples,
                ("trained_weather_blend", "hrrr_top3_rerank"),
                grid_step,
                regularization,
                PROBABILITY_FLOOR,
            )
            mode = "trained_checkpoint"
        else:
            weather_fit = global_weather_fit
            weather_weights = global_weather_weights
            hrrr_fit = global_hrrr_fit
            mode = "fallback_global"
        checkpoint_fits[checkpoint] = {
            "mode": mode,
            "weather_weights": {
                str(name): float(value) for name, value in weather_fit["weights"].items()
            },
            "hrrr_weights": {
                str(name): float(value) for name, value in hrrr_fit["weights"].items()
            },
            "training_events": len({example.event_key for example in checkpoint_examples}),
        }
    checkpoint_fits["__global__"] = {
        "mode": "global_fallback_for_unseen_checkpoint",
        "weather_weights": global_weather_weights,
        "hrrr_weights": {
            str(name): float(value) for name, value in global_hrrr_fit["weights"].items()
        },
        "training_events": len({example.event_key for example in examples}),
    }
    return checkpoint_fits


def score_examples(
    examples: list[ForecastExample],
    fits: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for example in examples:
        fit = fits.get(example.checkpoint) or fits["__global__"]
        probabilities = staged_hrrr_probabilities(
            example,
            fit["weather_weights"],
            fit["hrrr_weights"],
            PROBABILITY_FLOOR,
        )
        rows.append(
            {
                "city": example.city,
                "target_date": example.target_date,
                "event_ticker": example.event_ticker,
                "checkpoint": example.checkpoint,
                "as_of": example.as_of,
                "winner_ticker": example.winner_ticker,
                "model": MODEL_NAME,
                "tickers": list(example.tickers),
                "probabilities": list(probabilities),
                "fit_mode": fit["mode"],
                "training_events": fit["training_events"],
            }
        )
    return rows


def simulate(
    scores: list[dict[str, Any]],
    export: Path,
    start: str,
    end: str,
    min_ev: float,
    bankroll: float,
    kelly_multiplier: float,
    max_position_fraction: float,
    max_contracts: int,
    longshot_ask_threshold: float,
    longshot_max_contracts: int,
) -> list[dict[str, Any]]:
    markets = read_table(export, "market_snapshots")
    by_snapshot: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in markets:
        target = str(row.get("target_date", ""))[:10]
        if start <= target <= end:
            by_snapshot[(str(row["event_ticker"]), str(row["snapshot_time_utc"]))][
                str(row["market_ticker"])
            ] = row
    trades = []
    for row in scores:
        top = top_one_ticker(row["tickers"], row["probabilities"])
        if top is None:
            continue
        quote = by_snapshot.get((row["event_ticker"], row["as_of"]), {}).get(top)
        if quote is None:
            continue
        probability = float(row["probabilities"][row["tickers"].index(top)])
        ask = optional_float(quote.get("yes_ask_dollars"))
        if ask is None:
            continue
        fee_one = kalshi_fee(ask, 1, "taker")
        ev = probability - ask - fee_one
        if ev < min_ev:
            continue
        effective_max = min(max_contracts, longshot_max_contracts) if ask <= longshot_ask_threshold else max_contracts
        sizing = choose_contracts(
            probability,
            ask,
            "taker",
            1,
            "kelly",
            bankroll,
            kelly_multiplier,
            max_position_fraction,
            effective_max,
            optional_float(quote.get("yes_ask_size")),
            True,
        )
        contracts = int(sizing["contracts"] or 0)
        if contracts <= 0:
            continue
        won = top == row["winner_ticker"]
        fee = kalshi_fee(ask, contracts, "taker")
        payout = float(contracts) if won else 0.0
        ask_cost = ask * contracts
        trades.append(
            {
                "strategy": STRATEGY,
                "model": MODEL_NAME,
                "min_ev": min_ev,
                "city": row["city"],
                "target_date": row["target_date"],
                "checkpoint": row["checkpoint"],
                "as_of": row["as_of"],
                "event_ticker": row["event_ticker"],
                "ticker": top,
                "winner_ticker": row["winner_ticker"],
                "probability": probability,
                "yes_ask": ask,
                "expected_value": ev,
                "contracts": contracts,
                "effective_max_contracts": effective_max,
                "fee": fee,
                "ask_cost": ask_cost,
                "stake": ask_cost + fee,
                "payout": payout,
                "won": won,
                "gross_pnl_before_fees": payout - ask_cost,
                "net_pnl": payout - ask_cost - fee,
                "fit_mode": row["fit_mode"],
                "training_events": row["training_events"],
            }
        )
    return trades


def aggregate(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    output = []
    for key, group in sorted(groups.items()):
        stake = sum(float(row["stake"]) for row in group)
        pnl = sum(float(row["net_pnl"]) for row in group)
        output.append(
            {
                **{name: value for name, value in zip(keys, key, strict=True)},
                "trade_count": len(group),
                "net_pnl": pnl,
                "stake": stake,
                "roi": pnl / stake if stake else 0.0,
                "hit_rate": sum(1 for row in group if row["won"]) / len(group),
                "average_probability": statistics.mean(float(row["probability"]) for row in group),
                "average_ask": statistics.mean(float(row["yes_ask"]) for row in group),
                "average_expected_value": statistics.mean(float(row["expected_value"]) for row in group),
            }
        )
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default="2026-07-20")
    parser.add_argument("--legacy-root", type=Path, default=LEGACY_ROOT / "backtest_data")
    parser.add_argument("--cohort", default="pilot-v1")
    parser.add_argument("--grid-step", type=float, default=0.05)
    parser.add_argument("--regularization", type=float, default=0.01)
    parser.add_argument("--min-train-events", type=int, default=4)
    parser.add_argument("--min-ev", type=float, default=0.02)
    parser.add_argument(
        "--checkpoints",
        nargs="*",
        default=["t_plus_6h", "t_plus_10h", "t_plus_14h", "t_plus_18h"],
        help="Next-gen checkpoint labels to score. Empty list means all checkpoints.",
    )
    args = parser.parse_args()

    fits = fit_legacy_checkpoint_model(
        args.legacy_root,
        args.cohort,
        args.grid_step,
        args.regularization,
        args.min_train_events,
    )
    checkpoints = set(args.checkpoints) if args.checkpoints else None
    examples = build_new_examples(args.export, args.start, args.end, checkpoints)
    scores = score_examples(examples, fits)
    trades = simulate(
        scores,
        args.export,
        args.start,
        args.end,
        args.min_ev,
        bankroll=100.0,
        kelly_multiplier=0.25,
        max_position_fraction=0.05,
        max_contracts=10,
        longshot_ask_threshold=0.05,
        longshot_max_contracts=1,
    )
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "scores.csv", scores)
    write_csv(output / "trades.csv", trades)
    write_csv(output / "daily_pnl.csv", aggregate(trades, ("target_date",)))
    write_csv(output / "city_pnl.csv", aggregate(trades, ("city",)))
    summary_rows = aggregate(trades, ("strategy", "model", "min_ev"))
    write_csv(output / "summary.csv", summary_rows)
    summary = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "adapter": Path(__file__).name,
        "export": str(args.export),
        "legacy_training_root": str(args.legacy_root),
        "cohort": args.cohort,
        "start": args.start,
        "end": args.end,
        "model": MODEL_NAME,
        "strategy": STRATEGY,
        "min_ev": args.min_ev,
        "checkpoints": sorted(checkpoints) if checkpoints is not None else "all",
        "sizing": {
            "mode": "kelly",
            "bankroll": 100.0,
            "kelly_multiplier": 0.25,
            "max_position_fraction": 0.05,
            "max_contracts": 10,
            "longshot_ask_threshold": 0.05,
            "longshot_max_contracts": 1,
        },
        "source_distribution_note": (
            "New export adapter reconstructs legacy source-family distributions "
            "from normalized weather fields; raw legacy ensemble member highs are not present."
        ),
        "examples": len(examples),
        "scores": len(scores),
        "trades": len(trades),
        "summary": summary_rows[0] if summary_rows else None,
        "checkpoint_fits": fits,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary["summary"], indent=2, default=str))
    print(f"output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
