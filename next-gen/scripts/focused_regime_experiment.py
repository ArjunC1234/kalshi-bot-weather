"""Small, pre-specified, retrospective market/weather comparison. No live API writes."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import ndtr, softmax

from scripts.hit80_14d_research import _load_json_gz
from scripts.profitability_research import (
    KEYS, Policy, daily, fee, metrics, read_frame, replay, side_rows, write_json,
)

ANCHORS = ["nws_anchor_high_f", "hrrr_projected_high_f",
           "nbm_projected_high_f", "ensemble_raw_median_high_f"]
FIT_ASOF = pd.Timestamp("2026-08-24T18:00:00Z")
CAL_ASOF = pd.Timestamp("2026-08-28T18:00:00Z")
EVAL_START, EVAL_END = "2026-08-28", "2026-09-10"
POLICY = Policy(side="no", min_price=.5, max_price=.85, min_hours=14,
                min_net_edge=.05, min_probability=.85, max_spread=.05)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inputs(history: Path, fresh: Path):
    frames, audits, hashes = [], {}, {}
    for name, path in (("history", history), ("fresh", fresh)):
        frame, audits[name] = read_frame(path)
        temperatures = pd.DataFrame(_load_json_gz(path / "settlements.json.gz"))[
            ["event_ticker", "settlement_temperature_f"]]
        frame = frame.merge(temperatures, on="event_ticker", validate="many_to_one")
        frame = frame.loc[frame.target_date.lt("2026-09-01") if name == "history"
                          else frame.target_date.ge("2026-09-01")].copy()
        frame.attrs = {}
        frames.append(frame)
        for table in ("market_snapshots", "weather_snapshots", "events", "settlements"):
            file = path / f"{table}.json.gz"
            hashes[str(file.resolve())] = digest(file)
    frame = pd.concat(frames, ignore_index=True)
    if frame.duplicated(KEYS + ["market_ticker"]).any():
        raise ValueError("Repeated input keys")
    return frame, audits, hashes


def valid_snapshot(group: pd.DataFrame) -> bool:
    """Eligibility intentionally uses no settlement or target-label fields."""
    if len(group) != 6 or group.market_ticker.nunique() != 6:
        return False
    allowed = (group.depth_verified & group.availability_metadata_complete
               & ~group.known_future_feature & group.active
               & group.hours.ge(14) & group.hours.lt(20)
               & group.decision_time.lt(group.end_time)
               & group[ANCHORS].notna().sum(axis=1).ge(2))
    for side in ("yes", "no"):
        bid, ask = group[side + "_bid_dollars"], group[side + "_ask_dollars"]
        allowed &= bid.between(0, 1) & ask.between(0, 1) & ask.ge(bid)
    if not allowed.all():
        return False
    bounds = group.sort_values("bracket_index")
    lower, upper = bounds.bracket_lower_f.to_numpy(), bounds.bracket_upper_f.to_numpy()
    if not (pd.isna(lower[0]) and pd.isna(upper[-1])):
        return False
    if not (np.isfinite(lower[1:]).all() and np.isfinite(upper[:-1]).all()
            and np.allclose(lower[1:], upper[:-1] + 1)):
        return False
    return bool((group.yes_bid_dollars + group.yes_ask_dollars).sum() > 0)


def select_snapshots(frame: pd.DataFrame) -> pd.DataFrame:
    candidates = []
    for _, group in frame.groupby(KEYS, sort=False):
        if valid_snapshot(group):
            group = group.copy()
            # The full distribution is available only when every component is available.
            group["decision_time"] = group.decision_time.max()
            if (group.decision_time >= group.end_time).any():
                continue
            candidates.append(group)
    if not candidates:
        raise ValueError("No eligible complete snapshots")
    eligible = pd.concat(candidates, ignore_index=True)
    chosen = (eligible.sort_values(["decision_time", "snapshot_time_utc"])
              .drop_duplicates("event_ticker")[["event_ticker", "snapshot_time_utc"]])
    selected = eligible.merge(chosen, on=["event_ticker", "snapshot_time_utc"], validate="many_to_one")
    selected = selected.sort_values(["target_date", "event_ticker", "bracket_index"]).reset_index(drop=True)
    midpoint = (selected.yes_bid_dollars + selected.yes_ask_dollars) / 2
    midpoint = midpoint.clip(lower=1e-8)
    selected["market_p"] = midpoint / midpoint.groupby(selected.event_ticker).transform("sum")
    selected["anchor"] = selected[ANCHORS].mean(axis=1)
    return selected


def settled_subset(frame: pd.DataFrame, start: str, end: str, asof: pd.Timestamp):
    mask = (frame.target_date.between(start, end) & frame.valid_label
            & frame.settled_at_utc.lt(asof) & frame.decision_time.lt(asof)
            & frame.settlement_temperature_f.notna())
    result = frame.loc[mask].copy().reset_index(drop=True)
    if result.empty:
        raise ValueError("Empty settled fitting partition")
    if not result.groupby("event_ticker").y.sum().eq(1).all():
        raise ValueError("Not exactly one winning bracket per event")
    winning = result.loc[result.y.eq(1)]
    if not (winning.settlement_temperature_f.ge(winning.bracket_lower_f.fillna(-np.inf))
            & winning.settlement_temperature_f.le(winning.bracket_upper_f.fillna(np.inf))).all():
        raise ValueError("Settlement temperature disagrees with winning bracket")
    return result


def partition_audit(frame: pd.DataFrame):
    return {"events": int(frame.event_ticker.nunique()), "rows": len(frame),
            "first_target": frame.target_date.min(), "last_target": frame.target_date.max(),
            "latest_label_receipt": frame.settled_at_utc.max().isoformat(),
            "rule_sources": frame.drop_duplicates("event_ticker").rule_source.value_counts().to_dict()}


def fit_weather(frame: pd.DataFrame) -> dict:
    events = frame.drop_duplicates("event_ticker")
    errors = events.settlement_temperature_f - events.anchor
    pooled = float(errors.mean())
    cities = {}
    for city, group in events.groupby("city"):
        city_error = float((group.settlement_temperature_f - group.anchor).mean())
        cities[city] = pooled + len(group) / (len(group) + 20) * (city_error - pooled)
    residual = events.settlement_temperature_f - events.anchor - events.city.map(cities)
    sigma = max(1.5, float(np.sqrt(np.mean(residual ** 2))))
    return {"pooled_bias": pooled, "city_bias": cities, "sigma": sigma}


def forecast(frame: pd.DataFrame, weather: dict):
    return frame.anchor + frame.city.map(weather["city_bias"]).fillna(weather["pooled_bias"])


def weather_probabilities(frame: pd.DataFrame, weather: dict) -> np.ndarray:
    mu, sigma = forecast(frame, weather), weather["sigma"]
    lower = frame.bracket_lower_f.fillna(-np.inf) - .5
    upper = frame.bracket_upper_f.fillna(np.inf) + .5
    p = pd.Series(np.maximum(ndtr((upper - mu) / sigma) - ndtr((lower - mu) / sigma), 1e-8),
                  index=frame.index)
    return (p / p.groupby(frame.event_ticker).transform("sum")).to_numpy()


def matrices(frame: pd.DataFrame):
    if frame.empty or not frame.groupby("event_ticker").size().eq(6).all():
        raise ValueError("Expected six bracket rows per event")
    # Callers keep the complete event rows contiguous and bracket-ordered.
    if not (frame.event_ticker.to_numpy().reshape(-1, 6)
            == frame.event_ticker.to_numpy().reshape(-1, 6)[:, :1]).all():
        raise ValueError("Event rows are not contiguous")
    return np.log(frame.market_p.to_numpy().reshape(-1, 6)), frame.y.to_numpy().reshape(-1, 6)


def fit_model(frame: pd.DataFrame, use_weather: bool) -> dict:
    market, y = matrices(frame)
    weather = fit_weather(frame) if use_weather else None
    delta = (np.log(weather_probabilities(frame, weather).reshape(-1, 6)) - market
             if use_weather else np.zeros_like(market))

    def loss(params):
        a, b = params
        p = softmax((1 + a) * market + b * delta, axis=1)
        value = -np.mean(np.sum(y * np.log(p.clip(1e-15)), axis=1)) + .5 * (a*a + b*b)
        error = p - y
        gradient = np.array([np.mean(np.sum(error * market, axis=1)) + a,
                             np.mean(np.sum(error * delta, axis=1)) + b])
        return value, gradient

    result = minimize(loss, np.zeros(2), jac=True, method="L-BFGS-B",
                      bounds=[(-.5, .5), (0, .5) if use_weather else (0, 0)])
    if not result.success:
        raise ValueError(f"Base optimizer failed: {result.message}")
    return {"a": float(result.x[0]), "b": float(result.x[1]), "weather": weather,
            "calibration_scale": 1., "fit_asof": FIT_ASOF.isoformat(),
            "training": partition_audit(frame)}


def predict(frame: pd.DataFrame, model: dict | None, calibrated=True) -> np.ndarray:
    market, _ = matrices(frame)
    if model is None:
        return np.exp(market).ravel()
    logits = (1 + model["a"]) * market
    if model["weather"] is not None:
        logits += model["b"] * (np.log(weather_probabilities(frame, model["weather"]).reshape(-1, 6)) - market)
    scale = model["calibration_scale"] if calibrated else 1.
    return softmax(scale * logits, axis=1).ravel()


def calibrate(frame: pd.DataFrame, model: dict) -> dict:
    if (frame.decision_time < pd.Timestamp(model["fit_asof"])).any():
        raise ValueError("Calibration predictions precede model availability")
    if frame.target_date.min() <= model["training"]["last_target"]:
        raise ValueError("Calibration overlaps base training")
    if not frame.settled_at_utc.lt(CAL_ASOF).all():
        raise ValueError("Calibration uses late labels")
    logits = np.log(predict(frame, model, calibrated=False).reshape(-1, 6))
    _, y = matrices(frame)

    def loss(params):
        c = params[0]
        p = softmax(c * logits, axis=1)
        value = -np.mean(np.sum(y * np.log(p.clip(1e-15)), axis=1)) + .5 * (c - 1)**2
        gradient = np.mean(np.sum((p - y) * logits, axis=1)) + c - 1
        return value, np.array([gradient])

    result = minimize(loss, np.ones(1), jac=True, bounds=[(.5, 1.5)], method="L-BFGS-B")
    if not result.success:
        raise ValueError(f"Calibration optimizer failed: {result.message}")
    return {**model, "calibration_scale": float(result.x[0]),
            "ready_at": CAL_ASOF.isoformat(), "calibration": partition_audit(frame)}


def event_scores(frame: pd.DataFrame, probabilities: np.ndarray):
    p = probabilities.reshape(-1, 6)
    _, y = matrices(frame)
    if not np.isfinite(y).all() or not np.allclose(y.sum(axis=1), 1):
        raise ValueError("Evaluation requires complete, valid settlements")
    result = frame.drop_duplicates("event_ticker")[["event_ticker", "target_date", "city"]].copy()
    result["log_loss"] = -np.sum(y * np.log(p.clip(1e-15)), axis=1)
    result["brier"] = np.sum((p - y)**2, axis=1)
    result["top_pick_hit"] = np.argmax(p, axis=1) == np.argmax(y, axis=1)
    return result.reset_index(drop=True)


def paired_comparison(left: pd.DataFrame, right: pd.DataFrame):
    paired = left.merge(right, on=["event_ticker", "target_date", "city"], suffixes=("_left", "_right"),
                        validate="one_to_one")
    if len(paired) != len(left) or len(paired) != len(right):
        raise ValueError("Comparisons must use identical events")
    delta = paired.log_loss_left - paired.log_loss_right
    days = paired.assign(delta=delta).groupby("target_date").agg(total=("delta", "sum"), n=("delta", "size"))
    rng = np.random.default_rng(17)
    sampled = rng.integers(0, len(days), size=(10000, len(days)))
    draws = days.total.to_numpy()[sampled].sum(axis=1) / days.n.to_numpy()[sampled].sum(axis=1)
    return {"events": len(paired), "mean_log_loss_delta": float(delta.mean()),
            "day_bootstrap_95_interval": np.quantile(draws, [.025, .975]).tolist()}


def reliability(frame: pd.DataFrame, p: np.ndarray):
    rows = pd.DataFrame({"probability": 1 - p, "outcome": 1 - frame.y.to_numpy()})
    rows["bin"] = pd.cut(rows.probability, [0, .5, .7, .8, .85, .9, .95, 1], include_lowest=True)
    return rows.groupby("bin", observed=True).agg(brackets=("outcome", "size"),
        predicted=("probability", "mean"), observed=("outcome", "mean")).reset_index()


def temperature_diagnostics(frame: pd.DataFrame, model: dict, name: str):
    events = frame.drop_duplicates("event_ticker").copy()
    events["corrected"] = forecast(events, model["weather"])
    records = []
    for source in ANCHORS + ["anchor", "corrected"]:
        for city, group in [("all", events), *list(events.groupby("city"))]:
            good = group.dropna(subset=[source, "settlement_temperature_f"])
            error = good.settlement_temperature_f - good[source]
            records.append({"model": name, "source": source, "city": city, "events": len(good),
                "mean_actual_minus_forecast": float(error.mean()) if len(good) else None,
                "mae": float(error.abs().mean()) if len(good) else None,
                "rmse": float(np.sqrt(np.mean(error**2))) if len(good) else None,
                "interval_90_coverage": float(error.abs().le(1.644853627 * model["weather"]["sigma"]).mean())
                    if source == "corrected" and len(good) else None})
    return records


def verify_trades(trades: pd.DataFrame, frame: pd.DataFrame, ready: pd.Timestamp):
    if trades.empty:
        return 0
    if trades.event_ticker.duplicated().any():
        raise ValueError("More than one position per event")
    quotes = frame.set_index("market_ticker")
    for trade in trades.to_dict("records"):
        q = quotes.loc[trade["market_ticker"]]
        n, price = trade["contracts"], trade["price"]
        hit = float(q.y if trade["side"] == "yes" else 1 - q.y)
        assert n == int(n) and 0 < n <= q[trade["side"] + "_ask_size"]
        assert pd.Timestamp(trade["decision_time"]) >= ready
        assert pd.Timestamp(trade["decision_time"]) == q.decision_time
        assert q.decision_time < q.end_time and q.availability_metadata_complete and q.depth_verified
        assert q.settled_at_utc > q.decision_time
        assert abs(trade["hit"] - hit) < 1e-9
        assert abs(trade["fees"] - fee(n, price)) < 1e-9
        assert abs(trade["net_pnl"] - (n * (hit - price) - fee(n, price))) < 1e-9
        assert abs(trade["cash_debit"] - (n * price + fee(n, price))) < 1e-9
        assert trade["cash_debit"] <= 3 + 1e-9
    assert trades.groupby("target_date").cash_debit.sum().le(40 + 1e-9).all()
    return len(trades)


def emit_report(out: Path, summary: dict):
    lines = ["# Focused Regime Experiment", "", "Historical development comparison, not a fresh holdout.", "",
        "Fit through August 23, separate calibration August 24-27, evaluation August 28-September 10.",
        "One common, receipt-verified afternoon snapshot per event; no threshold sweep.", "",
        "| Model | Log loss | Brier | Trades | Wins | Hit rate | Net PnL |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for name, result in summary["models"].items():
        m = result["trading"]
        hit = f"{m['hit_rate']:.1%}" if m["hit_rate"] is not None else "n/a"
        lines.append(f"| {name} | {result['log_loss']:.4f} | {result['brier']:.4f} | {m['trades']} | {m['wins']} | {hit} | ${m['net_pnl']:.2f} |")
    lines += ["", "Lower log loss and Brier are better. Brier is the sum across six mutually exclusive brackets, averaged per event.",
        "", "## Paired Comparisons", "", "Differences are left minus right: negative favors left. Intervals resample whole days.", ""]
    for name, result in summary["comparisons"].items():
        lo, hi = result["day_bootstrap_95_interval"]
        lines.append(f"- {name}: {result['mean_log_loss_delta']:+.4f} log loss, descriptive 95% interval [{lo:+.4f}, {hi:+.4f}].")
    lines += ["", "## Decision", "", f"Provisional challenger chosen on calibration: {summary['preferred_challenger']}.",
        f"Forward qualification: {summary['forward_qualified']}.", "", "```json",
        json.dumps(summary["qualification_checks"], indent=2), "```", "",
        "No live trading is enabled. Forward testing is not running; a saved specification is not an active observation process.",
        "", "## Fourteen-Day Ledger", "", "| Date | Model | Trades | Wins | Net PnL |", "|---|---|---:|---:|---:|"]
    for name in ("raw_market", summary["preferred_challenger"]):
        for row in pd.read_csv(out / f"{name}_daily.csv").to_dict("records"):
            lines.append(f"| {row['target_date']} | {name} | {int(row['trades'])} | {int(row['wins'])} | ${row['net_pnl']:.2f} |")
    lines += ["", "## Evidence Limits", "",
        "- All evaluation dates were previously researched; these are development comparisons, not independent confirmation.",
        "- Regime composition, sample size and recency change together. This cannot isolate a settlement-rule causal effect.",
        "- Only four calibration days are available; uncertainty and earlier search bias remain substantial.",
        "- The common universe covers complete receipt-verified afternoon snapshots, not every collected event or hour.",
        "- Gaussian weather uncertainty and city-bias shrinkage are assumptions, tested descriptively against settlements.",
        "- Reliability bins contain dependent brackets, not independent bets; no binomial confidence is assigned to those rows.",
        "- Fees assume general taker multiplier 1 and conservative cents rounding. Price/depth availability is not proof of execution.",
        "- One-cent adverse execution is included. Zero/two-cent and next-quote results are in execution_sensitivity.csv.",
        "- Twenty trades and an observed 80% hit rate would still not establish a true 80% minimum.", "",
        "Method: [probability calibration](https://scikit-learn.org/stable/modules/calibration.html). "
        "Costs: [Kalshi fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf)."]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args):
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    if (out / "model_lock.json").exists() or (out / "experiment_spec.json").exists():
        raise ValueError("Preserve existing run; choose a new output directory")
    protocol = args.protocol
    write_json(out / "experiment_spec.json", {
        "recorded_at": pd.Timestamp.now(tz="UTC").isoformat(), "protocol_sha256": digest(protocol),
        "protocol_path": str(protocol.resolve()), "fit_asof": FIT_ASOF.isoformat(),
        "calibration_asof": CAL_ASOF.isoformat(), "evaluation": [EVAL_START, EVAL_END],
        "policy": asdict(POLICY), "live_trading_enabled": False})
    frame, input_audits, hashes = load_inputs(args.history, args.fresh)
    snapshots = select_snapshots(frame.loc[frame.target_date.between("2026-08-04", EVAL_END)])
    snapshots[KEYS + ["market_ticker", "target_date", "decision_time", "market_p", "rule_source"]].to_csv(
        out / "selected_snapshot_universe.csv", index=False)
    calibration = settled_subset(snapshots, "2026-08-24", "2026-08-27", CAL_ASOF)
    calibration = calibration.loc[calibration.decision_time.ge(FIT_ASOF)].reset_index(drop=True)
    evaluation = snapshots.loc[snapshots.target_date.between(EVAL_START, EVAL_END)].reset_index(drop=True)
    if not (evaluation.decision_time.ge(CAL_ASOF).all() and evaluation.valid_label.all()
            and evaluation.rule_source.eq("weather_company").all()):
        raise ValueError("Evaluation contains unavailable models, unresolved labels or non-current rules")
    if len(calibration) == 0 or evaluation.event_ticker.nunique() < 50:
        raise ValueError("Insufficient calibration/evaluation events")
    models, calibration_scores, training_audits = {}, {}, {}
    for regime, start in (("mixed", "2026-08-04"), ("post", "2026-08-14")):
        fit = settled_subset(snapshots, start, "2026-08-23", FIT_ASOF)
        if fit.event_ticker.nunique() < 40:
            raise ValueError("Fewer than forty base-training events")
        if regime == "post" and not fit.rule_source.eq("weather_company").all():
            raise ValueError("Post-switch training contains other settlement rules")
        training_audits[regime] = partition_audit(fit)
        for weather in (False, True):
            name = regime + ("_weather" if weather else "_market")
            models[name] = calibrate(calibration, fit_model(fit, weather))
            calibration_scores[name] = float(event_scores(calibration, predict(calibration, models[name])).log_loss.mean())
            print(f"Fitted {name}: events={fit.event_ticker.nunique()}, calibration log loss={calibration_scores[name]:.6f}", flush=True)
    preferred = min((n for n in models if n.endswith("_weather")), key=lambda n: calibration_scores[n])
    write_json(out / "models.json", models)
    write_json(out / "model_lock.json", {"locked_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "models_sha256": digest(out / "models.json"), "script_sha256": digest(Path(__file__)),
        "replay_sha256": digest(Path(__file__).with_name("profitability_research.py")),
        "input_sha256": hashes, "preferred_challenger": preferred,
        "calibration_log_loss": calibration_scores, "policy": asdict(POLICY),
        "note": "Models serialized before evaluation scoring; dates already examined in earlier research."})
    restored = json.loads((out / "models.json").read_text())
    for name in models:
        np.testing.assert_array_equal(predict(calibration, models[name]), predict(calibration, restored[name]))
    summary = {"preferred_challenger": preferred, "training": training_audits,
        "calibration": partition_audit(calibration), "evaluation": partition_audit(evaluation),
        "input_audits": input_audits, "models": {}, "comparisons": {}}
    scores, trades_by_model, prepost, sensitivities, weather_errors = {}, {}, [], [], []
    checked = 0
    for name, model in {"raw_market": None, **models}.items():
        p = predict(evaluation, model)
        scores[name] = event_scores(evaluation, p)
        scores[name].to_csv(out / f"{name}_event_scores.csv", index=False)
        pd.DataFrame({"market_ticker": evaluation.market_ticker, "probability": p}).to_csv(
            out / f"{name}_predictions.csv", index=False)
        reliability(evaluation, p).to_csv(out / f"{name}_reliability.csv", index=False)
        rows = side_rows(evaluation, p)
        trades = replay(rows, POLICY, EVAL_START, EVAL_END)
        trades_by_model[name] = trades
        checked += verify_trades(trades, evaluation, CAL_ASOF)
        trades.to_csv(out / f"{name}_trades.csv", index=False)
        ledger = daily(trades, EVAL_START, EVAL_END)
        ledger["eligible_events"] = ledger.target_date.map(evaluation.groupby("target_date").event_ticker.nunique()).fillna(0).astype(int)
        ledger.to_csv(out / f"{name}_daily.csv", index=False)
        summary["models"][name] = {"log_loss": float(scores[name].log_loss.mean()),
            "brier": float(scores[name].brier.mean()), "trading": metrics(trades, EVAL_START, EVAL_END, True)}
        for calibrated in (False, True):
            stage = event_scores(evaluation, predict(evaluation, model, calibrated))
            prepost.append({"model": name, "stage": "calibrated" if calibrated else "base",
                "log_loss": float(stage.log_loss.mean()), "brier": float(stage.brier.mean())})
        for slip in (0., .01, .02):
            trial = replay(rows, POLICY, EVAL_START, EVAL_END, slippage=slip)
            sensitivities.append({"model": name, "execution": "snapshot", "slippage": slip,
                                  **metrics(trial, EVAL_START, EVAL_END)})
        # Later quotes may fill an already-frozen intention; never generate new intentions here.
        later = frame.loc[frame.target_date.between(EVAL_START, EVAL_END)
                          & frame.market_ticker.isin(evaluation.market_ticker)].copy()
        intentions_and_later = side_rows(later, np.full(len(later), np.nan))
        intentions_and_later = intentions_and_later.loc[
            ~pd.MultiIndex.from_frame(intentions_and_later[KEYS + ["market_ticker"]]).isin(
                pd.MultiIndex.from_frame(rows[KEYS + ["market_ticker"]]))]
        combined = pd.concat([rows, intentions_and_later], ignore_index=True)
        delayed = replay(combined, POLICY, EVAL_START, EVAL_END, delayed=True)
        sensitivities.append({"model": name, "execution": "next_quote_limit_proxy", "slippage": .01,
                              **metrics(delayed, EVAL_START, EVAL_END)})
        if model and model["weather"]:
            weather_errors.extend(temperature_diagnostics(evaluation, model, name))
        print(f"Evaluated {name}: {summary['models'][name]}", flush=True)
    for left, right in (("mixed_weather", "mixed_market"), ("post_weather", "post_market"),
                        ("post_weather", "mixed_weather"), ("post_market", "mixed_market"),
                        (preferred, "raw_market")):
        summary["comparisons"][f"{left} minus {right}"] = paired_comparison(scores[left], scores[right])
    m = summary["models"][preferred]["trading"]
    preferred_daily = daily(trades_by_model[preferred], EVAL_START, EVAL_END)
    comparator = preferred.replace("weather", "market")
    paired = summary["comparisons"][f"{preferred} minus {comparator}"]
    checks = {
        "lower_log_loss_than_raw_market": summary["models"][preferred]["log_loss"] < summary["models"]["raw_market"]["log_loss"],
        "lower_log_loss_than_matched_market": paired["mean_log_loss_delta"] < 0,
        "paired_interval_upper_below_zero": paired["day_bootstrap_95_interval"][1] < 0,
        "at_least_twenty_trades": m["trades"] >= 20,
        "observed_hit_rate_at_least_eighty_percent": m["hit_rate"] is not None and m["hit_rate"] >= .8,
        "positive_net_profit": m["net_pnl"] > 0,
        "positive_each_week": bool(preferred_daily.net_pnl.iloc[:7].sum() > 0 and preferred_daily.net_pnl.iloc[7:].sum() > 0)}
    summary["qualification_checks"] = checks
    summary["forward_qualified"] = all(checks.values())
    pd.DataFrame(prepost).to_csv(out / "calibration_effect.csv", index=False)
    pd.DataFrame(sensitivities).to_csv(out / "execution_sensitivity.csv", index=False)
    pd.DataFrame(weather_errors).to_csv(out / "temperature_diagnostics.csv", index=False)
    write_json(out / "summary.json", summary)
    write_json(out / "verification.json", {"reconciled_main_trades": checked,
        "serialized_prediction_reproduction": "exact", "one_snapshot_per_event": True,
        "training_and_calibration_receipts_checked": True,
        "same_evaluation_events_for_all_models": int(evaluation.event_ticker.nunique()),
        "live_orders": 0})
    write_json(out / "forward_test_spec.json", {
        "status": "ready_for_paper_activation" if summary["forward_qualified"] else "inactive_no_qualified_candidate",
        "running": False, "live_trading_enabled": False, "duration_calendar_days": 14,
        "candidate": preferred if summary["forward_qualified"] else None,
        "models_sha256": digest(out / "models.json"), "policy": asdict(POLICY),
        "starts_at": None, "ends_at": None,
        "requirements": ["Activate actual forward decision logging before assigning start/end dates.",
            "Freeze model, policy and data schema; never backfill predictions as if logged live.",
            "Record every eligible event, abstention, contemporaneous quote and receipt time.",
            "Settle outcomes after prediction; report 14 daily rows including zero-trade days.",
            "At least 20 trades, observed hit >=80%, net profit positive; report uncertainty, not guarantees."]})
    emit_report(out, summary)
    print(f"Preferred={preferred}; forward_qualified={summary['forward_qualified']}; report={out / 'REPORT.md'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=Path("data/profitability_history_verified_20260701_20260901"))
    parser.add_argument("--fresh", type=Path, default=Path("data/profitability_fresh_verified_20260901_20260912"))
    parser.add_argument("--output", type=Path, default=Path("reports/research/focused_regime_20260913/run_v1"))
    parser.add_argument("--protocol", type=Path, default=Path("reports/research/focused_regime_20260913/PROTOCOL.md"))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
