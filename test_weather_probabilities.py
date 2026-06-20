from __future__ import annotations

import math
import unittest
from datetime import UTC, date, datetime

from weather_probabilities import (
    Bracket,
    CITIES,
    DataError,
    ENSEMBLE_MODELS,
    bracket_probability,
    center_values,
    extract_ensemble_members,
    fetch_nws_high,
    kernel_bandwidth,
    model_balanced_weights,
    parse_bracket,
    point_mass_bracket_probability,
    select_event,
    settlement_window,
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

    def test_derives_fixed_standard_time_window_from_close(self) -> None:
        city = CITIES[0]
        markets = [
            {
                "event_ticker": "KXHIGHNY-26JUN21",
                "close_time": "2026-06-22T04:59:00Z",
                "rules_primary": (
                    "If the highest temperature recorded in Central Park, New York "
                    "for June 21, 2026 as reported by the National Weather Service..."
                ),
            }
        ]
        start, end = settlement_window(city, date(2026, 6, 21), markets)
        self.assertEqual(start, datetime(2026, 6, 21, 5, tzinfo=UTC))
        self.assertEqual(end, datetime(2026, 6, 22, 5, tzinfo=UTC))

        okc_start, okc_end = settlement_window(
            CITIES[5],
            date(2026, 6, 21),
            [
                {
                    "event_ticker": "KXHIGHTOKC-26JUN21",
                    "close_time": "2026-06-22T06:00:00Z",
                    "rules_primary": "Oklahoma City for Jun 21, 2026",
                }
            ],
        )
        self.assertEqual(okc_start, datetime(2026, 6, 21, 6, tzinfo=UTC))
        self.assertEqual(okc_end, datetime(2026, 6, 22, 6, tzinfo=UTC))

    def test_rejects_inconsistent_close_and_wrong_station(self) -> None:
        base = {
            "event_ticker": "KXHIGHNY-26JUN21",
            "close_time": "2026-06-22T04:59:00Z",
            "rules_primary": "Central Park for June 21, 2026",
        }
        inconsistent = [base, {**base, "close_time": "2026-06-22T05:59:00Z"}]
        with self.assertRaises(DataError):
            settlement_window(CITIES[0], date(2026, 6, 21), inconsistent)
        with self.assertRaises(DataError):
            settlement_window(
                CITIES[0],
                date(2026, 6, 21),
                [{**base, "rules_primary": "LaGuardia for June 21, 2026"}],
            )


class EnsembleTests(unittest.TestCase):
    def test_bandwidth_has_one_degree_floor(self) -> None:
        self.assertEqual(kernel_bandwidth([70, 70.1, 70.2]), 1.0)

    @staticmethod
    def multi_model_hourly(include_gem: bool = True) -> dict[str, list[float] | list[str]]:
        hourly: dict[str, list[float] | list[str]] = {
            "time": ["2026-06-21T05:00", "2026-06-21T06:00", "2026-06-21T07:00"]
        }
        for model_index, (model, (_, suffix)) in enumerate(ENSEMBLE_MODELS.items()):
            if model == "GEM" and not include_gem:
                continue
            member_count = 20 if model == "ECMWF IFS" else 10
            for member_index in range(member_count):
                member_part = "" if member_index == 0 else f"_member{member_index:02d}"
                hourly[f"temperature_2m{member_part}_{suffix}"] = [
                    60 + model_index,
                    70 + model_index + member_index / 10,
                    72 + model_index + member_index / 10,
                ]
        return hourly

    def test_groups_models_and_balances_family_weights(self) -> None:
        members, counts, warnings = extract_ensemble_members(
            self.multi_model_hourly(),
            datetime(2026, 6, 21, 5, tzinfo=UTC),
            datetime(2026, 6, 22, 5, tzinfo=UTC),
            datetime(2026, 6, 21, 6, 30, tzinfo=UTC),
        )
        weights = model_balanced_weights(members, counts)
        self.assertEqual(dict(counts)["ECMWF IFS"], 20)
        for model, _ in counts:
            self.assertTrue(
                math.isclose(
                    sum(weight for member, weight in zip(members, weights) if member.model == model),
                    0.25,
                )
            )
        self.assertTrue(all(member.remaining_high_f is not None for member in members))
        self.assertEqual(warnings, ())

    def test_allows_three_model_fallback_with_warning(self) -> None:
        members, counts, warnings = extract_ensemble_members(
            self.multi_model_hourly(include_gem=False),
            datetime(2026, 6, 21, 5, tzinfo=UTC),
            datetime(2026, 6, 22, 5, tzinfo=UTC),
        )
        self.assertEqual(len(counts), 3)
        self.assertTrue(members)
        self.assertIn("GEM", warnings[0])

    def test_center_shift_preserves_member_differences(self) -> None:
        center, shift, adjusted = center_values([70, 72, 76], [0.25, 0.5, 0.25], 75)
        self.assertEqual((center, shift), (72, 3))
        self.assertEqual(adjusted, [73, 75, 79])
        self.assertEqual(adjusted[2] - adjusted[0], 6)


class NwsTests(unittest.TestCase):
    def test_uses_larger_of_daytime_and_climate_window_hourly_high(self) -> None:
        class FakeHttp:
            def get_json(self, url: str, params: object = None) -> dict[str, object]:
                if "/points/" in url:
                    return {"properties": {"forecast": "daily", "forecastHourly": "hourly"}}
                if url == "daily":
                    return {
                        "properties": {
                            "periods": [
                                {
                                    "startTime": "2026-06-21T12:00:00Z",
                                    "isDaytime": True,
                                    "temperature": 90,
                                    "temperatureUnit": "F",
                                }
                            ]
                        }
                    }
                return {
                    "properties": {
                        "periods": [
                            {"startTime": "2026-06-21T18:00:00Z", "temperature": 89},
                            {"startTime": "2026-06-22T03:00:00Z", "temperature": 93},
                        ]
                    }
                }

        forecast = fetch_nws_high(
            FakeHttp(),  # type: ignore[arg-type]
            CITIES[0],
            date(2026, 6, 21),
            datetime(2026, 6, 21, 5, tzinfo=UTC),
            datetime(2026, 6, 22, 5, tzinfo=UTC),
        )
        self.assertEqual(forecast.high_f, 93)
        self.assertEqual((forecast.daytime_high_f, forecast.hourly_high_f), (90, 93))


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

    def test_observed_floor_removes_impossible_lower_probability(self) -> None:
        brackets = validate_brackets(
            [
                Bracket("L", "80 or below", None, 80),
                Bracket("M", "81 to 82", 81, 82),
                Bracket("H", "83 or above", 83, None),
            ]
        )
        probabilities = [
            bracket_probability(item, [80, 84], 1.0, [0.5, 0.5], observed_floor=81.2)
            for item in brackets
        ]
        self.assertEqual(probabilities[0], 0.0)
        self.assertGreater(probabilities[2], 0.0)
        self.assertTrue(math.isclose(sum(probabilities), 1.0, abs_tol=1e-12))

    def test_fully_observed_point_mass_selects_one_bracket(self) -> None:
        brackets = validate_brackets(
            [
                Bracket("L", "79 or below", None, 79),
                Bracket("M", "80 to 81", 80, 81),
                Bracket("H", "82 or above", 82, None),
            ]
        )
        probabilities = [point_mass_bracket_probability(item, 81.6) for item in brackets]
        self.assertEqual(probabilities, [0.0, 0.0, 1.0])


if __name__ == "__main__":
    unittest.main()
