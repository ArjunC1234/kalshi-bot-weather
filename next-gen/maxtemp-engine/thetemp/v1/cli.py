"""Command line interface for the TheTemp model template."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
NEXT_GEN_DIR = CURRENT_DIR.parents[2]
for path in (CURRENT_DIR, NEXT_GEN_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from dataset import load_local_dataset
from evaluate import evaluate_dataset
from features import build_feature_rows
from predict import predict_dataset
from train import load_model, save_model, train_model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TheTemp v1 clonable model template")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("train", "predict", "evaluate"):
        subparser = commands.add_parser(command)
        subparser.add_argument("--data", required=True)
        subparser.add_argument("--output", required=True)
        if command == "predict":
            subparser.add_argument("--model", required=True)
    report = commands.add_parser("report")
    report.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    if args.command == "train":
        dataset = load_local_dataset(args.data)
        rows = build_feature_rows(dataset)
        model = train_model(rows)
        save_model(model, args.output, {"feature_row_count": len(rows)})
        print(f"trained {model.model_name}: mode={model.mode} output={args.output}")
        return 0
    if args.command == "predict":
        dataset = load_local_dataset(args.data)
        predictions, distributions = predict_dataset(dataset, load_model(args.model))
        Path(args.output).mkdir(parents=True, exist_ok=True)
        print(
            f"predicted thetemp_v1: temperatures={len(predictions)} "
            f"distributions={len(distributions)} output={args.output}"
        )
        return 0
    if args.command == "evaluate":
        dataset = load_local_dataset(args.data)
        summary = evaluate_dataset(dataset, args.output)
        print(
            f"evaluated {summary['model_name']}: "
            f"temperature_rows={summary['temperature_prediction_count']} "
            f"bracket_rows={summary['bracket_prediction_count']} output={args.output}"
        )
        return 0
    if args.command == "report":
        import json

        summary = json.loads((Path(args.run) / "summary.json").read_text(encoding="utf-8"))
        print(json.dumps(summary, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
