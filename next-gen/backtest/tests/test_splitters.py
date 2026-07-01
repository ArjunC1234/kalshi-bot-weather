from __future__ import annotations

import unittest
from datetime import date

from backtest.splitters import assert_prior_dates_only, expanding_window_dates


class SplitterTests(unittest.TestCase):
    def test_expanding_window_uses_prior_dates_only(self) -> None:
        dates = [date(2026, 7, 3), date(2026, 7, 1), date(2026, 7, 2)]
        splits = expanding_window_dates(dates, min_train_dates=1)
        self.assertEqual(splits[0], ([date(2026, 7, 1)], date(2026, 7, 2)))
        self.assertEqual(splits[1], ([date(2026, 7, 1), date(2026, 7, 2)], date(2026, 7, 3)))

    def test_rejects_future_training_date(self) -> None:
        with self.assertRaises(ValueError):
            assert_prior_dates_only([date(2026, 7, 2)], date(2026, 7, 2))


if __name__ == "__main__":
    unittest.main()
