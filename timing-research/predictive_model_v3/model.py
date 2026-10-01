"""Rolling walk-forward probability models and nested strategy evaluation."""

from __future__ import annotations

import csv
import json
import math
import platform
import random
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from predictive_model_v2.model import build_frame, feature_frame
from reliable_model_v1.model import fee, hit_lower_bound, load_requirements, sha256


@dataclass(frozen=True)
class FoldSpec:
    name: str
    type: str
    train_days: int | None


@dataclass(frozen=True)
class ModelSpec:
    name: str
    kind: str
    use_market: bool
    c: float | None


@dataclass(frozen=True)
class StrategyPolicy:
    fold_name: str
    model_name: str
    side: str
    minimum_probability: float
    minimum_net_edge: float
    minimum_entry_price: float
    maximum_entry_price: float
    maximum_spread: float
    minimum_depth: float
    minimum_climate_hour: int

    @property
    def policy_id(self) -> str:
        return (
            f"{self.fold_name}__{self.model_name}__{self.side}_"
            f"p{self.minimum_probability:.2f}_"
            f"e{self.minimum_net_edge:.2f}_px{self.minimum_entry_price:.2f}-"
            f"{self.maximum_entry_price:.2f}_s{self.maximum_spread:.2f}_"
            f"d{self.minimum_depth:.1f}_h{self.minimum_climate_hour}"
        )


