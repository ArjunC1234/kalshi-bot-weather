"""Focused capped-edge sweep over Neuralcaster audit candidate signals."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class Rule:
    name: str
    side_mode: str
    min_edge: float
    max_edge: float | None
    min_price: float
    max_price: float
    max_spread: float
    min_model_probability: float
    min_market_disagreement: float
    min_hours_elapsed: float | None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Focused capped-edge signal sweep.")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--search-start", default="2026-07-15")
    parser.add_argument("--search-end", default="2026-08-19")
    parser.add_argument("--test-start", default="2026-08-20")
    parser.add_argument("--test-end", default="2026-08-31")
    parser.add_argument("--min-search-trades", type=int, default=20)
    args = parser.parse_args(argv)

    candidates = pd.read_csv(args.candidates, parse_dates=["snapshot_hour_utc"])
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    rules = build_rules()
    rows = []
    for rule in rules:
        search = simulate(candidates, rule, args.search_start, args.search_end)
        test = simulate(candidates, rule, args.test_start, args.test_end)
        rows.append(
            {
                **rule.__dict__,
                **{f"search_{key}": value for key, value in search.items()},
                **{f"test_{key}": value for key, value in test.items()},
            }
        )
    sweep = pd.DataFrame(rows)
    sweep.to_csv(output / "focused_signal_sweep.csv", index=False)

    eligible = sweep[
        (sweep["search_trades"] >= args.min_search_trades)
        & (sweep["search_pnl"] > 0)
        & (sweep["search_hit_rate"] >= 0.60)
    ].copy()
    if eligible.empty:
        selected = sweep.sort_values(["search_hit_rate", "search_pnl"], ascending=False).head(10)
    else:
        selected = eligible.sort_values(
            [
                "search_hit_rate",
                "search_positive_clv_rate",
                "search_pnl",
                "search_trades",
            ],
            ascending=False,
        ).head(10)
    selected.to_csv(output / "focused_selected_rules.csv", index=False)

    window_rows = []
    for _, row in selected.iterrows():
        rule = rule_from_row(row)
        for label, start, end in (
            ("search", args.search_start, args.search_end),
            ("test", args.test_start, args.test_end),
            ("aug_06_12", "2026-08-06", "2026-08-12"),
            ("aug_13_19", "2026-08-13", "2026-08-19"),
            ("aug_20_26", "2026-08-20", "2026-08-26"),
            ("aug_27_31", "2026-08-27", "2026-08-31"),
        ):
            window_rows.append(
                {
                    "rule": rule.name,
                    "window": label,
                    "start": start,
                    "end": end,
                    **simulate(candidates, rule, start, end),
                }
            )
    windows = pd.DataFrame(window_rows)
    windows.to_csv(output / "focused_selected_windows.csv", index=False)
    write_summary(output, selected, windows)
    print(f"focused signal sweep complete: output={output}")
    return 0


def build_rules() -> list[Rule]:
    rules = [
        Rule("original_fixed_gate", "all", 0.02, None, 0.50, 0.65, 0.10, 0.0, -1.0, None),
        Rule("prior_no_60_65", "no_only", 0.02, None, 0.60, 0.65, 0.10, 0.0, -1.0, None),
    ]
    index = 0
    for side_mode in ("no_only", "all", "yes_only"):
        for min_edge, max_edge in (
            (0.02, 0.05),
            (0.02, 0.08),
            (0.02, 0.12),
            (0.05, 0.08),
            (0.05, 0.12),
            (0.08, 0.12),
        ):
            for min_price, max_price in (
                (0.50, 0.65),
                (0.55, 0.65),
                (0.60, 0.65),
            ):
                for min_model_probability in (0.55, 0.65, 0.75):
                    for min_market_disagreement in (0.00, 0.05, 0.10):
                        for min_hours_elapsed in (None, 6.0, 10.0):
                            index += 1
                            rules.append(
                                Rule(
                                    name=f"focused_{index:04d}",
                                    side_mode=side_mode,
                                    min_edge=min_edge,
                                    max_edge=max_edge,
                                    min_price=min_price,
                                    max_price=max_price,
                                    max_spread=0.10,
                                    min_model_probability=min_model_probability,
                                    min_market_disagreement=min_market_disagreement,
                                    min_hours_elapsed=min_hours_elapsed,
                                )
                            )
    return rules


def simulate(candidates: pd.DataFrame, rule: Rule, start: str, end: str) -> dict[str, Any]:
    frame = candidates[(candidates["target_date"] >= start) & (candidates["target_date"] <= end)]
    if rule.side_mode == "no_only":
        frame = frame[frame["side"] == "no"]
    elif rule.side_mode == "yes_only":
        frame = frame[frame["side"] == "yes"]
    frame = frame[
        (frame["edge"] >= rule.min_edge)
        & (frame["spread"] <= rule.max_spread)
        & (frame["entry_price"] >= rule.min_price)
        & (frame["entry_price"] <= rule.max_price)
        & (frame["model_probability"] >= rule.min_model_probability)
        & (frame["market_disagreement"] >= rule.min_market_disagreement)
    ]
    if rule.max_edge is not None:
        frame = frame[frame["edge"] <= rule.max_edge]
    if rule.min_hours_elapsed is not None:
        frame = frame[frame["hours_elapsed"] >= rule.min_hours_elapsed]
    if frame.empty:
        return metrics(frame)
    frame = frame.sort_values(
        ["target_date", "event_ticker", "edge", "model_probability", "snapshot_hour_utc"],
        ascending=[True, True, False, False, True],
    )
    selected = frame.groupby(["target_date", "event_ticker"], as_index=False).head(1)
    return metrics(selected)


def metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trades": 0,
            "risk": 0.0,
            "pnl": 0.0,
            "roi": 0.0,
            "hit_rate": 0.0,
            "positive_clv_rate": 0.0,
            "mean_clv": 0.0,
            "max_drawdown": 0.0,
        }
    pnl = frame["pnl_1_contract"].astype(float)
    risk = frame["entry_price"].astype(float).sum()
    clv = frame["clv"].astype(float)
    cumulative = pnl.cumsum()
    drawdown = cumulative - cumulative.cummax()
    return {
        "trades": int(len(frame)),
        "risk": float(risk),
        "pnl": float(pnl.sum()),
        "roi": float(pnl.sum() / risk) if risk else 0.0,
        "hit_rate": float(frame["hit"].mean()),
        "positive_clv_rate": float((clv > 0).mean()),
        "mean_clv": float(clv.mean()),
        "max_drawdown": float(drawdown.min()),
    }


def rule_from_row(row: pd.Series) -> Rule:
    raw_max_edge = row.get("max_edge")
    raw_hours = row.get("min_hours_elapsed")
    return Rule(
        name=str(row["name"]),
        side_mode=str(row["side_mode"]),
        min_edge=float(row["min_edge"]),
        max_edge=None if pd.isna(raw_max_edge) else float(raw_max_edge),
        min_price=float(row["min_price"]),
        max_price=float(row["max_price"]),
        max_spread=float(row["max_spread"]),
        min_model_probability=float(row["min_model_probability"]),
        min_market_disagreement=float(row["min_market_disagreement"]),
        min_hours_elapsed=None if pd.isna(raw_hours) else float(raw_hours),
    )


def write_summary(output: Path, selected: pd.DataFrame, windows: pd.DataFrame) -> None:
    lines = ["# Focused Capped-Edge Signal Sweep", ""]
    for _, row in selected.iterrows():
        lines.append(
            f"- {row['name']}: side={row['side_mode']} ev={row['min_edge']}-{row['max_edge']} "
            f"price={row['min_price']}-{row['max_price']} prob>={row['min_model_probability']} "
            f"disagree>={row['min_market_disagreement']} hours>={row['min_hours_elapsed']} "
            f"| search hit={row['search_hit_rate']:.3f} pnl={row['search_pnl']:.2f} "
            f"trades={int(row['search_trades'])} | test hit={row['test_hit_rate']:.3f} "
            f"pnl={row['test_pnl']:.2f} trades={int(row['test_trades'])}"
        )
    lines.extend(["", "## Selected Rule Windows", ""])
    for _, row in windows.iterrows():
        lines.append(
            f"- {row['rule']} {row['window']}: trades={int(row['trades'])} "
            f"hit={row['hit_rate']:.3f} pnl={row['pnl']:.2f} roi={row['roi']:.3f} "
            f"clv+={row['positive_clv_rate']:.3f}"
        )
    (output / "focused_signal_sweep.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
