from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

from libs.models import Bracket, MarketSnapshot, Settlement
from strategy.backtest import run_backtest
from strategy.ev_backtest import (
    NeuralEvConfig,
    _decision,
    _distance_outside_contract,
    _inside_contract,
    _intervals_overlap_flag,
    _passes_validation_constraints,
    _validation_sort_key,
    _within_date_window,
)
from strategy.fills import settle_order
from strategy.live_replay import LiveReplayConfig, build_slice_allowlist, run_live_replay
from strategy.metrics import edge_bucket, max_drawdown
from strategy.orders import PaperOrder
from strategy.signals import StrategyConfig, signal_for_market


class StrategyTests(unittest.TestCase):
    def test_signal_skips_wide_spread(self) -> None:
        market = _market("MKT-WIN", yes_bid=0.20, yes_ask=0.50)
        signal = signal_for_market(market, 0.70, StrategyConfig(max_spread=0.10))
        self.assertEqual(signal.decision, "skip")
        self.assertEqual(signal.skip_reason, "spread_too_wide")

    def test_signal_respects_max_edge_filter(self) -> None:
        market = _market("MKT-WIN", yes_bid=0.20, yes_ask=0.30)
        signal = signal_for_market(market, 0.60, StrategyConfig(max_edge=0.20))
        self.assertEqual(signal.decision, "skip")
        self.assertEqual(signal.skip_reason, "edge_above_max")

    def test_neural_ev_respects_max_ev_filter(self) -> None:
        decision, reason = _decision(
            entry_price=0.60,
            spread=0.05,
            ev=0.21,
            config=NeuralEvConfig(min_ev=0.02, max_ev=0.20),
            hours_elapsed=None,
        )
        self.assertEqual(decision, "skip")
        self.assertEqual(reason, "ev_above_threshold")

    def test_neural_ev_defaults_to_post_settlement_system_dates(self) -> None:
        config = NeuralEvConfig()

        self.assertFalse(_within_date_window("2026-08-26", config))
        self.assertTrue(_within_date_window("2026-08-27", config))

    def test_signal_respects_model_probability_filter(self) -> None:
        market = _market("MKT-WIN", yes_bid=0.20, yes_ask=0.30)
        signal = signal_for_market(
            market,
            0.60,
            StrategyConfig(max_model_probability=0.50),
        )
        self.assertEqual(signal.decision, "skip")
        self.assertEqual(signal.skip_reason, "model_probability_too_high")

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

    def test_learned_gate_contract_distance_features_boundary_risk(self) -> None:
        market = _market("MKT-BOUNDED", yes_bid=0.45, yes_ask=0.50)
        self.assertEqual(market.bracket.lower_f, 80)
        self.assertEqual(market.bracket.upper_f, 80)

        self.assertTrue(_inside_contract(market, 80.0))
        self.assertFalse(_inside_contract(market, 81.2))
        self.assertAlmostEqual(_distance_outside_contract(market, 81.2) or 0.0, 1.2)
        self.assertEqual(
            _intervals_overlap_flag(market.bracket.lower_f, market.bracket.upper_f, 79.2, 80.2),
            1,
        )
        self.assertEqual(
            _intervals_overlap_flag(market.bracket.lower_f, market.bracket.upper_f, 81.0, 83.0),
            0,
        )

    def test_hit_rate_validation_objective_prioritizes_hit_rate(self) -> None:
        high_hit_low_pnl = {
            "trades": 20,
            "hit_rate": 0.80,
            "positive_clv_rate": 0.70,
            "total_pnl": 1.0,
            "total_risk": 20.0,
            "max_drawdown": -4.0,
            "roi": 0.05,
        }
        lower_hit_higher_pnl = {
            "trades": 20,
            "hit_rate": 0.70,
            "positive_clv_rate": 0.90,
            "total_pnl": 10.0,
            "total_risk": 20.0,
            "max_drawdown": -1.0,
            "roi": 0.50,
        }
        self.assertGreater(
            _validation_sort_key(high_hit_low_pnl, "hit_rate"),
            _validation_sort_key(lower_hit_higher_pnl, "hit_rate"),
        )

    def test_hit_rate_validation_objective_requires_positive_pnl(self) -> None:
        metrics = {
            "trades": 20,
            "positive_clv_rate": 0.70,
            "total_pnl": -0.01,
        }
        self.assertFalse(
            _passes_validation_constraints(
                metrics,
                validation_objective="hit_rate",
                min_validation_trades=5,
                min_validation_positive_clv=0.50,
            )
        )
        self.assertTrue(
            _passes_validation_constraints(
                {**metrics, "total_pnl": 0.01},
                validation_objective="hit_rate",
                min_validation_trades=5,
                min_validation_positive_clv=0.50,
            )
        )

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

    def test_budget_sizing_uses_integer_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export"
            report = root / "model"
            output = root / "out"
            export.mkdir()
            report.mkdir()
            _write_export(export)
            _write_model_report(report)
            run_backtest(
                export,
                report,
                output,
                StrategyConfig(
                    edge_threshold=0.05,
                    max_spread=0.20,
                    daily_budget=10.0,
                    base_budget_fraction=0.10,
                ),
            )
            with (output / "trades.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(float(rows[0]["contracts"]), 2.0)

    def test_edge_tier_budget_sizing_increases_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export"
            report = root / "model"
            output = root / "out"
            export.mkdir()
            report.mkdir()
            _write_export(export)
            _write_model_report(report)
            run_backtest(
                export,
                report,
                output,
                StrategyConfig(
                    edge_threshold=0.05,
                    max_spread=0.20,
                    daily_budget=10.0,
                    sizing_policy="edge-tier",
                    base_budget_fraction=0.10,
                ),
            )
            with (output / "trades.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(float(rows[0]["contracts"]), 5.0)

    def test_live_replay_supports_no_side_and_settlement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export"
            report = root / "model"
            output = root / "out"
            export.mkdir()
            report.mkdir()
            _write_no_replay_export(export)
            _write_no_replay_model_report(report)
            summary = run_live_replay(
                export,
                report,
                output,
                LiveReplayConfig(
                    edge_threshold=0.08,
                    max_edge=0.20,
                    daily_budget=10.0,
                    max_order_cost=3.0,
                    enable_no_trading=True,
                    no_entry_mode="model",
                ),
            )
            self.assertEqual(summary["trades"], 1)
            self.assertGreater(summary["total_pnl"], 0.0)
            self.assertTrue((output / "fills.csv").exists())
            self.assertTrue((output / "side_metrics.csv").exists())
            self.assertTrue((output / "side_edge_buckets.csv").exists())
            with (output / "fills.csv").open(newline="", encoding="utf-8") as handle:
                fills = list(csv.DictReader(handle))
            self.assertEqual(fills[0]["side"], "no")
            self.assertEqual(fills[0]["blocked"], "False")
            with (output / "trades.csv").open(newline="", encoding="utf-8") as handle:
                trades = list(csv.DictReader(handle))
            self.assertEqual(float(trades[0]["settlement_value"]), 1.0)
            self.assertEqual(trades[0]["side"], "no")

    def test_live_replay_blocks_worse_execution_quote_with_lag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export"
            report = root / "model"
            output = root / "out"
            export.mkdir()
            report.mkdir()
            _write_lagged_execution_export(export)
            _write_lagged_execution_model_report(report)
            summary = run_live_replay(
                export,
                report,
                output,
                LiveReplayConfig(
                    edge_threshold=0.08,
                    daily_budget=10.0,
                    execution_lag_hours=1,
                    block_worse_execution_price=True,
                ),
            )
            self.assertEqual(summary["trades"], 0)
            self.assertEqual(summary["blocked_orders"], 1)
            with (output / "fills.csv").open(newline="", encoding="utf-8") as handle:
                fills = list(csv.DictReader(handle))
            self.assertEqual(fills[0]["block_reason"], "execution_price_worse_than_signal")

    def test_live_replay_blocks_entries_after_final_label_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export"
            report = root / "model"
            output = root / "out"
            export.mkdir()
            report.mkdir()
            _write_final_labeled_replay_export(export)
            _write_lagged_execution_model_report(report)
            summary = run_live_replay(
                export,
                report,
                output,
                LiveReplayConfig(edge_threshold=0.08, daily_budget=10.0),
            )
            self.assertEqual(summary["trades"], 0)
            self.assertEqual(summary["signals"], 0)

    def test_live_replay_early_entry_gate_blocks_weak_early_yes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export"
            report = root / "model"
            output = root / "out"
            export.mkdir()
            report.mkdir()
            _write_early_entry_export(export)
            _write_early_entry_model_report(report, 0.43)
            summary = run_live_replay(
                export,
                report,
                output,
                LiveReplayConfig(
                    edge_threshold=0.08,
                    max_edge=0.20,
                    daily_budget=10.0,
                    enable_early_entry_gate=True,
                    early_entry_edge_threshold=0.18,
                    early_entry_min_source_confirmations=2,
                    min_entry_price=0.25,
                    max_entry_price=0.50,
                ),
            )
            self.assertEqual(summary["signals"], 0)
            self.assertEqual(summary["trades"], 0)

    def test_live_replay_early_entry_gate_allows_confirmed_high_edge_yes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export"
            report = root / "model"
            output = root / "out"
            export.mkdir()
            report.mkdir()
            _write_early_entry_export(export)
            _write_early_entry_model_report(report, 0.49)
            summary = run_live_replay(
                export,
                report,
                output,
                LiveReplayConfig(
                    edge_threshold=0.08,
                    max_edge=0.20,
                    daily_budget=10.0,
                    enable_early_entry_gate=True,
                    early_entry_edge_threshold=0.18,
                    early_entry_min_source_confirmations=2,
                    min_entry_price=0.25,
                    max_entry_price=0.50,
                ),
            )
            self.assertEqual(summary["signals"], 1)
            self.assertEqual(summary["trades"], 1)

    def test_live_replay_slice_allowlist_blocks_unproven_slice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export"
            report = root / "model"
            output = root / "out"
            allowlist = root / "slice_allowlist.csv"
            export.mkdir()
            report.mkdir()
            _write_early_entry_export(export)
            _write_early_entry_model_report(report, 0.49)
            _write_slice_allowlist(allowlist, city="la")
            summary = run_live_replay(
                export,
                report,
                output,
                LiveReplayConfig(
                    edge_threshold=0.08,
                    max_edge=0.20,
                    daily_budget=10.0,
                    enable_early_entry_gate=True,
                    early_entry_edge_threshold=0.18,
                    early_entry_min_source_confirmations=2,
                    min_entry_price=0.25,
                    max_entry_price=0.50,
                    slice_allowlist_path=str(allowlist),
                ),
            )
            self.assertEqual(summary["signals"], 0)
            self.assertEqual(summary["trades"], 0)
            self.assertTrue(summary["slice_gate_enabled"])

    def test_live_replay_slice_allowlist_allows_matching_slice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export"
            report = root / "model"
            output = root / "out"
            allowlist = root / "slice_allowlist.csv"
            export.mkdir()
            report.mkdir()
            _write_early_entry_export(export)
            _write_early_entry_model_report(report, 0.49)
            _write_slice_allowlist(allowlist, city="nyc")
            summary = run_live_replay(
                export,
                report,
                output,
                LiveReplayConfig(
                    edge_threshold=0.08,
                    max_edge=0.20,
                    daily_budget=10.0,
                    enable_early_entry_gate=True,
                    early_entry_edge_threshold=0.18,
                    early_entry_min_source_confirmations=2,
                    min_entry_price=0.25,
                    max_entry_price=0.50,
                    slice_allowlist_path=str(allowlist),
                ),
            )
            self.assertEqual(summary["signals"], 1)
            self.assertEqual(summary["trades"], 1)
            with (output / "trades.csv").open(newline="", encoding="utf-8") as handle:
                trades = list(csv.DictReader(handle))
            self.assertEqual(trades[0]["bracket_type"], "bounded")

    def test_build_slice_allowlist_marks_positive_clv_pnl_slice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trades = root / "trades.csv"
            output = root / "slice_allowlist.csv"
            with trades.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "city",
                        "checkpoint",
                        "side",
                        "bracket_type",
                        "pnl",
                        "entry_price",
                        "contracts",
                        "clv",
                        "hit",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "city": "nyc",
                        "checkpoint": "utc_07",
                        "side": "yes",
                        "bracket_type": "bounded",
                        "pnl": "0.7",
                        "entry_price": "0.3",
                        "contracts": "1",
                        "clv": "0.1",
                        "hit": "1",
                    }
                )
            summary = build_slice_allowlist(
                trades,
                output,
                min_trades=1,
                min_pnl=0.0,
                min_avg_clv=0.0,
            )
            self.assertEqual(summary["allowed_groups"], 1)
            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["allowed"], "True")


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


