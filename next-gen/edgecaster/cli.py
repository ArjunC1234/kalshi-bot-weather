"""CLI for Edgecaster offline evaluations."""

from __future__ import annotations

import argparse

from edgecaster.model import EdgecasterConfig, run_edgecaster_evaluation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Edgecaster tradeability model")
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate = subparsers.add_parser("evaluate", help="run walk-forward Edgecaster evaluation")
    evaluate.add_argument("--data", required=True)
    evaluate.add_argument("--model-report", required=True)
    evaluate.add_argument("--output", required=True)
    evaluate.add_argument("--model-type", choices=["ridge", "hgb"], default="ridge")
    evaluate.add_argument("--min-training-days", type=int, default=5)
    evaluate.add_argument("--min-training-examples", type=int, default=400)
    evaluate.add_argument("--min-predicted-reward", type=float, default=0.03)
    evaluate.add_argument("--min-cloud-edge", type=float, default=0.0)
    evaluate.add_argument("--max-spread", type=float, default=0.15)
    evaluate.add_argument("--min-entry-price", type=float, default=0.02)
    evaluate.add_argument("--max-entry-price", type=float, default=0.80)
    evaluate.add_argument("--daily-budget", type=float, default=40.0)
    evaluate.add_argument("--max-order-cost", type=float, default=3.0)
    evaluate.add_argument("--budget-fraction", type=float, default=0.10)
    evaluate.add_argument("--max-contracts-per-order", type=int, default=20)
    evaluate.add_argument("--max-no-contracts-per-order", type=int, default=10)
    evaluate.add_argument("--max-positions-per-event", type=int, default=1)
    evaluate.add_argument(
        "--allow-fallback-trades",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    args = parser.parse_args(argv)
    if args.command == "evaluate":
        summary = run_edgecaster_evaluation(
            args.data,
            args.model_report,
            args.output,
            EdgecasterConfig(
                model_type=args.model_type,
                min_training_days=args.min_training_days,
                min_training_examples=args.min_training_examples,
                min_predicted_reward=args.min_predicted_reward,
                min_cloud_edge=args.min_cloud_edge,
                max_spread=args.max_spread,
                min_entry_price=args.min_entry_price,
                max_entry_price=args.max_entry_price,
                daily_budget=args.daily_budget,
                max_order_cost=args.max_order_cost,
                budget_fraction=args.budget_fraction,
                max_contracts_per_order=args.max_contracts_per_order,
                max_no_contracts_per_order=args.max_no_contracts_per_order,
                max_positions_per_event=args.max_positions_per_event,
                allow_fallback_trades=args.allow_fallback_trades,
            ),
        )
        print(
            f"edgecaster: trades={summary['trades']} pnl={summary['total_pnl']:.4f} "
            f"roi={summary['roi']:.4f} hit={summary['hit_rate']:.4f} "
            f"output={summary['output_dir']}"
        )
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
