"""Verify archived replay ledgers against original quotes and settlement records."""

import argparse
import hashlib
import json
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

import pandas as pd

from scripts.hit80_14d_research import _explode_probabilities, _load_json_gz
from scripts.profitability_research import read_frame, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--fresh", type=Path, required=True)
    parser.add_argument("--original-data", type=Path, required=True)
    parser.add_argument("--original-model", type=Path, required=True)
    parser.add_argument("--rolling-model", type=Path, required=True)
    parser.add_argument("--neural-reference-run", type=Path)
    args = parser.parse_args()
    neural_run = args.neural_reference_run or args.run
    summary = json.loads((args.run / "summary.json").read_text())
    lock = json.loads((args.run / "selection_lock.json").read_text())
    assert hashlib.sha256((args.run / "models.joblib").read_bytes()).hexdigest() == lock["models_sha256"]
    before = _explode_probabilities(args.original_model / "bracket_distributions.csv")
    after = _explode_probabilities(neural_run / "neural_reproduction" / "bracket_distributions.csv")
    prediction_keys = ["city", "event_ticker", "snapshot_hour_utc", "market_ticker"]
    matched = before.merge(after, on=prediction_keys, suffixes=("_before", "_after"), validate="one_to_one")
    assert len(before) == len(after) == len(matched)
    delta = float((matched.yes_probability_before - matched.yes_probability_after).abs().max())
    assert delta < 1e-10
    folds = json.loads((args.rolling_model / "summary.json").read_text())["folds"]
    settlements = pd.DataFrame(_load_json_gz(args.original_data / "settlements.json.gz"))
    settlements["available"] = pd.to_datetime(settlements.settled_at_utc, utc=True)
    checked = 0
    label_timing_violations = []
    fresh_diagnostics = None
    for name, info in summary["windows"].items():
        source = args.fresh if name == "fresh" else args.history
        quotes, _ = read_frame(source)
        trades = pd.read_csv(args.run / f"{name}_trades.csv")
        assert not trades.event_ticker.duplicated().any()
        trades["snapshot_time_utc"] = pd.to_datetime(trades.quote_time_utc, utc=True)
        keys = ["city", "event_ticker", "market_ticker", "snapshot_time_utc"]
        merged = trades.merge(quotes, on=keys, suffixes=("", "_quote"), validate="one_to_one")
        assert len(merged) == len(trades)
        for row in merged.to_dict("records"):
            expected_hit = float(row["market_ticker"] == row["winner_ticker"])
            if row["side"] == "no":
                expected_hit = 1 - expected_hit
            assert expected_hit == row["hit"]
            count, price = int(row["contracts"]), Decimal(str(row["price"]))
            expected_fee = (Decimal('.07') * count * price * (1 - price)).quantize(Decimal('.01'), rounding=ROUND_CEILING)
            assert abs(float(expected_fee) - row["fees"]) < 1e-9
            assert abs(count * row["hit"] - row["cash_debit"] - row["net_pnl"]) < 1e-9
            assert row["cash_debit"] <= 3. + 1e-9
            assert count <= row[row["side"] + "_ask_size"]
            decision = pd.Timestamp(row["decision_time"])
            assert decision >= pd.Timestamp(row["decision_available_at_utc"])
            assert decision < row["end_time"]
            assert row["depth_verified"] and row["availability_metadata_complete"]
            if name == "validation":
                fold = next(f for f in folds if f["test_start_date"] <= row["target_date"] <= f["test_end_date"])
                fit = settlements.loc[settlements.target_date.between(fold["train_start_date"], fold["train_end_date"])]
            else:
                fit = settlements.loc[settlements.target_date.between("2026-07-01", "2026-08-17")]
            if fit.available.max() > decision:
                label_timing_violations.append({"window": name, "event": row["event_ticker"]})
            checked += 1
        assert trades.groupby("target_date").cash_debit.sum().max() <= 40 + 1e-9
        d = pd.read_csv(args.run / f"{name}_daily.csv")
        assert len(d) == (pd.Timestamp(info["end"]) - pd.Timestamp(info["start"])).days + 1
        assert abs(d.net_pnl.sum() - info["net_pnl"]) < 1e-9
        if name == "fresh":
            actual = pd.DataFrame(_load_json_gz(source / "settlements.json.gz"))
            merged = merged.merge(actual[["event_ticker", "settlement_temperature_f"]], on="event_ticker")
            predictions = pd.read_csv(neural_run / "neural_fresh" / "predictions.csv")
            predictions["snapshot_time_utc"] = pd.to_datetime(predictions.snapshot_hour_utc, utc=True)
            predictions = predictions.drop_duplicates(["city", "event_ticker", "snapshot_time_utc"])
            merged = merged.merge(predictions[["city", "event_ticker", "snapshot_time_utc", "expected_high_f", "q05", "q95"]],
                on=["city", "event_ticker", "snapshot_time_utc"], validate="many_to_one")
            merged["actual_minus_prediction_f"] = merged.settlement_temperature_f - merged.expected_high_f
            merged["above_q95"] = merged.settlement_temperature_f > merged.q95
            merged["below_q05"] = merged.settlement_temperature_f < merged.q05
            fields = ["target_date", "city", "market_ticker", "quote_time_utc", "probability", "hit", "net_pnl",
                      "observed_high_so_far_f", "nws_anchor_high_f", "hrrr_projected_high_f", "expected_high_f",
                      "settlement_temperature_f", "actual_minus_prediction_f", "q05", "q95", "above_q95", "below_q05"]
            merged[fields].to_csv(args.run / "fresh_trade_diagnostics.csv", index=False)
            losses = merged.loc[merged.hit.eq(0)]
            fresh_diagnostics = {"losses": len(losses),
                "losses_in_miami_or_austin": int(losses.city.isin(["mia", "aus"]).sum()),
                "losses_above_q95": int(losses.above_q95.sum()),
                "all_entries_above_q95": int(merged.above_q95.sum()),
                "all_entries_below_q05": int(merged.below_q05.sum()),
                "mean_actual_minus_prediction_f": float(merged.actual_minus_prediction_f.mean()),
                "direct_observed_floor_contradictions_on_losses": int((losses.observed_high_so_far_f >= losses.bracket_upper_f + .5).sum())}
    labels = pd.DataFrame(_load_json_gz(args.original_data / "final_temperature_labels.json.gz"))
    labels = labels.drop_duplicates("event_ticker", keep="last")
    labels = labels.loc[labels.target_date.le("2026-08-17")].merge(settlements, on="event_ticker", suffixes=("", "_settlement"))
    verification = {"checked_trades": checked, "neural_probability_rows_reproduced": len(matched),
                   "maximum_probability_reproduction_error": delta,
                   "training_label_value_availability_violations": label_timing_violations,
                   "late_created_labels_equal_earlier_settlement_values": int(labels.final_high_f.eq(labels.settlement_temperature_f).sum()),
                   "training_labels_checked": len(labels), "fresh_diagnostics": fresh_diagnostics,
                   "limits": ["Does not prove fills at stale quotes", "Does not prove actual historical model training latency",
                              "Does not remove multiple-search bias", "Receipt timestamps do not establish provider data correctness"]}
    write_json(args.run / "verification.json", verification)
    dependencies = [args.run / "models.joblib", args.run / "selection_lock.json",
                    neural_run / "neural_artifact" / "artifact.json", neural_run / "neural_artifact" / "model.pt",
                    args.original_model / "bracket_distributions.csv", args.rolling_model / "bracket_distributions.csv",
                    args.rolling_model / "summary.json", neural_run / "neural_fresh" / "bracket_distributions.csv"]
    for dataset in (args.history, args.fresh):
        dependencies.extend(dataset / f"{name}.json.gz" for name in
                            ("events", "weather_snapshots", "market_snapshots", "settlements"))
    write_json(args.run / "artifact_manifest.json", {
        "compiled_after_scoring": True,
        "purpose": "Reproduction hashes; the earlier selection lock records pre-scoring selection",
        "dependencies": {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in dependencies}})
    write_json(args.run / "operational_policy.json", {"mode": "abstain", "live_trading_enabled": False,
        "reason": "Validation criteria failed and frozen September candidate lost money",
        "research_model": lock["model"], "research_policy": lock["policy"]})
    print(json.dumps(verification, indent=2))


if __name__ == "__main__":
    main()
