from __future__ import annotations

import gzip
import json
import math
import tempfile
import unittest
from pathlib import Path

from model_improvement_report import ask_edge_rows, summarize_edge
from weather_probabilities import DataError


class AskEdgeSimulationTests(unittest.TestCase):
    def write_snapshot(self, root: Path) -> None:
        path = root / "cohorts" / "test" / "snapshots" / "2026-06-22" / "den"
        path.mkdir(parents=True)
        payload = {
            "event": {
                "markets": [
                    {"ticker": "A", "yes_ask_dollars": 0.40},
                    {"ticker": "B", "yes_ask_dollars": 0.70},
                ]
            }
        }
        with gzip.open(path / "t_plus_18h.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def test_ask_edge_uses_yes_ask_and_scores_gross_pnl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_snapshot(root)
            rows = ask_edge_rows(
                root,
                "test",
                [
                    {
                        "model": "full",
                        "city": "den",
                        "target_date": "2026-06-22",
                        "checkpoint": "t_plus_18h",
                        "winner_ticker": "A",
                        "tickers_json": json.dumps(["A", "B"]),
                        "probabilities_json": json.dumps([0.55, 0.45]),
                    }
                ],
                margins=(0.10,),
                fee_per_contract=0.0,
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "A")
        self.assertTrue(math.isclose(rows[0]["gross_pnl"], 0.60))
        summary = summarize_edge(rows)
        full = [row for row in summary if row["model"] == "full" and row["margin"] == 0.10][0]
        self.assertEqual(full["trade_count"], 1)
        self.assertEqual(full["hit_rate"], 1.0)

    def test_ask_edge_rejects_malformed_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_snapshot(root)
            with self.assertRaises(DataError):
                ask_edge_rows(
                    root,
                    "test",
                    [
                        {
                            "model": "full",
                            "city": "den",
                            "target_date": "2026-06-22",
                            "checkpoint": "t_plus_18h",
                            "winner_ticker": "A",
                            "tickers_json": json.dumps(["A", "MISSING"]),
                            "probabilities_json": json.dumps([0.55, 0.45]),
                        }
                    ],
                    margins=(0.0,),
                    fee_per_contract=0.0,
                )


if __name__ == "__main__":
    unittest.main()