def _write_no_replay_export(path: Path) -> None:
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
            "yes_bid_dollars": 0.10,
            "yes_ask_dollars": 0.20,
            "no_bid_dollars": 0.80,
            "no_ask_dollars": 0.85,
            "yes_bid_size": 20,
            "yes_ask_size": 20,
            "no_bid_size": 20,
            "no_ask_size": 20,
            "normalized_market_midpoint_probability": 0.15,
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
            "yes_bid_dollars": 0.70,
            "yes_ask_dollars": 0.80,
            "no_bid_dollars": 0.20,
            "no_ask_dollars": 0.30,
            "yes_bid_size": 20,
            "yes_ask_size": 20,
            "no_bid_size": 20,
            "no_ask_size": 20,
            "normalized_market_midpoint_probability": 0.75,
        },
    ]
    settlement_rows = [
        {
            "city": "nyc",
            "event_ticker": "EVT",
            "target_date": "2026-07-01",
            "settled_at_utc": "2026-07-02T00:00:00+00:00",
            "winner_ticker": "MKT-LOSE",
        }
    ]
    (path / "market_snapshots.json").write_text(json.dumps(market_rows), encoding="utf-8")
    (path / "settlements.json").write_text(json.dumps(settlement_rows), encoding="utf-8")


def _write_lagged_execution_export(path: Path) -> None:
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
            "yes_bid_dollars": 0.10,
            "yes_ask_dollars": 0.20,
            "yes_ask_size": 20,
            "normalized_market_midpoint_probability": 0.15,
        },
        {
            "city": "nyc",
            "event_ticker": "EVT",
            "market_ticker": "MKT-WIN",
            "target_date": "2026-07-01",
            "snapshot_time_utc": "2026-07-01T13:00:00+00:00",
            "bracket_label": "Win",
            "bracket_lower_f": 80,
            "bracket_upper_f": 80,
            "bracket_index": 1,
            "yes_bid_dollars": 0.40,
            "yes_ask_dollars": 0.50,
            "yes_ask_size": 20,
            "normalized_market_midpoint_probability": 0.45,
        },
    ]
    (path / "market_snapshots.json").write_text(json.dumps(market_rows), encoding="utf-8")


