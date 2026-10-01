from __future__ import annotations

import unittest

import pandas as pd

from predictive_model_v3.model import (
    StrategyPolicy,
    generate_oos_predictions,
    replay_strategy,
    selection_passes,
    strategy_grid,
    success_checks,
)


def row(
    event: str,
    target_date: str,
    market_raw_id: str,
    ticker: str,
    index: int,
    requested: str,
    settled_at: str,
    y: float,
    probability: float,
) -> dict:
    return {
        "city": "nyc",
        "event": event,
        "target_date": target_date,
        "market_raw_id": market_raw_id,
        "market_ticker": ticker,
        "market_requested_at": pd.Timestamp(requested),
        "market_received_at": pd.Timestamp(requested) + pd.Timedelta(seconds=1),
        "weather_available_at": pd.Timestamp(requested) - pd.Timedelta(minutes=5),
        "weather_snapshot_id": "wx",
        "hours_since_climate_start": 10.0,
        "bracket_index": index,
        "bracket_lower_f": 80 + index,
        "bracket_upper_f": 80 + index,
        "is_lower_tail": int(index == 0),
        "is_upper_tail": int(index == 1),
        "yes_bid": probability - 0.01,
        "yes_ask": probability + 0.01,
        "yes_ask_size": 2.0,
        "no_bid": 1 - probability - 0.02,
        "no_ask": 1 - probability - 0.01,
        "no_ask_size": 2.0,
        "market_probability": probability,
        "winner_ticker": ticker if y else "other",
        "settled_at": pd.Timestamp(settled_at),
        "label_available": True,
        "availability_violation": False,
        "y": y,
        "weather_source_stddev_f": 1.0,
        "observed_high_so_far_f": 81.0,
        "nws_anchor_high_f": 82.0,
        "hrrr_projected_high_f": 82.0,
        "nbm_projected_high_f": 82.0,
        "ensemble_raw_median_high_f": 82.0,
    }


def tiny_frame() -> pd.DataFrame:
    rows = []
    for event, target_date, raw_id, settled_at in [
        ("TRAIN_READY", "2026-09-01", "q_train_ready", "2026-09-02T00:00:00Z"),
        ("TRAIN_LATE", "2026-09-02", "q_train_late", "2026-09-03T19:00:00Z"),
        ("TEST", "2026-09-03", "q_test", "2026-09-03T23:00:00Z"),
    ]:
        requested = f"{target_date}T18:00:00Z"
        rows.append(
            row(event, target_date, raw_id, f"{event}_A", 0, requested, settled_at, 1.0, 0.7)
        )
        rows.append(
            row(event, target_date, raw_id, f"{event}_B", 1, requested, settled_at, 0.0, 0.3)
        )
    return pd.DataFrame(rows)


def requirements() -> dict:
    return {
        "post_switch_start": "2026-09-01",
        "minimum_training_events": 1,
        "probability_floor": 0.0001,
        "folds": [{"name": "rolling_14", "type": "rolling", "train_days": 14}],
        "model_candidates": [
            {"name": "raw_market", "kind": "raw_market", "use_market": True, "c": None}
        ],
    }


