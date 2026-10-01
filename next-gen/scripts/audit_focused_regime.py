"""Post-run diagnostics only. Does not refit models or change qualification."""

import argparse
import json
from pathlib import Path
import platform

import numpy as np
import pandas as pd
import scipy
from scipy.special import softmax

from scripts.focused_regime_experiment import (
    ANCHORS, CAL_ASOF, EVAL_END, EVAL_START, FIT_ASOF, POLICY, digest,
    event_scores, load_inputs, matrices, predict, select_snapshots, settled_subset,
    weather_probabilities,
)
from scripts.profitability_research import side_rows, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=Path("reports/research/focused_regime_20260913/run_v1"))
    parser.add_argument("--history", type=Path, default=Path("data/profitability_history_verified_20260701_20260901"))
    parser.add_argument("--fresh", type=Path, default=Path("data/profitability_fresh_verified_20260901_20260912"))
    args = parser.parse_args()
    out = args.run
    lock = json.loads((out / "model_lock.json").read_text())
    assert digest(out / "models.json") == lock["models_sha256"]
    assert digest(Path(__file__).with_name("focused_regime_experiment.py")) == lock["script_sha256"]
    assert digest(Path(__file__).with_name("profitability_research.py")) == lock["replay_sha256"]
    for path, expected in lock["input_sha256"].items():
        assert digest(Path(path)) == expected
    frame, _, _ = load_inputs(args.history, args.fresh)
    selected = select_snapshots(frame.loc[frame.target_date.between("2026-08-04", EVAL_END)])
    evaluation = selected.loc[selected.target_date.between(EVAL_START, EVAL_END)].reset_index(drop=True)
    assert evaluation.decision_time.lt(evaluation.settled_at_utc).all()
    winning = evaluation.loc[evaluation.y.eq(1)]
    assert (winning.settlement_temperature_f.ge(winning.bracket_lower_f.fillna(-np.inf))
            & winning.settlement_temperature_f.le(winning.bracket_upper_f.fillna(np.inf))).all()
    models = json.loads((out / "models.json").read_text())
    gradients, gates = [], []
    reproduction = {}
    for name, model in models.items():
        p = predict(evaluation, model)
        saved = pd.read_csv(out / f"{name}_predictions.csv")
        assert evaluation.market_ticker.tolist() == saved.market_ticker.tolist()
        maximum = float(np.max(np.abs(p - saved.probability.to_numpy())))
        assert maximum < 1e-14
        reproduction[name] = maximum
        scores = event_scores(evaluation, p)
        assert evaluation.decision_time.ge(CAL_ASOF).all()
        expected = json.loads((out / "summary.json").read_text())["models"][name]
        assert abs(scores.log_loss.mean() - expected["log_loss"]) < 1e-14
        rows = side_rows(evaluation, p)
        rows = rows.loc[rows.side.eq("no")].copy()
        price = rows.ask + .01
        masks = [
            ("all_NO_brackets", pd.Series(True, index=rows.index)),
            ("ask_50_to_85_cents", rows.ask.between(POLICY.min_price, POLICY.max_price)),
            ("spread_at_most_5_cents", rows.spread.between(0, POLICY.max_spread + 1e-9)),
            ("visible_depth_at_least_one", rows.depth.ge(1)),
            ("predicted_probability_at_least_85_percent", rows.probability.ge(POLICY.min_probability)),
            ("net_edge_at_least_5_cents", (rows.probability - price - .07*price*(1-price)).ge(POLICY.min_net_edge))]
        mask = pd.Series(True, index=rows.index)
        for stage, condition in masks:
            mask &= condition
            gates.append({"model": name, "stage": stage, "brackets": int(mask.sum()),
                          "events": int(rows.loc[mask].event_ticker.nunique())})
        if model["weather"] is None:
            continue
        start = "2026-08-04" if name.startswith("mixed") else "2026-08-14"
        fit = settled_subset(selected, start, "2026-08-23", FIT_ASOF)
        assert fit.decision_time.lt(fit.settled_at_utc).all()
        market, y = matrices(fit)
        delta = np.log(weather_probabilities(fit, model["weather"]).reshape(-1, 6)) - market
        p0 = softmax((1 + model["a"]) * market, axis=1)
        gradient = float(np.mean(np.sum((p0 - y) * delta, axis=1)))
        epsilon = 1e-6
        p1 = softmax((1 + model["a"]) * market + epsilon * delta, axis=1)
        finite_difference = float(np.mean(np.sum(y * (np.log(p0) - np.log(p1)), axis=1)) / epsilon)
        assert abs(gradient - finite_difference) < 1e-4
        gradients.append({"model": name, "fitted_weather_coefficient": model["b"],
            "unpenalized_training_loss_derivative_at_zero_weather": gradient,
            "finite_difference": finite_difference,
            "interpretation": "Positive derivative means adding a small positive weather weight worsens training log loss, before its penalty."})
    coverage = []
    for day in pd.date_range(EVAL_START, EVAL_END).strftime("%Y-%m-%d"):
        for city in sorted(frame.city.unique()):
            source = frame.loc[frame.target_date.eq(day) & frame.city.eq(city)]
            target = evaluation.loc[evaluation.target_date.eq(day) & evaluation.city.eq(city)]
            afternoon = source.loc[source.hours.ge(14) & source.hours.lt(20)]
            flags = {
                "received_complete": afternoon.availability_metadata_complete & afternoon.depth_verified,
                "active": afternoon.active,
                "features_available": ~afternoon.known_future_feature,
                "at_least_two_anchors": afternoon[ANCHORS].notna().sum(axis=1).ge(2),
                "before_close": afternoon.decision_time.lt(afternoon.end_time)}
            record = {"target_date": day, "city": city, "selected": bool(len(target)),
                "collected_snapshots": source.snapshot_time_utc.nunique(),
                "afternoon_snapshots": afternoon.snapshot_time_utc.nunique()}
            for flag, mask in flags.items():
                record["snapshots_all_" + flag] = int(mask.groupby(afternoon.snapshot_time_utc).all().sum())
            coverage.append(record)
    pd.DataFrame(coverage).to_csv(out / "coverage_audit.csv", index=False)
    pd.DataFrame(gates).to_csv(out / "entry_gate_counts.csv", index=False)
    write_json(out / "supplemental_audit.json", {"post_scoring_diagnostic_only": True,
        "runtime": {"python": platform.python_version(), "numpy": np.__version__,
                    "pandas": pd.__version__, "scipy": scipy.__version__},
        "input_and_code_hashes_match_lock": True, "prediction_csv_max_roundtrip_difference": reproduction,
        "evaluation_predictions_precede_outcomes": True,
        "evaluation_temperature_winner_consistency": True,
        "weather_gradient_checks": gradients, "evaluation_city_days": len(coverage),
        "selected_city_days": int(evaluation.event_ticker.nunique()),
        "gate_counts": gates, "audit_code_sha256": digest(Path(__file__))})
    print(json.dumps({"gradients": gradients, "gates": gates,
        "coverage_by_city": pd.DataFrame(coverage).groupby("city").selected.sum().to_dict()}, indent=2))


if __name__ == "__main__":
    main()
