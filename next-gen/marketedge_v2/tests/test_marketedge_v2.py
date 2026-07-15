# ruff: noqa: E402
from __future__ import annotations

import sys
import unittest
from pathlib import Path

NEXT_GEN = Path(__file__).resolve().parents[2]
if str(NEXT_GEN) not in sys.path:
    sys.path.insert(0, str(NEXT_GEN))

from marketedge_v2.pipeline import _blend_probability, _normal_cdf, kalshi_fee


class MarketEdgeTests(unittest.TestCase):
    def test_normal_cdf_is_symmetric(self) -> None:
        self.assertAlmostEqual(float(_normal_cdf(__import__("numpy").array([0.0]))[0]), 0.5)

    def test_taker_fee_matches_existing_simulator_convention(self) -> None:
        self.assertEqual(kalshi_fee(0.50, 1, "taker"), 0.02)

    def test_blend_keeps_probability_between_inputs(self) -> None:
        import pandas as pd

        rows = pd.DataFrame({"market_probability": [0.2], "weather_probability": [0.8]})
        value = float(_blend_probability(rows, 0.5)[0])
        self.assertGreater(value, 0.2)
        self.assertLess(value, 0.8)


if __name__ == "__main__":
    unittest.main()