def _write_final_labeled_replay_export(path: Path) -> None:
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
            "yes_bid_dollars": 0.10,
            "yes_ask_dollars": 0.20,
            "yes_bid_size": 20,
            "yes_ask_size": 20,
            "normalized_market_midpoint_probability": 0.15,
        }
    ]
    event_rows = [
        {
            "city": "nyc",
            "event_ticker": "EVT",
            "target_date": "2026-07-01",
            "snapshot_time_utc": "2026-07-01T12:00:00+00:00",
            "climate_day_start_utc": "2026-07-01T05:00:00+00:00",
            "climate_day_end_utc": "2026-07-02T05:00:00+00:00",
            "station_id": "KNYC",
        }
    ]
    label_rows = [
        {
            "city": "nyc",
            "event_ticker": "EVT",
            "target_date": "2026-07-01",
            "station_id": "KNYC",
            "final_high_f": 80,
            "source_provider": "nws_cli",
            "issued_at_utc": "2026-07-01T11:00:00+00:00",
            "validation_status": "valid",
        }
    ]
    (path / "market_snapshots.json").write_text(json.dumps(market_rows), encoding="utf-8")
    (path / "events.json").write_text(json.dumps(event_rows), encoding="utf-8")
    (path / "final_temperature_labels.json").write_text(json.dumps(label_rows), encoding="utf-8")


