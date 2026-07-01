"""Command line interface for the next-gen backtest engine."""

from __future__ import annotations

import argparse
from pathlib import Path

from backtest.data_sources import LocalExportSource
from backtest.evaluate import evaluate_bracket_model
from backtest.export_supabase import export_supabase
from backtest.load_dataset import load_dataset
from backtest.reports import write_dataset_summary, write_result
from backtest.validators import validate_dataset
from libs.config import load_dotenv
from libs.json_utils import write_json


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Next-gen weather backtesting engine")
    commands = parser.add_subparsers(dest="command", required=True)

    export_parser = commands.add_parser("export", help="Export Supabase rows to local files.")
    export_parser.add_argument("--start", required=True)
    export_parser.add_argument("--end", required=True)
    export_parser.add_argument("--output", type=Path, required=True)

    validate_parser = commands.add_parser("validate", help="Validate a frozen local dataset.")
    validate_parser.add_argument("--data", type=Path, required=True)
    validate_parser.add_argument("--require-settlements", action="store_true")

    evaluate_parser = commands.add_parser("evaluate", help="Evaluate stored model probabilities.")
    evaluate_parser.add_argument("--data", type=Path, required=True)
    evaluate_parser.add_argument("--model", required=True)
    evaluate_parser.add_argument("--output", type=Path, required=True)

    report_parser = commands.add_parser("report", help="Print a compact report summary.")
    report_parser.add_argument("--run", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "export":
        export_supabase(args.start, args.end, args.output)
        return 0
    if args.command == "validate":
        dataset = load_dataset(LocalExportSource(args.data))
        validate_dataset(dataset, require_settlements=args.require_settlements)
        write_dataset_summary(dataset, args.data)
        return 0
    if args.command == "evaluate":
        dataset = load_dataset(LocalExportSource(args.data))
        validate_dataset(dataset)
        result = evaluate_bracket_model(dataset, args.model)
        write_dataset_summary(dataset, args.output)
        write_result(result, args.output)
        return 0
    if args.command == "report":
        import json

        summary_path = args.run / "dataset_summary.json"
        if summary_path.exists():
            print(json.dumps(__import__("json").loads(summary_path.read_text()), indent=2))
        else:
            write_json(args.run / "dataset_summary.json", {})
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
