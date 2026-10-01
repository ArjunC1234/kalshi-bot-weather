# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
NEXT_GEN_DIR = CURRENT_DIR.parents[2]
RAYCASTER_DIR = NEXT_GEN_DIR / "maxtemp-engine" / "raycaster" / "v1"
for path in (CURRENT_DIR, RAYCASTER_DIR, NEXT_GEN_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from residual_evaluate import evaluate_rolling_window
from residual_models import MODEL_KINDS

from libs.settlement_policy import POST_SETTLEMENT_SYSTEM_START, parse_policy_date


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Neuralcaster v3 NWS-residual models")
    subparsers = parser.add_subparsers(dest="command", required=True)
    rolling = subparsers.add_parser("rolling-eval", help="run rolling residual evaluation")
    rolling.add_argument("--data", required=True)
    rolling.add_argument("--output", required=True)
    rolling.add_argument("--model", choices=MODEL_KINDS, default="tree_residual")
    rolling.add_argument("--train-days", type=int, default=14)
    rolling.add_argument("--test-days", type=int, default=1)
    rolling.add_argument("--min-target-date", default=POST_SETTLEMENT_SYSTEM_START)
    rolling.add_argument("--min-training-rows", type=int, default=60)
    rolling.add_argument("--probability-floor", type=float, default=0.001)
    rolling.add_argument("--seed", type=int, default=31)
    rolling.add_argument("--device", default="auto", help="torch device for MLP/GRU models")
    rolling.add_argument("--source-export-id")
    args = parser.parse_args(argv)
    if args.command == "rolling-eval":
        summary = evaluate_rolling_window(
            args.data,
            args.output,
            model_kind=args.model,
            train_days=args.train_days,
            test_days=args.test_days,
            min_target_date=parse_policy_date(args.min_target_date, "min-target-date"),
            min_training_rows=args.min_training_rows,
            probability_floor=args.probability_floor,
            seed=args.seed,
            device=args.device,
            source_export_id=args.source_export_id,
        )
        print(
            f"neuralcaster v3: model={args.model} "
            f"temperature_rows={summary['temperature_rows']} "
            f"bracket_rows={summary['bracket_rows']} output={summary['output_dir']}"
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
