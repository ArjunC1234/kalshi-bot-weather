import unittest
import numpy as np
import pandas as pd

from scripts.hit80_14d_research import _select_rows
from scripts.profitability_research import Policy, daily, fee, metrics, model_ready_mask, replay, select, side_rows


def quote(hour=14, **changes):
    row = dict(target_date="2026-08-18", city="nyc", event_ticker="E1", market_ticker="M1",
               snapshot_time_utc=pd.Timestamp(f"2026-08-18T{hour:02}:00Z"),
               end_time=pd.Timestamp("2026-08-19T00:00Z"), hours=hour,
               active=True, known_future_feature=False, ask=.70, bid=.69,
               price=.71, spread=.01, depth=20., probability=.90, side="no",
               hit=1., bounded=True, rule_source="weather_company")
    return {**row, **changes}


class ReplayTests(unittest.TestCase):
    def test_model_cannot_use_late_arriving_training_labels(self):
        decisions = pd.Series(pd.to_datetime(["2026-08-18T15:00Z", "2026-08-18T16:00Z", "2026-08-18T17:00Z"]))
        ready = pd.Series(pd.to_datetime(["2026-08-18T15:01Z", "2026-08-18T15:01Z", None]))
        self.assertEqual(model_ready_mask(decisions, ready).tolist(), [False, True, False])

    def run_rows(self, rows, **kwargs):
        return replay(pd.DataFrame(rows), Policy(), "2026-08-18", "2026-08-31", **kwargs)

    def test_future_better_edge_cannot_replace_earlier_order(self):
        early = self.run_rows([quote()])
        future = self.run_rows([quote(), quote(15, probability=.99, ask=.65)])
        pd.testing.assert_frame_equal(early, future)

    def test_outcome_permutation_cannot_change_orders(self):
        won = self.run_rows([quote()])
        lost = self.run_rows([quote(hit=0.)])
        fields = ["market_ticker", "side", "price", "contracts", "decision_time"]
        pd.testing.assert_frame_equal(won[fields], lost[fields])
        self.assertNotEqual(won.net_pnl.iloc[0], lost.net_pnl.iloc[0])

    def test_missing_outcome_remains_unsettled(self):
        trades = self.run_rows([quote(hit=np.nan)])
        self.assertTrue(pd.isna(trades.net_pnl.iloc[0]))
        result = metrics(trades, "2026-08-18", "2026-08-31")
        self.assertEqual(result["unsettled_trades"], 1)
        self.assertEqual(result["wins"], 0)
        self.assertIsNone(result["hit_rate"])

    def test_missing_yes_outcome_does_not_become_no_win(self):
        frame = pd.DataFrame([dict(quote(), y=np.nan, yes_ask_dollars=.3, yes_bid_dollars=.29,
                                   no_ask_dollars=.71, no_bid_dollars=.7)])
        result = side_rows(frame, np.array([.2]))
        self.assertTrue(result.hit.isna().all())

    def test_fees_are_included_in_order_cap(self):
        trades = self.run_rows([quote(ask=.75, bid=.74)], slippage=0)
        self.assertEqual(trades.contracts.iloc[0], 3)
        self.assertLessEqual(trades.cash_debit.iloc[0], 3.)
        self.assertAlmostEqual(trades.net_pnl.iloc[0], .75 - fee(3, .75))

    def test_fee_rounding(self):
        self.assertEqual(fee(1, .5), .02)
        self.assertEqual(fee(100, .5), 1.75)

    def test_visible_depth_caps_size(self):
        self.assertEqual(self.run_rows([quote(depth=1.9)]).contracts.iloc[0], 1)
        self.assertTrue(self.run_rows([quote(depth=0)]).empty)
        self.assertTrue(self.run_rows([quote(depth=np.nan)]).empty)

    def test_daily_budget_includes_fees(self):
        trades = self.run_rows([quote(event_ticker=f"E{i}") for i in range(12)], daily_budget=5.)
        self.assertLessEqual(trades.cash_debit.sum(), 5.)

    def test_rank_only_current_snapshot(self):
        trades = self.run_rows([quote(market_ticker="M2", probability=.95), quote()])
        self.assertEqual(trades.market_ticker.tolist(), ["M2"])

    def test_known_future_feature_is_rejected(self):
        self.assertTrue(self.run_rows([quote(known_future_feature=True)]).empty)

    def test_receipt_time_controls_decision_order(self):
        first_quote = quote(decision_time=pd.Timestamp("2026-08-18T15:20Z"))
        second_quote = quote(15, decision_time=pd.Timestamp("2026-08-18T15:01Z"), probability=.95)
        result = self.run_rows([first_quote, second_quote])
        self.assertEqual(pd.Timestamp(result.decision_time.iloc[0]), pd.Timestamp("2026-08-18T15:01Z"))

    def test_unverified_depth_and_incomplete_receipts_are_rejected(self):
        self.assertTrue(self.run_rows([quote(depth_verified=False, availability_metadata_complete=True)]).empty)
        self.assertTrue(self.run_rows([quote(depth_verified=True, availability_metadata_complete=False)]).empty)

    def test_inputs_received_after_close_cannot_be_traded(self):
        self.assertTrue(self.run_rows([quote(decision_time=pd.Timestamp("2026-08-19T00:01Z"))]).empty)

    def test_closed_and_crossed_quotes_are_rejected(self):
        self.assertTrue(self.run_rows([quote(active=False)]).empty)
        self.assertTrue(self.run_rows([quote(spread=-.01)]).empty)
        self.assertTrue(self.run_rows([quote(end_time=pd.Timestamp("2026-08-18T14:00Z"))]).empty)

    def test_delayed_fill_never_uses_improved_future_price_to_select(self):
        trades = self.run_rows([quote(), quote(15, ask=.95, bid=.94, probability=.99)], delayed=True)
        self.assertTrue(trades.empty)
        trades = self.run_rows([quote(), quote(15, ask=.69, bid=.68)], delayed=True)
        self.assertEqual(trades.price.iloc[0], .71)
        self.assertEqual(pd.Timestamp(trades.decision_time.iloc[0]).hour, 14)
        self.assertEqual(pd.Timestamp(trades.entry_time.iloc[0]).hour, 15)

    def test_zero_trade_days_are_present(self):
        result = daily(pd.DataFrame(), "2026-08-18", "2026-08-31")
        self.assertEqual(len(result), 14)
        self.assertEqual(result.trades.sum(), 0)

    def test_selection_refuses_a_high_pnl_below_hit_target(self):
        rows = [dict(model="unqualified", trades=40, hit_rate=.7, net_pnl=50,
                     week1_pnl=25, week2_pnl=25),
                dict(model="qualified", trades=25, hit_rate=.84, net_pnl=6,
                     week1_pnl=2, week2_pnl=4)]
        selected, qualified = select(rows)
        self.assertTrue(qualified)
        self.assertEqual(selected["model"], "qualified")

    def test_validation_failure_is_explicit(self):
        selected, qualified = select([dict(model="bad", trades=30, hit_rate=.75,
            net_pnl=1, week1_pnl=-1, week2_pnl=2)])
        self.assertFalse(qualified)

    def test_legacy_selector_rejects_hindsight_mode(self):
        with self.assertRaises(ValueError):
            _select_rows([], {"entry_policy": "best-ev"}, 40, 3, 20, 10, 1)


if __name__ == "__main__":
    unittest.main()
