from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from backtest.replay import settled_distributions
from libs.models import BacktestDataset, BracketDistribution, Settlement


class ReplayTests(unittest.TestCase):
    def test_replay_uses_stored_distributions_only(self) -> None:
        distribution = BracketDistribution(
            city="den",
            event_ticker="EVENT",
            snapshot_hour_utc=datetime(2026, 7, 1, 18, tzinfo=UTC),
            model_name="baseline",
            probabilities={"A": 1.0},
        )
        settlement = Settlement(
            city="den",
            event_ticker="EVENT",
            target_date=date(2026, 7, 1),
            settled_at_utc=datetime(2026, 7, 2, tzinfo=UTC),
            winner_ticker="A",
        )
        dataset = BacktestDataset(model_outputs=[distribution], settlements=[settlement])
        self.assertEqual(settled_distributions(dataset, "baseline"), [(distribution, settlement)])


if __name__ == "__main__":
    unittest.main()
