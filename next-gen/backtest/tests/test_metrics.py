from __future__ import annotations

import math
import unittest

from libs.metrics import (
    bias,
    log_loss,
    mean_absolute_error,
    multiclass_brier,
    ranked_probability_score,
    root_mean_squared_error,
    top_one_accuracy,
)


class MetricsTests(unittest.TestCase):
    def test_temperature_metrics(self) -> None:
        actual = [80.0, 85.0, 90.0]
        predicted = [81.0, 83.0, 93.0]
        self.assertAlmostEqual(mean_absolute_error(actual, predicted), 2.0)
        self.assertAlmostEqual(root_mean_squared_error(actual, predicted), math.sqrt(14 / 3))
        self.assertAlmostEqual(bias(actual, predicted), 2 / 3)

    def test_bracket_metrics(self) -> None:
        probabilities = {"A": 0.2, "B": 0.7, "C": 0.1}
        self.assertAlmostEqual(log_loss(probabilities["B"]), -math.log(0.7))
        self.assertAlmostEqual(multiclass_brier(probabilities, "B"), 0.14)
        self.assertEqual(top_one_accuracy(probabilities, "B"), 1.0)
        self.assertAlmostEqual(ranked_probability_score(["A", "B", "C"], probabilities, "B"), 0.025)


if __name__ == "__main__":
    unittest.main()