def _write_early_entry_export(path: Path) -> None:
    market_rows = [
        {
            "city": "nyc",
            "event_ticker": "EVT",
            "market_ticker": "MKT-WIN",
            "target_date": "2026-07-01",
            "snapshot_time_utc": "2026-07-01T07:00:00+00:00",
            "bracket_label": "Win",
            "bracket_lower_f": 80,
            "bracket_upper_f": 81,
            "bracket_index": 1,
            "yes_bid_dollars": 0.24,
            "yes_ask_dollars": 0.30,
            "yes_bid_size": 20,
            "yes_ask_size": 20,
            "normalized_market_midpoint_probability": 0.27,
        }
    ]
    weather_rows = [
        {
            "city": "nyc",
            "event_ticker": "EVT",
            "target_date": "2026-07-01",
            "snapshot_time_utc": "2026-07-01T07:00:00+00:00",
            "nws_anchor_high_f": 80.0,
            "hrrr_projected_high_f": 81.0,
            "nbm_projected_high_f": 81.0,
            "ensemble_raw_median_high_f": 79.0,
            "observed_high_so_far_f": 72.0,
            "features": {"hours_elapsed": 2.0, "nws_hourly_window_max_f": 80.0},
        }
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
    (path / "weather_snapshots.json").write_text(json.dumps(weather_rows), encoding="utf-8")
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


def _write_no_replay_model_report(path: Path) -> None:
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
                "probabilities": "{'MKT-WIN': 0.05, 'MKT-LOSE': 0.95}",
                "snapshot_hour_utc": "2026-07-01T12:00:00+00:00",
            }
        )


def _write_lagged_execution_model_report(path: Path) -> None:
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
                "probabilities": "{'MKT-WIN': 0.30}",
                "snapshot_hour_utc": "2026-07-01T12:00:00+00:00",
            }
        )


def _write_early_entry_model_report(path: Path, probability: float) -> None:
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
                "probabilities": f"{{'MKT-WIN': {probability}}}",
                "snapshot_hour_utc": "2026-07-01T07:00:00+00:00",
            }
        )


def _write_slice_allowlist(path: Path, city: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["city", "checkpoint", "side", "bracket_type", "allowed"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "city": city,
                "checkpoint": "utc_07",
                "side": "yes",
                "bracket_type": "bounded",
                "allowed": "true",
            }
        )


if __name__ == "__main__":
    unittest.main()
