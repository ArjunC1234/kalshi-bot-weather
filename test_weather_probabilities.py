from __future__ import annotations

import math
import unittest
from datetime import UTC, date, datetime

from weather_probabilities import (
    Bracket,
    DataError,
    adjust_member_highs,
    bracket_probability,
    extract_member_highs,
    kernel_bandwidth,
    parse_bracket,
    select_event,
    validate_brackets,
)


class BracketTests(unittest.TestCase):
    def test_parses_middle_and_tails(self) -> None:
        cases = (
            ({"ticker": "LOW", "yes_sub_title": "81° or below"}, (None, 81)),
            ({"ticker": "MID", "yes_sub_title": "82° to 83°"}, (82, 83)),
            ({"ticker": "HIGH", "yes_sub_title": "84° or above"}, (84, None)),
            ({"ticker": "LOW2", "yes_sub_title": "less than 82°"}, (None, 81)),
            ({"ticker": "HIGH2", "yes_sub_title": "greater than 83°"}, (84, None)),
        )
        for market, expected in cases:
            bracket = parse_bracket(market)
            self.assertEqual((bracket.lower, bracket.upper), expected)

    def test_validates_and_orders_exhaustive_brackets(self) -> None:
        ordered = validate_brackets(
            [
                Bracket("H", "84 or above", 84, None),
                Bracket("L", "81 or below", None, 81),
                Bracket("M", "82 to 83", 82, 83),
            ]
        )
        self.assertEqual([item.ticker for item in ordered], ["L", "M", "H"])

    def test_rejects_gap_overlap_and_missing_tails(self) -> None:
        invalid_sets = (
            [Bracket("L", "80 or below", None, 80), Bracket("H", "82 or above", 82, None)],
            [Bracket("L", "81 or below", None, 81), Bracket("H", "81 or above", 81, None)],
            [Bracket("M", "81 to 82", 81, 82), Bracket("H", "83 or above", 83, None)],
        )
        for brackets in invalid_sets:
            with self.assertRaises(DataError):
                validate_brackets(brackets)

    def test_rejects_malformed_market_response(self) -> None:
        with self.assertRaises(DataError):
            parse_bracket({"ticker": "MISSING_LABEL"})


class EventTests(unittest.TestCase):
    def test_selects_nearest_event_independent_of_order(self) -> None:
        markets = [
            {"event_ticker": "KXHIGHNY-26JUN22", "ticker": "B"},
            {"event_ticker": "KXHIGHNY-26JUN21", "ticker": "A"},
        ]
        selected, rows = select_event(markets, None, today=date(2026, 6, 20))
        self.assertEqual(selected, date(2026, 6, 21))
        self.assertEqual(rows[0]["ticker"], "A")

    def test_rejects_unavailable_requested_date(self) -> None:
        with self.assertRaises(DataError):
            select_event(
                [{"event_ticker": "KXHIGHNY-26JUN21"}], date(2026, 6, 22)
            )


class EnsembleTests(unittest.TestCase):
    def test_extracts_daily_member_highs(self) -> None:
        hourly = {
            "time": [
                "2026-06-20T04:00",
                "2026-06-20T05:00",
                "2026-06-20T06:00",
                "2026-06-21T04:00",
                "2026-06-21T05:00",
            ],
        }
        for index in range(10):
            hourly[f"temperature_2m_member{index:02d}"] = [50, 70 + index, 72 + index, 68, 40]
        values = extract_member_highs(
            hourly,
            datetime(2026, 6, 20, 5, tzinfo=UTC),
            datetime(2026, 6, 21, 5, tzinfo=UTC),
        )
        self.assertEqual(values, [72 + index for index in range(10)])

    def test_bias_adjustment_and_observation_clamp(self) -> None:
        self.assertEqual(adjust_member_highs([70, 72, 74], 76, 75), [75, 76, 78])

    def test_bandwidth_has_one_degree_floor(self) -> None:
        self.assertEqual(kernel_bandwidth([70, 70.1, 70.2]), 1.0)


class ProbabilityTests(unittest.TestCase):
    def test_symmetric_ensemble_splits_at_center(self) -> None:
        members = [68.5, 70.5]
        low = Bracket("L", "69 or below", None, 69)
        high = Bracket("H", "70 or above", 70, None)
        self.assertTrue(math.isclose(bracket_probability(low, members, 1.0), 0.5))
        self.assertTrue(math.isclose(bracket_probability(high, members, 1.0), 0.5))

    def test_exhaustive_probabilities_sum_to_one(self) -> None:
        members = [78, 80, 82, 84, 86]
        brackets = validate_brackets(
            [
                Bracket("L", "79 or below", None, 79),
                Bracket("M1", "80 to 82", 80, 82),
                Bracket("M2", "83 to 85", 83, 85),
                Bracket("H", "86 or above", 86, None),
            ]
        )
        probabilities = [bracket_probability(item, members, 1.0) for item in brackets]
        self.assertTrue(math.isclose(sum(probabilities), 1.0, abs_tol=1e-12))


if __name__ == "__main__":
    unittest.main()
