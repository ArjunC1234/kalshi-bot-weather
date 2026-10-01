"""Causal regularized bracket-probability model and frozen replay."""

from __future__ import annotations

import csv
import itertools
import json
import platform
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from reliable_model_v1.model import (
    fee,
    load_quotes,
    load_raw_facts,
    load_requirements,
    parse_time,
    sha256,
    summarize,
    write_csv,
)

ANCHORS = [
    "nws_anchor_high_f",
    "hrrr_projected_high_f",
    "nbm_projected_high_f",
    "ensemble_raw_median_high_f",
]
BASE_NUMERIC = [
    "hours_since_climate_start",
    "bracket_index",
    "is_lower_tail",
    "is_upper_tail",
    "source_disagreement",
    "observed_above_upper",
    "observed_above_lower",
]


@dataclass(frozen=True)
class ModelSpec:
    name: str
    use_market: bool
    c: float


@dataclass(frozen=True)
class TradePolicy:
    side: str
    minimum_probability: float
    minimum_net_edge: float
    minimum_entry_price: float
    maximum_entry_price: float
    maximum_spread: float
    minimum_climate_hour: int

    @property
    def policy_id(self) -> str:
        return (
            f"{self.side}_p{self.minimum_probability:.2f}_e{self.minimum_net_edge:.2f}_"
            f"px{self.minimum_entry_price:.2f}-{self.maximum_entry_price:.2f}_"
            f"s{self.maximum_spread:.2f}_h{self.minimum_climate_hour}"
        )


def _number(value: object) -> float:
    result = pd.to_numeric(value, errors="coerce")
    return float(result) if pd.notna(result) else np.nan


def _load_weather_rows(
    path: Path, raw: dict
) -> tuple[dict[tuple[str, str], list[dict]], list[str], int]:
    by_event: dict[tuple[str, str], list[dict]] = {}
    errors: list[str] = []
    excluded_missing_receipts = 0
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                source_ids = json.loads(row["source_payload_ids"])
            except (json.JSONDecodeError, KeyError) as exc:
                errors.append(f"weather {row.get('weather_snapshot_id')} invalid sources: {exc}")
                continue
            source_facts = [raw.get(raw_id) for raw_id in source_ids.values()]
            if not source_facts or any(fact is None for fact in source_facts):
                # A provider failure is an expected exclusion, not evidence that a
                # row admitted to the model crossed the information boundary.
                excluded_missing_receipts += 1
                continue
            feature = {
                "available": max(fact.received for fact in source_facts),
                "weather_snapshot_id": row["weather_snapshot_id"],
                "observed_high_so_far_f": _number(row.get("observed_high_so_far_f")),
                "weather_source_stddev_f": _number(row.get("weather_source_stddev_f")),
            }
            for anchor in ANCHORS:
                feature[anchor] = _number(row.get(anchor))
            key = (row["city"], row["event_ticker"])
            by_event.setdefault(key, []).append(feature)
    for rows in by_event.values():
        rows.sort(key=lambda row: (row["available"], row["weather_snapshot_id"]))
    return by_event, errors, excluded_missing_receipts


