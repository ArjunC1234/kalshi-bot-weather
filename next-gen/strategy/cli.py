"""CLI for paper strategy backtests."""

from __future__ import annotations

import argparse

from strategy.backtest import run_backtest
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
