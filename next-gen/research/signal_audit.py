"""Signal audit for Neuralcaster weather strategy outputs.

This script is intentionally research-oriented: it reads a frozen local export
and a model report, builds side-level candidate signals, and writes calibration,
edge, CLV, and simple rule-search artifacts.
"""

from __future__ import annotations

import argparse
import ast
import csv
import gzip
import json
from dataclasses import dataclass
from datetime import date, datetime
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
    bracket_mode: str
    selection_score: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Neuralcaster tradable signal quality.")
    parser.add_argument("--data", required=True)
    parser.add_argument("--model-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--search-start", default="2026-07-15")
    parser.add_argument("--search-end", default="2026-08-19")
    parser.add_argument("--test-start", default="2026-08-20")
    parser.add_argument("--test-end", default="2026-08-31")
    parser.add_argument("--min-search-trades", type=int, default=20)
    args = parser.parse_args(argv)

    data = Path(args.data)
    model_report = Path(args.model_report)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    candidates = build_candidates(data, model_report)
    candidates.to_csv(output / "candidate_signals.csv", index=False)

    write_calibration(candidates, output)
    write_group_audits(candidates, output)
    rule_rows, selected = search_rules(
        candidates,
        search_start=args.search_start,
        search_end=args.search_end,
        test_start=args.test_start,
        test_end=args.test_end,
        min_search_trades=args.min_search_trades,
    )
    rule_rows.to_csv(output / "rule_search.csv", index=False)
    selected.to_csv(output / "selected_rule_windows.csv", index=False)
    write_summary(output, candidates, rule_rows, selected, args)
    print(f"signal audit complete: output={output}")
    return 0


