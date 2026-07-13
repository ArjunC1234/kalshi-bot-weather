"""Command line interface for Cloudcaster v1."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
RAYCASTER_DIR = CURRENT_DIR.parents[1] / "raycaster" / "v1"
NEXT_GEN_DIR = CURRENT_DIR.parents[2]
for path in (CURRENT_DIR, RAYCASTER_DIR, NEXT_GEN_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from cloud_evaluate import evaluate_expanding_window
from cloud_train import DEFAULT_MIN_TRAINING_ROWS
from dataset import load_local_dataset
from train import DEFAULT_MIN_TRAINING_EVENTS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cloudcaster v1 bracket probability model")
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate = subparsers.add_parser(
        "evaluate",
        help="evaluate Raycaster -> Cloudcaster with expanding target-date windows",
    )
    evaluate.add_argument("--data", required=True)
    evaluate.add_argument("--output", required=True)
    evaluate.add_argument(
        "--min-raycaster-training-events",
        type=int,
        default=DEFAULT_MIN_TRAINING_EVENTS,
    )
    evaluate.add_argument(
        "--min-cloudcaster-training-rows",
        type=int,
        default=DEFAULT_MIN_TRAINING_ROWS,
    )
    evaluate.add_argument("--probability-floor", type=float, default=0.001)
    args = parser.parse_args(argv)
    if args.command == "evaluate":
        return _evaluate(args)
    parser.error(f"unknown command {args.command}")
    return 2


def _evaluate(args) -> int:
    dataset = load_local_dataset(args.data)
    summary = evaluate_expanding_window(
        dataset,
        args.output,
        min_raycaster_training_events=args.min_raycaster_training_events,
        min_cloudcaster_training_rows=args.min_cloudcaster_training_rows,
        probability_floor=args.probability_floor,
        source_export_id=Path(args.data).name,
    )
    print(
        f"evaluated cloudcaster_v1: mode={summary['mode']} "
        f"temperature_rows={summary['temperature_rows']} "
        f"bracket_rows={summary['bracket_rows']} output={summary['output_dir']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
