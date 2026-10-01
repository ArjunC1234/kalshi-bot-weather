import copy
import unittest

import numpy as np
import pandas as pd

from scripts.investigate_forecast_timing import (
    FIT_CUTOFF, build_transitions, choose_probe, complete_market, day_interval,
    first_entry, fit_model, make_quote, next_exit, predict, revision, score_probe,
)
from scripts.investigate_small_edges import delayed_fills, detailed_metrics
from scripts.profitability_research import Policy, replay
from scripts.tests.test_focused_regime_experiment import sample
from scripts.tests.test_profitability_research import quote


def market(hour, day="2026-08-20"):
    data = sample(day=day, hour=hour)
    data["market_requested_at_utc"] = data.snapshot_time_utc + pd.Timedelta(seconds=5)
    data["market_received_at_utc"] = data.snapshot_time_utc + pd.Timedelta(seconds=10)
    data["no_bid_size"] = 10.
    data["yes_bid_size"] = 10.
    return data


def forecast(hour, high=83., day="2026-08-20"):
    stamp = pd.Timestamp(day+"T05:00Z") + pd.Timedelta(hours=hour)
    return dict(event_ticker="E", target_date=day, city="nyc", hour=hour, snapshot=stamp,
                daily_high=high, version=stamp-pd.Timedelta(minutes=5),
                generated=stamp-pd.Timedelta(minutes=1), received=stamp+pd.Timedelta(seconds=20), success=True)


class TimingTests(unittest.TestCase):
    def test_revision_requires_changed_version_when_high_changes(self):
        previous, current = forecast(5), forecast(6, 79.)
        self.assertEqual(revision(previous, current), (-4., "valid"))
        current["version"] = previous["version"]
        self.assertEqual(revision(previous, current)[1], "changed_without_new_version")

    def test_revision_rejects_future_publication_and_cross_day(self):
        previous, current = forecast(5), forecast(6)
        current["version"] = current["received"] + pd.Timedelta(seconds=1)
        self.assertEqual(revision(previous, current)[1], "publication_after_receipt")
        self.assertEqual(revision(previous, forecast(6, day="2026-08-21"))[1], "different_event")
        self.assertEqual(revision(previous, forecast(7))[1], "nonadjacent_snapshots")

    def test_unavailable_source_fails_closed(self):
        previous, current = forecast(5), forecast(6)
        current["received"] = pd.NaT
        self.assertEqual(revision(previous, current)[1], "missing_source_metadata")

    def test_inflight_request_is_not_a_post_signal_quote(self):
        data = market(6)
        signal = data.snapshot_time_utc.iloc[0] + pd.Timedelta(seconds=7)
        inflight = make_quote(data)
        self.assertIsNone(first_entry([inflight], signal))
        later = make_quote(market(7))
        self.assertIs(first_entry([inflight, later], signal), later)

    def test_missing_hourly_quote_does_not_skip_to_two_hours(self):
        entry = make_quote(market(6))
        self.assertIsNone(next_exit([entry, make_quote(market(8))], entry))
        self.assertIsNone(first_entry([make_quote(market(8))], forecast(6)["received"]))

    def test_invalid_market_and_depth_are_rejected(self):
        data = market(6)
        self.assertTrue(complete_market(data))
        data.loc[0, "depth_verified"] = False
        self.assertFalse(complete_market(data))
        data = market(6)
        data.loc[0, "no_bid_dollars"] = 1.
        self.assertFalse(complete_market(data))

    def test_probe_selection_does_not_depend_on_future_prices(self):
        frames = pd.concat([market(h) for h in (5, 7, 8, 9)], ignore_index=True)
        wx = pd.DataFrame([forecast(5, 83), forecast(6, 79), forecast(7, 83)])
        before, probes_before, _ = build_transitions(frames, wx)
        changed = frames.copy()
        mask = changed.hours.eq(8)
        changed.loc[mask, "no_bid_dollars"] = .01
        _, probes_after, _ = build_transitions(changed, wx)
        fields = ["market_ticker", "entry_ask", "entry_time", "mass_reduction"]
        self.assertEqual(len(probes_before), 1)
        pd.testing.assert_frame_equal(probes_before[fields], probes_after[fields])
        self.assertNotEqual(probes_before.net_markout.iloc[0], probes_after.net_markout.iloc[0])

    def test_missing_first_entry_cannot_promote_later_revision(self):
        frames = pd.concat([market(h) for h in (5, 8, 9)], ignore_index=True)
        wx = pd.DataFrame([forecast(5, 83), forecast(6, 79), forecast(7, 83)])
        transitions, probes, _ = build_transitions(frames, wx)
        self.assertTrue(transitions.first_material.iloc[0])
        self.assertFalse(transitions.pair_valid.iloc[0])
        self.assertFalse(transitions.first_material.iloc[1])
        self.assertTrue(probes.empty)

    def test_probe_charges_both_sides_and_rejects_missing_exit(self):
        probe = choose_probe(make_quote(market(7)), 83, 79)
        self.assertIsNotNone(probe)
        result = score_probe(probe, make_quote(market(8)))
        self.assertLess(result["net_markout"], result["gross_markout"])
        self.assertFalse(score_probe(probe, None)["scored"])
        no_depth = market(8)
        no_depth["no_bid_size"] = 0.
        self.assertFalse(score_probe(probe, make_quote(no_depth))["scored"])

    def test_bootstrap_weights_repeated_events_equally(self):
        frame = pd.DataFrame({"target_date": ["2026-08-28"]*3, "event_ticker": ["A", "A", "B"],
                              "value": [1., 1., 3.]})
        result = day_interval(frame, "value")
        self.assertEqual(result["mean"], 2.)
        self.assertEqual(result["events"], 2)

    def test_model_labels_must_precede_cutoff(self):
        frame = pd.DataFrame({"event_ticker": ["A", "B"], "exit_time": [FIT_CUTOFF.isoformat()]*2,
            "market_index": [1., 2.], "market_entropy": [1., 1.], "market_momentum": [0., .1],
            "climate_hour": [6, 7], "revision_f": [0., 1.], "next_change": [0., .1]})
        with self.assertRaisesRegex(ValueError, "Future"):
            fit_model(frame, True)
        frame["exit_time"] = "2026-08-27T18:00:00Z"
        model = fit_model(frame, True)
        expected = predict(frame, model)
        changed = frame.copy()
        changed["next_change"] = 10000.
        np.testing.assert_array_equal(expected, predict(changed, model))


