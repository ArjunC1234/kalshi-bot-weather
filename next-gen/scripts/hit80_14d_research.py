"""Hit-rate targeted Neuralcaster EV research run.

The script uses a frozen validation/test split:
- select a high-precision gate on validation dates only
- apply that gate unchanged to the 14-day test window

This legacy sweep does not verify upstream model-label availability or execution
costs. Use profitability_research.py for the audited research workflow.
"""

from __future__ import annotations

import argparse
import ast
import csv
import gzip
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

import pandas as pd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an 80% hit-rate targeted 14-day test")
    parser.add_argument("--data", required=True)
    parser.add_argument("--model-report", required=True)
    parser.add_argument("--test-model-report")
    parser.add_argument("--output", required=True)
    parser.add_argument("--validation-start", required=True)
    parser.add_argument("--validation-end", required=True)
    parser.add_argument("--test-start", required=True)
    parser.add_argument("--test-end", required=True)
    parser.add_argument("--min-validation-trades", type=int, default=20)
    parser.add_argument("--target-hit-rate", type=float, default=0.80)
    parser.add_argument("--daily-budget", type=float, default=40.0)
    parser.add_argument("--max-order-cost", type=float, default=3.0)
    parser.add_argument("--max-contracts-per-order", type=int, default=20)
    parser.add_argument("--max-no-contracts-per-order", type=int, default=10)
    parser.add_argument("--max-positions-per-event", type=int, default=1)
    args = parser.parse_args(argv)
    if not args.validation_start <= args.validation_end < args.test_start <= args.test_end:
        parser.error("Expected validation-start <= validation-end < test-start <= test-end")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    validation_candidates = _materialize_candidates(Path(args.data), Path(args.model_report))
    test_model_report = Path(args.test_model_report) if args.test_model_report else Path(args.model_report)
    test_candidates = (
        validation_candidates
        if test_model_report == Path(args.model_report)
        else _materialize_candidates(Path(args.data), test_model_report)
    )
    validation_rows = [
        row
        for row in validation_candidates
        if args.validation_start <= row["target_date"] <= args.validation_end
    ]
    test_rows = [
        row for row in test_candidates if args.test_start <= row["target_date"] <= args.test_end
    ]

    sweep_rows = []
    for index, policy in enumerate(_policy_grid(), start=1):
        if index % 250 == 0:
            print(f"evaluated {index} policies", flush=True)
        selected = _select_rows(
            validation_rows,
            policy,
            args.daily_budget,
            args.max_order_cost,
            args.max_contracts_per_order,
            args.max_no_contracts_per_order,
            args.max_positions_per_event,
        )
        metrics = _summary_metrics(selected)
        sweep_rows.append(
            {
                **policy,
                **_prefixed("validation", metrics),
                "validation_hit_lcb80": _wilson_lower_bound(
                    float(metrics.get("hit_rate") or 0.0),
                    int(metrics.get("trades") or 0),
                    z=1.28,
                ),
            }
        )

    viable = [
        row
        for row in sweep_rows
        if int(row["validation_trades"]) >= args.min_validation_trades
        and float(row["validation_hit_rate"]) >= args.target_hit_rate
        and float(row["validation_total_pnl"]) > 0.0
    ]
    if viable:
        selected_policy_row = max(
            viable,
            key=lambda row: (
                float(row["validation_hit_lcb80"]),
                float(row["validation_hit_rate"]),
                float(row["validation_total_pnl"]),
                min(int(row["validation_trades"]), 60),
            ),
        )
        selection_status = "selected_policy_met_validation_hit_target"
    else:
        selected_policy_row = max(
            sweep_rows,
            key=lambda row: (
                float(row["validation_hit_rate"] or 0.0),
                float(row["validation_total_pnl"] or 0.0),
                min(int(row["validation_trades"] or 0), 60),
            ),
        )
        selection_status = "selected_best_available_policy_below_validation_hit_target"

    selected_policy = {key: selected_policy_row[key] for key in _POLICY_FIELDS}
    test_trades = _select_rows(
        test_rows,
        selected_policy,
        args.daily_budget,
        args.max_order_cost,
        args.max_contracts_per_order,
        args.max_no_contracts_per_order,
        args.max_positions_per_event,
    )
    for index, row in enumerate(test_trades, start=1):
        row["order_id"] = f"hit80-{index:06d}"
    test_metrics = _summary_metrics(test_trades)
    summary = {
        "mode": "hit80_14d_research",
        "data_path": str(args.data),
        "model_report": str(args.model_report),
        "test_model_report": str(test_model_report),
        "validation_start": args.validation_start,
        "validation_end": args.validation_end,
        "test_start": args.test_start,
        "test_end": args.test_end,
        "target_hit_rate": args.target_hit_rate,
        "min_validation_trades": args.min_validation_trades,
        "selection_status": selection_status,
        "selected_policy": selected_policy,
        "selected_validation": selected_policy_row,
        "candidate_counts": {
            "validation_model_all": len(validation_candidates),
            "test_model_all": len(test_candidates),
            "validation": len(validation_rows),
            "test": len(test_rows),
        },
        **test_metrics,
        "test_hit_lcb80": _wilson_lower_bound(
            float(test_metrics.get("hit_rate") or 0.0),
            int(test_metrics.get("trades") or 0),
            z=1.28,
        ),
        "fee_stress": _fee_stress_rows(test_trades),
        "data_audit": _data_audit(Path(args.data), args.test_start, args.test_end),
    }

    _write_rows(output / "test_candidate_trades.csv", test_rows)
    _write_rows(output / "validation_policy_sweep.csv", sweep_rows)
    _write_rows(output / "trades.csv", test_trades)
    _write_rows(output / "daily_pnl.csv", _daily_pnl_rows(test_trades))
    _write_rows(output / "city_metrics.csv", _grouped_metrics(test_trades, "city"))
    _write_rows(output / "side_metrics.csv", _grouped_metrics(test_trades, "side"))
    _write_rows(output / "checkpoint_metrics.csv", _grouped_metrics(test_trades, "checkpoint"))
    _write_rows(output / "bracket_type_metrics.csv", _grouped_metrics(test_trades, "bracket_type"))
    _write_rows(output / "probability_buckets.csv", _bucket_metrics(test_trades, "model_probability", PROBABILITY_BUCKETS))
    _write_rows(output / "price_buckets.csv", _bucket_metrics(test_trades, "entry_price", PRICE_BUCKETS))
    _write_rows(output / "edge_buckets.csv", _bucket_metrics(test_trades, "ev", EDGE_BUCKETS))
    _write_rows(output / "fee_stress.csv", summary["fee_stress"])
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    _write_markdown_report(output / "hit80_14d_report.md", summary)

    print(
        "hit80 research: "
        f"status={selection_status} trades={summary['trades']} "
        f"hit_rate={float(summary['hit_rate']):.4f} "
        f"pnl={float(summary['total_pnl']):.4f} output={output}"
    )
    return 0


