from __future__ import annotations

import unittest
from datetime import date

from libs.constants import CITY_BY_KEY
from libs.models import Bracket, City
from libs.probabilities import apply_probability_floor, bracket_for_temperature, normalize
from libs.time_utils import climate_window
from libs.validation import validate_brackets_contiguous


class ModelsTimeProbabilitiesTests(unittest.TestCase):
    def test_model_validation(self) -> None:
        with self.assertRaises(ValueError):
            City("", "Bad", "SERIES", "STATION", 0, 0, "UTC", 0)

    def test_climate_windows_by_timezone(self) -> None:
        target = date(2026, 7, 1)
        self.assertEqual(climate_window(CITY_BY_KEY["nyc"], target)[0].hour, 5)
        self.assertEqual(climate_window(CITY_BY_KEY["aus"], target)[0].hour, 6)
        self.assertEqual(climate_window(CITY_BY_KEY["den"], target)[0].hour, 7)
        self.assertEqual(climate_window(CITY_BY_KEY["la"], target)[0].hour, 8)

    def test_probabilities_and_brackets(self) -> None:
        brackets = [Bracket("A", "80 or below", None, 80, 0), Bracket("B", "81+", 81, None, 1)]
        validate_brackets_contiguous(brackets)
        self.assertEqual(bracket_for_temperature(brackets, 80.4).ticker, "A")
        normalized = normalize({"A": 2, "B": 1})
        self.assertAlmostEqual(sum(normalized.values()), 1.0)
        floored = apply_probability_floor({"A": 0.0, "B": 1.0}, floor=0.01)
        self.assertGreater(floored["A"], 0.0)
        self.assertAlmostEqual(sum(floored.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