def _market_rows(path: Path) -> dict[tuple[str, str], list[dict]]:
    groups: dict[tuple[str, str], list[dict]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            groups.setdefault((row["event_ticker"], row["raw_payload_id"]), []).append(row)
    return groups


def _settlements(path: Path) -> tuple[dict[str, dict], list[str]]:
    rows: dict[str, dict] = {}
    errors: list[str] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["validation_status"] != "valid" or row["source_provider"] != "kalshi":
                continue
            value = {
                "winner_ticker": row["winner_ticker"],
                "settled_at": parse_time(row["settled_at_utc"]),
                "target_date": row["target_date"],
            }
            previous = rows.get(row["event_ticker"])
            if previous is not None and previous != value:
                errors.append(f"conflicting settlement for {row['event_ticker']}")
            rows[row["event_ticker"]] = value
    return rows, errors


def build_frame(data_dir: Path) -> tuple[pd.DataFrame, list[str], dict[str, Path], dict]:
    files = {
        "raw": data_dir / "raw_payloads.csv",
        "weather": data_dir / "weather_snapshots.csv",
        "market": data_dir / "market_snapshots.csv",
        "settlements": data_dir / "settlements.csv",
    }
    raw, raw_errors = load_raw_facts(files["raw"])
    quotes, quote_errors = load_quotes(files["market"], raw)
    weather, weather_errors, excluded_weather = _load_weather_rows(files["weather"], raw)
    market = _market_rows(files["market"])
    settlements, settlement_errors = _settlements(files["settlements"])
    errors = raw_errors + quote_errors + weather_errors + settlement_errors

    pointers: dict[tuple[str, str], int] = {}
    records: list[dict] = []
    universe: dict[str, set[str]] = {}
    for quote in quotes:
        if not quote.complete:
            errors.append(
                f"incomplete quote {quote.event}/{quote.raw_id}: {quote.integrity_reason}"
            )
            continue
        key = (quote.city, quote.event)
        available = weather.get(key, [])
        pointer = pointers.get(key, -1)
        while (
            pointer + 1 < len(available) and available[pointer + 1]["available"] <= quote.requested
        ):
            pointer += 1
        pointers[key] = pointer
        if pointer < 0:
            continue
        feature = available[pointer]
        if feature["available"] > quote.requested:
            errors.append(f"availability reversal {quote.event}/{quote.raw_id}")
            continue
        rows = market[(quote.event, quote.raw_id)]
        universe.setdefault(quote.event, set()).update(row["market_ticker"] for row in rows)
        settlement = settlements.get(quote.event)
        for row in rows:
            lower = _number(row.get("bracket_lower_f"))
            upper = _number(row.get("bracket_upper_f"))
            record = {
                "city": quote.city,
                "event": quote.event,
                "target_date": quote.target_date,
                "market_raw_id": quote.raw_id,
                "market_ticker": row["market_ticker"],
                "market_requested_at": quote.requested,
                "market_received_at": quote.received,
                "weather_available_at": feature["available"],
                "weather_snapshot_id": feature["weather_snapshot_id"],
                "hours_since_climate_start": quote.hours_since_climate_start,
                "bracket_index": int(row["bracket_index"]),
                "bracket_lower_f": lower,
                "bracket_upper_f": upper,
                "is_lower_tail": int(row["is_lower_tail"] == "True"),
                "is_upper_tail": int(row["is_upper_tail"] == "True"),
                "yes_bid": _number(row.get("yes_bid_dollars")),
                "yes_ask": _number(row.get("yes_ask_dollars")),
                "yes_ask_size": _number(row.get("yes_ask_size")),
                "no_bid": _number(row.get("no_bid_dollars")),
                "no_ask": _number(row.get("no_ask_dollars")),
                "no_ask_size": _number(row.get("no_ask_size")),
                "market_probability": _number(row.get("normalized_market_midpoint_probability")),
                "winner_ticker": settlement["winner_ticker"] if settlement else "",
                "settled_at": settlement["settled_at"] if settlement else pd.NaT,
                "label_available": settlement is not None,
                "availability_violation": feature["available"] > quote.requested,
            }
            record.update(feature)
            records.append(record)

    for event, settlement in settlements.items():
        if event in universe and settlement["winner_ticker"] not in universe[event]:
            errors.append(f"settlement winner absent from market universe for {event}")
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        raise ValueError("No causally aligned market/weather rows")
    frame["y"] = frame["market_ticker"].eq(frame["winner_ticker"]).astype(float)
    frame.loc[~frame["label_available"], "y"] = np.nan
    quality = {
        "weather_rows_excluded_missing_source_receipt": excluded_weather,
        "causally_aligned_contract_rows": len(frame),
    }
    return frame, errors, files, quality


def feature_frame(frame: pd.DataFrame, use_market: bool) -> pd.DataFrame:
    values = pd.DataFrame(index=frame.index)
    values["city"] = frame["city"]
    for column in BASE_NUMERIC[:5]:
        if column == "source_disagreement":
            values[column] = frame["weather_source_stddev_f"]
        else:
            values[column] = frame[column]

    lower = frame["bracket_lower_f"] - 0.5
    upper = frame["bracket_upper_f"] + 0.5
    observed = frame["observed_high_so_far_f"]
    values["observed_above_upper"] = (observed - upper).clip(-10, 10).fillna(-10)
    values["observed_above_lower"] = (observed - lower).clip(-10, 10).fillna(10)
    for anchor in ANCHORS:
        values[f"{anchor}_above_lower"] = (frame[anchor] - lower).clip(-15, 15).fillna(15)
        values[f"{anchor}_below_upper"] = (upper - frame[anchor]).clip(-15, 15).fillna(15)
    if use_market:
        probability = frame["market_probability"].clip(0.001, 0.999)
        values["market_logit"] = np.log(probability / (1 - probability))
    return values


def fit_model(
    frame: pd.DataFrame, spec: ModelSpec, requirements: dict
) -> tuple[Pipeline, datetime]:
    fit = frame[
        frame["target_date"].between(requirements["fit_start"], requirements["fit_end"])
        & frame["label_available"]
    ].copy()
    if fit["event"].nunique() < 60:
        raise ValueError("Fewer than 60 independent fit events")
    ready = max(fit["settled_at"])
    weights = 1 / fit.groupby("event")["event"].transform("size")
    features = feature_frame(fit, spec.use_market)
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
                LogisticRegression(C=spec.c, max_iter=2000, random_state=17),
            ),
        ]
    )
    pipeline.fit(features, fit["y"].astype(int), logit__sample_weight=weights)
    return pipeline, ready