def build_candidates(data: Path, model_report: Path) -> pd.DataFrame:
    markets = pd.DataFrame(_read_rows(data / "market_snapshots.json.gz"))
    settlements = pd.DataFrame(_read_rows(data / "settlements.json.gz"))
    events = pd.DataFrame(_read_rows(data / "events.json.gz"))
    probabilities = _read_probabilities(model_report / "bracket_distributions.csv")

    markets["snapshot_hour_utc"] = pd.to_datetime(markets["snapshot_time_utc"], utc=True)
    markets["target_date"] = markets["target_date"].astype(str)
    markets["market_ticker"] = markets["market_ticker"].astype(str)
    markets["event_ticker"] = markets["event_ticker"].astype(str)
    markets["city"] = markets["city"].astype(str)

    events = events[["city", "event_ticker", "snapshot_time_utc", "climate_day_start_utc"]].copy()
    events["snapshot_hour_utc"] = pd.to_datetime(events["snapshot_time_utc"], utc=True)
    events["climate_day_start_utc"] = pd.to_datetime(events["climate_day_start_utc"], utc=True)
    markets = markets.merge(
        events[["city", "event_ticker", "snapshot_hour_utc", "climate_day_start_utc"]],
        on=["city", "event_ticker", "snapshot_hour_utc"],
        how="left",
    )
    markets["hours_elapsed"] = (
        markets["snapshot_hour_utc"] - markets["climate_day_start_utc"]
    ).dt.total_seconds() / 3600.0

    if not settlements.empty:
        settlements = settlements[["event_ticker", "winner_ticker"]].copy()
        settlements["event_ticker"] = settlements["event_ticker"].astype(str)
        settlements["winner_ticker"] = settlements["winner_ticker"].astype(str)
        markets = markets.merge(settlements, on="event_ticker", how="left")
    else:
        markets["winner_ticker"] = None

    markets = markets.merge(
        probabilities,
        on=["city", "event_ticker", "snapshot_hour_utc", "market_ticker"],
        how="inner",
    )
    markets["market_yes_probability"] = pd.to_numeric(
        markets["normalized_market_midpoint_probability"], errors="coerce"
    )
    markets["yes_ask"] = pd.to_numeric(markets["yes_ask_dollars"], errors="coerce")
    markets["yes_bid"] = pd.to_numeric(markets["yes_bid_dollars"], errors="coerce")
    if "no_ask_dollars" in markets:
        markets["no_ask"] = pd.to_numeric(markets["no_ask_dollars"], errors="coerce")
    else:
        markets["no_ask"] = pd.NA
    if "no_bid_dollars" in markets:
        markets["no_bid"] = pd.to_numeric(markets["no_bid_dollars"], errors="coerce")
    else:
        markets["no_bid"] = pd.NA
    markets["no_ask"] = markets["no_ask"].fillna(1.0 - markets["yes_bid"])
    markets["no_bid"] = markets["no_bid"].fillna(1.0 - markets["yes_ask"])

    closing = _closing_side_midpoints(markets)
    rows = []
    for side in ("yes", "no"):
        side_frame = markets.copy()
        if side == "yes":
            side_frame["side"] = "yes"
            side_frame["model_probability"] = side_frame["yes_model_probability"]
            side_frame["market_probability"] = side_frame["market_yes_probability"]
            side_frame["entry_price"] = side_frame["yes_ask"]
            side_frame["opposite_bid"] = side_frame["yes_bid"]
            side_frame["hit"] = (
                side_frame["winner_ticker"].astype(str) == side_frame["market_ticker"].astype(str)
            ).astype(float)
        else:
            side_frame["side"] = "no"
            side_frame["model_probability"] = 1.0 - side_frame["yes_model_probability"]
            side_frame["market_probability"] = 1.0 - side_frame["market_yes_probability"]
            side_frame["entry_price"] = side_frame["no_ask"]
            side_frame["opposite_bid"] = side_frame["no_bid"]
            side_frame["hit"] = (
                side_frame["winner_ticker"].notna()
                & (side_frame["winner_ticker"].astype(str) != side_frame["market_ticker"].astype(str))
            ).astype(float)
        side_frame["spread"] = side_frame["entry_price"] - side_frame["opposite_bid"]
        side_frame["edge"] = side_frame["model_probability"] - side_frame["entry_price"]
        side_frame["market_disagreement"] = (
            side_frame["model_probability"] - side_frame["market_probability"]
        )
        side_frame["pnl_1_contract"] = side_frame["hit"] - side_frame["entry_price"]
        side_frame["bracket_type"] = side_frame.apply(_bracket_type, axis=1)
        side_frame["checkpoint"] = "utc_" + side_frame["snapshot_hour_utc"].dt.hour.astype(str).str.zfill(2)
        side_frame = side_frame.merge(
            closing,
            on=["event_ticker", "market_ticker", "side"],
            how="left",
        )
        side_frame["clv"] = side_frame["closing_mid"] - side_frame["entry_price"]
        rows.append(side_frame)

    combined = pd.concat(rows, ignore_index=True)
    keep = [
        "target_date",
        "city",
        "event_ticker",
        "market_ticker",
        "snapshot_hour_utc",
        "checkpoint",
        "side",
        "bracket_type",
        "hours_elapsed",
        "model_probability",
        "market_probability",
        "market_disagreement",
        "entry_price",
        "opposite_bid",
        "spread",
        "edge",
        "hit",
        "pnl_1_contract",
        "closing_mid",
        "clv",
        "winner_ticker",
    ]
    return combined[keep].sort_values(["target_date", "snapshot_hour_utc", "city", "market_ticker", "side"])