def _safe_probability(value: Any, floor: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return floor
    if not math.isfinite(parsed):
        return floor
    return min(1.0 - floor, max(floor, parsed))


def _available_dates(frame: pd.DataFrame, min_date: str) -> list[str]:
    labeled = frame[frame["label_available"] & frame["target_date"].ge(min_date)]
    return sorted(str(value) for value in labeled["target_date"].dropna().unique())


def _test_cutoff(frame: pd.DataFrame, target_date: str) -> pd.Timestamp | None:
    rows = frame[frame["target_date"].eq(target_date)]
    if rows.empty:
        return None
    return rows["market_requested_at"].min()


def _train_dates_for_fold(
    dates: list[str],
    target_date: str,
    fold: FoldSpec,
    post_switch_start: str,
) -> set[str]:
    prior = [value for value in dates if post_switch_start <= value < target_date]
    if fold.type == "expanding":
        return set(prior)
    if fold.type != "rolling" or fold.train_days is None:
        raise ValueError(f"unknown fold configuration: {fold}")
    return set(prior[-fold.train_days :])


def _fit_logit(train: pd.DataFrame, spec: ModelSpec) -> Pipeline:
    features = feature_frame(train, spec.use_market)
    numeric = [column for column in features if column != "city"]
    transform = ColumnTransformer(
        [
            (
                "numeric",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                numeric,
            ),
            ("city", OneHotEncoder(handle_unknown="ignore"), ["city"]),
        ]
    )
    pipeline = Pipeline(
        [
            ("features", transform),
            (
                "logit",
                LogisticRegression(
                    C=float(spec.c),
                    max_iter=500,
                    random_state=17,
                    solver="liblinear",
                ),
            ),
        ]
    )
    weights = 1 / train.groupby("event")["event"].transform("size")
    pipeline.fit(features, train["y"].astype(int), logit__sample_weight=weights)
    return pipeline


def _predict_model(
    test: pd.DataFrame,
    spec: ModelSpec,
    pipeline: Pipeline | None,
    probability_floor: float,
) -> pd.Series:
    if spec.kind == "raw_market":
        raw = test["market_probability"].map(
            lambda value: _safe_probability(value, probability_floor)
        )
    elif spec.kind == "logit" and pipeline is not None:
        raw = pd.Series(
            pipeline.predict_proba(feature_frame(test, spec.use_market))[:, 1],
            index=test.index,
        )
    else:
        raise ValueError(f"unsupported model spec: {spec}")
    totals = raw.groupby([test["event"], test["market_raw_id"]]).transform("sum")
    normalized = raw / totals.clip(lower=1e-12)
    return normalized.clip(probability_floor, 1.0 - probability_floor)


def generate_oos_predictions(
    frame: pd.DataFrame,
    requirements: dict,
    *,
    progress: bool = False,
) -> tuple[list[dict], list[dict]]:
    folds = [FoldSpec(**item) for item in requirements["folds"]]
    specs = [ModelSpec(**item) for item in requirements["model_candidates"]]
    dates = _available_dates(frame, requirements["post_switch_start"])
    prediction_rows: list[dict] = []
    fold_rows: list[dict] = []
    for fold in folds:
        if progress:
            print(f"[v3] fold {fold.name}", file=sys.stderr, flush=True)
        for target_date in dates:
            train_dates = _train_dates_for_fold(
                dates,
                target_date,
                fold,
                requirements["post_switch_start"],
            )
            cutoff = _test_cutoff(frame, target_date)
            if cutoff is None or not train_dates:
                continue
            train = frame[
                frame["target_date"].isin(train_dates)
                & frame["label_available"]
                & frame["settled_at"].notna()
                & frame["settled_at"].lt(cutoff)
            ].copy()
            training_hours = requirements.get("training_climate_hours")
            if training_hours:
                train = train[
                    train["hours_since_climate_start"].round().astype(int).isin(training_hours)
                ].copy()
            train_events = train["event"].nunique()
            if train_events < requirements["minimum_training_events"]:
                continue
            test = frame[
                frame["target_date"].eq(target_date)
                & frame["label_available"]
                & frame["market_requested_at"].ge(cutoff)
            ].copy()
            if test.empty:
                continue
            model_ready_at = train["settled_at"].max()
            if progress:
                print(
                    "[v3] "
                    f"{fold.name} target={target_date} "
                    f"train_events={train_events} test_events={test['event'].nunique()}",
                    file=sys.stderr,
                    flush=True,
                )
            for spec in specs:
                if progress:
                    print(
                        f"[v3] fit/predict {fold.name} {target_date} {spec.name}",
                        file=sys.stderr,
                        flush=True,
                    )
                pipeline = None
                if spec.kind == "logit":
                    pipeline = _fit_logit(train, spec)
                probabilities = _predict_model(
                    test,
                    spec,
                    pipeline,
                    requirements["probability_floor"],
                )
                fold_rows.append(
                    {
                        "fold": fold.name,
                        "model": spec.name,
                        "target_date": target_date,
                        "train_start": min(train_dates),
                        "train_end": max(train_dates),
                        "train_dates": len(train_dates),
                        "train_events": train_events,
                        "test_rows": len(test),
                        "test_events": test["event"].nunique(),
                        "model_ready_at": model_ready_at.isoformat(),
                    }
                )
                for index, row in test.iterrows():
                    prediction_rows.append(
                        {
                            "fold": fold.name,
                            "model": spec.name,
                            "target_date": row["target_date"],
                            "city": row["city"],
                            "event": row["event"],
                            "market_raw_id": row["market_raw_id"],
                            "market_ticker": row["market_ticker"],
                            "market_requested_at": row["market_requested_at"].isoformat(),
                            "market_received_at": row["market_received_at"].isoformat(),
                            "weather_available_at": row["weather_available_at"].isoformat(),
                            "model_ready_at": model_ready_at.isoformat(),
                            "hours_since_climate_start": row["hours_since_climate_start"],
                            "bracket_index": row["bracket_index"],
                            "is_lower_tail": row["is_lower_tail"],
                            "is_upper_tail": row["is_upper_tail"],
                            "yes_bid": row["yes_bid"],
                            "yes_ask": row["yes_ask"],
                            "yes_ask_size": row["yes_ask_size"],
                            "no_bid": row["no_bid"],
                            "no_ask": row["no_ask"],
                            "no_ask_size": row["no_ask_size"],
                            "market_probability": row["market_probability"],
                            "yes_probability": float(probabilities.loc[index]),
                            "winner_ticker": row["winner_ticker"],
                            "y": row["y"],
                            "availability_violation": (
                                row["weather_available_at"] > row["market_requested_at"]
                            ),
                        }
                    )
    return prediction_rows, fold_rows


def _prediction_groups(predictions: pd.DataFrame):
    return predictions.groupby(["fold", "model", "target_date", "event", "market_raw_id"])


def probability_metrics(predictions: list[dict], probability_floor: float) -> list[dict]:
    if not predictions:
        return []
    frame = pd.DataFrame(predictions)
    rows: list[dict] = []
    grouped_metrics: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for key, group in _prediction_groups(frame):
        fold, model, target_date, event, raw_id = key
        probabilities = group["yes_probability"].astype(float).clip(
            probability_floor,
            1.0 - probability_floor,
        )
        labels = group["y"].astype(float)
        winner_mask = labels.eq(1.0)
        if not winner_mask.any():
            continue
        winner_probability = float(probabilities[winner_mask].sum())
        log_loss = -math.log(max(probability_floor, winner_probability))
        brier = float(((probabilities - labels) ** 2).sum())
        top_row = group.loc[probabilities.idxmax()]
        top1 = float(top_row["y"]) == 1.0
        item = {
            "fold": fold,
            "model": model,
            "target_date": target_date,
            "event": event,
            "market_raw_id": raw_id,
            "log_loss": log_loss,
            "brier": brier,
            "top1": int(top1),
            "winner_probability": winner_probability,
        }
        grouped_metrics[(fold, model)].append(item)
    for (fold, model), items in sorted(grouped_metrics.items()):
        rows.append(
            {
                "fold": fold,
                "model": model,
                "quote_groups": len(items),
                "events": len({item["event"] for item in items}),
                "mean_log_loss": _mean(item["log_loss"] for item in items),
                "mean_brier": _mean(item["brier"] for item in items),
                "top1_accuracy": _mean(item["top1"] for item in items),
                "mean_winner_probability": _mean(
                    item["winner_probability"] for item in items
                ),
            }
        )
    return rows


def _mean(values) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def strategy_grid(
    requirements: dict,
    prediction_scopes: list[tuple[str, str]],
) -> list[StrategyPolicy]:
    grid = requirements["strategy_grid"]
    policies = []
    for fold_name, model_name in prediction_scopes:
        for side in grid["side"]:
            for probability in grid["minimum_probability"]:
                for edge in grid["minimum_net_edge"]:
                    for min_price in grid["minimum_entry_price"]:
                        for max_price in grid["maximum_entry_price"]:
                            if min_price >= max_price:
                                continue
                            for spread in grid["maximum_spread"]:
                                for depth in grid["minimum_depth"]:
                                    for hour in grid["minimum_climate_hour"]:
                                        policies.append(
                                            StrategyPolicy(
                                                fold_name=fold_name,
                                                model_name=model_name,
                                                side=side,
                                                minimum_probability=probability,
                                                minimum_net_edge=edge,
                                                minimum_entry_price=min_price,
                                                maximum_entry_price=max_price,
                                                maximum_spread=spread,
                                                minimum_depth=depth,
                                                minimum_climate_hour=hour,
                                            )
                                        )
    return policies


def replay_strategy(
    predictions: pd.DataFrame,
    policy: StrategyPolicy,
    start: str,
    end: str,
    requirements: dict,
) -> list[dict]:
    base = predictions[
        predictions["fold"].eq(policy.fold_name)
        & predictions["model"].eq(policy.model_name)
        & predictions["target_date"].between(start, end)
    ].copy()
    if base.empty:
        return []
    parts = []
    for side in ("yes", "no"):
        if policy.side == "no" and side != "no":
            continue
        part = base.copy()
        part["side"] = side
        part["probability"] = (
            part["yes_probability"].astype(float)
            if side == "yes"
            else 1.0 - part["yes_probability"].astype(float)
        )
        part["ask"] = part[f"{side}_ask"].astype(float)
        part["bid"] = part[f"{side}_bid"].astype(float)
        part["depth"] = part[f"{side}_ask_size"].astype(float)
        part["hit"] = part["y"].astype(float) if side == "yes" else 1.0 - part["y"].astype(float)
        parts.append(part)
    candidates = pd.concat(parts, ignore_index=False)
    candidates["spread"] = candidates["ask"] - candidates["bid"]
    candidates["execution_price"] = candidates["ask"] + requirements["entry_adverse_dollars"]
    candidates["fee"] = candidates["execution_price"].map(lambda price: fee(price, 1))
    candidates["stress_fee"] = candidates["execution_price"].map(lambda price: fee(price, 2))
    candidates["net_edge"] = (
        candidates["probability"] - candidates["execution_price"] - candidates["fee"]
    )
    candidates = candidates[
        candidates["probability"].ge(policy.minimum_probability)
        & candidates["net_edge"].ge(policy.minimum_net_edge)
        & candidates["execution_price"].between(
            policy.minimum_entry_price,
            policy.maximum_entry_price,
        )
        & candidates["spread"].between(0, policy.maximum_spread)
        & candidates["depth"].ge(policy.minimum_depth)
        & candidates["hours_since_climate_start"].astype(float).ge(policy.minimum_climate_hour)
        & candidates["execution_price"].lt(1)
    ].copy()
    if candidates.empty:
        return []
    candidates = candidates.sort_values(
        ["market_requested_at", "event", "net_edge", "market_ticker", "side"],
        ascending=[True, True, False, True, True],
    )
    per_quote = candidates.drop_duplicates(["event", "market_raw_id"], keep="first")
    chosen = per_quote.drop_duplicates("event", keep="first")
    trades = []
    for row in chosen.itertuples():
        hit = bool(row.hit)
        gross = (1.0 if hit else 0.0) - float(row.execution_price)
        trades.append(
            {
                "policy_id": policy.policy_id,
                "fold": row.fold,
                "model": row.model,
                "city": row.city,
                "event": row.event,
                "target_date": row.target_date,
                "market_raw_id": row.market_raw_id,
                "market_ticker": row.market_ticker,
                "side": row.side,
                "market_requested_at": row.market_requested_at,
                "weather_available_at": row.weather_available_at,
                "model_ready_at": row.model_ready_at,
                "hours_since_climate_start": row.hours_since_climate_start,
                "probability": float(row.probability),
                "net_edge": float(row.net_edge),
                "bid": float(row.bid),
                "ask": float(row.ask),
                "depth": float(row.depth),
                "spread": float(row.spread),
                "execution_price": float(row.execution_price),
                "fee": float(row.fee),
                "stress_fee": float(row.stress_fee),
                "winner_ticker": row.winner_ticker,
                "label_available": True,
                "hit": hit,
                "gross_pnl": gross,
                "net_pnl": gross - float(row.fee),
                "stress_net_pnl": gross - float(row.stress_fee),
                "availability_violation": bool(row.availability_violation),
            }
        )
    return trades


def day_bootstrap_lower(
    rows: list[dict],
    start: str,
    end: str,
    draws: int = 10_000,
) -> float | None:
    if draws <= 0:
        return None
    settled = [row for row in rows if row["label_available"]]
    if not settled:
        return None
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    by_day: dict[str, float] = defaultdict(float)
    for row in settled:
        by_day[row["target_date"]] += float(row["net_pnl"])
    days = [
        (first + timedelta(days=offset)).isoformat()
        for offset in range((last - first).days + 1)
    ]
    rng = random.Random(17)
    totals = [sum(by_day[days[rng.randrange(len(days))]] for _ in days) for _ in range(draws)]
    totals.sort()
    return totals[int(0.025 * len(totals))]


def summarize_trades(
    rows: list[dict],
    start: str,
    end: str,
    *,
    bootstrap_draws: int = 10_000,
) -> dict:
    settled = [row for row in rows if row["label_available"]]
    wins = sum(bool(row["hit"]) for row in settled)
    cities = Counter(row["city"] for row in settled)
    net_pnl = sum(float(row["net_pnl"]) for row in settled)
    stress_pnl = sum(float(row["stress_net_pnl"]) for row in settled)
    probabilities = [float(row["probability"]) for row in settled]
    hit_rate = wins / len(settled) if settled else None
    calibration_gap = (
        _mean(probabilities) - hit_rate if settled and hit_rate is not None else None
    )
    halves = []
    first = date.fromisoformat(start)
    for half in range(2):
        low = first + timedelta(days=7 * half)
        high = low + timedelta(days=6)
        half_rows = [
            row for row in settled if low <= date.fromisoformat(row["target_date"]) <= high
        ]
        halves.append(
            {
                "half": half + 1,
                "start": low.isoformat(),
                "end": high.isoformat(),
                "trades": len(half_rows),
                "net_pnl": sum(float(row["net_pnl"]) for row in half_rows),
            }
        )
    leave_one_city_out = {
        city: sum(float(row["net_pnl"]) for row in settled if row["city"] != city)
        for city in sorted(cities)
    }
    return {
        "intentions": len(rows),
        "settled_trades": len(settled),
        "label_coverage": len(settled) / len(rows) if rows else 0.0,
        "wins": wins,
        "losses": len(settled) - wins,
        "hit_rate": hit_rate,
        "average_probability": _mean(probabilities),
        "selected_calibration_gap": calibration_gap,
        "iid_exact_95_hit_lower_bound": hit_lower_bound(wins, len(settled)),
        "gross_pnl": sum(float(row["gross_pnl"]) for row in settled),
        "fees": sum(float(row["fee"]) for row in settled),
        "net_pnl": net_pnl,
        "stress_fees": sum(float(row["stress_fee"]) for row in settled),
        "stress_net_pnl": stress_pnl,
        "day_bootstrap_net_pnl_95_lower": day_bootstrap_lower(
            settled,
            start,
            end,
            bootstrap_draws,
        ),
        "active_days": len({row["target_date"] for row in settled}),
        "cities": len(cities),
        "city_counts": dict(sorted(cities.items())),
        "maximum_city_trade_fraction": max(cities.values()) / len(settled) if settled else None,
        "availability_violations": sum(bool(row["availability_violation"]) for row in rows),
        "leave_one_city_out_net_pnl": leave_one_city_out,
        "seven_day_halves": halves,
    }


def selection_passes(
    summary: dict,
    requirements: dict,
    *,
    require_bootstrap: bool = True,
) -> bool:
    gate = requirements["selection_requirements"]
    calibration_gap = summary["selected_calibration_gap"]
    bootstrap = summary["day_bootstrap_net_pnl_95_lower"]
    return (
        summary["settled_trades"] >= gate["minimum_trades"]
        and summary["active_days"] >= gate["minimum_active_days"]
        and summary["hit_rate"] is not None
        and summary["hit_rate"] >= gate["minimum_hit_rate"]
        and calibration_gap is not None
        and abs(calibration_gap) <= gate["maximum_selected_calibration_gap"]
        and (not gate["positive_net_pnl"] or summary["net_pnl"] > 0)
        and (not gate["positive_fee_stress_pnl"] or summary["stress_net_pnl"] > 0)
        and (
            not require_bootstrap
            or not gate["positive_day_bootstrap_95_lower_bound"]
            or bootstrap is not None
            and bootstrap > 0
        )
        and (
            not gate["zero_availability_violations"]
            or summary["availability_violations"] == 0
        )
    )


def success_checks(summary: dict, requirements: dict, integrity_errors: list[str]) -> dict:
    gate = requirements["success_requirements"]
    lower = summary["iid_exact_95_hit_lower_bound"]
    bootstrap = summary["day_bootstrap_net_pnl_95_lower"]
    halves = [half["net_pnl"] for half in summary["seven_day_halves"]]
    leave_out = list(summary["leave_one_city_out_net_pnl"].values())
    calibration_gap = summary["selected_calibration_gap"]
    return {
        "minimum_trades": summary["settled_trades"] >= gate["minimum_trades"],
        "minimum_active_days": summary["active_days"] >= gate["minimum_active_days"],
        "minimum_cities": summary["cities"] >= gate["minimum_cities"],
        "maximum_city_trade_fraction": summary["maximum_city_trade_fraction"] is not None
        and summary["maximum_city_trade_fraction"] <= gate["maximum_city_trade_fraction"],
        "minimum_hit_rate": summary["hit_rate"] is not None
        and summary["hit_rate"] >= gate["minimum_hit_rate"],
        "minimum_iid_exact_95_hit_lower_bound": lower is not None
        and lower >= gate["minimum_iid_exact_95_hit_lower_bound"],
        "maximum_selected_calibration_gap": calibration_gap is not None
        and abs(calibration_gap) <= gate["maximum_selected_calibration_gap"],
        "positive_net_pnl": summary["net_pnl"] > 0,
        "positive_fee_stress_pnl": summary["stress_net_pnl"] > 0,
        "positive_day_bootstrap_95_lower_bound": bootstrap is not None and bootstrap > 0,
        "positive_pnl_in_each_seven_day_half": len(halves) == 2
        and all(value > 0 for value in halves),
        "nonnegative_leave_one_city_out_pnl": bool(leave_out) and min(leave_out) >= 0,
        "minimum_label_coverage": summary["label_coverage"] >= gate["minimum_label_coverage"],
        "zero_availability_violations": summary["availability_violations"] == 0,
        "zero_input_integrity_errors": not integrity_errors,
    }


def select_policy(
    grid_rows: list[dict],
    policies: dict[str, StrategyPolicy],
) -> StrategyPolicy | None:
    passing = [row for row in grid_rows if row["passes"]]
    if not passing:
        return None
    passing.sort(
        key=lambda row: (
            -float(row["stress_net_pnl"]),
            abs(float(row["selected_calibration_gap"])),
            -float(row["hit_rate"]),
            -float(row["settled_trades"]),
            row["policy_id"],
        )
    )
    return policies[passing[0]["policy_id"]]


def evaluate_strategy(
    predictions: list[dict],
    requirements: dict,
    *,
    progress: bool = False,
) -> tuple[list[dict], StrategyPolicy | None, dict | None, list[dict]]:
    if not predictions:
        return [], None, None, []
    frame = pd.DataFrame(predictions)
    prediction_scopes = sorted(
        {
            (str(row.fold), str(row.model))
            for row in frame[["fold", "model"]].drop_duplicates().itertuples(index=False)
        }
    )
    policies = {
        policy.policy_id: policy for policy in strategy_grid(requirements, prediction_scopes)
    }
    grid_rows: list[dict] = []
    for index, policy in enumerate(policies.values(), start=1):
        if progress and (index == 1 or index % 100 == 0):
            print(
                f"[v3] strategy grid {index}/{len(policies)}",
                file=sys.stderr,
                flush=True,
            )
        trades = replay_strategy(
            frame,
            policy,
            requirements["strategy_validation_start"],
            requirements["strategy_validation_end"],
            requirements,
        )
        summary = summarize_trades(
            trades,
            requirements["strategy_validation_start"],
            requirements["strategy_validation_end"],
            bootstrap_draws=0,
        )
        passes_prebootstrap = selection_passes(
            summary,
            requirements,
            require_bootstrap=False,
        )
        if passes_prebootstrap:
            summary = summarize_trades(
                trades,
                requirements["strategy_validation_start"],
                requirements["strategy_validation_end"],
                bootstrap_draws=requirements.get("validation_bootstrap_draws", 10_000),
            )
        passes = passes_prebootstrap and selection_passes(summary, requirements)
        grid_rows.append(
            {"policy_id": policy.policy_id, **asdict(policy), **summary, "passes": passes}
        )
    selected = select_policy(grid_rows, policies)
    if selected is None:
        return grid_rows, None, None, []
    test_trades = replay_strategy(
        frame,
        selected,
        requirements["strategy_test_start"],
        requirements["strategy_test_end"],
        requirements,
    )
    test_summary = summarize_trades(
        test_trades,
        requirements["strategy_test_start"],
        requirements["strategy_test_end"],
        bootstrap_draws=requirements.get("test_bootstrap_draws", 10_000),
    )
    return grid_rows, selected, test_summary, test_trades


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _json_dump(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def render_report(result: dict) -> str:
    lines = [
        "# Rolling Walk-Forward v3 Result",
        "",
        f"**Status: {result['status']}**",
        "",
        result["status_reason"],
        "",
        "## Probability Models",
        "",
    ]
    for row in result["probability_leaderboard"][:12]:
        lines.append(
            f"- `{row['fold']} / {row['model']}`: log loss "
            f"{row['mean_log_loss']:.4f}, Brier {row['mean_brier']:.4f}, "
            f"top-one {row['top1_accuracy']:.1%}, groups {row['quote_groups']}"
        )
    lines.extend(["", "## Strategy", ""])
    selected = result.get("selected_policy")
    if selected is None:
        lines.append("No policy met the stricter validation requirements; status is abstention.")
    else:
        summary = result["test_summary"]
        lines.extend(
            [
                f"- Selected policy: `{selected['policy_id']}`",
                f"- Test trades: {summary['settled_trades']}",
                f"- Hit rate: {summary['hit_rate']:.1%}"
                if summary["hit_rate"] is not None
                else "- Hit rate: n/a",
                f"- Selected calibration gap: {summary['selected_calibration_gap']:.3f}"
                if summary["selected_calibration_gap"] is not None
                else "- Selected calibration gap: n/a",
                f"- Net PnL: ${summary['net_pnl']:.2f}",
                f"- Doubled-fee stress PnL: ${summary['stress_net_pnl']:.2f}",
                f"- Day-bootstrap lower PnL: "
                f"${summary['day_bootstrap_net_pnl_95_lower']:.2f}"
                if summary["day_bootstrap_net_pnl_95_lower"] is not None
                else "- Day-bootstrap lower PnL: n/a",
            ]
        )
        lines.extend(["", "## Success Checks", ""])
        for name, passed in result["success_checks"].items():
            lines.append(f"- {'PASS' if passed else 'FAIL'}: `{name}`")
    lines.extend(
        [
            "",
            "## Limits",
            "",
            result["test_independence_warning"],
            "These are historical rolling development results. A future frozen paper window "
            "is still required before any promotion.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(data_dir: Path, output_dir: Path, requirements_path: Path) -> dict:
    requirements = load_requirements(requirements_path)
    frame, integrity_errors, files, quality = build_frame(data_dir)
    predictions, fold_rows = generate_oos_predictions(frame, requirements, progress=True)
    metrics = probability_metrics(predictions, requirements["probability_floor"])
    metrics_sorted = sorted(metrics, key=lambda row: (row["mean_log_loss"], row["mean_brier"]))
    strategy_rows, selected, test_summary, test_trades = evaluate_strategy(
        predictions,
        requirements,
        progress=True,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "folds.csv", fold_rows)
    write_csv(output_dir / "fold_predictions.csv", predictions)
    write_csv(output_dir / "probability_metrics.csv", metrics)
    write_csv(output_dir / "strategy_grid.csv", strategy_rows)
    write_csv(output_dir / "test_trades.csv", test_trades)
    selected_payload = {**asdict(selected), "policy_id": selected.policy_id} if selected else None
    checks = success_checks(test_summary, requirements, integrity_errors) if selected else {}
    status = "ABSTAIN" if selected is None else ("PASS" if all(checks.values()) else "FAIL")
    status_reason = (
        "No policy met the stricter validation requirements."
        if selected is None
        else (
            "Every historical success check passed."
            if status == "PASS"
            else "At least one historical success check failed."
        )
    )
    result = {
        "model": requirements["name"],
        "status": status,
        "status_reason": status_reason,
        "selected_policy": selected_payload,
        "test_summary": test_summary,
        "success_checks": checks,
        "data_quality": quality,
        "input_integrity_errors": integrity_errors,
        "probability_leaderboard": metrics_sorted,
        "oos_prediction_rows": len(predictions),
        "fold_count": len(fold_rows),
        "test_independence_warning": requirements["test_independence_warning"],
    }
    provenance = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "requirements_sha256": sha256(requirements_path),
        "model_code_sha256": sha256(Path(__file__)),
        "v2_loader_sha256": sha256(Path(__file__).parents[1] / "predictive_model_v2" / "model.py"),
        "inputs": {
            name: {"path": str(path), "sha256": sha256(path)} for name, path in files.items()
        },
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
            "joblib": joblib.__version__,
        },
        "live_trading": False,
    }
    _json_dump(output_dir / "selected_policy.json", selected_payload)
    _json_dump(output_dir / "summary.json", result)
    _json_dump(output_dir / "provenance.json", provenance)
    (output_dir / "REPORT.md").write_text(render_report(result), encoding="utf-8")
    return result
