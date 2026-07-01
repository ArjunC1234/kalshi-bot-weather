from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from backtest.evaluate import evaluate_bracket_model
from libs.models import BacktestDataset, Bracket, BracketDistribution, MarketSnapshot, Settlement


class EvaluateTests(unittest.TestCase):
    def test_synthetic_forecast_scores_known_values(self) -> None:
        snapshot_hour = datetime(2026, 7, 1, 18, tzinfo=UTC)
        markets = [
            MarketSnapshot(
                "den",
                "EVENT",
                "A",
                date(2026, 7, 1),
                snapshot_hour,
                Bracket("A", "low", None, 80, 0),
                0.1,
                0.2,
            ),
            MarketSnapshot(
                "den",
                "EVENT",
                "B",
                date(2026, 7, 1),
                snapshot_hour,
                Bracket("B", "high", 81, None, 1),
                0.7,
                0.8,
            ),
        ]
        dataset = BacktestDataset(
            markets=markets,
            model_outputs=[
                BracketDistribution(
                    "den",
                    "EVENT",
                    snapshot_hour,
                    "baseline",
                    {"A": 0.25, "B": 0.75},
                )
            ],
            settlements=[
                Settlement("den", "EVENT", date(2026, 7, 1), datetime(2026, 7, 2, tzinfo=UTC), "B")
            ],
        )

        result = evaluate_bracket_model(dataset, "baseline")

        self.assertEqual(result.metadata["forecast_count"], 1)
        metrics = {metric.metric: metric.value for metric in result.metrics}
        self.assertAlmostEqual(metrics["winner_probability"], 0.75)
        self.assertAlmostEqual(metrics["top_one_accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