class SmallEdgeTests(unittest.TestCase):
    def test_delayed_quotes_require_verified_valid_depth(self):
        intentions = replay(pd.DataFrame([quote()]), Policy(), "2026-08-18", "2026-08-31")
        future = market(10, day="2026-08-18").iloc[:1].copy()
        future["market_ticker"] = "M1"
        future["no_bid_dollars"] = .69
        future["no_ask_dollars"] = .70
        # Intention is at 14:00 UTC; fixture's climate hour 10 is 15:00 UTC.
        self.assertEqual(len(delayed_fills(intentions, future)), 1)
        future["depth_verified"] = False
        self.assertTrue(delayed_fills(intentions, future).empty)
        future["depth_verified"] = True
        future["no_bid_dollars"] = .9
        self.assertTrue(delayed_fills(intentions, future).empty)

    def test_delayed_failure_never_replaces_original_intention(self):
        intentions = replay(pd.DataFrame([quote()]), Policy(), "2026-08-18", "2026-08-31")
        saved = copy.deepcopy(intentions)
        future = market(10, day="2026-08-18").iloc[:1].copy()
        future["market_ticker"] = "M1"
        future["no_ask_dollars"] = .99
        self.assertTrue(delayed_fills(intentions, future).empty)
        pd.testing.assert_frame_equal(intentions, saved)

    def test_empty_hit_rate_is_not_a_success(self):
        result = detailed_metrics(pd.DataFrame())
        self.assertIsNone(result["hit_rate"])
        self.assertIsNone(result["iid_one_sided_95_hit_lower_bound"])


if __name__ == "__main__":
    unittest.main()