def predict_probabilities(frame: pd.DataFrame, pipeline: Pipeline, spec: ModelSpec) -> pd.Series:
    raw = pd.Series(
        pipeline.predict_proba(feature_frame(frame, spec.use_market))[:, 1],
        index=frame.index,
    )
    totals = raw.groupby([frame["event"], frame["market_raw_id"]]).transform("sum")
    return (raw / totals.clip(lower=1e-12)).clip(0.0001, 0.9999)


def policy_grid(requirements: dict) -> list[TradePolicy]:
    grid = requirements["candidate_grid"]
    return [
        TradePolicy(*values)
        for values in itertools.product(
            grid["side"],
            grid["minimum_probability"],
            grid["minimum_net_edge"],
            grid["minimum_entry_price"],
            grid["maximum_entry_price"],
            grid["maximum_spread"],
            grid["minimum_climate_hour"],
        )
        if values[3] < values[4]
    ]


def replay(
    frame: pd.DataFrame,
    probabilities: pd.Series,
    policy: TradePolicy,
    start: str,
    end: str,
    model_ready: datetime,
) -> list[dict]:
    base = frame[frame["target_date"].between(start, end)].copy()
    base["yes_probability"] = probabilities.loc[base.index]
    parts = []
    for side in ("yes", "no"):
        if policy.side == "no" and side != "no":
            continue
        part = base.copy()
        part["side"] = side
        part["probability"] = (
            part["yes_probability"] if side == "yes" else 1 - part["yes_probability"]
        )
        part["ask"] = part[f"{side}_ask"]
        part["bid"] = part[f"{side}_bid"]
        part["depth"] = part[f"{side}_ask_size"]
        part["hit"] = part["y"] if side == "yes" else 1 - part["y"]
        parts.append(part)
    candidates = pd.concat(parts, ignore_index=False)
    candidates["spread"] = candidates["ask"] - candidates["bid"]
    candidates["execution_price"] = candidates["ask"] + 0.01
    candidates["fee"] = candidates["execution_price"].map(lambda price: fee(price, 1))
    candidates["stress_fee"] = candidates["execution_price"].map(lambda price: fee(price, 2))
    candidates["net_edge"] = (
        candidates["probability"] - candidates["execution_price"] - candidates["fee"]
    )
    candidates = candidates[
        (candidates["market_requested_at"] >= model_ready)
        & (candidates["weather_available_at"] <= candidates["market_requested_at"])
        & (candidates["hours_since_climate_start"] >= policy.minimum_climate_hour)
        & candidates["probability"].ge(policy.minimum_probability)
        & candidates["net_edge"].ge(policy.minimum_net_edge)
        & candidates["execution_price"].between(
            policy.minimum_entry_price, policy.maximum_entry_price
        )
        & candidates["spread"].between(0, policy.maximum_spread)
        & candidates["depth"].ge(1)
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
    results = []
    for row in chosen.itertuples():
        label_available = bool(row.label_available)
        hit = bool(row.hit) if label_available else ""
        gross = (float(hit) - row.execution_price) if label_available else ""
        results.append(
            {
                "model": "",
                "policy_id": policy.policy_id,
                "city": row.city,
                "event": row.event,
                "target_date": row.target_date,
                "market_raw_id": row.market_raw_id,
                "market_ticker": row.market_ticker,
                "side": row.side,
                "market_requested_at": row.market_requested_at.isoformat(),
                "market_received_at": row.market_received_at.isoformat(),
                "weather_snapshot_id": row.weather_snapshot_id,
                "weather_available_at": row.weather_available_at.isoformat(),
                "model_ready_at": model_ready.isoformat(),
                "hours_since_climate_start": row.hours_since_climate_start,
                "probability": row.probability,
                "net_edge": row.net_edge,
                "bid": row.bid,
                "ask": row.ask,
                "depth": row.depth,
                "spread": row.spread,
                "execution_price": row.execution_price,
                "fee": row.fee,
                "stress_fee": row.stress_fee,
                "winner_ticker": row.winner_ticker,
                "label_available": label_available,
                "hit": hit,
                "gross_pnl": gross,
                "net_pnl": gross - row.fee if label_available else "",
                "stress_net_pnl": gross - row.stress_fee if label_available else "",
                "availability_violation": row.weather_available_at > row.market_requested_at,
                "observation_label_contradiction": False,
            }
        )
    return results


def selection_passes(summary: dict, requirements: dict) -> bool:
    gate = requirements["selection_requirements"]
    return (
        summary["settled_trades"] >= gate["minimum_trades"]
        and summary["hit_rate"] is not None
        and summary["hit_rate"] >= gate["minimum_hit_rate"]
        and summary["net_pnl"] > 0
        and summary["stress_net_pnl"] > 0
        and summary["availability_violations"] == 0
    )


def calendar_day_bootstrap_lower(
    rows: list[dict], start: str, end: str, draws: int = 10_000
) -> float | None:
    settled = [row for row in rows if row["label_available"]]
    if not settled:
        return None
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    by_day: dict[str, float] = defaultdict(float)
    for row in settled:
        by_day[row["target_date"]] += float(row["net_pnl"])
    days = [
        (first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)
    ]
    rng = random.Random(17)
    totals = [sum(by_day[days[rng.randrange(len(days))]] for _ in days) for _ in range(draws)]
    totals.sort()
    return totals[int(0.025 * len(totals))]


def settlement_counts(frame: pd.DataFrame, start: str, end: str) -> dict[str, int]:
    rows = frame[frame["target_date"].between(start, end) & frame["label_available"]]
    counts = rows.drop_duplicates("event").groupby("target_date")["event"].nunique()
    return {str(day): int(count) for day, count in counts.items()}


def success_checks(
    summary: dict,
    requirements: dict,
    integrity_errors: list[str],
    labels_by_day: dict[str, int],
) -> dict:
    gate = requirements["success_requirements"]
    lower = summary["iid_exact_95_hit_lower_bound"]
    bootstrap = summary["day_bootstrap_net_pnl_95_lower"]
    halves = [half["net_pnl"] for half in summary["seven_day_halves"]]
    leave_out = list(summary["leave_one_city_out_net_pnl"].values())
    start = date.fromisoformat(requirements["test_start"])
    end = date.fromisoformat(requirements["test_end"])
    expected_days = [
        (start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)
    ]
    return {
        "full_14_day_window_complete": (end - start).days + 1 == 14
        and all(labels_by_day.get(day) == 6 for day in expected_days),
        "minimum_trades": summary["settled_trades"] >= gate["minimum_trades"],
        "minimum_active_days": summary["active_days"] >= gate["minimum_active_days"],
        "minimum_cities": summary["cities"] >= gate["minimum_cities"],
        "maximum_city_trade_fraction": summary["maximum_city_trade_fraction"] is not None
        and summary["maximum_city_trade_fraction"] <= gate["maximum_city_trade_fraction"],
        "minimum_hit_rate": summary["hit_rate"] is not None
        and summary["hit_rate"] >= gate["minimum_hit_rate"],
        "minimum_iid_exact_95_hit_lower_bound": lower is not None
        and lower >= gate["minimum_iid_exact_95_hit_lower_bound"],
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


def _selection_key(row: dict) -> tuple:
    policy = row["policy"]
    spec = row["spec"]
    return (
        -row["summary"]["stress_net_pnl"],
        -policy.minimum_probability,
        -policy.minimum_net_edge,
        policy.maximum_entry_price,
        policy.maximum_spread,
        -policy.minimum_climate_hour,
        policy.side != "no",
        spec.c,
        row["candidate_id"],
    )


def render_report(result: dict) -> str:
    selected = result.get("selected")
    lines = [
        "# Causal Bracket Logit v2 Result",
        "",
        f"**Status: {result['status']}**",
        "",
        result["status_reason"],
        "",
        "## Selection",
        "",
    ]
    if selected is None:
        lines.append("No model-policy pair met every frozen selection gate.")
    else:
        selection = selected["selection"]
        lines.extend(
            [
                f"- Model: `{selected['model']}`",
                f"- Policy: `{selected['policy_id']}`",
                f"- Model ready: {selected['model_ready_at']}",
                f"- Selection trades / hit / stress PnL: {selection['settled_trades']} / "
                f"{selection['hit_rate']:.1%} / ${selection['stress_net_pnl']:.2f}",
            ]
        )
    test = result.get("test")
    if test:
        lines.extend(
            [
                "",
                "## Frozen 14-day test",
                "",
                f"- Trades: {test['settled_trades']}",
                f"- Wins / losses: {test['wins']} / {test['losses']}",
                f"- Hit rate: {test['hit_rate']:.1%}",
                f"- Exact one-sided 95% hit lower bound: "
                f"{test['iid_exact_95_hit_lower_bound']:.1%}",
                f"- Net PnL: ${test['net_pnl']:.2f}",
                f"- Doubled-fee stress PnL: ${test['stress_net_pnl']:.2f}",
                f"- Day-bootstrap 95% lower PnL: ${test['day_bootstrap_net_pnl_95_lower']:.2f}",
                f"- Active days / cities: {test['active_days']} / {test['cities']}",
                "",
                "## Frozen checks",
                "",
            ]
        )
        for name, passed in result["success_checks"].items():
            lines.append(f"- {'PASS' if passed else 'FAIL'}: `{name}`")
    lines.extend(
        [
            "",
            "## Limits",
            "",
            result["test_independence_warning"],
            "Passing this replay would support prospective paper trading only. It would not prove "
            "future profitability, fill quality, or authorize live orders.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(data_dir: Path, output_dir: Path, requirements_path: Path) -> dict:
    requirements = load_requirements(requirements_path)
    frame, integrity_errors, files, quality = build_frame(data_dir)
    specs = [ModelSpec(**row) for row in requirements["model_candidates"]]
    policies = policy_grid(requirements)
    candidates: list[dict] = []
    fitted: dict[str, tuple[Pipeline, datetime, pd.Series]] = {}
    grid_rows: list[dict] = []

    for spec in specs:
        pipeline, ready = fit_model(frame, spec, requirements)
        probabilities = predict_probabilities(frame, pipeline, spec)
        fitted[spec.name] = (pipeline, ready, probabilities)
        for policy in policies:
            trades = replay(
                frame,
                probabilities,
                policy,
                requirements["selection_start"],
                requirements["selection_end"],
                ready,
            )
            summary = summarize(trades)
            passed = selection_passes(summary, requirements)
            candidate_id = f"{spec.name}__{policy.policy_id}"
            row = {
                "candidate_id": candidate_id,
                "model": spec.name,
                "policy": policy,
                "spec": spec,
                "summary": summary,
                "passes": passed,
            }
            candidates.append(row)
            grid_rows.append(
                {
                    "candidate_id": candidate_id,
                    "model": spec.name,
                    **asdict(policy),
                    **summary,
                    "passes": passed,
                }
            )
    passing = sorted((row for row in candidates if row["passes"]), key=_selection_key)
    selected = passing[0] if passing else None
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "selection_grid.csv", grid_rows)
    result = {
        "model": requirements["name"],
        "status": "FAIL",
        "status_reason": "No model-policy pair met every frozen selection gate.",
        "selected": None,
        "test": None,
        "success_checks": {},
        "input_integrity_errors": integrity_errors,
        "data_quality": quality,
        "test_independence_warning": requirements["test_independence_warning"],
    }
    if selected is not None:
        spec = selected["spec"]
        policy = selected["policy"]
        pipeline, ready, probabilities = fitted[spec.name]
        test_trades = replay(
            frame,
            probabilities,
            policy,
            requirements["test_start"],
            requirements["test_end"],
            ready,
        )
        for trade in test_trades:
            trade["model"] = spec.name
        test_summary = summarize(test_trades, requirements["test_start"])
        test_summary["day_bootstrap_net_pnl_95_lower"] = calendar_day_bootstrap_lower(
            test_trades, requirements["test_start"], requirements["test_end"]
        )
        labels_by_day = settlement_counts(
            frame, requirements["test_start"], requirements["test_end"]
        )
        quality["test_valid_settlements_by_day"] = labels_by_day
        checks = success_checks(test_summary, requirements, integrity_errors, labels_by_day)
        result.update(
            {
                "selected": {
                    "model": spec.name,
                    "policy_id": policy.policy_id,
                    "policy": asdict(policy),
                    "model_ready_at": ready.isoformat(),
                    "selection": selected["summary"],
                },
                "test": test_summary,
                "success_checks": checks,
                "status": "PASS" if all(checks.values()) else "FAIL",
                "status_reason": (
                    "Every frozen success requirement passed."
                    if all(checks.values())
                    else "At least one frozen 14-day success requirement failed."
                ),
            }
        )
        write_csv(output_dir / "test_trades.csv", test_trades)
        selection_trades = replay(
            frame,
            probabilities,
            policy,
            requirements["selection_start"],
            requirements["selection_end"],
            ready,
        )
        for trade in selection_trades:
            trade["model"] = spec.name
        write_csv(output_dir / "selection_trades.csv", selection_trades)
        joblib.dump(pipeline, output_dir / "selected_model.joblib")

    serializable = json.loads(json.dumps(result, default=str))
    (output_dir / "summary.json").write_text(
        json.dumps(serializable, indent=2) + "\n", encoding="utf-8"
    )
    provenance = {
        "requirements_sha256": sha256(requirements_path),
        "model_code_sha256": sha256(Path(__file__)),
        "shared_evaluator_sha256": sha256(
            Path(__file__).parents[1] / "reliable_model_v1" / "model.py"
        ),
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
        "test_scored_after_selection": True,
        "live_trading": False,
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "REPORT.md").write_text(render_report(serializable), encoding="utf-8")
    return serializable
