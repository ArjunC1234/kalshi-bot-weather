from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

from libs.models import Bracket, MarketSnapshot, Settlement
from strategy.backtest import run_backtest
from strategy.fills import settle_order
from strategy.metrics import edge_bucket, max_drawdown
from strategy.orders import PaperOrder
from strategy.signals import StrategyConfig, signal_for_market


class StrategyTests(unittest.TestCase):
    def test_signal_skips_wide_spread(self) -> None:
        market = _market("MKT-WIN", yes_bid=0.20, yes_ask=0.50)
        signal = signal_for_market(market, 0.70, StrategyConfig(max_spread=0.10))
        self.assertEqual(signal.decision, "skip")
        self.assertEqual(signal.skip_reason, "spread_too_wide")

    def test_settlement_pnl_and_clv(self) -> None:
        order = PaperOrder(
            order_id="paper-1",
            side="buy_yes",
            city="nyc",
            event_ticker="EVT",
            market_ticker="MKT-WIN",
            target_date="2026-07-01",
            snapshot_hour_utc=datetime(2026, 7, 1, 12, tzinfo=UTC),
            model_probability=0.70,
            entry_price=0.40,
            edge=0.30,
            contracts=1.0,
        )
        trade = settle_order(
            order,
            Settlement(
                city="nyc",
                event_ticker="EVT",
                target_date=date(2026, 7, 1),
                settled_at_utc=datetime(2026, 7, 2, tzinfo=UTC),
                winner_ticker="MKT-WIN",
            ),
            [_market("MKT-WIN", yes_bid=0.60, yes_ask=0.70)],
        )
        self.assertAlmostEqual(trade.pnl, 0.60)
        self.assertAlmostEqual(trade.clv or 0.0, 0.25)

    def test_drawdown_and_edge_bucket(self) -> None:
        self.assertEqual(edge_bucket(0.06), "0.05-0.08")
        trades = [
            _trade_like(1.0, datetime(2026, 7, 1, tzinfo=UTC)),
            _trade_like(-2.0, datetime(2026, 7, 2, tzinfo=UTC)),
            _trade_like(0.5, datetime(2026, 7, 3, tzinfo=UTC)),
        ]
        self.assertAlmostEqual(max_drawdown(trades), -2.0)

    def test_end_to_end_backtest_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export"
            report = root / "model"
            output = root / "out"
            export.mkdir()
            report.mkdir()
            _write_export(export)
            _write_model_report(report)
            summary = run_backtest(
                export,
                report,
                output,
                StrategyConfig(edge_threshold=0.05, max_spread=0.20),
            )
            self.assertEqual(summary["trades"], 1)
            self.assertTrue((output / "trades.csv").exists())
            self.assertTrue((output / "edge_buckets.csv").exists())
            with (output / "trades.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["market_ticker"], "MKT-WIN")


def _market(market_ticker: str, yes_bid: float, yes_ask: float) -> MarketSnapshot:
    return MarketSnapshot(
        city="nyc",
        event_ticker="EVT",
        market_ticker=market_ticker,
        target_date=date(2026, 7, 1),
        snapshot_hour_utc=datetime(2026, 7, 1, 12, tzinfo=UTC),
        bracket=Bracket(market_ticker, "Win", 80, 80, 1),
        yes_bid=yes_bid,
        yes_ask=yes_ask,
    )


def _trade_like(pnl: float, entry_time: datetime):
    from strategy.orders import PaperTrade

    return PaperTrade(
        order_id=str(entry_time),
        city="nyc",
        event_ticker="EVT",
        market_ticker="MKT",
        target_date=entry_time.date().isoformat(),
        entry_time_utc=entry_time,
        model_probability=0.6,
        entry_price=0.5,
        edge=0.1,
        contracts=1.0,
        winner_ticker="MKT",
        settlement_value=1.0,
        pnl=pnl,
        roi=pnl / 0.5,
        hit=1.0 if pnl > 0 else 0.0,
        closing_mid=0.6,
        clv=0.1,
        checkpoint="utc_12",
    )


def _write_export(path: Path) -> None:
    market_rows = [
        {
            "city": "nyc",
            "event_ticker": "EVT",
            "market_ticker": "MKT-WIN",
            "target_date": "2026-07-01",
            "snapshot_time_utc": "2026-07-01T12:00:00+00:00",
            "bracket_label": "Win",
            "bracket_lower_f": 80,
            "bracket_upper_f": 80,
            "bracket_index": 1,
            "yes_bid_dollars": 0.35,
            "yes_ask_dollars": 0.40,
            "normalized_market_midpoint_probability": 0.375,
        },
        {
            "city": "nyc",
            "event_ticker": "EVT",
            "market_ticker": "MKT-LOSE",
            "target_date": "2026-07-01",
            "snapshot_time_utc": "2026-07-01T12:00:00+00:00",
            "bracket_label": "Lose",
            "bracket_lower_f": 81,
            "bracket_upper_f": 81,
            "bracket_index": 2,
            "yes_bid_dollars": 0.20,
            "yes_ask_dollars": 0.43,
            "normalized_market_midpoint_probability": 0.225,
        },
    ]
    settlement_rows = [
        {
            "city": "nyc",
            "event_ticker": "EVT",
            "target_date": "2026-07-01",
            "settled_at_utc": "2026-07-02T00:00:00+00:00",
            "winner_ticker": "MKT-WIN",
        }
    ]
    (path / "market_snapshots.json").write_text(json.dumps(market_rows), encoding="utf-8")
    (path / "settlements.json").write_text(json.dumps(settlement_rows), encoding="utf-8")


def _write_model_report(path: Path) -> None:
    with (path / "bracket_distributions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["city", "event_ticker", "model_name", "probabilities", "snapshot_hour_utc"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "city": "nyc",
                "event_ticker": "EVT",
                "model_name": "test",
                "probabilities": "{'MKT-WIN': 0.55, 'MKT-LOSE': 0.45}",
                "snapshot_hour_utc": "2026-07-01T12:00:00+00:00",
            }
        )


if __name__ == "__main__":
    unittest.main()
