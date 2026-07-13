"""CLI for paper strategy backtests."""

from __future__ import annotations

import argparse

from strategy.backtest import run_backtest
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
            ),
        )
        print(
            f"paper backtest: trades={summary['trades']} pnl={summary['total_pnl']:.4f} "
            f"roi={summary['roi']:.4f} output={summary['output_dir']}"
        )
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
