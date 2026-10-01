import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from scripts.focused_regime_experiment import (
    ANCHORS, CAL_ASOF, FIT_ASOF, calibrate, event_scores, fit_model, fit_weather,
    paired_comparison, predict, select_snapshots, settled_subset, valid_snapshot,
    weather_probabilities,
)
from libs.io_utils import write_json_gz
from scripts.hit80_14d_research import _load_json_gz
from scripts.repair_research_quotes import main as repair_main


def sample(day="2026-08-20", event="E", hour=14, winner=3):
    time = pd.Timestamp(day + "T05:00Z") + pd.Timedelta(hours=hour)
    probabilities = [.04, .12, .28, .30, .20, .06]
    rows = []
    for i, (lo, hi) in enumerate(zip([np.nan, 77, 79, 81, 83, 85], [76, 78, 80, 82, 84, np.nan])):
        p = probabilities[i]
        rows.append(dict(city="nyc", event_ticker=event, market_ticker=f"{event}-{i}",
            target_date=day, snapshot_time_utc=time, decision_time=time + pd.Timedelta(seconds=30),
            end_time=pd.Timestamp(day + "T05:00Z") + pd.Timedelta(days=1), hours=hour,
            depth_verified=True, availability_metadata_complete=True, known_future_feature=False,
            active=True, bracket_index=i, bracket_lower_f=lo, bracket_upper_f=hi,
            yes_bid_dollars=p-.005, yes_ask_dollars=p+.005,
            no_bid_dollars=1-p-.005, no_ask_dollars=1-p+.005,
            yes_ask_size=10., no_ask_size=10., valid_label=True, y=float(i == winner),
            winner_ticker=f"{event}-{winner}",
            settled_at_utc=pd.Timestamp(day + "T13:00Z") + pd.Timedelta(days=1),
            settlement_temperature_f=[75, 77, 79, 81, 83, 85][winner],
            rule_source="weather_company", **{a:80. for a in ANCHORS}))
    return pd.DataFrame(rows)