def search_rules(
    candidates: pd.DataFrame,
    *,
    search_start: str,
    search_end: str,
    test_start: str,
    test_end: str,
    min_search_trades: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rules = []
    for side_mode in ("all", "no_only", "yes_only"):
        for min_edge, max_edge in (
            (0.02, None),
            (0.02, 0.05),
            (0.02, 0.08),
            (0.02, 0.12),
            (0.05, 0.08),
            (0.05, 0.12),
            (0.08, 0.12),
            (0.08, 0.18),
        ):
            for min_price, max_price in (
                (0.50, 0.65),
                (0.50, 0.60),
                (0.50, 0.55),
                (0.55, 0.65),
                (0.60, 0.65),
            ):
                for min_model_probability in (0.55, 0.65, 0.75):
                    for min_market_disagreement in (0.00, 0.05, 0.10):
                        for min_hours_elapsed in (None, 6.0, 10.0):
                            for bracket_mode in ("all", "bounded"):
                                for selection_score in ("edge",):
                                    rules.append(
                                        Rule(
                                            name="",
                                            side_mode=side_mode,
                                            min_edge=min_edge,
                                            max_edge=max_edge,
                                            min_price=min_price,
                                            max_price=max_price,
                                            max_spread=0.10,
                                            min_model_probability=min_model_probability,
                                            min_market_disagreement=min_market_disagreement,
                                            min_hours_elapsed=min_hours_elapsed,
                                            bracket_mode=bracket_mode,
                                            selection_score=selection_score,
                                        )
                                    )

    rows = []
    for index, rule in enumerate(rules, start=1):
        named = Rule(name=f"rule_{index:05d}", **{k: v for k, v in rule.__dict__.items() if k != "name"})
        search = simulate_rule(candidates, named, search_start, search_end)
        test = simulate_rule(candidates, named, test_start, test_end)
        rows.append(
            {
                **named.__dict__,
                **{f"search_{key}": value for key, value in search.items()},
                **{f"test_{key}": value for key, value in test.items()},
            }
        )
    frame = pd.DataFrame(rows)
    selectable = frame[
        (frame["search_trades"] >= min_search_trades)
        & (frame["search_pnl"] > 0)
        & (frame["search_hit_rate"] >= 0.60)
    ].copy()
    if selectable.empty:
        selected = frame.sort_values(
            ["search_hit_rate", "search_pnl", "search_trades"],
            ascending=False,
        ).head(10)
    else:
        selected = selectable.sort_values(
            [
                "search_hit_rate",
                "search_positive_clv_rate",
                "search_pnl",
                "search_trades",
            ],
            ascending=False,
        ).head(10)
    windows = []
    for _, row in selected.iterrows():
        rule = _rule_from_row(row)
        for label, start, end in [
            ("search", search_start, search_end),
            ("test", test_start, test_end),
            ("aug_06_12", "2026-08-06", "2026-08-12"),
            ("aug_13_19", "2026-08-13", "2026-08-19"),
            ("aug_20_26", "2026-08-20", "2026-08-26"),
            ("aug_27_31", "2026-08-27", "2026-08-31"),
        ]:
            windows.append({"rule": rule.name, "window": label, "start": start, "end": end, **simulate_rule(candidates, rule, start, end)})
    return frame, pd.DataFrame(windows)


def simulate_rule(candidates: pd.DataFrame, rule: Rule, start: str, end: str) -> dict[str, Any]:
    frame = candidates[(candidates["target_date"] >= start) & (candidates["target_date"] <= end)].copy()
    frame = _apply_rule_filter(frame, rule)
    if frame.empty:
        return _metrics(frame)
    ascending = False
    frame = frame.sort_values(
        ["target_date", "event_ticker", rule.selection_score, "edge", "model_probability"],
        ascending=[True, True, ascending, False, False],
    )
    selected = frame.groupby(["target_date", "event_ticker"], as_index=False).head(1)
    return _metrics(selected)


def _apply_rule_filter(frame: pd.DataFrame, rule: Rule) -> pd.DataFrame:
    if rule.side_mode == "no_only":
        frame = frame[frame["side"] == "no"]
    elif rule.side_mode == "yes_only":
        frame = frame[frame["side"] == "yes"]
    frame = frame[
        (frame["edge"] >= rule.min_edge)
        & (frame["edge"] <= rule.max_edge if rule.max_edge is not None else True)
        & (frame["spread"] <= rule.max_spread)
        & (frame["entry_price"] >= rule.min_price)
        & (frame["entry_price"] <= rule.max_price)
        & (frame["model_probability"] >= rule.min_model_probability)
        & (frame["market_disagreement"] >= rule.min_market_disagreement)
    ]
    if rule.min_hours_elapsed is not None:
        frame = frame[frame["hours_elapsed"] >= rule.min_hours_elapsed]
    if rule.bracket_mode == "bounded":
        frame = frame[frame["bracket_type"] == "bounded"]
    return frame


def _metrics(frame: pd.DataFrame) -> dict[str, Any]:
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
    running_max = cumulative.cummax()
    drawdown = cumulative - running_max
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


def write_calibration(candidates: pd.DataFrame, output: Path) -> None:
    rows = []
    for probability_column in ("model_probability", "market_probability"):
        frame = candidates.dropna(subset=[probability_column, "hit"]).copy()
        frame["bucket"] = pd.cut(
            frame[probability_column],
            bins=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.000001],
            include_lowest=True,
        ).astype(str)
        grouped = frame.groupby(["side", "bucket"], observed=False)
        for (side, bucket), group in grouped:
            if group.empty:
                continue
            predicted = group[probability_column].mean()
            realized = group["hit"].mean()
            rows.append(
                {
                    "probability_source": probability_column,
                    "side": side,
                    "bucket": bucket,
                    "rows": len(group),
                    "predicted_probability": predicted,
                    "realized_hit_rate": realized,
                    "calibration_error": realized - predicted,
                    "brier": ((group[probability_column] - group["hit"]) ** 2).mean(),
                }
            )
    pd.DataFrame(rows).to_csv(output / "calibration_by_probability_bucket.csv", index=False)

    bucket_rows = []
    for column in ("edge", "market_disagreement"):
        frame = candidates.dropna(subset=[column]).copy()
        frame["bucket"] = pd.cut(
            frame[column],
            bins=[-1.0, -0.2, -0.1, 0.0, 0.02, 0.05, 0.08, 0.12, 0.18, 0.25, 0.35, 1.0],
            include_lowest=True,
        ).astype(str)
        for (side, bucket), group in frame.groupby(["side", "bucket"], observed=False):
            if group.empty:
                continue
            metrics = _metrics(group)
            bucket_rows.append({"metric": column, "side": side, "bucket": bucket, **metrics})
    pd.DataFrame(bucket_rows).to_csv(output / "edge_and_disagreement_buckets.csv", index=False)


