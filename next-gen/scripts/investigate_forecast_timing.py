"""Versioned NWS revisions and subsequent prices, with explicit availability gates."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import ndtr
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from scripts.focused_regime_experiment import digest, load_inputs
from scripts.hit80_14d_research import _load_json_gz
from scripts.profitability_research import KEYS, fee, write_json

FIT_CUTOFF = pd.Timestamp("2026-08-28T00:00:00Z")
FEATURES = ["market_index", "market_entropy", "market_momentum", "climate_hour"]


@dataclass
class Quote:
    snapshot: pd.Timestamp
    requested: pd.Timestamp
    received: pd.Timestamp
    frame: pd.DataFrame
    centroid: float
    entropy: float


def complete_market(group: pd.DataFrame) -> bool:
    if len(group) != 6 or group.market_ticker.nunique() != 6:
        return False
    mask = (group.depth_verified & group.active & group.market_requested_at_utc.notna()
            & group.market_received_at_utc.notna()
            & group.market_requested_at_utc.le(group.market_received_at_utc)
            & group.market_received_at_utc.lt(group.end_time))
    for side in ("yes", "no"):
        bid, ask = group[side + "_bid_dollars"], group[side + "_ask_dollars"]
        mask &= bid.between(0, 1) & ask.between(0, 1) & ask.ge(bid)
    bounds = group.sort_values("bracket_index")
    lo, hi = bounds.bracket_lower_f.to_numpy(), bounds.bracket_upper_f.to_numpy()
    return bool(mask.all() and np.isnan(lo[0]) and np.isnan(hi[-1])
                and np.isfinite(lo[1:]).all() and np.isfinite(hi[:-1]).all()
                and np.allclose(lo[1:], hi[:-1] + 1))


def make_quote(group: pd.DataFrame) -> Quote:
    group = group.sort_values("bracket_index").reset_index(drop=True)
    mid = ((group.yes_bid_dollars + group.yes_ask_dollars) / 2).to_numpy().clip(1e-8)
    p = mid / mid.sum()
    return Quote(group.snapshot_time_utc.iloc[0], group.market_requested_at_utc.max(),
                 group.market_received_at_utc.max(), group,
                 float(np.dot(np.arange(6), p)), float(-np.sum(p*np.log(p))))


def first_entry(quotes: list[Quote], signal_time: pd.Timestamp) -> Quote | None:
    later = [q for q in quotes if q.requested >= signal_time]
    if not later:
        return None
    first = min(later, key=lambda q: (q.received, q.snapshot))
    elapsed = (first.received - signal_time).total_seconds() / 60
    return first if 0 <= elapsed <= 75 else None


def next_exit(quotes: list[Quote], entry: Quote) -> Quote | None:
    later = [q for q in quotes if q.snapshot > entry.snapshot and q.requested > entry.received]
    if not later:
        return None
    first = min(later, key=lambda q: (q.received, q.snapshot))
    elapsed = (first.received - entry.received).total_seconds() / 60
    return first if 45 <= elapsed <= 75 else None


def revision(previous: dict, current: dict) -> tuple[float | None, str]:
    if previous["event_ticker"] != current["event_ticker"] or previous["target_date"] != current["target_date"]:
        return None, "different_event"
    minutes = (current["snapshot"] - previous["snapshot"]).total_seconds() / 60
    if not 45 <= minutes <= 75:
        return None, "nonadjacent_snapshots"
    for item in (previous, current):
        if (not item["success"] or not np.isfinite(item["daily_high"])
                or any(pd.isna(item[k]) for k in ("received", "version", "generated"))):
            return None, "missing_source_metadata"
        if item["version"] > item["received"] or item["generated"] > item["received"]:
            return None, "publication_after_receipt"
    if current["received"] <= previous["received"] or current["version"] < previous["version"]:
        return None, "source_time_reversal"
    delta = current["daily_high"] - previous["daily_high"]
    if delta != 0 and current["version"] == previous["version"]:
        return None, "changed_without_new_version"
    return float(delta), "valid"


def choose_probe(entry: Quote, old_high: float, new_high: float) -> dict | None:
    frame = entry.frame
    lo = frame.bracket_lower_f.fillna(-np.inf).to_numpy() - .5
    hi = frame.bracket_upper_f.fillna(np.inf).to_numpy() + .5
    old_p = ndtr((hi-old_high)/2) - ndtr((lo-old_high)/2)
    new_p = ndtr((hi-new_high)/2) - ndtr((lo-new_high)/2)
    gain = old_p - new_p
    choices = []
    for i, row in frame.iterrows():
        if (gain[i] >= .01 and .5 <= row.no_ask_dollars <= .85
                and 0 <= row.no_ask_dollars-row.no_bid_dollars <= .0500001
                and row.no_ask_size >= 1):
            choices.append({"market_ticker": row.market_ticker, "side": "no", "contracts": 1,
                            "entry_ask": float(row.no_ask_dollars), "mass_reduction": float(gain[i]),
                            "entry_time": entry.received.isoformat()})
    return min(choices, key=lambda r: (-r["mass_reduction"], r["market_ticker"])) if choices else None


def score_probe(probe: dict, exit_quote: Quote | None) -> dict:
    if exit_quote is None:
        return {**probe, "scored": False, "net_markout": None, "reason": "missing_next_quote"}
    matches = exit_quote.frame.loc[exit_quote.frame.market_ticker.eq(probe["market_ticker"])]
    if len(matches) != 1:
        return {**probe, "scored": False, "net_markout": None, "reason": "changed_market_universe"}
    row = matches.iloc[0]
    if not row.no_bid_size >= 1:
        return {**probe, "scored": False, "net_markout": None, "reason": "insufficient_exit_depth"}
    buy, sell = probe["entry_ask"] + .01, max(0., float(row.no_bid_dollars)-.01)
    costs = fee(1, buy) + fee(1, sell)
    return {**probe, "scored": True, "reason": "scored", "exit_time": exit_quote.received.isoformat(),
            "exit_bid": float(row.no_bid_dollars), "buy_price": buy, "sell_price": sell,
            "fees": costs, "gross_markout": sell-buy, "net_markout": sell-buy-costs}


def read_weather(path: Path) -> pd.DataFrame:
    receipts = {r["raw_payload_id"]: r for r in _load_json_gz(path / "source_receipts.json.gz")}
    rows = []
    for wx in _load_json_gz(path / "weather_snapshots.json.gz"):
        source = receipts.get(wx["source_payload_ids"].get("nws_daily_forecast"), {})
        feature = wx.get("features") or {}
        rows.append({"event_ticker": wx["event_ticker"], "target_date": wx["target_date"], "city": wx["city"],
            "snapshot": pd.Timestamp(wx["snapshot_time_utc"]), "hour": wx["hours_since_climate_start"],
            "daily_high": float(wx["nws_daily_daytime_high_f"]) if wx.get("nws_daily_daytime_high_f") is not None else np.nan,
            "version": pd.to_datetime(feature.get("nws_daily_update_time"), utc=True),
            "generated": pd.to_datetime(feature.get("nws_daily_generated_at"), utc=True),
            "received": pd.to_datetime(source.get("received_at_utc"), utc=True),
            "success": source.get("success", False), "source_payload_id": source.get("raw_payload_id")})
    return pd.DataFrame(rows)


def build_transitions(frame: pd.DataFrame, weather: pd.DataFrame):
    by_event = {}
    for _, group in frame.groupby(KEYS, sort=False):
        if complete_market(group):
            quote = make_quote(group)
            by_event.setdefault(group.event_ticker.iloc[0], []).append(quote)
    transitions, probes, audits = [], [], Counter()
    for event, group in weather.groupby("event_ticker", sort=True):
        observations = group.sort_values("snapshot").to_dict("records")
        quotes = sorted(by_event.get(event, []), key=lambda q: q.received)
        previous = None
        first_material_seen = False
        for current in observations:
            prev, previous = previous, current
            if prev is None or not 6 <= current["hour"] <= 12:
                continue
            audits["candidate_hourly_transitions"] += 1
            delta, reason = revision(prev, current)
            audits[reason] += 1
            if delta is None:
                continue
            material = abs(delta) >= 1
            first_material = material and not first_material_seen
            # The first revision is fixed even if its later entry or exit is missing.
            first_material_seen |= material
            entry = first_entry(quotes, current["received"])
            latest_pre = [q for q in quotes if q.received < current["received"]]
            pre = max(latest_pre, key=lambda q: q.received) if latest_pre else None
            old = next((q for q in quotes if q.snapshot == prev["snapshot"]), None)
            base = {"event_ticker": event, "target_date": current["target_date"], "city": current["city"],
                "signal_time": current["received"].isoformat(), "snapshot_time": current["snapshot"].isoformat(),
                "previous_source_receipt": prev["received"].isoformat(),
                "old_version": prev["version"].isoformat(), "new_version": current["version"].isoformat(),
                "old_high": prev["daily_high"], "new_high": current["daily_high"],
                "revision_f": delta, "material": material, "first_material": first_material,
                "publication_age_minutes": (current["received"]-current["version"]).total_seconds()/60,
                "climate_hour": current["hour"], "pair_valid": False}
            if entry is None or pre is None:
                audits["missing_entry_or_previous_quote"] += 1
                transitions.append({**base, "reason": "missing_entry_or_previous_quote"})
                continue
            base.update({"entry_time": entry.received.isoformat(), "entry_request_time": entry.requested.isoformat(),
                "wait_minutes": (entry.received-current["received"]).total_seconds()/60,
                "market_index": entry.centroid, "market_entropy": entry.entropy,
                "market_momentum": entry.centroid-pre.centroid,
                "already_moved": entry.centroid-old.centroid if old else np.nan})
            probe = choose_probe(entry, prev["daily_high"], current["daily_high"]) if first_material else None
            exit_quote = next_exit(quotes, entry)
            if probe:
                probe_base = {k: v for k, v in base.items() if k != "pair_valid"}
                probes.append({**probe_base, **score_probe(probe, exit_quote)})
            if exit_quote is None or entry.frame.market_ticker.tolist() != exit_quote.frame.market_ticker.tolist():
                audits["missing_exit_or_changed_brackets"] += 1
                transitions.append({**base, "reason": "missing_exit_or_changed_brackets"})
                continue
            if pre.frame.market_ticker.tolist() != entry.frame.market_ticker.tolist():
                audits["changed_previous_brackets"] += 1
                transitions.append({**base, "reason": "changed_previous_brackets"})
                continue
            transitions.append({**base, "exit_time": exit_quote.received.isoformat(), "pair_valid": True,
                "reason": "scored", "next_change": exit_quote.centroid-entry.centroid})
            audits["complete_transitions"] += 1
    return pd.DataFrame(transitions), pd.DataFrame(probes), dict(audits)


def x_values(frame: pd.DataFrame, use_weather: bool) -> np.ndarray:
    values = frame[FEATURES].to_numpy(dtype=float)
    return np.column_stack([values, frame.revision_f.clip(-5, 5)]) if use_weather else values


def fit_model(frame: pd.DataFrame, use_weather: bool) -> dict:
    if not pd.to_datetime(frame.exit_time, utc=True).lt(FIT_CUTOFF).all():
        raise ValueError("Future market-change labels in fitting set")
    weights = 1 / frame.groupby("event_ticker").event_ticker.transform("size")
    x, y = x_values(frame, use_weather), frame.next_change.to_numpy()
    scaler = StandardScaler().fit(x, sample_weight=weights)
    model = Ridge(alpha=1.).fit(scaler.transform(x), y, sample_weight=weights)
    return {"weather": use_weather, "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(),
            "coef": model.coef_.tolist(), "intercept": float(model.intercept_),
            "alpha": 1., "fit_cutoff": FIT_CUTOFF.isoformat(), "fit_events": int(frame.event_ticker.nunique()),
            "latest_fit_exit": frame.exit_time.max()}


def predict(frame: pd.DataFrame, model: dict) -> np.ndarray:
    return ((x_values(frame, model["weather"])-model["mean"])/model["scale"]) @ np.array(model["coef"]) + model["intercept"]


def day_interval(frame: pd.DataFrame, value: str) -> dict:
    good = frame.dropna(subset=[value])
    if good.empty:
        return {"rows": 0, "events": 0, "days": 0, "mean": None, "day_bootstrap_95_interval": [None, None]}
    per_event = good.groupby(["target_date", "event_ticker"])[value].mean().reset_index()
    days = per_event.groupby("target_date")[value].agg(["sum", "size"])
    rng = np.random.default_rng(17)
    sampled = rng.integers(0, len(days), size=(10000, len(days)))
    averages = days["sum"].to_numpy()[sampled].sum(axis=1)/days["size"].to_numpy()[sampled].sum(axis=1)
    return {"rows": len(good), "events": len(per_event), "days": len(days),
            "mean": float(per_event[value].mean()),
            "day_bootstrap_95_interval": np.quantile(averages, [.025, .975]).tolist()}


def randomization_check(frame: pd.DataFrame):
    good = frame.dropna(subset=["next_change", "revision_f"]).reset_index(drop=True)
    if good.empty:
        return None
    signs, change = np.sign(good.revision_f.to_numpy()), good.next_change.to_numpy()
    actual = float(np.mean(signs * change))
    groups = list(good.groupby("target_date").indices.values())
    rng = np.random.default_rng(17)
    greater = 0
    for _ in range(5000):
        shuffled = signs.copy()
        for indices in groups:
            shuffled[indices] = rng.permutation(signs[indices])
        greater += np.mean(shuffled * change) >= actual
    return {"observed_mean_signed_change": actual, "within_day_sign_permutation_one_sided_p": (greater+1)/5001,
            "days_with_both_revision_signs": sum(np.unique(signs[indices]).size > 1 for indices in groups),
            "note": "Descriptive only; sign exchangeability within days is not guaranteed."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=Path("data/timing_history_verified_20260701_20260901"))
    parser.add_argument("--fresh", type=Path, default=Path("data/timing_fresh_verified_20260901_20260912"))
    parser.add_argument("--output", type=Path, default=Path("reports/research/timing_small_edges_20260913/timing"))
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    if (out / "experiment_spec.json").exists():
        raise ValueError("Preserve an existing run")
    write_json(out / "experiment_spec.json", {"created_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "protocol_sha256": digest(out.parent / "PROTOCOL.md"),
        "availability_note_sha256": digest(out.parent / "TIMING_AVAILABILITY_NOTE.md"),
        "source_code_sha256": digest(Path(__file__)), "fit_cutoff": FIT_CUTOFF.isoformat(),
        "live_trading_enabled": False})
    frame, audits, hashes = load_inputs(args.history, args.fresh)
    frame = frame.loc[frame.target_date.between("2026-08-14", "2026-09-10")].copy()
    for column in ("market_requested_at_utc", "market_received_at_utc"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    if not frame.rule_source.eq("weather_company").all():
        raise ValueError("Timing experiment requires current settlement rules")
    weather_parts = []
    for name, path in (("history", args.history), ("fresh", args.fresh)):
        weather = read_weather(path)
        weather = weather.loc[weather.target_date.lt("2026-09-01") if name == "history"
                              else weather.target_date.ge("2026-09-01")]
        weather_parts.append(weather)
        hashes[str((path / "source_receipts.json.gz").resolve())] = digest(path / "source_receipts.json.gz")
    weather = pd.concat(weather_parts, ignore_index=True)
    weather = weather.loc[weather.target_date.between("2026-08-14", "2026-09-10")]
    if weather.duplicated(["event_ticker", "snapshot"]).any():
        raise ValueError("Duplicate weather observations")
    transitions, probes, coverage = build_transitions(frame, weather)
    fit = transitions.loc[transitions.pair_valid & transitions.target_date.le("2026-08-27")].copy()
    fit = fit.loc[pd.to_datetime(fit.exit_time, utc=True).lt(FIT_CUTOFF)]
    if fit.event_ticker.nunique() < 40:
        raise ValueError("Insufficient independent fitting events")
    models = {name: fit_model(fit, weather) for name, weather in (("market", False), ("market_weather", True))}
    write_json(out / "models.json", models)
    write_json(out / "model_lock.json", {"locked_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "models_sha256": digest(out / "models.json"), "input_sha256": hashes,
        "note": "Saved before evaluation metrics. Transition labels and probe markouts were materialized before fitting; only pre-cutoff price labels entered fitting. No evaluation-based model or parameter selection."})
    evaluation = transitions.loc[transitions.target_date.ge("2026-08-28") & transitions.pair_valid].copy()
    if not pd.to_datetime(evaluation.entry_time, utc=True).ge(FIT_CUTOFF).all():
        raise ValueError("Evaluation precedes fitting cutoff")
    for name, model in models.items():
        evaluation["prediction_"+name] = predict(evaluation, model)
        evaluation["squared_error_"+name] = (evaluation["prediction_"+name]-evaluation.next_change)**2
    evaluation["paired_squared_error_delta"] = evaluation.squared_error_market_weather-evaluation.squared_error_market
    evaluation["signed_later_change"] = np.sign(evaluation.revision_f)*evaluation.next_change
    evaluation["signed_already_moved"] = np.sign(evaluation.revision_f)*evaluation.already_moved
    first = evaluation.loc[evaluation.first_material].copy()
    material_all = transitions.loc[transitions.target_date.ge("2026-08-28") & transitions.first_material]
    probe_eval = probes.loc[probes.target_date.ge("2026-08-28")].copy() if len(probes) else pd.DataFrame(
        columns=["target_date", "event_ticker", "net_markout", "scored"])
    probe_stats = day_interval(probe_eval, "net_markout")
    comparison = day_interval(evaluation, "paired_squared_error_delta")
    revision_comparison = day_interval(first, "paired_squared_error_delta")
    checks = {"at_least_twenty_complete_revision_events": len(first) >= 20,
        "revision_model_error_interval_upper_below_zero": revision_comparison["mean"] is not None and revision_comparison["day_bootstrap_95_interval"][1] < 0,
        "net_probe_interval_lower_above_zero": probe_stats["mean"] is not None and probe_stats["day_bootstrap_95_interval"][0] > 0}
    summary = {"training_events": int(fit.event_ticker.nunique()), "training_transitions": len(fit),
        "evaluation_events": int(evaluation.event_ticker.nunique()), "evaluation_transitions": len(evaluation),
        "first_material_revisions": len(material_all), "scored_first_revisions": len(first),
        "market_mse": day_interval(evaluation, "squared_error_market"),
        "market_weather_mse": day_interval(evaluation, "squared_error_market_weather"),
        "paired_error_all": comparison, "paired_error_first_revisions": revision_comparison,
        "signed_later_change": day_interval(first, "signed_later_change"),
        "signed_already_moved": day_interval(first, "signed_already_moved"),
        "sign_randomization": randomization_check(first), "net_probe_markout": probe_stats,
        "probe_intentions": len(probe_eval), "missing_probe_exits": int(probe_eval.net_markout.isna().sum()),
        "median_forecast_publication_age_minutes": float(material_all.publication_age_minutes.median()) if len(material_all) else None,
        "median_wait_for_entry_minutes": float(material_all.wait_minutes.median()) if len(material_all) else None,
        "coverage": coverage, "mechanism_checks": checks, "mechanism_qualified": all(checks.values()),
        "live_trading_enabled": False, "hourly_cannot_resolve_subhour_edges": True}
    transitions.to_csv(out / "all_transitions.csv", index=False)
    evaluation.to_csv(out / "evaluation_predictions.csv", index=False)
    first.to_csv(out / "first_revision_results.csv", index=False)
    probe_eval.to_csv(out / "probe_intentions.csv", index=False)
    days = pd.DataFrame({"target_date": pd.date_range("2026-08-28", "2026-09-10").strftime("%Y-%m-%d")})
    for name, data in (("complete_transitions", evaluation), ("first_revisions", material_all),
                       ("scored_revisions", first), ("probe_intentions", probe_eval)):
        days[name] = days.target_date.map(data.groupby("target_date").size()).fillna(0).astype(int)
    days["mean_net_probe_markout"] = days.target_date.map(probe_eval.groupby("target_date").net_markout.mean())
    days.to_csv(out / "daily.csv", index=False)
    write_json(out / "summary.json", summary)
    write_json(out / "verification.json", {"fit_price_labels_before_cutoff": True,
        "entry_requests_after_signal_receipt": bool(pd.to_datetime(evaluation.entry_request_time, utc=True).ge(
            pd.to_datetime(evaluation.signal_time, utc=True)).all()),
        "exit_receipts_after_entry": bool(pd.to_datetime(evaluation.exit_time, utc=True).gt(
            pd.to_datetime(evaluation.entry_time, utc=True)).all()),
        "timing_model_uses_no_settlement_labels": True, "input_audits": audits})
    lines = ["# Hour-Scale Forecast Timing", "", "Versioned NWS daily-high revisions, post-switch data only.",
        "Fit August 14-27; retrospective evaluation August 28-September 10.",
        "Entry quotes were requested after receipt of the forecast. Hourly snapshots cannot establish faster effects.", "",
        f"Evaluation: {len(evaluation)} complete hourly transitions across {evaluation.event_ticker.nunique()} city-days; {len(first)} scored first material revisions.",
        "", "```json", json.dumps(summary, indent=2), "```", "",
        "A negative squared-error difference favors adding weather. Bootstrap groups complete days and weights events equally.",
        "Probe markouts are diagnostic one-contract round trips, not portfolio PnL or an 80%-hit strategy.",
        "No models or thresholds were selected using evaluation performance. No live trades or forward logging were activated.", "",
        "## Fourteen Days", "", "| Date | Complete transitions | First revisions | Probe intentions | Mean net probe markout |",
        "|---|---:|---:|---:|---:|"]
    for row in days.to_dict("records"):
        value = f"${row['mean_net_probe_markout']:.2f}" if pd.notna(row["mean_net_probe_markout"]) else "n/a"
        lines.append(f"| {row['target_date']} | {row['complete_transitions']} | {row['first_revisions']} | {row['probe_intentions']} | {value} |")
    lines += ["", "## Limitations", "", "Previously researched dates, correlated city weather, short sample and source publication/polling delays limit inference.",
        "NWS daily-high changes are not necessarily independent news. Remaining information can reach markets through other sources.",
        "The within-day sign randomization is descriptive; exchangeability is an assumption, not a causal guarantee.",
        "Strictly positive timing-mechanism gates are required before further model development, not direct deployment.",
        "Sources: https://www.weather.gov/documentation/services-web-api and https://kalshi.com/docs/kalshi-fee-schedule.pdf"]
    (out / "REPORT.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