class PredictiveModelV3Tests(unittest.TestCase):
    def test_oos_generation_excludes_labels_settled_after_test_cutoff(self):
        predictions, folds = generate_oos_predictions(tiny_frame(), requirements())
        test_folds = [fold for fold in folds if fold["target_date"] == "2026-09-03"]
        self.assertEqual(len(test_folds), 1)
        self.assertEqual(test_folds[0]["train_events"], 1)
        test_predictions = [
            row for row in predictions if row["target_date"] == "2026-09-03"
        ]
        self.assertEqual({row["event"] for row in test_predictions}, {"TEST"})

    def test_selection_requires_positive_bootstrap_lower_bound(self):
        summary = {
            "settled_trades": 12,
            "active_days": 6,
            "hit_rate": 0.8,
            "selected_calibration_gap": 0.01,
            "net_pnl": 1.0,
            "stress_net_pnl": 0.5,
            "day_bootstrap_net_pnl_95_lower": -0.1,
            "availability_violations": 0,
        }
        req = {
            "selection_requirements": {
                "minimum_trades": 12,
                "minimum_active_days": 5,
                "minimum_hit_rate": 0.75,
                "maximum_selected_calibration_gap": 0.12,
                "positive_net_pnl": True,
                "positive_fee_stress_pnl": True,
                "positive_day_bootstrap_95_lower_bound": True,
                "zero_availability_violations": True,
            }
        }
        self.assertFalse(selection_passes(summary, req))

    def test_success_checks_reject_overconfident_selected_trades(self):
        summary = {
            "settled_trades": 30,
            "active_days": 10,
            "cities": 5,
            "maximum_city_trade_fraction": 0.3,
            "hit_rate": 0.8,
            "iid_exact_95_hit_lower_bound": 0.7,
            "selected_calibration_gap": 0.2,
            "net_pnl": 1.0,
            "stress_net_pnl": 0.5,
            "day_bootstrap_net_pnl_95_lower": 0.1,
            "seven_day_halves": [{"net_pnl": 0.5}, {"net_pnl": 0.5}],
            "leave_one_city_out_net_pnl": {"nyc": 0.1},
            "label_coverage": 1.0,
            "availability_violations": 0,
        }
        req = {
            "success_requirements": {
                "minimum_trades": 25,
                "minimum_active_days": 8,
                "minimum_cities": 4,
                "maximum_city_trade_fraction": 0.4,
                "minimum_hit_rate": 0.8,
                "minimum_iid_exact_95_hit_lower_bound": 0.65,
                "maximum_selected_calibration_gap": 0.1,
                "positive_net_pnl": True,
                "positive_fee_stress_pnl": True,
                "positive_day_bootstrap_95_lower_bound": True,
                "positive_pnl_in_each_seven_day_half": True,
                "nonnegative_leave_one_city_out_pnl": True,
                "minimum_label_coverage": 0.95,
                "zero_availability_violations": True,
                "zero_input_integrity_errors": True,
            }
        }
        checks = success_checks(summary, req, [])
        self.assertFalse(checks["maximum_selected_calibration_gap"])

    def test_strategy_grid_includes_market_baseline(self):
        req = {
            "strategy_grid": {
                "side": ["no"],
                "minimum_probability": [0.8],
                "minimum_net_edge": [0.02],
                "minimum_entry_price": [0.5],
                "maximum_entry_price": [0.8],
                "maximum_spread": [0.05],
                "minimum_depth": [1.0],
                "minimum_climate_hour": [10],
            }
        }
        policies = strategy_grid(req, [("rolling_14", "raw_market")])
        self.assertEqual(policies[0].fold_name, "rolling_14")
        self.assertEqual(policies[0].model_name, "raw_market")

    def test_replay_strategy_filters_to_selected_fold(self):
        base = {
            "model": "raw_market",
            "target_date": "2026-09-04",
            "city": "nyc",
            "event": "EVT",
            "market_raw_id": "q",
            "market_ticker": "TICKER",
            "market_requested_at": "2026-09-04T18:00:00Z",
            "weather_available_at": "2026-09-04T17:55:00Z",
            "model_ready_at": "2026-09-04T00:00:00Z",
            "hours_since_climate_start": 10.0,
            "yes_probability": 0.9,
            "yes_bid": 0.5,
            "yes_ask": 0.55,
            "yes_ask_size": 2.0,
            "no_bid": 0.4,
            "no_ask": 0.45,
            "no_ask_size": 2.0,
            "winner_ticker": "TICKER",
            "y": 1.0,
            "availability_violation": False,
        }
        predictions = pd.DataFrame(
            [
                {**base, "fold": "rolling_14"},
                {**base, "fold": "rolling_21", "event": "OTHER"},
            ]
        )
        policy = StrategyPolicy(
            fold_name="rolling_14",
            model_name="raw_market",
            side="yes",
            minimum_probability=0.8,
            minimum_net_edge=0.0,
            minimum_entry_price=0.5,
            maximum_entry_price=0.8,
            maximum_spread=0.1,
            minimum_depth=1.0,
            minimum_climate_hour=10,
        )
        trades = replay_strategy(
            predictions,
            policy,
            "2026-09-04",
            "2026-09-04",
            {"entry_adverse_dollars": 0.01},
        )
        self.assertEqual([trade["fold"] for trade in trades], ["rolling_14"])


if __name__ == "__main__":
    unittest.main()