def write_group_audits(candidates: pd.DataFrame, output: Path) -> None:
    eligible = candidates[
        (candidates["edge"] >= 0.02)
        & (candidates["spread"] <= 0.10)
        & (candidates["entry_price"] >= 0.50)
        & (candidates["entry_price"] <= 0.65)
    ].copy()
    for keys, filename in [
        (["target_date"], "eligible_by_day.csv"),
        (["city"], "eligible_by_city.csv"),
        (["checkpoint"], "eligible_by_checkpoint.csv"),
        (["side"], "eligible_by_side.csv"),
        (["bracket_type"], "eligible_by_bracket_type.csv"),
        (["city", "side"], "eligible_by_city_side.csv"),
    ]:
        rows = []
        for values, group in eligible.groupby(keys, observed=False):
            if not isinstance(values, tuple):
                values = (values,)
            rows.append({**dict(zip(keys, values, strict=True)), **_metrics(group)})
        pd.DataFrame(rows).sort_values("pnl").to_csv(output / filename, index=False)


def write_summary(
    output: Path,
    candidates: pd.DataFrame,
    rule_rows: pd.DataFrame,
    selected: pd.DataFrame,
    args: argparse.Namespace,
) -> None:
    eligible = candidates[
        (candidates["edge"] >= 0.02)
        & (candidates["spread"] <= 0.10)
        & (candidates["entry_price"] >= 0.50)
        & (candidates["entry_price"] <= 0.65)
    ]
    top = rule_rows.sort_values(
        ["search_hit_rate", "search_pnl", "search_trades"],
        ascending=False,
    ).head(5)
    lines = [
        "# Neuralcaster Tradable Signal Audit",
        "",
        f"- Data: `{args.data}`",
        f"- Model report: `{args.model_report}`",
        f"- Candidate rows: {len(candidates):,}",
        f"- Base eligible rows: {len(eligible):,}",
        f"- Search window: {args.search_start} through {args.search_end}",
        f"- Test window: {args.test_start} through {args.test_end}",
        "",
        "## Top Search Rules",
        "",
    ]
    for _, row in top.iterrows():
        lines.append(
            "- "
            f"{row['name']}: side={row['side_mode']} edge>={row['min_edge']} "
            f"edge<={row['max_edge']} "
            f"price={row['min_price']}-{row['max_price']} "
            f"prob>={row['min_model_probability']} disagreement>={row['min_market_disagreement']} "
            f"hours>={row['min_hours_elapsed']} bracket={row['bracket_mode']} score={row['selection_score']} "
            f"| search hit={row['search_hit_rate']:.3f} pnl={row['search_pnl']:.2f} trades={int(row['search_trades'])} "
            f"| test hit={row['test_hit_rate']:.3f} pnl={row['test_pnl']:.2f} trades={int(row['test_trades'])}"
        )
    lines.extend(["", "## Selected Rule Windows", ""])
    for _, row in selected.head(24).iterrows():
        lines.append(
            f"- {row['rule']} {row['window']}: trades={int(row['trades'])} "
            f"hit={row['hit_rate']:.3f} pnl={row['pnl']:.2f} roi={row['roi']:.3f} "
            f"clv+={row['positive_clv_rate']:.3f}"
        )
    (output / "signal_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        plain = path.with_suffix("")
        if plain.exists():
            return json.loads(plain.read_text(encoding="utf-8"))
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _read_probabilities(path: Path) -> pd.DataFrame:
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            snapshot = pd.Timestamp(datetime.fromisoformat(row["snapshot_hour_utc"].replace("Z", "+00:00")))
            probabilities = ast.literal_eval(row["probabilities"])
            for market_ticker, probability in probabilities.items():
                rows.append(
                    {
                        "city": row["city"],
                        "event_ticker": row["event_ticker"],
                        "snapshot_hour_utc": snapshot,
                        "market_ticker": str(market_ticker),
                        "yes_model_probability": float(probability),
                    }
                )
    return pd.DataFrame(rows)


def _closing_side_midpoints(markets: pd.DataFrame) -> pd.DataFrame:
    frame = markets.sort_values("snapshot_hour_utc").copy()
    frame["yes_mid"] = (frame["yes_ask"] + frame["yes_bid"]) / 2.0
    frame["no_mid"] = (frame["no_ask"] + frame["no_bid"]) / 2.0
    rows = []
    for (event_ticker, market_ticker), group in frame.groupby(["event_ticker", "market_ticker"], observed=False):
        last = group.iloc[-1]
        rows.append(
            {
                "event_ticker": event_ticker,
                "market_ticker": market_ticker,
                "side": "yes",
                "closing_mid": float(last["yes_mid"]),
            }
        )
        rows.append(
            {
                "event_ticker": event_ticker,
                "market_ticker": market_ticker,
                "side": "no",
                "closing_mid": float(last["no_mid"]),
            }
        )
    return pd.DataFrame(rows)


def _bracket_type(row: pd.Series) -> str:
    lower = row.get("bracket_lower_f")
    upper = row.get("bracket_upper_f")
    if pd.isna(lower):
        return "lower_tail"
    if pd.isna(upper):
        return "upper_tail"
    return "bounded"


def _rule_from_row(row: pd.Series) -> Rule:
    raw_hours = row.get("min_hours_elapsed")
    min_hours = None if pd.isna(raw_hours) else float(raw_hours)
    return Rule(
        name=str(row["name"]),
        side_mode=str(row["side_mode"]),
        min_edge=float(row["min_edge"]),
        max_edge=None if pd.isna(row.get("max_edge")) else float(row["max_edge"]),
        min_price=float(row["min_price"]),
        max_price=float(row["max_price"]),
        max_spread=float(row["max_spread"]),
        min_model_probability=float(row["min_model_probability"]),
        min_market_disagreement=float(row["min_market_disagreement"]),
        min_hours_elapsed=min_hours,
        bracket_mode=str(row["bracket_mode"]),
        selection_score=str(row["selection_score"]),
    )


if __name__ == "__main__":
    raise SystemExit(main())
