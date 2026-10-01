"""Independent artifact, raw-price, receipt and cash arithmetic checks."""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.focused_regime_experiment import digest
from scripts.hit80_14d_research import _load_json_gz
from scripts.profitability_research import write_json
from scripts.repair_research_quotes import quantity


def cents_fee(n, price):
    return math.ceil((.07*n*price*(1-price)-1e-12)*100)/100


def main():
    root = Path("reports/research/timing_small_edges_20260913")
    timing = root / "timing_v2"
    small = root / "small_edges"
    lock = json.loads((timing / "model_lock.json").read_text())
    spec = json.loads((timing / "experiment_spec.json").read_text())
    assert digest(timing / "models.json") == lock["models_sha256"]
    assert digest(Path(__file__).with_name("investigate_forecast_timing.py")) == spec["source_code_sha256"]
    for path, expected in lock["input_sha256"].items():
        assert digest(Path(path)) == expected
    small_lock = json.loads((small / "experiment_lock.json").read_text())
    assert digest(Path(__file__).with_name("investigate_small_edges.py")) == small_lock["code_sha256"]
    assert digest(root / "PROTOCOL.md") == spec["protocol_sha256"] == small_lock["protocol_sha256"]
    assert digest(timing / "models.json") == digest(root / "timing/models.json")
    old_scores = pd.read_csv(root / "timing/evaluation_predictions.csv")
    scores = pd.read_csv(timing / "evaluation_predictions.csv")
    pd.testing.assert_frame_equal(old_scores, scores)
    raw_cache, quote_by_time, quote_by_receipt, wx_lookup, settlements = {}, {}, {}, {}, {}
    prices_checked, quantities_checked = 0, 0
    event_quotes = {}
    for directory in ("timing_history_verified_20260701_20260901", "timing_fresh_verified_20260901_20260912"):
        path = Path("data") / directory
        weather = {(r["event_ticker"], pd.Timestamp(r["snapshot_time_utc"])): r
                   for r in _load_json_gz(path / "weather_snapshots.json.gz")}
        receipts = {r["raw_payload_id"]: r for r in _load_json_gz(path / "source_receipts.json.gz")}
        wx_lookup.update(weather)
        settlements.update({r["event_ticker"]: r for r in _load_json_gz(path / "settlements.json.gz")})
        for row in _load_json_gz(path / "market_snapshots.json.gz"):
            if not row.get("depth_verified"):
                continue
            key = row["event_ticker"], pd.Timestamp(row["snapshot_time_utc"])
            wx = weather[key]
            raw_id = wx["source_payload_ids"]["kalshi_open_markets"]
            receipt = receipts[raw_id]
            assert receipt["success"]
            assert pd.Timestamp(receipt["received_at_utc"]) == pd.Timestamp(row["market_received_at_utc"])
            assert pd.Timestamp(receipt["requested_at_utc"]) == pd.Timestamp(row["market_requested_at_utc"])
            if raw_id not in raw_cache:
                raw_cache[raw_id] = {r["ticker"]: r for r in _load_json_gz(Path("data/profitability_raw_quote_cache") / f"{raw_id}.json.gz")["markets"]}
            raw = raw_cache[raw_id][row["market_ticker"]]
            for side in ("yes", "no"):
                for leg in ("bid", "ask"):
                    field = side + "_" + leg
                    raw_price = float(raw[field+"_dollars"]) if raw.get(field+"_dollars") is not None else float(raw[field])/100
                    assert abs(raw_price-row[field+"_dollars"]) < 1e-12
                    prices_checked += 1
                    raw_quantity = quantity(raw, side, leg)
                    stored = row.get(field+"_size")
                    assert raw_quantity == stored or (raw_quantity is not None and stored is not None and abs(raw_quantity-stored) < 1e-12)
                    quantities_checked += 1
            quote_by_time[row["market_ticker"], key[1]] = row
            quote_by_receipt[row["market_ticker"], pd.Timestamp(row["market_received_at_utc"])] = row
            event_quotes.setdefault(key[0], {}).setdefault(pd.Timestamp(row["market_received_at_utc"]), []).append(row)

    def centroid(event, receipt):
        rows = sorted(event_quotes[event][receipt], key=lambda r: r["bracket_index"])
        assert len(rows) == 6
        mid = np.maximum([(r["yes_bid_dollars"]+r["yes_ask_dollars"])/2 for r in rows], 1e-8)
        return float(np.dot(np.arange(6), mid/mid.sum()))

    models = json.loads((timing / "models.json").read_text())
    all_rows = pd.read_csv(timing / "all_transitions.csv")
    fit = all_rows.loc[all_rows.target_date.le("2026-08-27")]
    assert set(fit.event_ticker).isdisjoint(scores.event_ticker)
    assert pd.to_datetime(fit.exit_time, utc=True).lt(pd.Timestamp("2026-08-28T00:00Z")).all()
    for row in scores.to_dict("records"):
        event = row["event_ticker"]
        signal, entry, exit_time = (pd.Timestamp(row[k]) for k in ("signal_time", "entry_time", "exit_time"))
        assert pd.Timestamp(row["entry_request_time"]) >= signal
        assert 0 <= (entry-signal).total_seconds()/60 <= 75
        assert 45 <= (exit_time-entry).total_seconds()/60 <= 75
        assert pd.Timestamp(row["new_version"]) <= signal
        wx = wx_lookup[event, pd.Timestamp(row["snapshot_time"])]
        assert wx["nws_daily_daytime_high_f"] == row["new_high"]
        assert pd.Timestamp(wx["features"]["nws_daily_update_time"]) == pd.Timestamp(row["new_version"])
        assert abs(centroid(event, exit_time)-centroid(event, entry)-row["next_change"]) < 1e-12
        for name, model in models.items():
            x = [row[k] for k in ("market_index", "market_entropy", "market_momentum", "climate_hour")]
            if model["weather"]:
                x.append(np.clip(row["revision_f"], -5, 5))
            pred = float(np.dot((np.array(x)-model["mean"])/model["scale"], model["coef"]) + model["intercept"])
            assert abs(pred-row["prediction_"+name]) < 1e-12
    trades = pd.read_csv(small / "mixed_weather_edge01_trades.csv")
    for trade in trades.to_dict("records"):
        quote = quote_by_time[trade["market_ticker"], pd.Timestamp(trade["quote_time_utc"])]
        n, price = trade["contracts"], trade["price"]
        assert 0 < n == int(n) <= quote["no_ask_size"]
        assert abs(price-quote["no_ask_dollars"]-.01) < 1e-12
        hit = float(trade["market_ticker"] != settlements[trade["event_ticker"]]["winner_ticker"])
        assert hit == trade["hit"]
        assert abs(trade["fees"]-cents_fee(n, price)) < 1e-12
        assert abs(trade["net_pnl"]-(n*(hit-price)-cents_fee(n, price))) < 1e-12
        assert trade["cash_debit"] <= 3 + 1e-12
        assert trade["probability"]-price-cents_fee(n, price)/n >= .01-1e-12
    assert trades.groupby("target_date").cash_debit.sum().le(40).all()
    delayed = pd.read_csv(small / "mixed_weather_edge01_delayed_trades.csv")
    for trade in delayed.to_dict("records"):
        quote = quote_by_receipt[trade["market_ticker"], pd.Timestamp(trade["entry_time"])]
        assert pd.Timestamp(quote["market_requested_at_utc"]) >= pd.Timestamp(trade["decision_time"])
        assert quote["no_ask_dollars"] <= trade["price"]
        assert quote["no_ask_size"] >= trade["contracts"]
    probes = pd.read_csv(timing / "probe_intentions.csv")
    assert "pair_valid" not in probes.columns
    for probe in probes.to_dict("records"):
        entry = quote_by_receipt[probe["market_ticker"], pd.Timestamp(probe["entry_time"])]
        exit_quote = quote_by_receipt[probe["market_ticker"], pd.Timestamp(probe["exit_time"])]
        buy, sell = entry["no_ask_dollars"]+.01, max(0, exit_quote["no_bid_dollars"]-.01)
        assert entry["no_ask_size"] >= 1 and exit_quote["no_bid_size"] >= 1
        assert abs(probe["net_markout"]-(sell-buy-cents_fee(1, buy)-cents_fee(1, sell))) < 1e-12
    first = pd.read_csv(timing / "first_revision_results.csv")
    generated_age = []
    for row in first.to_dict("records"):
        wx = wx_lookup[row["event_ticker"], pd.Timestamp(row["snapshot_time"])]
        generated_age.append((pd.Timestamp(row["signal_time"])-pd.Timestamp(wx["features"]["nws_daily_generated_at"])).total_seconds()/60)
    audit = {"raw_market_payloads": len(raw_cache), "price_fields_matched": prices_checked,
        "quantity_fields_matched": quantities_checked, "input_model_code_hashes_match": True,
        "timing_v1_v2_models_and_predictions_equal": True,
        "evaluation_price_changes_and_predictions_reconciled": len(scores),
        "small_edge_trades_reconciled": len(trades), "delayed_requests_after_signals_verified": len(delayed),
        "probe_markouts_reconciled": len(probes),
        "posthoc_no_change_predictor_mse": float(scores.assign(error=scores.next_change**2).groupby("event_ticker").error.mean().mean()),
        "posthoc_no_change_note": "Descriptive persistence control only; not a new fitted or selected trading model.",
        "median_updateTime_age_at_receipt_minutes": float(first.publication_age_minutes.median()),
        "median_generatedAt_age_at_receipt_minutes": float(np.median(generated_age)),
        "median_updateTime_age_at_entry_minutes": float((first.publication_age_minutes+first.wait_minutes).median()),
        "warning": "updateTime/generatedAt are stored source timestamps, not independently observed first-publication times.",
        "audit_code_sha256": digest(Path(__file__))}
    write_json(root / "verification.json", audit)
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
