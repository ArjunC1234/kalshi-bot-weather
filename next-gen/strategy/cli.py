"""CLI for paper strategy backtests."""

from __future__ import annotations

import argparse

from libs.settlement_policy import POST_SETTLEMENT_SYSTEM_START
from strategy.backtest import run_backtest
from strategy.ev_backtest import (
    NeuralEvConfig,
    NeuralLearnedGateConfig,
    run_neural_ev_backtest,
    run_neural_ev_learned_gate,
    run_neural_ev_validation_fixed_window,
)
from strategy.live_replay import LiveReplayConfig, build_slice_allowlist, run_live_replay
from strategy.signals import StrategyConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper strategy backtest engine")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backtest = subparsers.add_parser("backtest", help="run a paper strategy backtest")
    backtest.add_argument("--data", required=True)
    backtest.add_argument("--model-report", required=True)
    backtest.add_argument("--output", required=True)
    backtest.add_argument("--edge-threshold", type=float, default=0.05)
    backtest.add_argument("--max-spread", type=float, default=0.15)
    backtest.add_argument("--stake", type=float, default=1.0)
    backtest.add_argument("--max-positions-per-event", type=int, default=1)
    backtest.add_argument("--daily-budget", type=float)
    backtest.add_argument(
        "--sizing-policy",
        choices=["fixed", "prob-tier", "edge-tier", "cheap-tier", "fractional-kelly"],
        default="fixed",
    )
    backtest.add_argument("--base-budget-fraction", type=float, default=0.10)
    backtest.add_argument("--max-budget-fraction", type=float, default=0.25)
    backtest.add_argument("--kelly-fraction", type=float, default=0.25)
    backtest.add_argument("--min-price", type=float, default=0.02)
    backtest.add_argument("--max-price", type=float, default=0.98)
    backtest.add_argument("--min-model-probability", type=float)
    backtest.add_argument("--max-model-probability", type=float)
    backtest.add_argument("--max-edge", type=float)
    backtest.add_argument("--min-entry-hour-utc", type=int)
    backtest.add_argument("--max-entry-hour-utc", type=int)
    backtest.add_argument("--include-cities", nargs="*", default=[])
    backtest.add_argument("--exclude-cities", nargs="*", default=[])
    backtest.add_argument(
        "--entry-policy",
        choices=["first", "latest", "best-edge"],
        default="first",
    )
    neural_ev = subparsers.add_parser(
        "neural-ev",
        help="run a Neuralcaster probability EV strategy backtest",
    )
    neural_ev.add_argument("--data", required=True)
    neural_ev.add_argument("--model-report", required=True)
    neural_ev.add_argument("--output", required=True)
    neural_ev.add_argument("--start-date")
    neural_ev.add_argument("--end-date")
    neural_ev.add_argument("--min-target-date", default=POST_SETTLEMENT_SYSTEM_START)
    neural_ev.add_argument("--min-ev", type=float, default=0.03)
    neural_ev.add_argument("--max-ev", type=float)
    neural_ev.add_argument("--max-spread", type=float, default=0.15)
    neural_ev.add_argument("--min-entry-price", type=float, default=0.02)
    neural_ev.add_argument("--max-entry-price", type=float, default=0.80)
    neural_ev.add_argument("--daily-budget", type=float, default=40.0)
    neural_ev.add_argument("--max-order-cost", type=float, default=3.0)
    neural_ev.add_argument("--max-contracts-per-order", type=int, default=20)
    neural_ev.add_argument("--max-no-contracts-per-order", type=int, default=10)
    neural_ev.add_argument("--max-positions-per-event", type=int, default=1)
    neural_ev.add_argument("--min-hours-elapsed", type=float)
    neural_ev.add_argument("--allow-yes", action=argparse.BooleanOptionalAction, default=True)
    neural_ev.add_argument("--allow-no", action=argparse.BooleanOptionalAction, default=True)
    neural_ev.add_argument(
        "--entry-policy",
        choices=["first", "latest", "best-ev"],
        default="best-ev",
    )
    neural_ev_validation = subparsers.add_parser(
        "neural-ev-validation-fixed-window",
        help="select a Neuralcaster EV policy on a validation window and apply it to a test window",
    )
    neural_ev_validation.add_argument("--data", required=True)
    neural_ev_validation.add_argument("--model-report", required=True)
    neural_ev_validation.add_argument("--output", required=True)
    neural_ev_validation.add_argument("--train-start", required=True)
    neural_ev_validation.add_argument("--train-end", required=True)
    neural_ev_validation.add_argument("--test-start", required=True)
    neural_ev_validation.add_argument("--test-end", required=True)
    neural_ev_validation.add_argument("--min-target-date", default=POST_SETTLEMENT_SYSTEM_START)
    neural_ev_validation.add_argument("--daily-budget", type=float, default=40.0)
    neural_ev_validation.add_argument("--max-order-cost", type=float, default=3.0)
    neural_ev_validation.add_argument("--max-contracts-per-order", type=int, default=20)
    neural_ev_validation.add_argument("--max-no-contracts-per-order", type=int, default=10)
    neural_ev_validation.add_argument("--max-positions-per-event", type=int, default=1)
    neural_ev_validation.add_argument("--min-hours-elapsed", type=float)
    neural_ev_validation.add_argument("--min-validation-trades", type=int, default=5)
    neural_ev_validation.add_argument(
        "--validation-objective",
        choices=["robust", "pnl", "hit_rate"],
        default="robust",
    )
    neural_ev_validation.add_argument("--min-validation-positive-clv", type=float, default=0.50)
    neural_ev_validation.add_argument(
        "--entry-policy",
        choices=["first", "latest", "best-ev"],
        default="best-ev",
    )
    neural_ev_gate = subparsers.add_parser(
        "neural-ev-learned-gate",
        help="train a leak-free learned gate on candidate trades and apply it to a test window",
    )
    neural_ev_gate.add_argument("--data", required=True)
    neural_ev_gate.add_argument("--model-report", required=True)
    neural_ev_gate.add_argument("--output", required=True)
    neural_ev_gate.add_argument("--train-start", required=True)
    neural_ev_gate.add_argument("--train-end", required=True)
    neural_ev_gate.add_argument("--test-start", required=True)
    neural_ev_gate.add_argument("--test-end", required=True)
    neural_ev_gate.add_argument("--min-target-date", default=POST_SETTLEMENT_SYSTEM_START)
    neural_ev_gate.add_argument(
        "--gate-model-type",
        choices=["hist_gradient_boosting", "ridge"],
        default="hist_gradient_boosting",
    )
    neural_ev_gate.add_argument("--min-training-examples", type=int, default=400)
    neural_ev_gate.add_argument("--min-training-dates", type=int, default=5)
    neural_ev_gate.add_argument("--min-predicted-reward", type=float, default=0.0)
    neural_ev_gate.add_argument("--min-trade-probability", type=float, default=0.55)
    neural_ev_gate.add_argument(
        "--selection-score",
        choices=["predicted_reward", "trade_probability"],
        default="predicted_reward",
    )
    neural_ev_gate.add_argument(
        "--gate-application",
        choices=["fixed_gate_veto", "rerank_candidates"],
        default="fixed_gate_veto",
        help="fixed_gate_veto only rejects fixed-gate trades; rerank_candidates can replace them",
    )
    neural_ev_gate.add_argument("--min-ev", type=float, default=0.0)
    neural_ev_gate.add_argument("--max-ev", type=float)
    neural_ev_gate.add_argument("--max-spread", type=float, default=0.10)
    neural_ev_gate.add_argument("--min-entry-price", type=float, default=0.50)
    neural_ev_gate.add_argument("--max-entry-price", type=float, default=0.65)
    neural_ev_gate.add_argument("--daily-budget", type=float, default=40.0)
    neural_ev_gate.add_argument("--max-order-cost", type=float, default=3.0)
    neural_ev_gate.add_argument("--max-contracts-per-order", type=int, default=20)
    neural_ev_gate.add_argument("--max-no-contracts-per-order", type=int, default=10)
    neural_ev_gate.add_argument("--max-positions-per-event", type=int, default=1)
    neural_ev_gate.add_argument("--min-hours-elapsed", type=float)
    neural_ev_gate.add_argument("--allow-yes", action=argparse.BooleanOptionalAction, default=True)
    neural_ev_gate.add_argument("--allow-no", action=argparse.BooleanOptionalAction, default=True)
    neural_ev_gate.add_argument(
        "--entry-policy",
        choices=["first", "latest", "best-ev"],
        default="best-ev",
    )
    replay = subparsers.add_parser("live-replay", help="run a live-like strategy replay")
    replay.add_argument("--data", required=True)
    replay.add_argument("--model-report", required=True)
    replay.add_argument("--output", required=True)
    replay.add_argument("--edge-threshold", type=float, default=0.08)
    replay.add_argument("--max-edge", type=float, default=0.12)
    replay.add_argument("--max-spread", type=float, default=0.15)
    replay.add_argument("--min-model-probability", type=float, default=0.20)
    replay.add_argument("--max-model-probability", type=float, default=0.50)
    replay.add_argument("--enable-no-trading", action=argparse.BooleanOptionalAction, default=True)
    replay.add_argument(
        "--no-entry-mode",
        choices=["disabled", "observed_only", "model"],
        default="observed_only",
    )
    replay.add_argument("--min-no-model-probability", type=float, default=0.75)
    replay.add_argument("--min-no-ask", type=float, default=0.05)
    replay.add_argument("--max-contracts-per-order", type=int, default=20)
    replay.add_argument("--max-no-contracts-per-order", type=int, default=10)
    replay.add_argument("--min-entry-exit-bid", type=float, default=0.01)
    replay.add_argument("--min-entry-quote-size", type=float, default=1.0)
    replay.add_argument("--min-entry-price", type=float)
    replay.add_argument("--max-entry-price", type=float)
    replay.add_argument(
        "--enable-early-entry-gate",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    replay.add_argument("--early-entry-hours-elapsed", type=float, default=6.0)
    replay.add_argument("--early-entry-edge-threshold", type=float, default=0.18)
    replay.add_argument("--mid-entry-hours-elapsed", type=float, default=10.0)
    replay.add_argument("--mid-entry-edge-threshold", type=float, default=0.15)
    replay.add_argument("--early-entry-min-source-confirmations", type=int, default=2)
    replay.add_argument("--observed-exclusion-margin-f", type=float, default=1.0)
    replay.add_argument("--exit-edge-threshold", type=float, default=0.08)
    replay.add_argument("--daily-budget", type=float, default=40.0)
    replay.add_argument("--max-order-cost", type=float, default=3.0)
    replay.add_argument("--base-budget-fraction", type=float, default=0.10)
    replay.add_argument("--max-budget-fraction", type=float, default=0.25)
    replay.add_argument(
        "--sizing-policy",
        choices=["fixed", "prob-tier", "edge-tier", "cheap-tier"],
        default="cheap-tier",
    )
    replay.add_argument("--max-positions-per-event", type=int, default=1)
    replay.add_argument("--max-open-positions", type=int, default=12)
    replay.add_argument("--execution-lag-hours", type=int, default=0)
    replay.add_argument(
        "--block-worse-execution-price",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    replay.add_argument("--min-exit-price", type=float, default=0.01)
    replay.add_argument("--no-bid-exit-cooldown-hours", type=int, default=1)
    replay.add_argument("--slice-allowlist")
    replay.add_argument(
        "--slice-key-fields",
        nargs="+",
        default=["city", "checkpoint", "side", "bracket_type"],
    )
    allowlist = subparsers.add_parser(
        "slice-allowlist",
        help="build a replay slice allowlist from prior settled trades",
    )
    allowlist.add_argument("--trades", required=True)
    allowlist.add_argument("--output", required=True)
    allowlist.add_argument(
        "--slice-key-fields",
        nargs="+",
        default=["city", "checkpoint", "side", "bracket_type"],
    )
    allowlist.add_argument("--min-trades", type=int, default=2)
    allowlist.add_argument("--min-pnl", type=float, default=0.0)
    allowlist.add_argument("--min-avg-clv", type=float, default=0.0)
    allowlist.add_argument("--min-roi", type=float)
    allowlist.add_argument("--min-hit-rate", type=float)
    args = parser.parse_args(argv)
    if args.command == "backtest":
        summary = run_backtest(
            args.data,
            args.model_report,
            args.output,
            StrategyConfig(
                edge_threshold=args.edge_threshold,
                max_spread=args.max_spread,
                stake=args.stake,
                max_positions_per_event=args.max_positions_per_event,
                daily_budget=args.daily_budget,
                sizing_policy=args.sizing_policy,
                base_budget_fraction=args.base_budget_fraction,
                max_budget_fraction=args.max_budget_fraction,
                kelly_fraction=args.kelly_fraction,
                min_price=args.min_price,
                max_price=args.max_price,
                min_model_probability=args.min_model_probability,
                max_model_probability=args.max_model_probability,
                max_edge=args.max_edge,
                min_entry_hour_utc=args.min_entry_hour_utc,
                max_entry_hour_utc=args.max_entry_hour_utc,
                include_cities=tuple(args.include_cities),
                exclude_cities=tuple(args.exclude_cities),
                entry_policy=args.entry_policy,
            ),
        )
        print(
            f"paper backtest: trades={summary['trades']} pnl={summary['total_pnl']:.4f} "
            f"roi={summary['roi']:.4f} output={summary['output_dir']}"
        )
        return 0
    if args.command == "neural-ev":
        summary = run_neural_ev_backtest(
            args.data,
            args.model_report,
            args.output,
            NeuralEvConfig(
                start_date=args.start_date,
                end_date=args.end_date,
                min_target_date=args.min_target_date,
                min_ev=args.min_ev,
                max_ev=args.max_ev,
                max_spread=args.max_spread,
                min_entry_price=args.min_entry_price,
                max_entry_price=args.max_entry_price,
                daily_budget=args.daily_budget,
                max_order_cost=args.max_order_cost,
                max_contracts_per_order=args.max_contracts_per_order,
                max_no_contracts_per_order=args.max_no_contracts_per_order,
                max_positions_per_event=args.max_positions_per_event,
                min_hours_elapsed=args.min_hours_elapsed,
                allow_yes=args.allow_yes,
                allow_no=args.allow_no,
                entry_policy=args.entry_policy,
            ),
        )
        print(
            f"neural ev: trades={summary['trades']} pnl={summary['total_pnl']:.4f} "
            f"roi={summary['roi']:.4f} output={summary['output_dir']}"
        )
        return 0
    if args.command == "neural-ev-validation-fixed-window":
        summary = run_neural_ev_validation_fixed_window(
            args.data,
            args.model_report,
            args.output,
            NeuralEvConfig(
                daily_budget=args.daily_budget,
                min_target_date=args.min_target_date,
                max_order_cost=args.max_order_cost,
                max_contracts_per_order=args.max_contracts_per_order,
                max_no_contracts_per_order=args.max_no_contracts_per_order,
                max_positions_per_event=args.max_positions_per_event,
                min_hours_elapsed=args.min_hours_elapsed,
                entry_policy=args.entry_policy,
            ),
            train_start=args.train_start,
            train_end=args.train_end,
            test_start=args.test_start,
            test_end=args.test_end,
            min_validation_trades=args.min_validation_trades,
            validation_objective=args.validation_objective,
            min_validation_positive_clv=args.min_validation_positive_clv,
        )
        print(
            f"neural ev validation: trades={summary['trades']} "
            f"pnl={summary['total_pnl']:.4f} roi={summary['roi']:.4f} "
            f"output={summary['output_dir']}"
        )
        return 0
    if args.command == "neural-ev-learned-gate":
        summary = run_neural_ev_learned_gate(
            args.data,
            args.model_report,
            args.output,
            NeuralLearnedGateConfig(
                train_start=args.train_start,
                train_end=args.train_end,
                test_start=args.test_start,
                test_end=args.test_end,
                min_target_date=args.min_target_date,
                gate_model_type=args.gate_model_type,
                min_training_examples=args.min_training_examples,
                min_training_dates=args.min_training_dates,
                min_predicted_reward=args.min_predicted_reward,
                min_trade_probability=args.min_trade_probability,
                selection_score=args.selection_score,
                gate_application=args.gate_application,
                min_ev=args.min_ev,
                max_ev=args.max_ev,
                max_spread=args.max_spread,
                min_entry_price=args.min_entry_price,
                max_entry_price=args.max_entry_price,
                daily_budget=args.daily_budget,
                max_order_cost=args.max_order_cost,
                max_contracts_per_order=args.max_contracts_per_order,
                max_no_contracts_per_order=args.max_no_contracts_per_order,
                max_positions_per_event=args.max_positions_per_event,
                min_hours_elapsed=args.min_hours_elapsed,
                allow_yes=args.allow_yes,
                allow_no=args.allow_no,
                entry_policy=args.entry_policy,
            ),
        )
        print(
            f"neural ev learned gate: trades={summary['trades']} "
            f"pnl={summary['total_pnl']:.4f} roi={summary['roi']:.4f} "
            f"output={summary['output_dir']}"
        )
        return 0
    if args.command == "live-replay":
        summary = run_live_replay(
            args.data,
            args.model_report,
            args.output,
            LiveReplayConfig(
                edge_threshold=args.edge_threshold,
                max_edge=args.max_edge,
                max_spread=args.max_spread,
                min_model_probability=args.min_model_probability,
                max_model_probability=args.max_model_probability,
                enable_no_trading=args.enable_no_trading,
                no_entry_mode=args.no_entry_mode,
                min_no_model_probability=args.min_no_model_probability,
                min_no_ask=args.min_no_ask,
                max_contracts_per_order=args.max_contracts_per_order,
                max_no_contracts_per_order=args.max_no_contracts_per_order,
                min_entry_exit_bid=args.min_entry_exit_bid,
                min_entry_quote_size=args.min_entry_quote_size,
                min_entry_price=args.min_entry_price,
                max_entry_price=args.max_entry_price,
                enable_early_entry_gate=args.enable_early_entry_gate,
                early_entry_hours_elapsed=args.early_entry_hours_elapsed,
                early_entry_edge_threshold=args.early_entry_edge_threshold,
                mid_entry_hours_elapsed=args.mid_entry_hours_elapsed,
                mid_entry_edge_threshold=args.mid_entry_edge_threshold,
                early_entry_min_source_confirmations=args.early_entry_min_source_confirmations,
                observed_exclusion_margin_f=args.observed_exclusion_margin_f,
                exit_edge_threshold=args.exit_edge_threshold,
                daily_budget=args.daily_budget,
                max_order_cost=args.max_order_cost,
                base_budget_fraction=args.base_budget_fraction,
                max_budget_fraction=args.max_budget_fraction,
                sizing_policy=args.sizing_policy,
                max_positions_per_event=args.max_positions_per_event,
                max_open_positions=args.max_open_positions,
                execution_lag_hours=args.execution_lag_hours,
                block_worse_execution_price=args.block_worse_execution_price,
                min_exit_price=args.min_exit_price,
                no_bid_exit_cooldown_hours=args.no_bid_exit_cooldown_hours,
                slice_allowlist_path=args.slice_allowlist,
                slice_key_fields=tuple(args.slice_key_fields),
            ),
        )
        print(
            f"live replay: trades={summary['trades']} pnl={summary['total_pnl']:.4f} "
            f"roi={summary['roi']:.4f} blocked={summary['blocked_orders']} "
            f"output={summary['output_dir']}"
        )
        return 0
    if args.command == "slice-allowlist":
        summary = build_slice_allowlist(
            args.trades,
            args.output,
            key_fields=tuple(args.slice_key_fields),
            min_trades=args.min_trades,
            min_pnl=args.min_pnl,
            min_avg_clv=args.min_avg_clv,
            min_roi=args.min_roi,
            min_hit_rate=args.min_hit_rate,
        )
        print(
            f"slice allowlist: groups={summary['groups']} "
            f"allowed={summary['allowed_groups']} output={summary['output']}"
        )
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
