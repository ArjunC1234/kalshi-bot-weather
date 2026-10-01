from __future__ import annotations

# ruff: noqa: E402
import sys
import unittest
from pathlib import Path

RAYCASTER_V1 = Path(__file__).resolve().parents[1]
NEXT_GEN = Path(__file__).resolve().parents[4]
for path in (NEXT_GEN, RAYCASTER_V1):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from distribution import PiecewiseQuantileCdf, bracket_distribution, monotonic_quantiles

from libs.models import Bracket


class DistributionTests(unittest.TestCase):
    def test_monotonic_quantiles_sorts_values(self) -> None:
        self.assertEqual(
            monotonic_quantiles({0.1: 90, 0.5: 85, 0.9: 100}),
            {0.1: 85, 0.5: 90, 0.9: 100},
        )

    def test_observed_floor_removes_lower_tail(self) -> None:
        cdf = PiecewiseQuantileCdf({0.1: 70, 0.5: 75, 0.9: 80}, observed_floor=77)
        self.assertEqual(cdf.at(76), 0.0)
        self.assertGreater(cdf.at(79), 0.0)

    def test_bracket_distribution_sums_to_one(self) -> None:
        brackets = [
            Bracket("LOW", "Low", None, 79, 0),
            Bracket("MID", "Mid", 80, 84, 1),
            Bracket("HIGH", "High", 85, None, 2),
        ]
        probabilities = bracket_distribution(
            brackets,
            expected_high_f=82,
            quantiles={0.1: 79, 0.5: 82, 0.9: 86},
            observed_high_so_far_f=80,
            probability_floor=0.0,
        )
        self.assertAlmostEqual(sum(probabilities.values()), 1.0)
        self.assertEqual(set(probabilities), {"LOW", "MID", "HIGH"})

    def test_observed_floor_uses_rounded_settlement_value(self) -> None:
        brackets = [
            Bracket("LOW", "91 or below", None, 91, 0),
            Bracket("MID", "92 to 94", 92, 94, 1),
            Bracket("HIGH", "95 or above", 95, None, 2),
        ]
        probabilities = bracket_distribution(
            brackets,
            expected_high_f=93,
            quantiles={0.1: 91.5, 0.5: 93, 0.9: 95},
            observed_high_so_far_f=91.94,
            probability_floor=0.0,
        )

        self.assertEqual(probabilities["LOW"], 0.0)
        self.assertAlmostEqual(sum(probabilities.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
