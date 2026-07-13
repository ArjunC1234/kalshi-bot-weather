"""Command line interface for Raycaster v1."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
NEXT_GEN_DIR = CURRENT_DIR.parents[2]
for path in (CURRENT_DIR, NEXT_GEN_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from artifacts import write_training_rows
from baselines import DEFAULT_ESTIMATORS
from benchmark import benchmark_expanding_window
from dataset import load_local_dataset
from evaluate import evaluate_expanding_window, evaluate_fixed_model, evaluate_rolling_window
from features import MODEL_NAME, build_feature_rows
from predict import predict_dataset
from train import DEFAULT_MIN_TRAINING_EVENTS, load_model, save_model, train_raycaster_model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Raycaster v1 final NWS high model")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_train(subparsers)
    _add_predict(subparsers)
    _add_evaluate(subparsers)
    _add_rolling_eval(subparsers)
    _add_benchmark(subparsers)
    _add_report(subparsers)
    args = parser.parse_args(argv)
    if args.command == "train":
        return _train(args)
    if args.command == "predict":
        return _predict(args)
    if args.command == "evaluate":
        return _evaluate(args)
    if args.command == "rolling-eval":
        return _rolling_eval(args)
    if args.command == "benchmark":
        return _benchmark(args)
    if args.command == "report":
        return _report(args)
    parser.error(f"unknown command {args.command}")
    return 2


def _add_train(subparsers) -> None:
    parser = subparsers.add_parser("train", help="train a Raycaster v1 artifact")
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--min-training-events", type=int, default=DEFAULT_MIN_TRAINING_EVENTS)


def _add_predict(subparsers) -> None:
    parser = subparsers.add_parser("predict", help="run a saved Raycaster v1 artifact")
    parser.add_argument("--data", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--probability-floor", type=float, default=0.001)


def _add_evaluate(subparsers) -> None:
    parser = subparsers.add_parser("evaluate", help="evaluate Raycaster v1")
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model")
    parser.add_argument("--min-training-events", type=int, default=DEFAULT_MIN_TRAINING_EVENTS)
    parser.add_argument("--probability-floor", type=float, default=0.001)


def _add_rolling_eval(subparsers) -> None:
    parser = subparsers.add_parser(
        "rolling-eval",
        help="evaluate Raycaster v1 using prior N target dates to score the next date",
    )
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--train-days", type=int, default=14)
    parser.add_argument("--test-days", type=int, default=1)
    parser.add_argument("--min-training-events", type=int, default=DEFAULT_MIN_TRAINING_EVENTS)
    parser.add_argument("--probability-floor", type=float, default=0.001)


def _add_benchmark(subparsers) -> None:
    parser = subparsers.add_parser(
        "benchmark",
        help="compare Raycaster against source and MOS baselines using expanding windows",
    )
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--estimators",
        nargs="+",
        default=list(DEFAULT_ESTIMATORS),
        help=f"Estimator names. Defaults to: {', '.join(DEFAULT_ESTIMATORS)}",
    )
    parser.add_argument("--min-training-events", type=int, default=DEFAULT_MIN_TRAINING_EVENTS)
    parser.add_argument("--probability-floor", type=float, default=0.001)


def _add_report(subparsers) -> None:
    parser = subparsers.add_parser("report", help="print a concise evaluation report")
    parser.add_argument("--run", required=True)


def _train(args) -> int:
    dataset = load_local_dataset(args.data)
    rows = build_feature_rows(dataset)
    model = train_raycaster_model(rows, min_training_events=args.min_training_events)
    save_model(
        model,
        args.output,
        manifest={
            "data_path": str(args.data),
            "feature_row_count": len(rows),
        },
    )
    write_training_rows(rows, args.output)
    print(
        f"trained {MODEL_NAME}: mode={model.mode} "
        f"training_rows={model.training_rows} output={args.output}"
    )
    return 0


def _predict(args) -> int:
    dataset = load_local_dataset(args.data)
    model = load_model(args.model)
    predictions, distributions = predict_dataset(
        dataset,
        model,
        probability_floor=args.probability_floor,
    )
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    _write_prediction_csv(output / "predictions.csv", predictions)
    _write_distribution_csv(output / "bracket_distributions.csv", distributions)
    print(
        f"predicted {MODEL_NAME}: temperatures={len(predictions)} "
        f"distributions={len(distributions)} output={output}"
    )
    return 0


def _evaluate(args) -> int:
    dataset = load_local_dataset(args.data)
    source_export_id = Path(args.data).name
    if args.model:
        summary = evaluate_fixed_model(
            dataset,
            load_model(args.model),
            args.output,
            probability_floor=args.probability_floor,
            source_export_id=source_export_id,
        )
    else:
        summary = evaluate_expanding_window(
            dataset,
            args.output,
            min_training_events=args.min_training_events,
            probability_floor=args.probability_floor,
            source_export_id=source_export_id,
        )
    print(
        f"evaluated {MODEL_NAME}: mode={summary['mode']} "
        f"temperature_rows={summary['temperature_rows']} "
        f"bracket_rows={summary['bracket_rows']} output={summary['output_dir']}"
    )
    return 0


def _rolling_eval(args) -> int:
    dataset = load_local_dataset(args.data)
    summary = evaluate_rolling_window(
        dataset,
        args.output,
        train_days=args.train_days,
        test_days=args.test_days,
        min_training_events=args.min_training_events,
        probability_floor=args.probability_floor,
        source_export_id=Path(args.data).name,
    )
    print(
        f"evaluated {MODEL_NAME}: mode={summary['mode']} "
        f"temperature_rows={summary['temperature_rows']} "
        f"bracket_rows={summary['bracket_rows']} output={summary['output_dir']}"
    )
    return 0


def _benchmark(args) -> int:
    dataset = load_local_dataset(args.data)
    summary = benchmark_expanding_window(
        dataset,
        args.output,
        estimator_names=args.estimators,
        min_training_events=args.min_training_events,
        probability_floor=args.probability_floor,
        source_export_id=Path(args.data).name,
    )
    print(
        f"benchmarked {MODEL_NAME}: models={summary['models']} "
        f"temperature_rows={summary['temperature_rows']} "
        f"bracket_rows={summary['bracket_rows']} output={summary['output_dir']}"
    )
    return 0


def _report(args) -> int:
    import json

    summary_path = Path(args.run) / "summary.json"
    if not summary_path.exists():
        raise SystemExit(f"missing summary.json in {args.run}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    print(f"model: {summary.get('model_name')}")
    print(f"mode: {summary.get('mode')}")
    for section in ("temperature_metrics", "bracket_metrics"):
        print(section)
        for row in summary.get(section, []):
            print(f"  {row['metric']}: {float(row['value']):.4f} n={row['count']}")
    return 0


def _write_prediction_csv(path: Path, predictions) -> None:
    rows = []
    for prediction in predictions:
        row = {
            "city": prediction.city,
            "event_ticker": prediction.event_ticker,
            "snapshot_hour_utc": prediction.snapshot_hour_utc.isoformat(),
            "model_name": prediction.model_name,
            "expected_high_f": prediction.expected_high_f,
        }
        row.update(
            {
                f"q{int(level * 100):02d}": value
                for level, value in prediction.quantiles.items()
            }
        )
        rows.append(row)
    _write_rows(path, rows)


def _write_distribution_csv(path: Path, distributions) -> None:
    rows = [
        {
            "city": distribution.city,
            "event_ticker": distribution.event_ticker,
            "snapshot_hour_utc": distribution.snapshot_hour_utc.isoformat(),
            "model_name": distribution.model_name,
            "probabilities": distribution.probabilities,
        }
        for distribution in distributions
    ]
    _write_rows(path, rows)


def _write_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({key for row in rows for key in row}))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