_POLICY_FIELDS = (
    "side_mode",
    "min_ev",
    "max_ev",
    "max_spread",
    "min_entry_price",
    "max_entry_price",
    "min_model_probability",
    "min_hours_elapsed",
    "entry_policy",
)
EDGE_BUCKETS = (
    (0.00, 0.03, "0.00-0.03"),
    (0.03, 0.05, "0.03-0.05"),
    (0.05, 0.08, "0.05-0.08"),
    (0.08, 0.12, "0.08-0.12"),
    (0.12, float("inf"), "0.12+"),
)
PROBABILITY_BUCKETS = (
    (0.00, 0.20, "0.00-0.20"),
    (0.20, 0.35, "0.20-0.35"),
    (0.35, 0.50, "0.35-0.50"),
    (0.50, 0.65, "0.50-0.65"),
    (0.65, 1.01, "0.65-1.00"),
)
PRICE_BUCKETS = (
    (0.00, 0.15, "0.00-0.15"),
    (0.15, 0.25, "0.15-0.25"),
    (0.25, 0.35, "0.25-0.35"),
    (0.35, 0.50, "0.35-0.50"),
    (0.50, 1.01, "0.50-1.00"),
)


def _materialize_candidates(data: Path, model_report: Path) -> list[dict[str, Any]]:
    probabilities = _explode_probabilities(model_report / "bracket_distributions.csv")
    markets = pd.DataFrame(_load_json_gz(data / "market_snapshots.json.gz"))
    events = pd.DataFrame(_load_json_gz(data / "events.json.gz"))
    settlements = pd.DataFrame(_load_json_gz(data / "settlements.json.gz"))

    markets = markets.rename(columns={"snapshot_time_utc": "snapshot_hour_utc"})
    events = events.rename(columns={"snapshot_time_utc": "snapshot_hour_utc"})
    keep_market = [
        "city",
        "event_ticker",
        "market_ticker",
        "target_date",
        "snapshot_hour_utc",
        "bracket_lower_f",
        "bracket_upper_f",
        "yes_bid_dollars",
        "yes_ask_dollars",
        "no_bid_dollars",
        "no_ask_dollars",
        "normalized_market_midpoint_probability",
    ]
    keys = ["city", "event_ticker", "market_ticker", "snapshot_hour_utc"]
    merged = probabilities.merge(markets[keep_market], on=keys, how="inner", validate="one_to_one")
    event_times = events[["city", "event_ticker", "snapshot_hour_utc", "climate_day_start_utc"]].drop_duplicates()
    merged = merged.merge(
        event_times, on=["city", "event_ticker", "snapshot_hour_utc"],
        how="left", validate="many_to_one",
    )

    settlement_lookup = settlements.set_index("event_ticker").to_dict("index")
    closing = _closing_midpoints(markets)
    rows = []
    for item in merged.to_dict("records"):
        for side in ("yes", "no"):
            yes_probability = _finite(item["yes_probability"], 0.0)
            model_probability = yes_probability if side == "yes" else 1.0 - yes_probability
            if side == "yes":
                entry_price = _none_or_float(item.get("yes_ask_dollars"))
                opposite_bid = _none_or_float(item.get("yes_bid_dollars"))
            else:
                entry_price = _none_or_float(item.get("no_ask_dollars"))
                if entry_price is None:
                    yes_bid = _none_or_float(item.get("yes_bid_dollars"))
                    entry_price = None if yes_bid is None else max(0.0, 1.0 - yes_bid)
                opposite_bid = _none_or_float(item.get("no_bid_dollars"))
                if opposite_bid is None:
                    yes_ask = _none_or_float(item.get("yes_ask_dollars"))
                    opposite_bid = None if yes_ask is None else max(0.0, 1.0 - yes_ask)
            if entry_price is None or opposite_bid is None:
                continue
            settlement = settlement_lookup.get(item["event_ticker"], {})
            winner_ticker = settlement.get("winner_ticker")
            if not winner_ticker or settlement.get("validation_status") != "valid":
                continue
            if not 0.0 < entry_price < 1.0 or not 0.0 <= opposite_bid <= entry_price:
                continue
            yes_hit = winner_ticker == item["market_ticker"]
            hit = yes_hit if side == "yes" else not yes_hit
            closing_mid = closing.get((item["event_ticker"], item["market_ticker"], side))
            rows.append(
                {
                    "target_date": str(item["target_date"])[:10],
                    "snapshot_hour_utc": str(item["snapshot_hour_utc"]),
                    "entry_time_utc": str(item["snapshot_hour_utc"]),
                    "checkpoint": f"utc_{pd.Timestamp(item['snapshot_hour_utc']).hour:02d}",
                    "city": item["city"],
                    "event_ticker": item["event_ticker"],
                    "market_ticker": item["market_ticker"],
                    "side": side,
                    "bracket_type": _bracket_type(item),
                    "model_probability": model_probability,
                    "entry_price": entry_price,
                    "opposite_bid": opposite_bid,
                    "market_mid": (entry_price + opposite_bid) / 2.0,
                    "spread": entry_price - opposite_bid,
                    "ev": model_probability - entry_price,
                    "winner_ticker": winner_ticker,
                    "settlement_value": 1.0 if hit else 0.0,
                    "reward": (1.0 if hit else 0.0) - entry_price,
                    "pnl": None,
                    "roi": None,
                    "hit": 1.0 if hit else 0.0,
                    "closing_mid": closing_mid,
                    "clv": None if closing_mid is None else closing_mid - entry_price,
                    "hours_elapsed": _hours_elapsed(item),
                    "bracket_lower_f": item.get("bracket_lower_f"),
                    "bracket_upper_f": item.get("bracket_upper_f"),
                    "final_high_f": settlement.get("settlement_temperature_f"),
                }
            )
    return rows


