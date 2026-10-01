"""Frozen, chronological research with explicit costs and outcome separation.

Run with python -m scripts.profitability_research from next-gen.
No exchange orders or remote writes are implemented.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.special import ndtr
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from scripts.hit80_14d_research import (
    _explode_probabilities, _load_json_gz, _materialize_candidates,
    _select_rows, _summary_metrics,
)

KEYS = ["city", "event_ticker", "snapshot_time_utc"]
WX = ["nws_anchor_high_f", "observed_high_so_far_f", "hrrr_projected_high_f",
      "nbm_projected_high_f", "ensemble_raw_median_high_f"]
FIT_START, FIT_END, FIT_ASOF = "2026-07-15", "2026-08-03", "2026-08-04T00:00:00Z"
VAL_START, VAL_END = "2026-08-04", "2026-08-17"
TEST_START, TEST_END = "2026-08-18", "2026-08-31"


def read_frame(path: Path) -> tuple[pd.DataFrame, dict]:
    tables = {name: pd.DataFrame(_load_json_gz(path / f"{name}.json.gz"))
              for name in ("market_snapshots", "weather_snapshots", "events", "settlements")}
    market, weather, events, settlements = (tables[k] for k in tables)
    for frame in (market, weather, events):
        frame["snapshot_time_utc"] = pd.to_datetime(frame.snapshot_time_utc, utc=True)
    if market.duplicated(KEYS + ["market_ticker"]).any():
        raise ValueError("Duplicate quote keys")
    if settlements.event_ticker.duplicated().any():
        raise ValueError("Duplicate settlements")
    weather["known_future_feature"] = False
    weather["latest_feature_time"] = pd.Series(pd.NaT, index=weather.index, dtype="datetime64[ns, UTC]")
    for index, row in weather.iterrows():
        features = row.get("features") or {}
        times = [features.get(k) for k in ("nws_daily_generated_at", "nws_daily_update_time",
                                          "nws_hourly_generated_at", "nws_hourly_update_time")]
        times.append(row.get("latest_observation_time_utc"))
        weather.at[index, "known_future_feature"] = any(
            pd.notna(t) and pd.Timestamp(t) > row.snapshot_time_utc for t in times if t)
        valid_times = [pd.Timestamp(t) for t in times if t and pd.notna(t)]
        if valid_times:
            weather.at[index, "latest_feature_time"] = max(valid_times)
    frame = market.merge(weather[KEYS + WX + ["known_future_feature", "latest_feature_time"]], on=KEYS,
                         validate="many_to_one", how="inner")
    event_times = events[KEYS + ["climate_day_start_utc", "climate_day_end_utc"]].drop_duplicates()
    frame = frame.merge(event_times,
                        on=KEYS, suffixes=("", "_event"), validate="many_to_one", how="inner")
    frame = frame.merge(settlements[["event_ticker", "winner_ticker", "settled_at_utc",
                                     "validation_status"]], on="event_ticker",
                        validate="many_to_one", how="left")
    frame["settled_at_utc"] = pd.to_datetime(frame.settled_at_utc, utc=True)
    frame["hours"] = (frame.snapshot_time_utc - pd.to_datetime(
        frame.climate_day_start_utc, utc=True)).dt.total_seconds() / 3600
    frame["end_time"] = pd.to_datetime(frame.climate_day_end_utc, utc=True)
    frame["decision_time"] = pd.to_datetime(frame.get("decision_available_at_utc", frame.snapshot_time_utc), utc=True)
    frame["decision_time"] = frame.decision_time.fillna(frame.snapshot_time_utc)
    frame["depth_verified"] = frame.get("depth_verified", False)
    frame["availability_metadata_complete"] = frame.get("availability_metadata_complete", False)
    observed_time = pd.to_datetime(frame.latest_feature_time, utc=True)
    frame["known_future_feature"] = observed_time > frame.decision_time
    frame["valid_label"] = frame.winner_ticker.notna() & frame.validation_status.eq("valid")
    universe = market.groupby("event_ticker").market_ticker.agg(set)
    for event, winner in settlements[["event_ticker", "winner_ticker"]].itertuples(index=False):
        if event in universe and winner and winner not in universe[event]:
            raise ValueError(f"Settlement winner absent from market universe: {event}")
    frame["y"] = np.where(frame.valid_label, frame.market_ticker.eq(frame.winner_ticker), np.nan)
    frame["bounded"] = frame.bracket_lower_f.notna() & frame.bracket_upper_f.notna()
    frame["market_p"] = pd.to_numeric(frame.normalized_market_midpoint_probability).clip(.001, .999)
    frame["active"] = True
    frame["rule_source"] = "unknown"
    if "metadata" in frame:
        frame["active"] = frame.metadata.map(lambda x: (x or {}).get("status", "active") == "active")
        frame["rule_source"] = frame.metadata.map(lambda x: (
            "weather_company" if "weather company" in str((x or {}).get("rules_primary", "")).lower()
            else "nws" if "national weather" in str((x or {}).get("rules_primary", "")).lower()
            else "unknown"))
    audit = {
        "path": str(path), "quotes": len(market), "joined_quotes": len(frame),
        "settled_events": int(settlements.event_ticker.nunique()),
        "settled_date_min": str(settlements.target_date.min()),
        "settled_date_max": str(settlements.target_date.max()),
        "settled_events_by_date": settlements.groupby("target_date").event_ticker.nunique().to_dict(),
        "known_future_weather_snapshots": int(weather.known_future_feature.sum()),
        "identical_event_time_duplicates_removed": len(events) - len(event_times),
        "rule_source_quote_counts": frame.rule_source.value_counts().to_dict(),
        "quote_size_columns_present": all(k in frame for k in ("yes_ask_size", "no_ask_size")),
        "verified_depth_rows": int(frame.depth_verified.sum()),
        "complete_availability_metadata_rows": int(frame.availability_metadata_complete.sum()),
        "actual_collection_timestamps_present": any(k in market for k in ("collected_at_utc", "created_at_utc")),
    }
    frame = frame.sort_values(KEYS + ["market_ticker"]).reset_index(drop=True)
    frame.attrs["settlement_availability"] = {
        str(day): str(value) for day, value in settlements.assign(
            available=pd.to_datetime(settlements.settled_at_utc, utc=True)
        ).groupby("target_date").available.max().items()
    }
    return frame, audit


def features(frame: pd.DataFrame, weather: bool) -> pd.DataFrame:
    p = frame.market_p.clip(.001, .999)
    values = pd.DataFrame({"market_logit": np.log(p / (1 - p))}, index=frame.index)
    if not weather:
        return values
    values["hours"] = frame.hours / 24
    values["bounded"] = frame.bounded.astype(float)
    lower = frame.bracket_lower_f.fillna(-np.inf) - .5
    upper = frame.bracket_upper_f.fillna(np.inf) + .5
    numerical = frame[["hrrr_projected_high_f", "nbm_projected_high_f"]].mean(axis=1)
    anchors = {"nws": frame.nws_anchor_high_f, "numerical": numerical,
               "ensemble": frame.ensemble_raw_median_high_f}
    for name, anchor in anchors.items():
        wp = (ndtr((upper - anchor) / 2) - ndtr((lower - anchor) / 2)).clip(.001, .999)
        values[name + "_logit"] = np.log(wp / (1 - wp))
    observed = frame.observed_high_so_far_f
    values["observed_above_upper"] = ((observed - upper) / 3).clip(-3, 3).replace([np.inf, -np.inf], 0)
    values["observed_above_lower"] = ((observed - lower) / 3).clip(-3, 3).replace([np.inf, -np.inf], 0)
    values["source_disagreement"] = frame[WX].std(axis=1).clip(0, 10)
    values["late_market"] = values.market_logit * values.hours
    return values


def fit_models(frame: pd.DataFrame) -> dict:
    fit = frame.loc[frame.target_date.between(FIT_START, FIT_END)
                    & frame.valid_label & (frame.settled_at_utc < pd.Timestamp(FIT_ASOF))
                    & (frame.snapshot_time_utc < pd.Timestamp(FIT_ASOF))
                    & ~frame.known_future_feature & frame.hours.between(0, 23.999)].copy()
    if fit.event_ticker.nunique() < 50:
        raise ValueError("Insufficient independent training events")
    weights = 1 / fit.groupby("event_ticker").event_ticker.transform("size")
    models = {"market": {"kind": "market"}}
    for name, weather, strength in (("market_calibrated", False, 1.),
                                     ("weather_regularized", True, .1),
                                     ("weather_market", True, 1.)):
        pipeline = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                                 StandardScaler(), LogisticRegression(C=strength, max_iter=1000,
                                                                       random_state=17))
        pipeline.fit(features(fit, weather), fit.y.astype(int), logisticregression__sample_weight=weights)
        models[name] = {"kind": "logistic", "weather": weather, "pipeline": pipeline}
    models["training_audit"] = {"rows": len(fit), "events": int(fit.event_ticker.nunique()),
                                 "max_target_date": fit.target_date.max(),
                                 "max_settlement_available": fit.settled_at_utc.max().isoformat(),
                                 "fit_asof": FIT_ASOF,
                                 "sample_weight_sum": float(weights.sum())}
    return models


def predict(frame: pd.DataFrame, model: dict) -> np.ndarray:
    if model["kind"] == "market":
        raw = frame.market_p.to_numpy()
    else:
        raw = model["pipeline"].predict_proba(features(frame, model["weather"]))[:, 1]
    # Mutually exclusive bracket probabilities must sum to one at each snapshot.
    raw = pd.Series(raw, index=frame.index)
    total = raw.groupby([frame[k] for k in KEYS]).transform("sum")
    return (raw / total.clip(lower=1e-12)).clip(.0001, .9999).to_numpy()


def neural_probabilities(frame: pd.DataFrame, report: Path) -> np.ndarray:
    probs = _explode_probabilities(report / "bracket_distributions.csv").rename(
        columns={"snapshot_hour_utc": "snapshot_time_utc"})
    probs.snapshot_time_utc = pd.to_datetime(probs.snapshot_time_utc, utc=True)
    joined = frame[KEYS + ["market_ticker"]].merge(probs, on=KEYS + ["market_ticker"],
                                                how="left", validate="one_to_one")
    summary = json.loads((report / "summary.json").read_text())
    folds = summary.get("folds")
    if not folds:
        folds = [{"train_start_date": summary.get("train_start_date", summary.get("artifact_train_start_date")),
                  "train_end_date": summary.get("train_end_date", summary.get("artifact_train_end_date")),
                  "test_start_date": str(frame.target_date.min()), "test_end_date": str(frame.target_date.max())}]
    ready = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    availability = frame.attrs.get("settlement_availability", {})
    for fold in folds:
        if not fold["train_start_date"] or not fold["train_end_date"]:
            raise ValueError("Model report does not identify its training dates")
        times = [pd.Timestamp(t) for d, t in availability.items()
                 if fold["train_start_date"] <= d <= fold["train_end_date"]]
        if not times:
            raise ValueError("Missing settlement availability for the model's training range")
        mask = frame.target_date.between(fold["test_start_date"], fold["test_end_date"])
        ready.loc[mask] = max(times)
    probabilities = joined.yes_probability.to_numpy()
    probabilities[~model_ready_mask(frame.decision_time, ready)] = np.nan
    return probabilities


def model_ready_mask(decision_times: pd.Series, model_ready_times: pd.Series) -> np.ndarray:
    return (model_ready_times.notna() & (decision_times >= model_ready_times)).to_numpy()


@dataclass(frozen=True)
class Policy:
    side: str = "no"
    min_price: float = .65
    max_price: float = .95
    min_hours: float = 14
    min_net_edge: float = .02
    bounded_only: bool = False
    min_probability: float = .85
    max_spread: float = .05


def policies() -> list[Policy]:
    return [Policy(side, lo, hi, hour, edge, bounded)
            for side, (lo, hi), hour, edge, bounded in itertools.product(
                ("no", "all"), ((.50, .85), (.65, .95)), (10, 14, 18), (.02, .05), (False, True))]


def fee(contracts: int, price: float, multiplier: float = 1.) -> float:
    # Conservative per-order cash debit; no speculative rounding rebates.
    return math.ceil((multiplier * .07 * contracts * price * (1 - price) - 1e-12) * 100) / 100


def side_rows(frame: pd.DataFrame, probabilities: np.ndarray) -> pd.DataFrame:
    parts = []
    for side in ("yes", "no"):
        part = frame.copy()
        part["probability"] = probabilities if side == "yes" else 1 - probabilities
        part["side"] = side
        part["ask"] = pd.to_numeric(part[side + "_ask_dollars"], errors="coerce")
        part["bid"] = pd.to_numeric(part[side + "_bid_dollars"], errors="coerce")
        part["depth"] = pd.to_numeric(part.get(side + "_ask_size", np.nan), errors="coerce")
        part["hit"] = part.y if side == "yes" else 1 - part.y
        parts.append(part)
    result = pd.concat(parts, ignore_index=True)
    result["spread"] = result.ask - result.bid
    return result


def replay(rows: pd.DataFrame, policy: Policy, start: str, end: str,
           slippage: float = .01, daily_budget: float = 40., order_cap: float = 3.,
           require_depth: bool = True, delayed: bool = False) -> pd.DataFrame:
    if delayed:
        # Freeze intentions first. Future fills never change earlier reservations
        # or cause retrospective replacement by a different trade.
        intentions = replay(rows, policy, start, end, slippage, daily_budget,
                            order_cap, require_depth, delayed=False)
        fills = []
        for intent in intentions.to_dict("records"):
            later = rows.loc[rows.event_ticker.eq(intent["event_ticker"])
                             & rows.market_ticker.eq(intent["market_ticker"])
                             & rows.side.eq(intent["side"])
                             & (rows.snapshot_time_utc > pd.Timestamp(intent["decision_time"]))]
            if later.empty:
                continue
            quote = later.sort_values("snapshot_time_utc").iloc[0]
            if (quote.snapshot_time_utc >= quote.end_time or not quote.active
                    or not np.isfinite(quote.ask) or quote.ask > intent["price"]
                    or (require_depth and not quote.depth >= intent["contracts"])):
                continue
            fills.append({**intent, "entry_time": str(quote.snapshot_time_utc)})
        return pd.DataFrame(fills)
    data = rows.loc[rows.target_date.between(start, end)].copy()
    if "decision_time" not in data:
        data["decision_time"] = data.snapshot_time_utc
    data["price"] = data.ask + slippage
    data["estimated_edge"] = data.probability - data.price - .07 * data.price * (1 - data.price)
    allowed = (data.active & ~data.known_future_feature & data.hours.ge(policy.min_hours)
               & (data.decision_time < data.end_time)
               & data.ask.between(policy.min_price, policy.max_price)
               & data.bid.ge(0) & data.spread.between(0, policy.max_spread + 1e-9)
               & data.price.gt(0) & data.price.lt(1)
               & data.probability.ge(policy.min_probability)
               & data.estimated_edge.ge(policy.min_net_edge - 1e-9))
    if policy.side != "all":
        allowed &= data.side.eq(policy.side)
    if policy.bounded_only:
        allowed &= data.bounded
    if require_depth:
        allowed &= data.depth.ge(1)
        if "depth_verified" in data:
            allowed &= data.depth_verified & data.availability_metadata_complete
    # Outcomes are deliberately absent from the decision mask and sorting keys.
    eligible = data.loc[allowed].sort_values(
        ["decision_time", "estimated_edge", "event_ticker", "market_ticker", "side"],
        ascending=[True, False, True, True, True])
    held, budget, trades = set(), {}, []
    for row in eligible.to_dict("records"):
        event, day = row["event_ticker"], row["target_date"]
        if event in held:
            continue
        decision_time = row["decision_time"]
        fill = row
        price = row["price"]
        depth = min(row["depth"], fill["depth"]) if require_depth else 10
        cap = min(10 if row["side"] == "no" else 20, int(depth) if np.isfinite(depth) else 0)
        cash = min(order_cap, budget.get(day, daily_budget))
        count = min(cap, math.floor(cash / price))
        while count and (count * price + fee(count, price) > cash + 1e-9
                         or row["probability"] - price - fee(count, price) / count < policy.min_net_edge - 1e-9):
            count -= 1
        if not count:
            continue
        cost = count * price
        fees = fee(count, price)
        budget[day] = budget.get(day, daily_budget) - cost - fees
        held.add(event)
        trades.append({"target_date": day, "city": row["city"], "event_ticker": event,
                       "market_ticker": row["market_ticker"], "side": row["side"],
                       "quote_time_utc": str(row["snapshot_time_utc"]),
                       "decision_time": str(decision_time), "entry_time": str(decision_time),
                       "probability": row["probability"], "quote_ask": row["ask"],
                       "price": price, "contracts": count, "fees": fees, "premium": cost,
                       "cash_debit": cost + fees, "hit": row["hit"],
                       "gross_pnl": count * (row["hit"] - price),
                       "net_pnl": count * (row["hit"] - price) - fees,
                       "bounded": row["bounded"], "rule_source": row["rule_source"],
                       "visible_depth": depth, "estimated_net_edge": row["probability"] - price - fees / count})
    return pd.DataFrame(trades)


def daily(trades: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    days = pd.Index(pd.date_range(start, end).strftime("%Y-%m-%d"), name="target_date")
    if trades.empty:
        result = pd.DataFrame(0., index=days, columns=["trades", "wins", "unsettled", "net_pnl", "fees", "cash_debit"])
    else:
        result = trades.groupby("target_date").agg(trades=("hit", "size"), wins=("hit", "sum"),
            unsettled=("hit", lambda x: x.isna().sum()), net_pnl=("net_pnl", "sum"),
            fees=("fees", "sum"), cash_debit=("cash_debit", "sum")).reindex(days, fill_value=0)
    result["hit_rate"] = result.wins / (result.trades - result.unsettled).replace(0, np.nan)
    result["cumulative_net_pnl"] = result.net_pnl.cumsum()
    return result.reset_index()


def metrics(trades: pd.DataFrame, start: str, end: str, bootstrap: bool = False) -> dict:
    d = daily(trades, start, end)
    scored = trades.loc[trades.hit.notna()] if not trades.empty else trades
    n, wins = len(scored), int(scored.hit.sum()) if len(scored) else 0
    net = float(scored.net_pnl.sum()) if n else 0.
    risk = float(scored.cash_debit.sum()) if n else 0.
    contract_count = float(scored.contracts.sum()) if n else 0.
    result = {"trades": len(trades), "settled_trades": n, "wins": wins,
              "unsettled_trades": len(trades) - n, "hit_rate": wins / n if n else None,
              "net_pnl": net, "fees": float(scored.fees.sum()) if n else 0.,
              "gross_pnl": float(scored.gross_pnl.sum()) if n else 0.,
              "cash_debit": risk, "roi": net / risk if risk else None,
              "contracts": contract_count,
              "breakeven_contract_hit_rate": risk / contract_count if contract_count else None,
              "contract_hit_rate": float((scored.hit * scored.contracts).sum()) / contract_count if contract_count else None,
              "max_daily_drawdown": float((d.cumulative_net_pnl - d.cumulative_net_pnl.cummax().clip(lower=0)).min()),
              "mean_probability": float(scored.probability.mean()) if n else None}
    if bootstrap:
        rng = np.random.default_rng(17)
        sampled = rng.integers(0, len(d), size=(10000, len(d)))
        pnl = d.net_pnl.to_numpy()[sampled].sum(axis=1)
        counts = (d.trades - d.unsettled).to_numpy()[sampled].sum(axis=1)
        hits = d.wins.to_numpy()[sampled].sum(axis=1)
        rates = np.divide(hits, counts, out=np.full(len(hits), np.nan), where=counts > 0)
        result["day_bootstrap_pnl_95_interval"] = np.quantile(pnl, [.025, .975]).tolist()
        result["day_bootstrap_hit_95_interval"] = np.nanquantile(rates, [.025, .975]).tolist() if n else [None, None]
    return result


def select(validation_results: list[dict]) -> tuple[dict, bool]:
    eligible = [r for r in validation_results if r["trades"] >= 20 and (r["hit_rate"] or 0) >= .8
                and r["net_pnl"] > 0 and r["week1_pnl"] > 0 and r["week2_pnl"] > 0]
    pool = eligible or [r for r in validation_results if r["trades"] >= 20]
    if not pool:
        pool = validation_results
    return max(pool, key=lambda r: (min(r["week1_pnl"], r["week2_pnl"]), r["net_pnl"], r["trades"])), bool(eligible)


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, default=str, allow_nan=False), encoding="utf-8")


def emit_report(output: Path, summary: dict) -> None:
    lines = ["# Profitability Research", "", f"Status: {summary['status']}", "",
             f"Selected research candidate: {summary['selected']['model']}", "",
             "Selection was frozen using August 4-17 only. August 18-31 had been examined in prior work and is retrospective.",
             "September performance was scored after saving the selection. Historical fills are simulated, not actual trades.", "",
             "September was first scored in experiment_v2 and has now been examined. The corrected experiment_v3 is an audit-controlled retrospective rerun, not another untouched test.", "",
             "| Window | Trades | Wins | Hit rate | Net PnL | Fees | ROI |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name, m in summary["windows"].items():
        rate = f"{m['hit_rate']:.1%}" if m["hit_rate"] is not None else "n/a"
        roi = f"{m['roi']:.1%}" if m["roi"] is not None else "n/a"
        lines.append(f"| {name} | {m['trades']} | {m['wins']} | {rate} | ${m['net_pnl']:.2f} | ${m['fees']:.2f} | {roi} |")
    lines += ["", "## Frozen Policy", "", "```json", json.dumps(summary["selected"], indent=2), "```", "",
              "## Fourteen-Day Breakdown", "", "| Date | Trades | Wins | Net PnL | Cumulative |",
              "|---|---:|---:|---:|---:|"]
    for row in pd.read_csv(output / "test_daily.csv").to_dict("records"):
        lines.append(f"| {row['target_date']} | {int(row['trades'])} | {int(row['wins'])} | ${row['net_pnl']:.2f} | ${row['cumulative_net_pnl']:.2f} |")
    lines += ["",
              "## Limitations", "", "- General taker multiplier 1 is assumed; fees are conservatively rounded up to cents per order, without rebates.",
              "- One-cent adverse execution is included in selection and reported main results. Price and delayed-quote sensitivities are separate files.",
              "- One position per event, $3 maximum all-in cost per order and $40 daily budget. Visible depth caps size.",
              "- Reconstructed receipt timestamps gate decision time. Filling a quote seen earlier in the collection cycle still requires an execution assumption; the next-quote stress test is only a proxy.",
              "- Bootstrap intervals group trades by day but cannot eliminate search bias or demonstrate a future minimum hit rate.",
              "- The research candidate is not enabled for live trading. An abstention status means the validation criteria failed.",
              "", "Fee references: https://kalshi.com/docs/kalshi-fee-schedule.pdf and https://docs.kalshi.com/getting_started/fee_rounding"]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def run(args):
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    if (out / "selection_lock.json").exists():
        raise ValueError("Selection already locked; choose a new output directory to preserve the experiment")
    frame, audit = read_frame(args.history)
    models = fit_models(frame)
    candidates = list(policies())
    write_json(out / "experiment_spec.json", {"fit_start": FIT_START, "fit_end": FIT_END,
        "fit_asof": FIT_ASOF, "validation": [VAL_START, VAL_END], "retrospective_test": [TEST_START, TEST_END],
        "model_names": [k for k in models if k != "training_audit"] + ["neural", "neural_shrink50", "neural_shrink25"],
        "policies": [asdict(p) for p in candidates], "slippage": .01, "seed": 17,
        "selection": "min 20 trades, 80% hit, each validation week profitable; rank worst-week net PnL"})
    validation = frame.loc[frame.target_date.between(VAL_START, VAL_END)].copy()
    neural = neural_probabilities(validation, args.rolling_report)
    validation_rows = {}
    for name, model in models.items():
        if name == "training_audit":
            continue
        validation_rows[name] = side_rows(validation, predict(validation, model))
    for name, weight in (("neural", 1.), ("neural_shrink50", .5), ("neural_shrink25", .25)):
        validation_rows[name] = side_rows(validation, weight * neural + (1 - weight) * validation.market_p.to_numpy())
    sweep = []
    for name, rows in validation_rows.items():
        for index, policy in enumerate(candidates):
            trades = replay(rows, policy, VAL_START, VAL_END)
            m = metrics(trades, VAL_START, VAL_END)
            week1 = metrics(trades.loc[trades.target_date <= "2026-08-10"] if len(trades) else trades, VAL_START, "2026-08-10")
            week2 = metrics(trades.loc[trades.target_date >= "2026-08-11"] if len(trades) else trades, "2026-08-11", VAL_END)
            sweep.append({"model": name, "policy_index": index, **m,
                          "week1_pnl": week1["net_pnl"], "week2_pnl": week2["net_pnl"]})
        print(f"Validation evaluated: {name}", flush=True)
    pd.DataFrame(sweep).to_csv(out / "validation_sweep.csv", index=False)
    selected, qualified = select(sweep)
    policy = candidates[selected["policy_index"]]
    lock = {"model": selected["model"], "policy": asdict(policy), "validation_qualified": qualified,
            "validation_metrics": selected, "locked_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
            "history_sha256": hashlib.sha256((args.history / "market_snapshots.json.gz").read_bytes()).hexdigest(),
            "status": "research_candidate" if qualified else "abstain_validation_failed"}
    joblib.dump(models, out / "models.joblib")
    lock["models_sha256"] = hashlib.sha256((out / "models.joblib").read_bytes()).hexdigest()
    lock["research_code_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write_json(out / "selection_lock.json", lock)
    print(f"LOCKED: {selected['model']} policy={selected['policy_index']} qualified={qualified}", flush=True)
    score_locked(args, frame, audit, lock, models)


def score_locked(args, frame=None, audit=None, lock=None, models=None):
    out = args.output
    lock = lock or json.loads((out / "selection_lock.json").read_text())
    if hashlib.sha256((out / "models.joblib").read_bytes()).hexdigest() != lock["models_sha256"]:
        raise ValueError("Saved models differ from the selection lock")
    models = models or joblib.load(out / "models.joblib")
    if frame is None:
        frame, audit = read_frame(args.history)
    policy, name = Policy(**lock["policy"]), lock["model"]
    windows = {"validation": (frame, VAL_START, VAL_END, args.rolling_report),
               "test": (frame, TEST_START, TEST_END, args.fixed_report)}
    audits = {"history": audit, "training": models["training_audit"]}
    if args.fresh is not None:
        fresh, audits["fresh"] = read_frame(args.fresh)
        fresh.attrs["settlement_availability"] = {
            **frame.attrs["settlement_availability"], **fresh.attrs["settlement_availability"]}
        complete = [d for d, n in audits["fresh"]["settled_events_by_date"].items()
                    if n == fresh.city.nunique() and args.fresh_start <= d <= args.fresh_end]
        if not complete:
            raise ValueError("No fully settled fresh date")
        last = max(complete)
        windows["fresh"] = (fresh, args.fresh_start, last, args.fresh_neural_report)
    summary = {"status": lock["status"], "selected": {"model": name, "policy": lock["policy"],
               "validation_qualified": lock["validation_qualified"]}, "windows": {}, "audits": audits}
    sensitivities = []
    comparison = []
    for window, (data, start, end, report) in windows.items():
        data = data.loc[data.target_date.between(start, end)].copy()
        if name.startswith("neural"):
            if report is None:
                print(f"Need frozen Neuralcaster report to score {window}; selection remains locked", flush=True)
                write_json(out / "summary.json", summary)
                return
            p = neural_probabilities(data, report)
            weight = {"neural": 1., "neural_shrink50": .5, "neural_shrink25": .25}[name]
            p = weight * p + (1 - weight) * data.market_p.to_numpy()
        else:
            p = predict(data, models[name])
        rows = side_rows(data, p)
        trades = replay(rows, policy, start, end)
        trades.to_csv(out / f"{window}_trades.csv", index=False)
        daily_frame = daily(trades, start, end)
        coverage = data.groupby("target_date").event_ticker.nunique()
        daily_frame["events_with_snapshots"] = daily_frame.target_date.map(coverage).fillna(0).astype(int)
        daily_frame.to_csv(out / f"{window}_daily.csv", index=False)
        summary["windows"][window] = {"start": start, "end": end, **metrics(trades, start, end, True)}
        for slip in (0, .01, .02):
            trial = replay(rows, policy, start, end, slippage=slip)
            sensitivities.append({"window": window, "slippage": slip, "execution": "snapshot",
                                  **metrics(trial, start, end)})
        trial = replay(rows, policy, start, end, delayed=True)
        sensitivities.append({"window": window, "slippage": .01, "execution": "next_quote_limit_proxy",
                              **metrics(trial, start, end)})
        if len(trades):
            for dimension in ("city", "side", "bounded", "rule_source"):
                group_rows = [{dimension: str(key), **metrics(g, start, end)}
                              for key, g in trades.groupby(dimension)]
                pd.DataFrame(group_rows).to_csv(out / f"{window}_{dimension}.csv", index=False)
        for model_name, model in models.items():
            if model_name == "training_audit":
                continue
            model_p = predict(data, model)
            mt = replay(side_rows(data, model_p), policy, start, end)
            comparison.append({"window": window, "model": model_name, **metrics(mt, start, end)})
        calibration = rows.loc[rows.valid_label & rows.ask.between(.5, .95)].copy()
        calibration["bucket"] = pd.cut(calibration.probability, [0, .5, .7, .8, .85, .9, .95, 1.], include_lowest=True)
        calibration.groupby("bucket", observed=True).agg(rows=("hit", "size"),
            predicted=("probability", "mean"), observed=("hit", "mean")).to_csv(out / f"{window}_calibration.csv")
        print(f"{window}: {summary['windows'][window]}", flush=True)
    pd.DataFrame(sensitivities).to_csv(out / "execution_sensitivity.csv", index=False)
    pd.DataFrame(comparison).to_csv(out / "same_policy_model_comparison.csv", index=False)
    write_json(out / "summary.json", summary)
    emit_report(out, summary)


def audit_previous(args):
    """Isolate timing using identical probabilities and policy; leave archives intact."""
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    policy = {"side_mode": "all", "min_ev": .05, "max_ev": None, "max_spread": .05,
              "min_entry_price": .65, "max_entry_price": .9, "min_model_probability": .85,
              "min_hours_elapsed": None, "entry_policy": "first-eligible"}
    comparison = []
    for name, report in (("frozen", args.fixed_report), ("rolling", args.rolling_report)):
        rows = _materialize_candidates(args.history, report)
        rows = [r for r in rows if TEST_START <= r["target_date"] <= TEST_END]
        selected = _select_rows(rows, policy, 40, 3, 20, 10, 1)
        pd.DataFrame(selected).to_csv(out / f"{name}_chronological_trades.csv", index=False)
        m = _summary_metrics(selected)
        comparison.append({"model": name, "timing": "chronological", **m})
        # This reproduction is diagnostic only and is not exposed as a strategy.
        from scripts.hit80_14d_research import _passes_policy
        groups = {}
        for row in rows:
            if _passes_policy(row, policy):
                groups.setdefault(row["event_ticker"], []).append(row)
        hindsight = [max(group, key=lambda r: (r["ev"], r["model_probability"], r["snapshot_hour_utc"]))
                     for group in groups.values()]
        selected = _select_rows(hindsight, policy, 40, 3, 20, 10, 1)
        comparison.append({"model": name, "timing": "hindsight_diagnostic_only", **_summary_metrics(selected)})
    pd.DataFrame(comparison).to_csv(out / "timing_ablation.csv", index=False)
    print(pd.DataFrame(comparison)[["model", "timing", "trades", "hit_rate", "total_pnl"]].to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "score", "audit"))
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rolling-report", type=Path)
    parser.add_argument("--fixed-report", type=Path)
    parser.add_argument("--fresh", type=Path)
    parser.add_argument("--fresh-neural-report", type=Path)
    parser.add_argument("--fresh-start", default="2026-09-01")
    parser.add_argument("--fresh-end", default="2026-09-11")
    args = parser.parse_args()
    if args.command == "audit":
        audit_previous(args)
    elif args.command == "score":
        score_locked(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
