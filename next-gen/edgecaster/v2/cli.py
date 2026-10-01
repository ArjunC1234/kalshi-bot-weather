"""CLI for Edgecaster v2 PyTorch evaluations."""

from __future__ import annotations

import argparse

from edgecaster.v2.evaluate import (
    run_fixed_window,
    run_rolling_eval,
    run_validation_gate_fixed_window,
)
from edgecaster.v2.model import EdgecasterV2Config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Edgecaster v2 PyTorch candidate-set ranker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    fixed = subparsers.add_parser("fixed-window", help="train on fixed dates and test later dates")
    _add_common(fixed)
    fixed.add_argument("--train-start", required=True)
    fixed.add_argument("--train-end", required=True)
    fixed.add_argument("--test-start", required=True)
    fixed.add_argument("--test-end", required=True)

    validation_gate = subparsers.add_parser(
        "validation-gate-fixed-window",
        help="choose a gate on the held-out validation day and apply it to fixed test dates",
    )
    _add_common(validation_gate)
    validation_gate.add_argument("--train-start", required=True)
    validation_gate.add_argument("--train-end", required=True)
    validation_gate.add_argument("--test-start", required=True)
    validation_gate.add_argument("--test-end", required=True)
    validation_gate.add_argument("--min-validation-trades", type=int, default=5)

    rolling = subparsers.add_parser("rolling-eval", help="walk-forward Edgecaster v2 evaluation")
    _add_common(rolling)
    rolling.add_argument("--train-days", type=int, default=7)
    rolling.add_argument("--test-start")
    rolling.add_argument("--test-end")
    args = parser.parse_args(argv)
    config = _config(args)
    if args.command == "fixed-window":
        summary = run_fixed_window(
            args.data,
            args.model_report,
            args.output,
            args.train_start,
            args.train_end,
            args.test_start,
            args.test_end,
            config,
        )
    elif args.command == "validation-gate-fixed-window":
        summary = run_validation_gate_fixed_window(
            args.data,
            args.model_report,
            args.output,
            args.train_start,
            args.train_end,
            args.test_start,
            args.test_end,
            config,
            min_validation_trades=args.min_validation_trades,
        )
    elif args.command == "rolling-eval":
        summary = run_rolling_eval(
            args.data,
            args.model_report,
            args.output,
            args.train_days,
            args.test_start,
            args.test_end,
            config,
        )
    else:
        parser.error(f"unknown command {args.command}")
        return 2
    print(
        f"edgecaster_v2: trades={summary['trades']} pnl={summary['total_pnl']:.4f} "
        f"roi={summary['roi']:.4f} output={summary['output_dir']}"
    )
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data", required=True)
    parser.add_argument("--model-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--hidden-size", type=int, default=48)
    parser.add_argument("--dropout", type=float, default=0.20)
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--weight-decay", type=float, default=0.03)
    parser.add_argument("--rank-loss-weight", type=float, default=0.50)
    parser.add_argument("--reward-loss-weight", type=float, default=0.50)
    parser.add_argument("--trade-loss-weight", type=float, default=0.20)
    parser.add_argument("--min-predicted-reward", type=float, default=0.03)
    parser.add_argument("--min-trade-probability", type=float, default=0.50)
    parser.add_argument("--min-raw-edge", type=float, default=0.0)
    parser.add_argument("--max-spread", type=float, default=0.15)
    parser.add_argument("--min-entry-price", type=float, default=0.02)
    parser.add_argument("--max-entry-price", type=float, default=0.80)
    parser.add_argument("--daily-budget", type=float, default=40.0)
    parser.add_argument("--max-order-cost", type=float, default=3.0)
    parser.add_argument("--budget-fraction", type=float, default=0.10)
    parser.add_argument("--max-contracts-per-order", type=int, default=20)
    parser.add_argument("--max-no-contracts-per-order", type=int, default=10)
    parser.add_argument("--max-positions-per-event", type=int, default=1)
    parser.add_argument("--seed", type=int, default=29)
    parser.add_argument(
        "--selection-policy",
        choices=["standard", "calibrated"],
        default="standard",
    )
    parser.add_argument(
        "--calibration-source",
        choices=["validation", "train"],
        default="validation",
    )
    parser.add_argument("--calibration-shrinkage", type=float, default=24.0)
    parser.add_argument("--calibration-min-count", type=int, default=10)
    parser.add_argument("--calibration-lcb-z", type=float, default=0.75)
    parser.add_argument("--min-calibrated-ev", type=float, default=0.0)
    parser.add_argument("--min-calibrated-ev-lcb", type=float, default=-0.01)


def _config(args: argparse.Namespace) -> EdgecasterV2Config:
    return EdgecasterV2Config(
        hidden_size=args.hidden_size,
        dropout=args.dropout,
        epochs=args.epochs,
        patience=args.patience,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        rank_loss_weight=args.rank_loss_weight,
        reward_loss_weight=args.reward_loss_weight,
        trade_loss_weight=args.trade_loss_weight,
        min_predicted_reward=args.min_predicted_reward,
        min_trade_probability=args.min_trade_probability,
        min_raw_edge=args.min_raw_edge,
        max_spread=args.max_spread,
        min_entry_price=args.min_entry_price,
        max_entry_price=args.max_entry_price,
        daily_budget=args.daily_budget,
        max_order_cost=args.max_order_cost,
        budget_fraction=args.budget_fraction,
        max_contracts_per_order=args.max_contracts_per_order,
        max_no_contracts_per_order=args.max_no_contracts_per_order,
        max_positions_per_event=args.max_positions_per_event,
        seed=args.seed,
        selection_policy=args.selection_policy,
        calibration_source=args.calibration_source,
        calibration_shrinkage=args.calibration_shrinkage,
        calibration_min_count=args.calibration_min_count,
        calibration_lcb_z=args.calibration_lcb_z,
        min_calibrated_ev=args.min_calibrated_ev,
        min_calibrated_ev_lcb=args.min_calibrated_ev_lcb,
    )


if __name__ == "__main__":
    raise SystemExit(main())