def _explode_probabilities(path: Path) -> pd.DataFrame:
    rows = []
    for row in pd.read_csv(path).to_dict("records"):
        probs = ast.literal_eval(str(row["probabilities"]))
        for market_ticker, probability in probs.items():
            rows.append(
                {
                    "city": row["city"],
                    "event_ticker": row["event_ticker"],
                    "snapshot_hour_utc": row["snapshot_hour_utc"],
                    "market_ticker": market_ticker,
                    "yes_probability": float(probability),
                }
            )
    result = pd.DataFrame(rows).drop_duplicates()
    keys = ["city", "event_ticker", "snapshot_hour_utc", "market_ticker"]
    if result.duplicated(keys).any():
        raise ValueError("Conflicting model probabilities for the same snapshot")
    if not result.yes_probability.between(0, 1).all():
        raise ValueError("Invalid model probabilities")
    return result


def _closing_midpoints(markets: pd.DataFrame) -> dict[tuple[str, str, str], float]:
    output = {}
    for item in markets.sort_values("snapshot_hour_utc").to_dict("records"):
        yes_ask = _none_or_float(item.get("yes_ask_dollars"))
        yes_bid = _none_or_float(item.get("yes_bid_dollars"))
        no_ask = _none_or_float(item.get("no_ask_dollars"))
        no_bid = _none_or_float(item.get("no_bid_dollars"))
        if no_ask is None and yes_bid is not None:
            no_ask = max(0.0, 1.0 - yes_bid)
        if no_bid is None and yes_ask is not None:
            no_bid = max(0.0, 1.0 - yes_ask)
        if yes_ask is not None and yes_bid is not None:
            output[(item["event_ticker"], item["market_ticker"], "yes")] = (yes_ask + yes_bid) / 2.0
        if no_ask is not None and no_bid is not None:
            output[(item["event_ticker"], item["market_ticker"], "no")] = (no_ask + no_bid) / 2.0
    return output


