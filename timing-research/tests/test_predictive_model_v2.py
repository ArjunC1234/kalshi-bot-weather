from __future__ import annotations

import unittest

import pandas as pd

from predictive_model_v2.model import (
    TradePolicy,
    calendar_day_bootstrap_lower,
    feature_frame,
    replay,
    success_checks,
)


def frame() -> pd.DataFrame:
    rows = []
    for minute, raw_id in ((5, "q1"), (10, "q2")):
        for index in range(6):
            rows.append(
                {
                    "city": "nyc",
                    "event": "EVENT",
                    "target_date": "2026-09-04",
                    "market_raw_id": raw_id,
                    "market_ticker": f"T{index}",
                    "market_requested_at": pd.Timestamp(f"2026-09-04T18:{minute:02d}:00Z"),
                    "market_received_at": pd.Timestamp(f"2026-09-04T18:{minute:02d}:01Z"),
                    "weather_available_at": pd.Timestamp("2026-09-04T18:00:00Z"),
                    "weather_snapshot_id": "wx",
                    "hours_since_climate_start": 13,
                    "bracket_index": index,
                    "bracket_lower_f": None if index == 0 else 80 + index * 2,
                    "bracket_upper_f": None if index == 5 else 81 + index * 2,
                    "is_lower_tail": int(index == 0),
                    "is_upper_tail": int(index == 5),
                    "yes_bid": 0.14 if index == 0 else 0.01,
                    "yes_ask": 0.15 if index == 0 else 0.02,
                    "yes_ask_size": 2.0,
                    "no_bid": 0.84 if index == 0 else 0.97,
                    "no_ask": 0.85 if index == 0 else 0.98,
                    "no_ask_size": 2.0,
                    "market_probability": 0.15 if index == 0 else 0.02,
                    "winner_ticker": "T2",
                    "label_available": True,
                    "y": float(index == 2),
                    "weather_source_stddev_f": 1.0,
                    "observed_high_so_far_f": 82.0,
                    "nws_anchor_high_f": 84.0,
                    "hrrr_projected_high_f": 84.0,
                    "nbm_projected_high_f": 84.0,
                    "ensemble_raw_median_high_f": 84.0,
                }
            )
    return pd.DataFrame(rows)


class PredictiveModelTests(unittest.TestCase):
    def test_label_fields_are_not_features(self):
        features = feature_frame(frame(), use_market=True)
        self.assertNotIn("y", features)
        self.assertNotIn("winner_ticker", features)

    def test_model_ready_time_blocks_earlier_quotes(self):
        data = frame()
        probabilities = pd.Series(
            [0.1 if index % 6 == 0 else 0.18 for index in range(len(data))],
            index=data.index,
        )
        policy = TradePolicy("no", 0.8, 0.02, 0.5, 0.95, 0.05, 10)
        trades = replay(
            data,
            probabilities,
            policy,
            "2026-09-04",
            "2026-09-04",
            pd.Timestamp("2026-09-04T18:07:00Z"),
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["market_raw_id"], "q2")

    def test_first_eligible_quote_locks_one_trade_per_event(self):
        data = frame()
        probabilities = pd.Series(
            [0.1 if index % 6 == 0 else 0.18 for index in range(len(data))],
            index=data.index,
        )
        policy = TradePolicy("no", 0.8, 0.02, 0.5, 0.95, 0.05, 10)
        trades = replay(
            data,
            probabilities,
            policy,
            "2026-09-04",
            "2026-09-04",
            pd.Timestamp("2026-09-04T18:00:00Z"),
        )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["market_raw_id"], "q1")

    def test_missing_full_contract_depth_blocks_trade(self):
        data = frame()
        data["no_ask_size"] = 0.99
        probabilities = pd.Series(
            [0.1 if index % 6 == 0 else 0.18 for index in range(len(data))],
            index=data.index,
        )
        policy = TradePolicy("no", 0.8, 0.02, 0.5, 0.95, 0.05, 10)
        trades = replay(
            data,
            probabilities,
            policy,
            "2026-09-04",
            "2026-09-04",
            pd.Timestamp("2026-09-04T18:00:00Z"),
        )
        self.assertEqual(trades, [])

    def test_calendar_bootstrap_includes_zero_trade_days(self):
        rows = [
            {
                "label_available": True,
                "target_date": "2026-09-04",
                "net_pnl": 1.0,
            }
        ]
        lower = calendar_day_bootstrap_lower(rows, "2026-09-04", "2026-09-17")
        self.assertEqual(lower, 0.0)

    def test_missing_city_settlement_makes_window_incomplete(self):
        summary = {
            "settled_trades": 25,
            "active_days": 14,
            "cities": 6,
            "maximum_city_trade_fraction": 0.2,
            "hit_rate": 0.9,
            "iid_exact_95_hit_lower_bound": 0.7,
            "net_pnl": 2.0,
            "stress_net_pnl": 1.0,
            "day_bootstrap_net_pnl_95_lower": 0.1,
            "seven_day_halves": [{"net_pnl": 1.0}, {"net_pnl": 1.0}],
            "leave_one_city_out_net_pnl": {"nyc": 1.0},
            "label_coverage": 1.0,
            "availability_violations": 0,
        }
        requirements = {
            "test_start": "2026-09-04",
            "test_end": "2026-09-17",
            "success_requirements": {
                "minimum_trades": 25,
                "minimum_active_days": 8,
                "minimum_cities": 4,
                "maximum_city_trade_fraction": 0.4,
                "minimum_hit_rate": 0.8,
                "minimum_iid_exact_95_hit_lower_bound": 0.65,
                "minimum_label_coverage": 0.95,
            },
        }
        labels = {str(day.date()): 6 for day in pd.date_range("2026-09-04", "2026-09-17", tz="UTC")}
        labels["2026-09-16"] = 5
        checks = success_checks(summary, requirements, [], labels)
        self.assertFalse(checks["full_14_day_window_complete"])


if __name__ == "__main__":
    unittest.main()
