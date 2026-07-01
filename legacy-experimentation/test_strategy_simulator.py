from __future__ import annotations

import gzip
import json
import math
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from strategy_simulator import (
    GROUPED_REGRESSION_EDGE_STRATEGY,
    TOP_GAP_EV_STRATEGY,
    aggregate,
    build_trade_candidate_records,
    candidate_trades,
    fit_trade_decision_model,
    fit_grouped_regression_edge_model,
    grouped_strategy_feature_dict,
    kalshi_fee,
    latest_keys,
    prepare_grouped_strategy_predictions,
    prepare_trade_model_predictions,
    select_grouped_strategy_trade,
    simulate_trades,
)


def write_snapshot(
    root: Path,
    checkpoint: str = "t_plus_18h",
    ask_size: int | None = None,
    target_date: str = "2026-06-22",
    city: str = "den",
    ask_a: float = 0.40,
    ask_b: float = 0.70,
) -> None:
    path = root / "cohorts" / "test" / "snapshots" / target_date / city
    path.mkdir(parents=True, exist_ok=True)
    market_a = {"ticker": "A", "yes_ask_dollars": ask_a}
    if ask_size is not None:
        market_a["yes_ask_size"] = ask_size
    payload = {
        "event": {
            "markets": [
                market_a,
                {"ticker": "B", "yes_ask_dollars": ask_b},
            ]
        }
    }
    with gzip.open(path / f"{checkpoint}.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)


def row(
    probabilities: list[float],
    checkpoint: str = "t_plus_18h",
    model: str = "full",
    as_of: str = "2026-06-22T18:00:00+00:00",
    target_date: str = "2026-06-22",
    city: str = "den",
    winner_ticker: str = "A",
) -> dict[str, object]:
    return {
        "model": model,
        "city": city,
        "target_date": target_date,
        "checkpoint": checkpoint,
        "as_of": as_of,
        "winner_ticker": winner_ticker,
        "tickers_json": json.dumps(["A", "B"]),
        "probabilities_json": json.dumps(probabilities),
    }


def trade_feature_record(
    target_date: str,
    city: str,
    model_probability: float,
    yes_ask: float,
    profitable: int,
) -> dict[str, object]:
    feature = {
        "model_probability": model_probability,
        "model_top_probability_gap": 0.65,
        "candidate_probability_gap_to_leader": 0.0,
        "yes_ask": yes_ask,
        "fee_per_contract": 0.0,
        "model_ev": model_probability - yes_ask,
        "market_midpoint_probability": yes_ask,
        "model_minus_market_probability": model_probability - yes_ask,
        "ask_minus_market_probability": 0.0,
        "bracket_index": 0.0,
        "relative_bracket_index": 0.0,
        "bracket_count": 2.0,
        "model_top_distance": 0.0,
        "abs_model_top_distance": 0.0,
        "market_top_distance": 0.0,
        "abs_market_top_distance": 0.0,
        "hrrr_top_distance": 0.0,
        "abs_hrrr_top_distance": 0.0,
        "ask_size": 0.0,
        "checkpoint_order": 18.0,
        "city": city,
        "checkpoint": "t_plus_18h",
        "is_model_top": "True",
        "is_market_top": "True",
        "is_hrrr_top_or_adjacent": "True",
        "is_low_ask": str(yes_ask <= 0.10),
        "is_tiny_ask": str(yes_ask <= 0.05),
    }
    return {
        "target_date": target_date,
        "city": city,
        "features": feature,
        "profitable": profitable,
    }


def grouped_record(
    target_date: str,
    city: str,
    ticker: str,
    probability: float,
    yes_ask: float,
    won: bool,
) -> dict[str, object]:
    features = {
        "candidate_probability_gap_to_leader": 0.0 if ticker == "A" else -0.6,
        "model_top_probability_gap": 0.6,
        "relative_bracket_index": 0.0 if ticker == "A" else 1.0,
    }
    return {
        "target_date": target_date,
        "city": city,
        "checkpoint": "t_plus_18h",
        "as_of": f"{target_date}T18:00:00+00:00",
        "model": "regression_trained_weather_hrrr",
        "ticker": ticker,
        "winner_ticker": "A" if won else "B",
        "probability": probability,
        "yes_ask": yes_ask,
        "yes_ask_size": None,
        "fee_per_contract": 0.0,
        "model_ev": probability - yes_ask,
        "market_midpoint_probability": yes_ask,
        "hrrr_probability": probability,
        "hrrr_agrees_with_weather_top": ticker == "A",
        "bracket_index": 0 if ticker == "A" else 1,
        "model_top_distance": 0 if ticker == "A" else 1,
        "hrrr_top_distance": 0 if ticker == "A" else 1,
        "profitable": 0,
        "won": won,
        "realized_net_pnl_per_contract": (1.0 if won else 0.0) - yes_ask,
        "features": features,
        "row": {
            "model": "regression_trained_weather_hrrr",
            "city": city,
            "target_date": target_date,
            "checkpoint": "t_plus_18h",
            "as_of": f"{target_date}T18:00:00+00:00",
            "winner_ticker": "A" if won else "B",
        },
    }


class StrategySimulatorTests(unittest.TestCase):
    def test_kalshi_fee_modes_and_rounding(self) -> None:
        self.assertTrue(math.isclose(kalshi_fee(0.50, mode="taker"), 0.02))
        self.assertTrue(math.isclose(kalshi_fee(0.50, mode="maker"), 0.01))
        self.assertEqual(kalshi_fee(0.50, mode="none"), 0.0)
        with self.assertRaises(ValueError):
            kalshi_fee(0.50, mode="bad")

    def test_candidate_trade_pnl_uses_ask_and_fee(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_snapshot(root)
            trades = candidate_trades(
                root,
                "test",
                row([0.60, 0.40]),
                fee_mode="none",
                contracts=1,
                sizing="fixed",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.0,
                longshot_ask_threshold=0.05,
                longshot_max_contracts=None,
                longshot_min_ev=0.0,
                max_ask=0.95,
                min_probability=0.0,
                min_ev=0.0,
                strategy="taker_ev",
                market_probs={},
                disagreement_margin=0.05,
            )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["ticker"], "A")
        self.assertTrue(math.isclose(trades[0]["net_pnl"], 0.60))

    def test_simulation_selects_single_best_ev_contract_per_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_snapshot(root)
            trades = simulate_trades(
                root,
                "test",
                [row([0.60, 0.80])],
                models=("full",),
                min_evs=(0.0,),
                fee_mode="none",
                contracts=1,
                sizing="fixed",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.0,
                longshot_ask_threshold=0.05,
                longshot_max_contracts=None,
                longshot_min_ev=0.0,
                max_ask=0.95,
                min_probability=0.0,
                strategies=("taker_ev",),
                disagreement_margin=0.05,
            )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["ticker"], "A")

    def test_filters_reject_low_ev_high_ask_and_latest_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_snapshot(root, "t_plus_10h")
            write_snapshot(root, "t_plus_18h")
            rows = [
                row([0.45, 0.55], checkpoint="t_plus_10h", as_of="2026-06-22T10:00:00+00:00"),
                row([0.60, 0.40], checkpoint="t_plus_18h", as_of="2026-06-22T18:00:00+00:00"),
            ]
            self.assertEqual(latest_keys(rows), {("2026-06-22", "den", "full", "t_plus_18h")})
            trades = simulate_trades(
                root,
                "test",
                rows,
                models=("full",),
                min_evs=(0.50,),
                fee_mode="none",
                contracts=1,
                sizing="fixed",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.0,
                longshot_ask_threshold=0.05,
                longshot_max_contracts=None,
                longshot_min_ev=0.0,
                max_ask=0.50,
                min_probability=0.0,
                strategies=("taker_ev_latest_only",),
                disagreement_margin=0.05,
            )
        self.assertEqual(trades, [])

    def test_kelly_sizing_caps_contracts_by_max_position_and_max_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_snapshot(root)
            trades = candidate_trades(
                root,
                "test",
                row([0.80, 0.20]),
                fee_mode="none",
                contracts=1,
                sizing="kelly",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.0,
                longshot_ask_threshold=0.05,
                longshot_max_contracts=None,
                longshot_min_ev=0.0,
                max_ask=0.95,
                min_probability=0.0,
                min_ev=0.0,
                strategy="taker_ev",
                market_probs={},
                disagreement_margin=0.05,
            )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["contracts"], 10)
        self.assertTrue(math.isclose(trades[0]["kelly_fraction_raw"], 2.0 / 3.0))
        self.assertTrue(math.isclose(trades[0]["kelly_fraction_used"], 0.05))

    def test_kelly_sizing_rejects_tiny_position(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_snapshot(root)
            trades = candidate_trades(
                root,
                "test",
                row([0.45, 0.55]),
                fee_mode="none",
                contracts=1,
                sizing="kelly",
                bankroll=1.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.0,
                longshot_ask_threshold=0.05,
                longshot_max_contracts=None,
                longshot_min_ev=0.0,
                max_ask=0.95,
                min_probability=0.0,
                min_ev=0.0,
                strategy="taker_ev",
                market_probs={},
                disagreement_margin=0.05,
            )
        self.assertEqual(trades, [])

    def test_ask_size_caps_kelly_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_snapshot(root, ask_size=3)
            trades = candidate_trades(
                root,
                "test",
                row([0.80, 0.20]),
                fee_mode="none",
                contracts=1,
                sizing="kelly",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.0,
                longshot_ask_threshold=0.05,
                longshot_max_contracts=None,
                longshot_min_ev=0.0,
                max_ask=0.95,
                min_probability=0.0,
                min_ev=0.0,
                strategy="taker_ev",
                market_probs={},
                disagreement_margin=0.05,
            )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["contracts"], 3)
        self.assertEqual(trades[0]["ask_size_cap"], 3)

    def test_min_ask_rejects_cheap_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_snapshot(root)
            trades = candidate_trades(
                root,
                "test",
                row([0.80, 0.20]),
                fee_mode="none",
                contracts=1,
                sizing="kelly",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.50,
                longshot_ask_threshold=0.05,
                longshot_max_contracts=None,
                longshot_min_ev=0.0,
                max_ask=0.95,
                min_probability=0.0,
                min_ev=0.0,
                strategy="taker_ev",
                market_probs={},
                disagreement_margin=0.05,
            )
        self.assertEqual(trades, [])

    def test_top_gap_ev_requires_confident_top_pick(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_snapshot(root)
            low_gap = candidate_trades(
                root,
                "test",
                row([0.52, 0.48]),
                fee_mode="none",
                contracts=1,
                sizing="fixed",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.0,
                longshot_ask_threshold=0.05,
                longshot_max_contracts=None,
                longshot_min_ev=0.0,
                max_ask=0.95,
                min_probability=0.0,
                min_ev=0.0,
                strategy=TOP_GAP_EV_STRATEGY,
                market_probs={},
                disagreement_margin=0.05,
                min_confidence_gap=0.10,
            )
            high_gap = candidate_trades(
                root,
                "test",
                row([0.80, 0.20]),
                fee_mode="none",
                contracts=1,
                sizing="fixed",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.0,
                longshot_ask_threshold=0.05,
                longshot_max_contracts=None,
                longshot_min_ev=0.0,
                max_ask=0.95,
                min_probability=0.0,
                min_ev=0.0,
                strategy=TOP_GAP_EV_STRATEGY,
                market_probs={},
                disagreement_margin=0.05,
                min_confidence_gap=0.10,
            )
        self.assertEqual(low_gap, [])
        self.assertEqual(len(high_gap), 1)
        self.assertEqual(high_gap[0]["ticker"], "A")
        self.assertTrue(math.isclose(high_gap[0]["top_two_probability_gap"], 0.60))
        self.assertTrue(math.isclose(high_gap[0]["min_confidence_gap"], 0.10))

    def test_longshot_controls_cap_contracts_and_raise_required_ev(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_snapshot(root)
            capped = candidate_trades(
                root,
                "test",
                row([0.80, 0.20]),
                fee_mode="none",
                contracts=1,
                sizing="kelly",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.0,
                longshot_ask_threshold=0.50,
                longshot_max_contracts=2,
                longshot_min_ev=0.0,
                max_ask=0.95,
                min_probability=0.0,
                min_ev=0.0,
                strategy="taker_ev",
                market_probs={},
                disagreement_margin=0.05,
            )
            rejected = candidate_trades(
                root,
                "test",
                row([0.80, 0.20]),
                fee_mode="none",
                contracts=1,
                sizing="kelly",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.0,
                longshot_ask_threshold=0.50,
                longshot_max_contracts=2,
                longshot_min_ev=0.50,
                max_ask=0.95,
                min_probability=0.0,
                min_ev=0.0,
                strategy="taker_ev",
                market_probs={},
                disagreement_margin=0.05,
            )
        self.assertEqual(capped[0]["contracts"], 2)
        self.assertEqual(capped[0]["effective_max_contracts"], 2)
        self.assertEqual(rejected, [])

    def test_trade_model_features_exclude_settlement_label_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_snapshot(root)
            records = build_trade_candidate_records(
                root,
                "test",
                [row([0.80, 0.20], model="regression_trained_weather_hrrr")],
                base_model="regression_trained_weather_hrrr",
                fee_mode="none",
            )
        self.assertEqual(len(records), 2)
        feature_keys = set(records[0]["features"])
        self.assertNotIn("winner_ticker", feature_keys)
        self.assertNotIn("won", feature_keys)
        self.assertNotIn("profitable", feature_keys)

    def test_trade_decision_model_falls_back_before_minimum_events(self) -> None:
        records = [
            trade_feature_record("2026-06-01", "den", 0.80, 0.40, 1),
            trade_feature_record("2026-06-01", "den", 0.20, 0.70, 0),
        ]
        fit = fit_trade_decision_model(records, min_events=12)
        self.assertEqual(fit["mode"], "fallback_min_train_events")
        self.assertIsNone(fit["model"])

    def test_trade_decision_model_learns_synthetic_profitable_pattern(self) -> None:
        records = []
        for day in range(1, 14):
            records.append(
                trade_feature_record(f"2026-06-{day:02d}", "den", 0.85, 0.40, 1)
            )
            records.append(
                trade_feature_record(f"2026-06-{day:02d}", "den", 0.20, 0.70, 0)
            )
        fit = fit_trade_decision_model(records, min_events=12)
        self.assertEqual(fit["mode"], "trained")
        high = fit["model"].predict_proba(  # type: ignore[union-attr]
            pd.DataFrame(
                [trade_feature_record("2026-06-20", "den", 0.85, 0.40, 1)["features"]]
            )
        )[0][1]
        low = fit["model"].predict_proba(  # type: ignore[union-attr]
            pd.DataFrame(
                [trade_feature_record("2026-06-20", "den", 0.20, 0.70, 0)["features"]]
            )
        )[0][1]
        self.assertGreater(high, low)

    def test_trade_model_predictions_train_only_on_prior_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = []
            for day in range(1, 15):
                target_date = f"2026-06-{day:02d}"
                write_snapshot(root, target_date=target_date)
                rows.append(
                    row(
                        [0.80, 0.20],
                        model="regression_trained_weather_hrrr",
                        target_date=target_date,
                        as_of=f"{target_date}T18:00:00+00:00",
                    )
                )
            _, scores, metadata = prepare_trade_model_predictions(
                root,
                "test",
                rows,
                base_model="regression_trained_weather_hrrr",
                fee_mode="none",
                min_events=12,
            )
        by_date = {item["target_date"]: item for item in metadata}
        self.assertEqual(by_date["2026-06-12"]["mode"], "fallback_min_train_events")
        self.assertEqual(by_date["2026-06-13"]["mode"], "trained")
        june_13_scores = [row for row in scores if row["target_date"] == "2026-06-13"]
        self.assertTrue(all(row["trade_model_training_events"] == 12 for row in june_13_scores))

    def test_regression_trade_filter_selects_one_learned_trade(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = []
            for day in range(1, 15):
                target_date = f"2026-06-{day:02d}"
                write_snapshot(root, target_date=target_date)
                rows.append(
                    row(
                        [0.80, 0.20],
                        model="regression_trained_weather_hrrr",
                        target_date=target_date,
                        as_of=f"{target_date}T18:00:00+00:00",
                    )
                )
            trades = simulate_trades(
                root,
                "test",
                rows,
                models=("regression_trained_weather_hrrr",),
                min_evs=(0.0,),
                fee_mode="none",
                contracts=1,
                sizing="fixed",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.05,
                longshot_ask_threshold=0.05,
                longshot_max_contracts=None,
                longshot_min_ev=0.0,
                max_ask=0.95,
                min_probability=0.0,
                strategies=("regression_trade_filter",),
                disagreement_margin=0.05,
                trade_model_base_model="regression_trained_weather_hrrr",
                trade_model_min_events=12,
                trade_probability_threshold=0.55,
                trade_model_min_ev=0.02,
                trade_model_min_probability=0.05,
            )
        self.assertEqual({trade["ticker"] for trade in trades}, {"A"})
        self.assertTrue(all(trade["strategy"] == "regression_trade_filter" for trade in trades))
        self.assertTrue(any(not trade["trade_model_used_fallback"] for trade in trades))

    def test_regression_trade_filter_rejects_below_min_ask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = []
            for day in range(1, 15):
                target_date = f"2026-06-{day:02d}"
                write_snapshot(root, target_date=target_date, ask_a=0.04)
                rows.append(
                    row(
                        [0.80, 0.20],
                        model="regression_trained_weather_hrrr",
                        target_date=target_date,
                        as_of=f"{target_date}T18:00:00+00:00",
                    )
                )
            trades = simulate_trades(
                root,
                "test",
                rows,
                models=("regression_trained_weather_hrrr",),
                min_evs=(0.0,),
                fee_mode="none",
                contracts=1,
                sizing="fixed",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.05,
                longshot_ask_threshold=0.05,
                longshot_max_contracts=None,
                longshot_min_ev=0.0,
                max_ask=0.95,
                min_probability=0.0,
                strategies=("regression_trade_filter",),
                disagreement_margin=0.05,
                trade_model_base_model="regression_trained_weather_hrrr",
                trade_model_min_events=12,
                trade_probability_threshold=0.55,
                trade_model_min_ev=0.02,
                trade_model_min_probability=0.05,
            )
        self.assertEqual(trades, [])

    def test_grouped_strategy_features_exclude_settlement_label_fields(self) -> None:
        record = grouped_record("2026-06-01", "den", "A", 0.80, 0.40, True)
        feature_keys = set(grouped_strategy_feature_dict(record))
        self.assertNotIn("winner_ticker", feature_keys)
        self.assertNotIn("won", feature_keys)
        self.assertNotIn("profitable", feature_keys)

    def test_grouped_strategy_trains_on_won_not_profitability(self) -> None:
        records = []
        for day in range(1, 22):
            target_date = f"2026-06-{day:02d}"
            winner = grouped_record(target_date, "den", "A", 0.85, 0.99, True)
            loser = grouped_record(target_date, "den", "B", 0.15, 0.01, False)
            winner["profitable"] = 0
            loser["profitable"] = 1
            records.extend([winner, loser])
        fit = fit_grouped_regression_edge_model(records, min_events=20)
        self.assertEqual(fit["mode"], "trained")
        high = fit["model"].predict_proba(  # type: ignore[union-attr]
            pd.DataFrame([grouped_strategy_feature_dict(records[0])])
        )[0][1]
        low = fit["model"].predict_proba(  # type: ignore[union-attr]
            pd.DataFrame([grouped_strategy_feature_dict(records[1])])
        )[0][1]
        self.assertGreater(high, low)

    def test_grouped_strategy_predictions_train_only_on_prior_dates_and_normalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = []
            for day in range(1, 23):
                target_date = f"2026-06-{day:02d}"
                write_snapshot(root, target_date=target_date)
                rows.append(
                    row(
                        [0.80, 0.20],
                        model="regression_trained_weather_hrrr",
                        target_date=target_date,
                        as_of=f"{target_date}T18:00:00+00:00",
                    )
                )
            _, scores, metadata = prepare_grouped_strategy_predictions(
                root,
                "test",
                rows,
                base_model="regression_trained_weather_hrrr",
                fee_mode="none",
                min_events=20,
            )
        by_date = {item["target_date"]: item for item in metadata}
        self.assertEqual(by_date["2026-06-20"]["mode"], "insufficient_training_data")
        self.assertEqual(by_date["2026-06-21"]["mode"], "trained")
        june_21 = [item for item in scores if item["target_date"] == "2026-06-21"]
        self.assertTrue(all(item["grouped_training_events"] == 20 for item in june_21))
        self.assertTrue(
            math.isclose(
                sum(float(item["fair_probability"]) for item in june_21),
                1.0,
            )
        )

    def test_grouped_strategy_no_trade_before_minimum_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = []
            decisions: list[dict[str, object]] = []
            for day in range(1, 5):
                target_date = f"2026-06-{day:02d}"
                write_snapshot(root, target_date=target_date)
                rows.append(
                    row(
                        [0.80, 0.20],
                        model="regression_trained_weather_hrrr",
                        target_date=target_date,
                        as_of=f"{target_date}T18:00:00+00:00",
                    )
                )
            trades = simulate_trades(
                root,
                "test",
                rows,
                models=("regression_trained_weather_hrrr",),
                min_evs=(0.0,),
                fee_mode="none",
                contracts=1,
                sizing="fixed",
                bankroll=100.0,
                kelly_multiplier=0.25,
                max_position_fraction=0.05,
                max_contracts=10,
                use_ask_size=True,
                min_ask=0.05,
                longshot_ask_threshold=0.05,
                longshot_max_contracts=None,
                longshot_min_ev=0.0,
                max_ask=0.95,
                min_probability=0.0,
                strategies=(GROUPED_REGRESSION_EDGE_STRATEGY,),
                disagreement_margin=0.05,
                grouped_strategy_min_events=20,
                grouped_strategy_decision_rows=decisions,
            )
        self.assertEqual(trades, [])
        self.assertEqual(len(decisions), 4)
        self.assertTrue(
            all(item["no_trade_reason"] == "insufficient_training_data" for item in decisions)
        )

    def test_grouped_strategy_selects_one_trade_and_records_decision(self) -> None:
        predictions = [
            {
                "record": grouped_record("2026-06-21", "den", "A", 0.80, 0.40, True),
                "grouped_raw_score": 0.8,
                "fair_probability": 0.70,
                "learned_ev": 0.30,
                "grouped_model_mode": "trained",
                "grouped_training_events": 20,
                "grouped_training_candidates": 40,
            },
            {
                "record": grouped_record("2026-06-21", "den", "B", 0.20, 0.70, False),
                "grouped_raw_score": 0.2,
                "fair_probability": 0.30,
                "learned_ev": -0.40,
                "grouped_model_mode": "trained",
                "grouped_training_events": 20,
                "grouped_training_candidates": 40,
            },
        ]
        trade, decision = select_grouped_strategy_trade(
            predictions,
            min_ev=0.0,
            grouped_min_ev=0.03,
            fee_mode="none",
            contracts=1,
            sizing="fixed",
            bankroll=100.0,
            kelly_multiplier=0.25,
            max_position_fraction=0.05,
            max_contracts=10,
            use_ask_size=True,
            min_ask=0.05,
            longshot_ask_threshold=0.05,
            longshot_max_contracts=None,
            longshot_min_ev=0.0,
            max_ask=0.95,
            min_fair_probability=0.08,
            min_candidate_gap=-0.20,
        )
        self.assertIsNotNone(trade)
        self.assertEqual(trade["ticker"], "A")  # type: ignore[index]
        self.assertEqual(decision["decision"], "trade")

    def test_grouped_strategy_gates_emit_no_trade_reason(self) -> None:
        prediction = {
            "record": grouped_record("2026-06-21", "den", "B", 0.20, 0.70, False),
            "grouped_raw_score": 0.2,
            "fair_probability": 0.30,
            "learned_ev": -0.40,
            "grouped_model_mode": "trained",
            "grouped_training_events": 20,
            "grouped_training_candidates": 40,
        }
        trade, decision = select_grouped_strategy_trade(
            [prediction],
            min_ev=0.0,
            grouped_min_ev=0.03,
            fee_mode="none",
            contracts=1,
            sizing="fixed",
            bankroll=100.0,
            kelly_multiplier=0.25,
            max_position_fraction=0.05,
            max_contracts=10,
            use_ask_size=True,
            min_ask=0.05,
            longshot_ask_threshold=0.05,
            longshot_max_contracts=None,
            longshot_min_ev=0.0,
            max_ask=0.95,
            min_fair_probability=0.08,
            min_candidate_gap=-0.20,
        )
        self.assertIsNone(trade)
        self.assertEqual(decision["decision"], "no_trade")
        self.assertEqual(decision["no_trade_reason"], "too_far_from_weather_leader")

    def test_aggregate_reports_net_pnl_roi_and_hit_rate(self) -> None:
        rows = [
            {
                "strategy": "s",
                "model": "m",
                "min_ev": 0.0,
                "stake": 0.5,
                "net_pnl": 0.5,
                "fee": 0.01,
                "gross_pnl_before_fees": 0.51,
                "won": True,
                "probability": 0.8,
                "yes_ask": 0.5,
                "expected_value": 0.29,
                "contracts": 1,
            },
            {
                "strategy": "s",
                "model": "m",
                "min_ev": 0.0,
                "stake": 0.4,
                "net_pnl": -0.4,
                "fee": 0.0,
                "gross_pnl_before_fees": -0.4,
                "won": False,
                "probability": 0.7,
                "yes_ask": 0.4,
                "expected_value": 0.3,
                "contracts": 1,
            },
        ]
        summary = aggregate(rows, ("strategy", "model", "min_ev"))[0]
        self.assertTrue(math.isclose(summary["net_pnl"], 0.1))
        self.assertTrue(math.isclose(summary["roi"], 0.1 / 0.9))
        self.assertEqual(summary["hit_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