class FocusedRegimeTests(unittest.TestCase):
    def _assert_snapshot_repair(self, selection, expected):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, cache, output = root / "source", root / "cache", root / "output"
            source.mkdir()
            cache.mkdir()
            markets, weather, metadata = [], [], []
            for hour in (13, 14, 19, 20):
                stamp = f"2026-08-14T{hour:02}:00:00Z"
                raw_id = f"raw-{hour}"
                markets.append({"target_date": "2026-08-14", "event_ticker": "E",
                    "market_ticker": "M", "snapshot_time_utc": stamp,
                    "climate_day_start_utc": "2026-08-14T00:00:00Z",
                    "yes_ask_dollars": .99, "yes_bid_dollars": .5,
                    "no_ask_dollars": .99, "no_bid_dollars": .5})
                weather.append({"event_ticker": "E", "snapshot_time_utc": stamp,
                    "source_payload_ids": {"kalshi_open_markets": raw_id}})
                metadata.append({"raw_payload_id": raw_id, "received_at_utc": stamp,
                    "requested_at_utc": stamp, "success": True, "storage_path": "unused"})
                write_json_gz(cache / f"{raw_id}.json.gz", {"markets": [
                    {"ticker": "M", "yes_bid_size_fp": "10.25", "yes_ask_size_fp": "5.50"}]})
            write_json_gz(source / "market_snapshots.json.gz", markets)
            write_json_gz(source / "weather_snapshots.json.gz", weather)
            (source / "manifest.json").write_text(json.dumps({}), encoding="utf-8")
            client = Mock()
            client.select.return_value = metadata
            argv = ["repair", "--data", str(source), "--output", str(output), "--cache", str(cache),
                    "--start", "2026-08-14", "--end", "2026-08-14", *selection]
            with patch("sys.argv", argv), patch("scripts.repair_research_quotes.SupabaseClient.from_env", return_value=client):
                repair_main()
            repaired = _load_json_gz(output / "market_snapshots.json.gz")
            self.assertEqual([r["depth_verified"] for r in repaired], expected)
            self.assertEqual(repaired[1]["no_ask_size"], 10.25)
            self.assertTrue(repaired[1]["availability_metadata_complete"])
            if "--all-hours" in selection:
                self.assertTrue((output / "source_receipts.json.gz").exists())
                self.assertEqual(repaired[1]["market_requested_at_utc"], repaired[1]["market_received_at_utc"])

    def test_full_afternoon_repair_has_no_price_selection(self):
        self._assert_snapshot_repair(["--all-afternoon"], [False, True, True, False])

    def test_all_hours_verifies_exact_half_open_range_and_request_times(self):
        self._assert_snapshot_repair(["--all-hours", "4", "16"], [True, True, False, False])

    def test_snapshot_selection_does_not_use_outcomes(self):
        data = pd.concat([sample(), sample(hour=15)], ignore_index=True)
        before = select_snapshots(data)
        data["y"] = np.nan
        data["winner_ticker"] = None
        data["settlement_temperature_f"] = np.nan
        data["valid_label"] = False
        after = select_snapshots(data)
        fields = ["market_ticker", "snapshot_time_utc", "decision_time", "market_p"]
        pd.testing.assert_frame_equal(before[fields], after[fields])
        self.assertTrue(before.hours.eq(14).all())

    def test_incomplete_or_crossed_snapshot_is_rejected(self):
        self.assertFalse(valid_snapshot(sample().iloc[:-1]))
        crossed = sample()
        crossed.loc[0, "yes_bid_dollars"] = 1.
        self.assertFalse(valid_snapshot(crossed))

    def test_receipts_and_common_distribution_gate(self):
        bad = sample()
        bad.loc[0, "availability_metadata_complete"] = False
        self.assertFalse(valid_snapshot(bad))
        bad = sample()
        bad.loc[0, "decision_time"] = bad.end_time.iloc[0]
        self.assertFalse(valid_snapshot(bad))
        bad = sample()
        bad.loc[2, "bracket_lower_f"] = 78
        self.assertFalse(valid_snapshot(bad))

    def test_late_labels_are_excluded_from_fitting(self):
        good, late = sample(event="good"), sample(event="late")
        late["settled_at_utc"] = FIT_ASOF
        data = select_snapshots(pd.concat([good, late], ignore_index=True))
        fit = settled_subset(data, "2026-08-14", "2026-08-23", FIT_ASOF)
        self.assertEqual(fit.event_ticker.unique().tolist(), ["good"])

    def test_contradictory_temperature_is_rejected(self):
        data = select_snapshots(sample())
        data["settlement_temperature_f"] = 100
        with self.assertRaisesRegex(ValueError, "disagrees"):
            settled_subset(data, "2026-08-14", "2026-08-23", FIT_ASOF)

    def test_predictions_are_normalized_and_label_independent(self):
        fit = select_snapshots(pd.concat([sample(event=f"E{i}", winner=i % 6) for i in range(12)], ignore_index=True))
        for weather in (False, True):
            model = fit_model(fit, weather)
            p = predict(fit, model)
            changed = fit.copy()
            changed["y"] = 1 - changed.y
            changed["settlement_temperature_f"] = 1000
            np.testing.assert_array_equal(p, predict(changed, model))
            np.testing.assert_allclose(p.reshape(-1, 6).sum(axis=1), 1)
            self.assertTrue(np.isfinite(p).all())

    def test_weather_bias_sign_and_sigma_floor(self):
        data = select_snapshots(sample())
        weather = fit_weather(data)
        self.assertEqual(weather["pooled_bias"], 1.)
        self.assertEqual(weather["sigma"], 1.5)
        self.assertAlmostEqual(weather_probabilities(data, weather).sum(), 1.)

    def test_calibration_must_be_separate_and_available(self):
        fit = select_snapshots(sample())
        model = fit_model(fit, True)
        with self.assertRaises(ValueError):
            calibrate(fit, model)
        cal = select_snapshots(sample(day="2026-08-25", event="cal"))
        saved = copy.deepcopy(model)
        calibrated = calibrate(cal, model)
        self.assertEqual(saved, model)
        self.assertTrue(.5 <= calibrated["calibration_scale"] <= 1.5)
        cal["settled_at_utc"] = CAL_ASOF
        with self.assertRaisesRegex(ValueError, "late labels"):
            calibrate(cal, model)

    def test_scoring_is_one_event_not_six_independent_rows(self):
        frame = select_snapshots(sample())
        result = event_scores(frame, frame.market_p.to_numpy())
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result.log_loss.iloc[0], -np.log(.30))
        comparison = paired_comparison(result, result)
        self.assertEqual(comparison["day_bootstrap_95_interval"], [0., 0.])

    def test_comparisons_reject_different_events(self):
        frame = select_snapshots(sample())
        score = event_scores(frame, frame.market_p.to_numpy())
        other = score.copy()
        other["event_ticker"] = "different"
        with self.assertRaisesRegex(ValueError, "identical events"):
            paired_comparison(score, other)


if __name__ == "__main__":
    unittest.main()
