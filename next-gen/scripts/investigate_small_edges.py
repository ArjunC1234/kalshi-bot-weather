"""Locked-model edge-threshold ablation. Local historical research only."""

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import beta

from scripts.focused_regime_experiment import (
    CAL_ASOF, EVAL_END, EVAL_START, POLICY, digest, load_inputs, predict,
    select_snapshots, verify_trades,
)
from scripts.profitability_research import daily, metrics, replay, side_rows, write_json


def delayed_fills(intentions: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    """Freeze orders before matching the first subsequent received quote."""
    result = []
    for intent in intentions.to_dict("records"):
        decision = pd.Timestamp(intent["decision_time"])
        later = quotes.loc[quotes.market_ticker.eq(intent["market_ticker"])
                           & quotes.market_received_at_utc.gt(decision)]
        if later.empty:
            continue
        quote = later.sort_values(["market_received_at_utc", "snapshot_time_utc"]).iloc[0]
        side = intent["side"]
        ask, bid = quote[side + "_ask_dollars"], quote[side + "_bid_dollars"]
        elapsed = (quote.market_received_at_utc - decision).total_seconds() / 60
        valid = (0 < elapsed <= 75 and quote.active and quote.depth_verified
                 and quote.availability_metadata_complete and quote.market_received_at_utc < quote.end_time
                 and np.isfinite(ask) and np.isfinite(bid) and 0 <= bid <= ask < 1
                 and ask <= intent["price"] and quote[side + "_ask_size"] >= intent["contracts"])
        if valid:
            result.append({**intent, "entry_time": quote.market_received_at_utc.isoformat(),
                           "fill_quote_ask": ask, "fill_visible_depth": quote[side + "_ask_size"]})
    return pd.DataFrame(result)


def detailed_metrics(trades: pd.DataFrame) -> dict:
    result = metrics(trades, EVAL_START, EVAL_END, True)
    ledger = daily(trades, EVAL_START, EVAL_END)
    result["first_week_pnl"] = float(ledger.net_pnl.iloc[:7].sum())
    result["second_week_pnl"] = float(ledger.net_pnl.iloc[7:].sum())
    result["worst_leave_one_day_out_pnl"] = float((ledger.net_pnl.sum() - ledger.net_pnl).min())
    n, wins = result["settled_trades"], result["wins"]
    result["iid_one_sided_95_hit_lower_bound"] = float(beta.ppf(.05, wins, n-wins+1)) if wins else 0. if n else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/research/timing_small_edges_20260913/small_edges"))
    parser.add_argument("--source-run", type=Path, default=Path("reports/research/focused_regime_20260913/run_v2"))
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    if (out / "experiment_lock.json").exists():
        raise ValueError("Preserve the existing run")
    lock = json.loads((args.source_run / "model_lock.json").read_text())
    if digest(args.source_run / "models.json") != lock["models_sha256"]:
        raise ValueError("Saved models changed")
    for path, expected in lock["input_sha256"].items():
        if digest(Path(path)) != expected:
            raise ValueError(f"Source data changed: {path}")
    for file, field in (("focused_regime_experiment.py", "script_sha256"),
                        ("profitability_research.py", "replay_sha256")):
        if digest(Path(__file__).with_name(file)) != lock[field]:
            raise ValueError("Frozen source implementation changed")
    model_name = lock["preferred_challenger"]
    models = json.loads((args.source_run / "models.json").read_text())
    protocol = out.parent / "PROTOCOL.md"
    write_json(out / "experiment_lock.json", {"created_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "source_model_lock_sha256": digest(args.source_run / "model_lock.json"),
        "code_sha256": digest(Path(__file__)), "protocol_sha256": digest(protocol),
        "model": model_name, "primary_edge": .01, "reference_edge": .05,
        "descriptive_edges": [0., .02], "policy": asdict(POLICY),
        "evaluation": [EVAL_START, EVAL_END], "live_trading_enabled": False})
    history = Path("data/focused_history_afternoon_verified_20260701_20260901")
    fresh = Path("data/focused_fresh_afternoon_verified_20260901_20260912")
    frame, _, _ = load_inputs(history, fresh)
    evaluation = select_snapshots(frame.loc[frame.target_date.between(EVAL_START, EVAL_END)])
    frame["market_received_at_utc"] = pd.to_datetime(frame.market_received_at_utc, utc=True)
    results = []
    main_trades = {}
    for name, model in (("raw_market", None), (model_name, models[model_name])):
        p = predict(evaluation, model)
        if model:
            saved = pd.read_csv(args.source_run / f"{name}_predictions.csv")
            if saved.market_ticker.tolist() != evaluation.market_ticker.tolist():
                raise ValueError("Frozen prediction universe changed")
            np.testing.assert_allclose(saved.probability, p, rtol=0, atol=1e-14)
        rows = side_rows(evaluation, p)
        for edge in (0., .01, .02, .05):
            policy = replace(POLICY, min_net_edge=edge)
            key = f"{name}_edge{int(edge*100):02}"
            for slip in (0., .01, .02):
                trades = replay(rows, policy, EVAL_START, EVAL_END, slippage=slip)
                verify_trades(trades, evaluation, CAL_ASOF)
                m = detailed_metrics(trades)
                results.append({"model": name, "edge": edge, "slippage": slip, "execution": "snapshot", **m})
                if slip == .01:
                    main_trades[key] = trades
                    trades.to_csv(out / f"{key}_trades.csv", index=False)
                    ledger = daily(trades, EVAL_START, EVAL_END)
                    ledger["eligible_events"] = ledger.target_date.map(evaluation.groupby("target_date").event_ticker.nunique())
                    ledger.to_csv(out / f"{key}_daily.csv", index=False)
                    filled = delayed_fills(trades, frame)
                    filled.to_csv(out / f"{key}_delayed_trades.csv", index=False)
                    results.append({"model": name, "edge": edge, "slippage": slip,
                                    "execution": "next_received_quote_limit_proxy", **detailed_metrics(filled)})
            print(f"Scored {key}", flush=True)
    main = [r for r in results if r["slippage"] == .01 and r["execution"] == "snapshot"]
    primary = next(r for r in main if r["model"] == model_name and r["edge"] == .01)
    checks = {"at_least_twenty_trades": primary["trades"] >= 20,
              "observed_hit_at_least_eighty_percent": (primary["hit_rate"] or 0) >= .8,
              "both_weeks_profitable": primary["first_week_pnl"] > 0 and primary["second_week_pnl"] > 0,
              "pnl_bootstrap_lower_positive": primary["day_bootstrap_pnl_95_interval"][0] > 0}
    write_json(out / "summary.json", {"primary": primary, "qualification_checks": checks,
        "provisional_forward_candidate": all(checks.values()), "results": results,
        "fresh_evidence": False, "live_trading_enabled": False})
    pd.DataFrame(results).to_csv(out / "comparisons.csv", index=False)
    lines = ["# Smaller-Edge Ablation", "", "Frozen model and identical 84 city-days, August 28-September 10.",
        "Historical development evidence. No threshold was selected by evaluation profit.", "",
        "| Model | Net edge | Trades | Wins | Hit rate | Net PnL | First week | Second week |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in main:
        hit = f"{r['hit_rate']:.1%}" if r["hit_rate"] is not None else "n/a"
        lines.append(f"| {r['model']} | ${r['edge']:.2f} | {r['trades']} | {r['wins']} | {hit} | ${r['net_pnl']:.2f} | ${r['first_week_pnl']:.2f} | ${r['second_week_pnl']:.2f} |")
    lines += ["", "## Primary One-Cent Rule", "", "```json", json.dumps(primary, indent=2), "```", "",
        f"Provisional qualification: {all(checks.values())}. No live trading or forward logging is activated.", "",
        "The exact hit lower bound assumes independent trades. Day-bootstrap intervals account for within-day grouping,",
        "but neither corrects prior research selection or guarantees future profitability.", "",
        "## Fourteen Days", "", "| Date | Trades | Wins | Net PnL |", "|---|---:|---:|---:|"]
    for r in daily(main_trades[f"{model_name}_edge01"], EVAL_START, EVAL_END).to_dict("records"):
        lines.append(f"| {r['target_date']} | {int(r['trades'])} | {int(r['wins'])} | ${r['net_pnl']:.2f} |")
    lines += ["", "Fees use the general taker multiplier 1 with conservative cent rounding per order.",
        "Main results include one-cent adverse execution; delayed fills are limit-price proxies, not proven executions.",
        "All costs, zero/two-cent sensitivities and delayed comparisons are in comparisons.csv.",
        "Fee reference: https://kalshi.com/docs/kalshi-fee-schedule.pdf"]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"primary": primary, "checks": checks}, indent=2))


if __name__ == "__main__":
    main()