def _policy_grid() -> list[dict[str, Any]]:
    policies = []
    for side_mode in ("all", "yes_only", "no_only"):
        for min_ev in (0.05, 0.08, 0.12, 0.16, 0.20):
            for max_ev in (None, 0.25):
                if max_ev is not None and min_ev > max_ev:
                    continue
                for max_spread in (0.05, 0.08, 0.10):
                    price_bands = (
                        (0.50, 0.65),
                        (0.60, 0.75),
                        (0.65, 0.90),
                    )
                    for min_entry_price, max_entry_price in price_bands:
                        for min_model_probability in (0.75, 0.80, 0.85, 0.90):
                            for min_hours_elapsed in (None, 10.0, 14.0, 18.0):
                                policies.append(
                                    {
                                        "side_mode": side_mode,
                                        "min_ev": min_ev,
                                        "max_ev": max_ev,
                                        "max_spread": max_spread,
                                        "min_entry_price": min_entry_price,
                                        "max_entry_price": max_entry_price,
                                        "min_model_probability": min_model_probability,
                                        "min_hours_elapsed": min_hours_elapsed,
                                        "entry_policy": "first-eligible",
                                    }
                                )
    return policies


def _select_rows(
    rows: list[dict[str, Any]],
    policy: dict[str, Any],
    daily_budget: float,
    max_order_cost: float,
    max_contracts_per_order: int,
    max_no_contracts_per_order: int,
    max_positions_per_event: int,
) -> list[dict[str, Any]]:
    if policy.get("entry_policy", "first-eligible") != "first-eligible":
        raise ValueError("Only chronological first-eligible entry is supported; best-ev looks ahead")
    # Rank edges only within a timestamp, never across future observations.
    selected = sorted(
        (row for row in rows if _passes_policy(row, policy)),
        key=lambda row: (
            pd.Timestamp(row["snapshot_hour_utc"]),
            -_finite(row["ev"], -999.0),
            -_finite(row["model_probability"], 0.0),
            row["event_ticker"], row["market_ticker"], row["side"],
        ),
    )

    budget_remaining: dict[str, float] = {}
    positions: dict[str, int] = {}
    held: set[tuple[str, str]] = set()
    trades = []
    for row in selected:
        event = row["event_ticker"]
        position_key = (event, row["market_ticker"])
        if positions.get(event, 0) >= max_positions_per_event or position_key in held:
            continue
        cap = max_no_contracts_per_order if row["side"] == "no" else max_contracts_per_order
        remaining = budget_remaining.setdefault(row["target_date"], daily_budget)
        premium = min(remaining, max_order_cost)
        contracts = float(max(0, min(cap, int(premium // max(0.01, float(row["entry_price"]))))))
        if contracts <= 0:
            continue
        positions[event] = positions.get(event, 0) + 1
        held.add(position_key)
        budget_remaining[row["target_date"]] -= contracts * float(row["entry_price"])
        pnl = float(row["reward"]) * contracts
        risk = float(row["entry_price"]) * contracts
        trades.append(
            {
                **row,
                "contracts": contracts,
                "pnl": pnl,
                "roi": pnl / max(1e-9, risk),
            }
        )
    return trades


def _passes_policy(row: dict[str, Any], policy: dict[str, Any]) -> bool:
    if policy["side_mode"] == "yes_only" and row["side"] != "yes":
        return False
    if policy["side_mode"] == "no_only" and row["side"] != "no":
        return False
    if _finite(row["ev"], -999.0) < float(policy["min_ev"]):
        return False
    if policy["max_ev"] is not None and _finite(row["ev"], 999.0) > float(policy["max_ev"]):
        return False
    if _finite(row["spread"], 999.0) > float(policy["max_spread"]):
        return False
    if _finite(row["entry_price"], -999.0) < float(policy["min_entry_price"]):
        return False
    if _finite(row["entry_price"], 999.0) > float(policy["max_entry_price"]):
        return False
    if _finite(row["model_probability"], 0.0) < float(policy["min_model_probability"]):
        return False
    if policy["min_hours_elapsed"] is not None:
        if row["hours_elapsed"] is None:
            return False
        if float(row["hours_elapsed"]) < float(policy["min_hours_elapsed"]):
            return False
    return True


def _summary_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trades": 0,
            "total_contracts": 0.0,
            "total_risk": 0.0,
            "total_pnl": 0.0,
            "roi": 0.0,
            "hit_rate": 0.0,
            "max_drawdown": 0.0,
            "mean_clv": None,
            "median_clv": None,
            "positive_clv_rate": None,
        }
    risk = sum(float(row["entry_price"]) * float(row["contracts"]) for row in rows)
    pnl = sum(float(row["pnl"]) for row in rows)
    clv_values = [float(row["clv"]) for row in rows if row["clv"] is not None]
    return {
        "trades": len(rows),
        "total_contracts": sum(float(row["contracts"]) for row in rows),
        "total_risk": risk,
        "total_pnl": pnl,
        "roi": pnl / max(1e-9, risk),
        "hit_rate": mean(float(row["hit"]) for row in rows),
        "average_entry_price": mean(float(row["entry_price"]) for row in rows),
        "average_model_probability": mean(float(row["model_probability"]) for row in rows),
        "average_edge": mean(float(row["ev"]) for row in rows),
        "max_drawdown": _max_drawdown(rows),
        "mean_clv": mean(clv_values) if clv_values else None,
        "median_clv": _median(clv_values) if clv_values else None,
        "positive_clv_rate": (
            mean(1.0 if value > 0 else 0.0 for value in clv_values) if clv_values else None
        ),
    }


def _daily_pnl_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = _group(rows, "target_date")
    cumulative = 0.0
    output = []
    for day, trades in sorted(grouped.items()):
        pnl = sum(float(row["pnl"]) for row in trades)
        cumulative += pnl
        output.append(
            {
                "target_date": day,
                "trades": len(trades),
                "pnl": pnl,
                "cumulative_pnl": cumulative,
                "hit_rate": mean(float(row["hit"]) for row in trades),
            }
        )
    return output


def _grouped_metrics(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return [_group_row(group, trades) for group, trades in sorted(_group(rows, key).items())]


def _bucket_metrics(
    rows: list[dict[str, Any]],
    field: str,
    buckets: tuple[tuple[float, float, str], ...],
) -> list[dict[str, Any]]:
    grouped = {label: [] for _, _, label in buckets}
    for row in rows:
        value = float(row[field])
        for low, high, label in buckets:
            if low <= value < high:
                grouped[label].append(row)
                break
    return [_group_row(group, trades) for group, trades in grouped.items()]


def _group_row(group: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = _summary_metrics(rows)
    return {"group": group, **metrics}


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        output.setdefault(str(row[key]), []).append(row)
    return output


def _fee_stress_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gross_pnl = sum(float(row["pnl"]) for row in rows)
    total_contracts = sum(float(row["contracts"]) for row in rows)
    risk = sum(float(row["entry_price"]) * float(row["contracts"]) for row in rows)
    output = []
    for fee_per_contract in (0.0, 0.005, 0.01, 0.015, 0.02):
        total_fee = total_contracts * fee_per_contract
        net_pnl = gross_pnl - total_fee
        output.append(
            {
                "fee_per_contract": fee_per_contract,
                "total_fee": total_fee,
                "net_pnl": net_pnl,
                "net_roi": net_pnl / max(1e-9, risk),
            }
        )
    return output


def _data_audit(data: Path, start_date: str, end_date: str) -> dict[str, Any]:
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    events = _load_json_gz(data / "events.json.gz")
    settlements = _load_json_gz(data / "settlements.json.gz")
    labels = _load_json_gz(data / "final_temperature_labels.json.gz")
    markets = _load_json_gz(data / "market_snapshots.json.gz")
    window_events = [row for row in events if start_date <= row["target_date"] <= end_date]
    window_settlements = [row for row in settlements if start_date <= row["target_date"] <= end_date]
    window_labels = [row for row in labels if start_date <= row["target_date"] <= end_date]
    window_markets = [row for row in markets if start_date <= row["target_date"] <= end_date]
    market_cities = {row["city"] for row in window_markets}
    settlement_cities = {row["city"] for row in window_settlements}
    observed_cities = sorted(market_cities | settlement_cities)
    observed_dates = sorted(
        {row["target_date"] for row in window_markets}
        | {row["target_date"] for row in window_settlements}
    )
    return {
        "source_export_id": manifest.get("export_name"),
        "export_start": manifest.get("start"),
        "export_end": manifest.get("end"),
        "target_dates": observed_dates,
        "target_date_count": len(observed_dates),
        "cities": observed_cities,
        "city_count": len(observed_cities),
        "event_rows": len(window_events),
        "market_rows": len(window_markets),
        "settlement_rows": len(window_settlements),
        "final_temperature_rows": len(window_labels),
        "settled_city_days": len({(row["city"], row["target_date"]) for row in window_settlements}),
        "settlement_sources": manifest.get("settlement_sources"),
    }


def _load_json_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _hours_elapsed(row: dict[str, Any]) -> float | None:
    start = row.get("climate_day_start_utc")
    snap = row.get("snapshot_hour_utc")
    if not start or not snap:
        return None
    return (pd.Timestamp(snap) - pd.Timestamp(start)).total_seconds() / 3600.0


def _bracket_type(row: dict[str, Any]) -> str:
    lower = row.get("bracket_lower_f")
    upper = row.get("bracket_upper_f")
    if _is_missing(lower):
        return "lower_tail"
    if _is_missing(upper):
        return "upper_tail"
    return "bounded"


def _none_or_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _finite(value: Any, fallback: float) -> float:
    parsed = _none_or_float(value)
    return fallback if parsed is None else parsed


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, float) and math.isnan(value))


def _prefixed(prefix: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {f"{prefix}_{key}": value for key, value in payload.items()}


def _wilson_lower_bound(rate: float, n: int, z: float = 1.28) -> float:
    if n <= 0:
        return 0.0
    denominator = 1.0 + z * z / n
    center = rate + z * z / (2.0 * n)
    margin = z * ((rate * (1.0 - rate) + z * z / (4.0 * n)) / n) ** 0.5
    return (center - margin) / denominator


def _max_drawdown(rows: list[dict[str, Any]]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for row in sorted(rows, key=lambda item: item["entry_time_utc"]):
        equity += float(row["pnl"])
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    clean_rows = [{key: _csv_value(value) for key, value in row.items()} for row in rows]
    fieldnames = sorted({key for row in clean_rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(clean_rows)


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return value


def _write_markdown_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Hit80 14-Day Research Report",
        "",
        f"- Validation window: {summary['validation_start']} through {summary['validation_end']}",
        f"- Frozen test window: {summary['test_start']} through {summary['test_end']}",
        f"- Selection status: {summary['selection_status']}",
        f"- Trades: {summary['trades']}",
        f"- Hit rate: {float(summary['hit_rate']):.4f}",
        f"- 80% Wilson lower bound: {float(summary['test_hit_lcb80']):.4f}",
        f"- Gross PnL: {float(summary['total_pnl']):.4f}",
        f"- Gross ROI: {float(summary['roi']):.4f}",
        f"- Max drawdown: {float(summary['max_drawdown']):.4f}",
        "",
        "## Selected Policy",
        "",
    ]
    for key, value in summary["selected_policy"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Data Audit", ""])
    for key, value in summary["data_audit"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Fee Stress",
            "",
            "| Fee/contract | Total fee | Net PnL | Net ROI |",
            "|---:|---:|---:|---:|",
        ]
    )
    for row in summary["fee_stress"]:
        lines.append(
            f"| {float(row['fee_per_contract']):.3f} | {float(row['total_fee']):.2f} | "
            f"{float(row['net_pnl']):.2f} | {float(row['net_roi']):.4f} |"
        )
    lines.extend(
        [
            "",
            "This is a paper-only research result. Policy selection used only the validation window.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
