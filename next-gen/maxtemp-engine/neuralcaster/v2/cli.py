"""PyTorch temporal Neuralcaster v2 experiment."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
import torch
from torch import nn

CURRENT_DIR = Path(__file__).resolve().parent
NEXT_GEN_DIR = CURRENT_DIR.parents[2]
RAYCASTER_DIR = NEXT_GEN_DIR / "maxtemp-engine" / "raycaster" / "v1"
for path in (CURRENT_DIR, RAYCASTER_DIR, NEXT_GEN_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import evaluate as raycaster_evaluate
from dataset import brackets_for_snapshot, load_local_dataset, markets_by_snapshot
from distribution import bracket_distribution, monotonic_quantiles
from features import FeatureRow, baseline_prediction, build_feature_rows, rows_with_temperature

from libs.models import BacktestDataset, Bracket, BracketDistribution, TemperaturePrediction
from libs.settlement_policy import (
    POST_SETTLEMENT_SYSTEM_START,
    POST_SETTLEMENT_SYSTEM_START_DATE,
    post_settlement_target_dates,
)

MODEL_NAME = "neuralcaster_v2"
CITY_VALUES = ["aus", "den", "la", "mia", "nyc", "okc"]
WEATHER_SEQUENCE_FEATURES = [
    "hours_elapsed",
    "hours_remaining",
    "nws_anchor_high_f",
    "observed_high_so_far_f",
    "settlement_observed_high_so_far_f",
    "settlement_observed_source_count",
    "settlement_observed_age_hours",
    "settlement_observed_source_range_f",
    "settlement_observed_source_stddev_f",
    "settlement_observed_nws_delta_f",
    "hrrr_projected_high_f",
    "nbm_projected_high_f",
    "ensemble_raw_median_high_f",
    "observation_age_hours",
    "nws_hourly_window_max_f",
    "hrrr_next_3h_max_f",
    "hrrr_next_6h_max_f",
    "hrrr_next_8h_max_f",
    "hrrr_slope_3h_f_per_hour",
    "hrrr_slope_6h_f_per_hour",
    "nbm_next_3h_max_f",
    "nbm_next_6h_max_f",
    "nbm_next_8h_max_f",
    "nbm_slope_3h_f_per_hour",
    "nbm_slope_6h_f_per_hour",
    "warming_rate_last_1h_f_per_hour",
    "warming_rate_last_3h_f_per_hour",
    "source_std_f",
    "source_range_f",
    "hrrr_minus_nws",
    "nbm_minus_nws",
    "ensemble_minus_nws",
    "observed_minus_nws",
    "hrrr_minus_observed",
    "nbm_minus_observed",
    "family_baseline_high_f",
    "family_numerical_anchor_high_f",
    "family_nws_minus_nbm_f",
    "family_hrrr_minus_nbm_f",
    "family_ensemble_minus_nbm_f",
    "family_numerical_disagreement_f",
    "family_forecast_count",
    "family_disagreement_range_f",
    "family_disagreement_std_f",
]
MARKET_SEQUENCE_FEATURES = [
    "market_expected_high_f",
    "market_top_probability",
    "market_entropy",
    "market_avg_spread",
    "market_probability_mass",
]
QUANTILE_Z = {
    0.05: -1.645,
    0.10: -1.282,
    0.25: -0.674,
    0.50: 0.0,
    0.75: 0.674,
    0.90: 1.282,
    0.95: 1.645,
}


def _resolve_torch_device(requested: str) -> torch.device:
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(requested)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA device requested, but this PyTorch install cannot see CUDA")
    return device


@dataclass(frozen=True)
class Example:
    row: FeatureRow
    sequence_rows: list[FeatureRow]
    target_high_f: float
    winner_ticker: str | None
    baseline_high_f: float
    weight: float


@dataclass(frozen=True)
class FoldTensors:
    sequence: torch.Tensor
    lengths: torch.Tensor
    static: torch.Tensor
    baseline: torch.Tensor
    target_residual: torch.Tensor
    weights: torch.Tensor


@dataclass
class Normalizer:
    means: dict[str, float]
    stds: dict[str, float]

    @classmethod
    def fit(cls, examples: list[Example], feature_names: list[str], market_lookup) -> Normalizer:
        values: dict[str, list[float]] = {name: [] for name in feature_names}
        for example in examples:
            for row in example.sequence_rows:
                feature_values = _sequence_features(row, market_lookup)
                for name in feature_names:
                    parsed = _finite_float(feature_values.get(name))
                    if parsed is not None:
                        values[name].append(parsed)
        means = {name: mean(items) if items else 0.0 for name, items in values.items()}
        stds = {}
        for name, items in values.items():
            if len(items) < 2:
                stds[name] = 1.0
                continue
            variance = sum((item - means[name]) ** 2 for item in items) / len(items)
            stds[name] = max(math.sqrt(variance), 1e-6)
        return cls(means=means, stds=stds)

    def transform(self, name: str, value: Any) -> tuple[float, float]:
        parsed = _finite_float(value)
        if parsed is None:
            return 0.0, 0.0
        return (parsed - self.means.get(name, 0.0)) / self.stds.get(name, 1.0), 1.0


class TemporalDistributionNet(nn.Module):
    def __init__(
        self,
        sequence_dim: int,
        static_dim: int,
        hidden_size: int,
        layers: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.encoder = nn.GRU(
            input_size=sequence_dim,
            hidden_size=hidden_size,
            num_layers=layers,
            batch_first=True,
            dropout=dropout if layers > 1 else 0.0,
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_size + static_dim, hidden_size),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_size, hidden_size // 2),
            nn.ReLU(),
            nn.Linear(hidden_size // 2, 2),
        )

    def forward(
        self,
        sequence: torch.Tensor,
        lengths: torch.Tensor,
        static: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        packed = nn.utils.rnn.pack_padded_sequence(
            sequence,
            lengths.cpu(),
            batch_first=True,
            enforce_sorted=False,
        )
        _, hidden = self.encoder(packed)
        encoded = hidden[-1]
        output = self.head(torch.cat([encoded, static], dim=1))
        mean_residual = output[:, 0]
        sigma = torch.nn.functional.softplus(output[:, 1]) + 0.75
        return mean_residual, sigma


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PyTorch temporal Neuralcaster v2")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_fixed_window(subparsers)
    _add_rolling_eval(subparsers)
    _add_train_artifact(subparsers)
    _add_score_artifact(subparsers)
    _add_report(subparsers)
    args = parser.parse_args(argv)
    if args.command == "fixed-window":
        return _fixed_window(args)
    if args.command == "rolling-eval":
        return _rolling_eval(args)
    if args.command == "train-artifact":
        return _train_artifact(args)
    if args.command == "score-artifact":
        return _score_artifact(args)
    if args.command == "report":
        return _report(args)
    parser.error(f"unknown command {args.command}")
    return 2


def _add_fixed_window(subparsers) -> None:
    parser = subparsers.add_parser(
        "fixed-window", help="train on fixed dates and score later dates"
    )
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--train-start", required=True)
    parser.add_argument("--train-end", required=True)
    parser.add_argument("--test-start", required=True)
    parser.add_argument("--test-end", required=True)
    parser.add_argument("--min-target-date", default=POST_SETTLEMENT_SYSTEM_START)
    parser.add_argument("--mode", choices=["weather", "market"], default="weather")
    parser.add_argument("--max-seq-len", type=int, default=24)
    parser.add_argument("--hidden-size", type=int, default=48)
    parser.add_argument("--layers", type=int, default=1)
    parser.add_argument("--dropout", type=float, default=0.10)
    parser.add_argument("--epochs", type=int, default=350)
    parser.add_argument("--patience", type=int, default=45)
    parser.add_argument("--learning-rate", type=float, default=0.003)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--min-training-examples", type=int, default=60)
    parser.add_argument("--probability-floor", type=float, default=0.001)
    parser.add_argument(
        "--market-probability-blend",
        type=float,
        default=0.0,
        help="blend market-implied bracket probabilities into market-mode output",
    )
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--device", default="auto", help="torch device: auto, cpu, cuda, or cuda:0")


def _add_rolling_eval(subparsers) -> None:
    parser = subparsers.add_parser("rolling-eval", help="run date-rolling PyTorch evaluation")
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--training-policy",
        choices=["expanding", "rolling"],
        default="expanding",
        help="expanding uses all prior dates after the warmup; rolling uses only train-days",
    )
    parser.add_argument("--train-days", type=int, default=14)
    parser.add_argument("--test-days", type=int, default=1)
    parser.add_argument("--min-target-date", default=POST_SETTLEMENT_SYSTEM_START)
    parser.add_argument("--mode", choices=["weather", "market"], default="weather")
    parser.add_argument("--max-seq-len", type=int, default=24)
    parser.add_argument("--hidden-size", type=int, default=48)
    parser.add_argument("--layers", type=int, default=1)
    parser.add_argument("--dropout", type=float, default=0.10)
    parser.add_argument("--epochs", type=int, default=350)
    parser.add_argument("--patience", type=int, default=45)
    parser.add_argument("--learning-rate", type=float, default=0.003)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--min-training-examples", type=int, default=60)
    parser.add_argument("--probability-floor", type=float, default=0.001)
    parser.add_argument(
        "--market-probability-blend",
        type=float,
        default=0.0,
        help="blend market-implied bracket probabilities into market-mode output",
    )
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--device", default="auto", help="torch device: auto, cpu, cuda, or cuda:0")


def _add_train_artifact(subparsers) -> None:
    parser = subparsers.add_parser(
        "train-artifact",
        help="train once on a fixed window and save a reusable frozen model artifact",
    )
    parser.add_argument("--data", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--train-start", required=True)
    parser.add_argument("--train-end", required=True)
    parser.add_argument("--min-target-date", default=POST_SETTLEMENT_SYSTEM_START)
    parser.add_argument("--mode", choices=["weather", "market"], default="weather")
    parser.add_argument("--max-seq-len", type=int, default=24)
    parser.add_argument("--hidden-size", type=int, default=48)
    parser.add_argument("--layers", type=int, default=1)
    parser.add_argument("--dropout", type=float, default=0.10)
    parser.add_argument("--epochs", type=int, default=350)
    parser.add_argument("--patience", type=int, default=45)
    parser.add_argument("--learning-rate", type=float, default=0.003)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--min-training-examples", type=int, default=60)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--device", default="auto", help="torch device: auto, cpu, cuda, or cuda:0")


def _add_score_artifact(subparsers) -> None:
    parser = subparsers.add_parser(
        "score-artifact",
        help="load a frozen model artifact and score a date range without training",
    )
    parser.add_argument("--data", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--min-target-date", default=POST_SETTLEMENT_SYSTEM_START)
    parser.add_argument("--probability-floor", type=float, default=0.001)
    parser.add_argument(
        "--market-probability-blend",
        type=float,
        default=None,
        help="override artifact blend only when intentionally scoring market-mode output",
    )
    parser.add_argument("--device", default="auto", help="torch device: auto, cpu, cuda, or cuda:0")


def _add_report(subparsers) -> None:
    parser = subparsers.add_parser("report", help="print a concise evaluation report")
    parser.add_argument("--run", required=True)


def _fixed_window(args) -> int:
    _seed_everything(args.seed)
    dataset = load_local_dataset(args.data)
    summary = evaluate_fixed_window(
        dataset=dataset,
        output_dir=args.output,
        train_start=_parse_iso_date(args.train_start, "train-start"),
        train_end=_parse_iso_date(args.train_end, "train-end"),
        test_start=_parse_iso_date(args.test_start, "test-start"),
        test_end=_parse_iso_date(args.test_end, "test-end"),
        min_target_date=_parse_iso_date(args.min_target_date, "min-target-date"),
        mode=args.mode,
        max_seq_len=args.max_seq_len,
        hidden_size=args.hidden_size,
        layers=args.layers,
        dropout=args.dropout,
        epochs=args.epochs,
        patience=args.patience,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        min_training_examples=args.min_training_examples,
        probability_floor=args.probability_floor,
        market_probability_blend=args.market_probability_blend,
        seed=args.seed,
        device=args.device,
        source_export_id=Path(args.data).name,
    )
    print(
        f"evaluated {MODEL_NAME}: mode={summary['mode']} "
        f"temperature_rows={summary['temperature_rows']} "
        f"bracket_rows={summary['bracket_rows']} output={summary['output_dir']}"
    )
    return 0


def _rolling_eval(args) -> int:
    _seed_everything(args.seed)
    dataset = load_local_dataset(args.data)
    summary = evaluate_rolling_window(
        dataset=dataset,
        output_dir=args.output,
        train_days=args.train_days,
        test_days=args.test_days,
        training_policy=args.training_policy,
        min_target_date=_parse_iso_date(args.min_target_date, "min-target-date"),
        mode=args.mode,
        max_seq_len=args.max_seq_len,
        hidden_size=args.hidden_size,
        layers=args.layers,
        dropout=args.dropout,
        epochs=args.epochs,
        patience=args.patience,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        min_training_examples=args.min_training_examples,
        probability_floor=args.probability_floor,
        market_probability_blend=args.market_probability_blend,
        seed=args.seed,
        device=args.device,
        source_export_id=Path(args.data).name,
    )
    print(
        f"evaluated {MODEL_NAME}: mode={summary['mode']} "
        f"temperature_rows={summary['temperature_rows']} "
        f"bracket_rows={summary['bracket_rows']} output={summary['output_dir']}"
    )
    return 0


def _train_artifact(args) -> int:
    _seed_everything(args.seed)
    dataset = load_local_dataset(args.data)
    summary = train_artifact(
        dataset=dataset,
        artifact_dir=args.artifact,
        train_start=_parse_iso_date(args.train_start, "train-start"),
        train_end=_parse_iso_date(args.train_end, "train-end"),
        min_target_date=_parse_iso_date(args.min_target_date, "min-target-date"),
        mode=args.mode,
        max_seq_len=args.max_seq_len,
        hidden_size=args.hidden_size,
        layers=args.layers,
        dropout=args.dropout,
        epochs=args.epochs,
        patience=args.patience,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        min_training_examples=args.min_training_examples,
        seed=args.seed,
        device=args.device,
        source_export_id=Path(args.data).name,
    )
    print(
        f"trained {MODEL_NAME} artifact: mode={summary['neural_mode']} "
        f"train={summary['train_start_date']}..{summary['train_end_date']} "
        f"model_mode={summary['fold']['model_mode']} artifact={summary['artifact_dir']}"
    )
    return 0


def _score_artifact(args) -> int:
    dataset = load_local_dataset(args.data)
    summary = score_artifact(
        dataset=dataset,
        artifact_dir=args.artifact,
        output_dir=args.output,
        start_date=_parse_iso_date(args.start_date, "start-date"),
        end_date=_parse_iso_date(args.end_date, "end-date"),
        min_target_date=_parse_iso_date(args.min_target_date, "min-target-date"),
        probability_floor=args.probability_floor,
        market_probability_blend=args.market_probability_blend,
        device=args.device,
        source_export_id=Path(args.data).name,
    )
    print(
        f"scored {MODEL_NAME} artifact without training: "
        f"temperature_rows={summary['temperature_rows']} "
        f"bracket_rows={summary['bracket_rows']} output={summary['output_dir']}"
    )
    return 0


def _parse_iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD format") from exc


def evaluate_fixed_window(
    dataset: BacktestDataset,
    output_dir: str | Path,
    train_start: date,
    train_end: date,
    test_start: date,
    test_end: date,
    min_target_date: date | None,
    mode: str,
    max_seq_len: int,
    hidden_size: int,
    layers: int,
    dropout: float,
    epochs: int,
    patience: int,
    learning_rate: float,
    weight_decay: float,
    min_training_examples: int,
    probability_floor: float,
    market_probability_blend: float,
    seed: int,
    device: str = "auto",
    source_export_id: str | None = None,
) -> dict[str, Any]:
    torch_device = _resolve_torch_device(device)
    if train_start > train_end:
        raise ValueError("train-start must be on or before train-end")
    if test_start > test_end:
        raise ValueError("test-start must be on or before test-end")
    rows = build_feature_rows(dataset)
    rows_by_event = _rows_by_event(rows)
    market_lookup = _market_lookup(dataset)
    markets_grouped = markets_by_snapshot(dataset)
    examples = _examples(rows, rows_by_event, market_lookup, mode, max_seq_len)
    target_dates = post_settlement_target_dates(
        (example.row.target_date for example in examples),
        min_target_date or POST_SETTLEMENT_SYSTEM_START_DATE,
    )
    train_set = {value for value in target_dates if train_start <= value <= train_end}
    test_set = {value for value in target_dates if test_start <= value <= test_end}
    if not train_set:
        raise ValueError("fixed window contains no labeled training target dates")
    if not test_set:
        raise ValueError("fixed window contains no labeled test target dates")

    train_examples = [example for example in examples if example.row.target_date in train_set]
    test_examples = [example for example in examples if example.row.target_date in test_set]
    _seed_everything(seed)
    model, normalizer, fold_summary = _fit_fold(
        train_examples,
        market_lookup,
        mode,
        max_seq_len,
        hidden_size,
        layers,
        dropout,
        epochs,
        patience,
        learning_rate,
        weight_decay,
        min_training_examples,
        seed,
        torch_device,
    )
    predictions, distributions = _predict_examples(
        test_examples,
        model,
        normalizer,
        market_lookup,
        markets_grouped,
        mode,
        max_seq_len,
        probability_floor,
        market_probability_blend,
        torch_device,
    )

    train_dates = sorted(train_set)
    test_dates = sorted(test_set)
    fold_summary.update(
        {
            "train_start_date": train_dates[0].isoformat(),
            "train_end_date": train_dates[-1].isoformat(),
            "test_start_date": test_dates[0].isoformat(),
            "test_end_date": test_dates[-1].isoformat(),
        }
    )
    diagnostics = [
        {
            "target_date": example.row.target_date.isoformat(),
            "city": example.row.city,
            "event_ticker": example.row.event_ticker,
            "snapshot_hour_utc": example.row.snapshot_hour_utc.isoformat(),
            "mode": fold_summary["model_mode"],
            "training_rows": fold_summary["training_examples"],
            "train_days": len(train_set),
            "test_days": len(test_set),
            "train_start_date": train_dates[0].isoformat(),
            "train_end_date": train_dates[-1].isoformat(),
            "test_start_date": test_dates[0].isoformat(),
            "test_end_date": test_dates[-1].isoformat(),
            "neural_mode": mode,
            "epochs_run": fold_summary["epochs_run"],
            "best_validation_loss": fold_summary["best_validation_loss"],
        }
        for example in test_examples
    ]
    raycaster_evaluate.MODEL_NAME = MODEL_NAME
    result = raycaster_evaluate.write_evaluation_outputs(
        dataset,
        output_dir,
        predictions,
        distributions,
        diagnostics,
        mode=(
            f"{mode}_fixed_window_train_{train_dates[0].isoformat()}_{train_dates[-1].isoformat()}"
            f"_test_{test_dates[0].isoformat()}_{test_dates[-1].isoformat()}"
        ),
        source_export_id=source_export_id,
    )
    _update_summary(
        Path(output_dir),
        {
            "model_name": MODEL_NAME,
            "model_family": "pytorch_gru_gaussian_residual",
            "neural_mode": mode,
            "train_start_date": train_dates[0].isoformat(),
            "train_end_date": train_dates[-1].isoformat(),
            "test_start_date": test_dates[0].isoformat(),
            "test_end_date": test_dates[-1].isoformat(),
            "min_target_date": (min_target_date or POST_SETTLEMENT_SYSTEM_START_DATE).isoformat(),
            "max_seq_len": max_seq_len,
            "hidden_size": hidden_size,
            "layers": layers,
            "dropout": dropout,
            "epochs": epochs,
            "patience": patience,
            "learning_rate": learning_rate,
            "weight_decay": weight_decay,
            "market_probability_blend": market_probability_blend,
            "seed": seed,
            "device": str(torch_device),
            "labeled_target_dates": [value.isoformat() for value in target_dates],
            "independent_city_days": len({(ex.row.city, ex.row.target_date) for ex in examples}),
            "snapshot_rows_with_labels": len(examples),
            "folds": [fold_summary],
        },
    )
    _write_loss_history(Path(output_dir), [fold_summary])
    return result


def evaluate_rolling_window(
    dataset: BacktestDataset,
    output_dir: str | Path,
    train_days: int,
    test_days: int,
    training_policy: str,
    min_target_date: date | None,
    mode: str,
    max_seq_len: int,
    hidden_size: int,
    layers: int,
    dropout: float,
    epochs: int,
    patience: int,
    learning_rate: float,
    weight_decay: float,
    min_training_examples: int,
    probability_floor: float,
    market_probability_blend: float,
    seed: int,
    device: str = "auto",
    source_export_id: str | None = None,
) -> dict[str, Any]:
    torch_device = _resolve_torch_device(device)
    if training_policy not in {"expanding", "rolling"}:
        raise ValueError("training_policy must be 'expanding' or 'rolling'")
    rows = build_feature_rows(dataset)
    rows_by_event = _rows_by_event(rows)
    market_lookup = _market_lookup(dataset)
    markets_grouped = markets_by_snapshot(dataset)
    examples = _examples(rows, rows_by_event, market_lookup, mode, max_seq_len)
    target_dates = post_settlement_target_dates(
        (example.row.target_date for example in examples),
        min_target_date or POST_SETTLEMENT_SYSTEM_START_DATE,
    )
    predictions: list[TemperaturePrediction] = []
    distributions: list[BracketDistribution] = []
    diagnostics: list[dict[str, Any]] = []
    fold_summaries: list[dict[str, Any]] = []
    start_index = train_days
    while start_index < len(target_dates):
        _seed_everything(seed + start_index)
        train_dates = (
            target_dates[:start_index]
            if training_policy == "expanding"
            else target_dates[start_index - train_days : start_index]
        )
        test_dates = target_dates[start_index : start_index + test_days]
        train_set = set(train_dates)
        test_set = set(test_dates)
        train_examples = [example for example in examples if example.row.target_date in train_set]
        test_examples = [example for example in examples if example.row.target_date in test_set]
        model, normalizer, fold_summary = _fit_fold(
            train_examples,
            market_lookup,
            mode,
            max_seq_len,
            hidden_size,
            layers,
            dropout,
            epochs,
            patience,
            learning_rate,
            weight_decay,
            min_training_examples,
            seed + start_index,
            torch_device,
        )
        batch_predictions, batch_distributions = _predict_examples(
            test_examples,
            model,
            normalizer,
            market_lookup,
            markets_grouped,
            mode,
            max_seq_len,
            probability_floor,
            market_probability_blend,
            torch_device,
        )
        predictions.extend(batch_predictions)
        distributions.extend(batch_distributions)
        fold_summary.update(
            {
                "train_start_date": train_dates[0].isoformat(),
                "train_end_date": train_dates[-1].isoformat(),
                "test_start_date": test_dates[0].isoformat(),
                "test_end_date": test_dates[-1].isoformat(),
            }
        )
        fold_summaries.append(fold_summary)
        diagnostics.extend(
            {
                "target_date": example.row.target_date.isoformat(),
                "city": example.row.city,
                "event_ticker": example.row.event_ticker,
                "snapshot_hour_utc": example.row.snapshot_hour_utc.isoformat(),
                "mode": fold_summary["model_mode"],
                "training_rows": fold_summary["training_examples"],
                "train_days": len(train_dates),
                "min_train_days": train_days,
                "training_policy": training_policy,
                "test_days": test_days,
                "train_start_date": train_dates[0].isoformat(),
                "train_end_date": train_dates[-1].isoformat(),
                "test_start_date": test_dates[0].isoformat(),
                "test_end_date": test_dates[-1].isoformat(),
                "neural_mode": mode,
                "epochs_run": fold_summary["epochs_run"],
                "best_validation_loss": fold_summary["best_validation_loss"],
            }
            for example in test_examples
        )
        start_index += test_days
    raycaster_evaluate.MODEL_NAME = MODEL_NAME
    result = raycaster_evaluate.write_evaluation_outputs(
        dataset,
        output_dir,
        predictions,
        distributions,
        diagnostics,
        mode=(
            f"{mode}_{training_policy}_walkforward_"
            f"{train_days}d_min_train_{test_days}d_test"
        ),
        source_export_id=source_export_id,
    )
    _update_summary(
        Path(output_dir),
        {
            "model_name": MODEL_NAME,
            "model_family": "pytorch_gru_gaussian_residual",
            "neural_mode": mode,
            "training_policy": training_policy,
            "train_days": train_days,
            "test_days": test_days,
            "min_target_date": (min_target_date or POST_SETTLEMENT_SYSTEM_START_DATE).isoformat(),
            "max_seq_len": max_seq_len,
            "hidden_size": hidden_size,
            "layers": layers,
            "dropout": dropout,
            "epochs": epochs,
            "patience": patience,
            "learning_rate": learning_rate,
            "weight_decay": weight_decay,
            "market_probability_blend": market_probability_blend,
            "seed": seed,
            "device": str(torch_device),
            "labeled_target_dates": [value.isoformat() for value in target_dates],
            "independent_city_days": len({(ex.row.city, ex.row.target_date) for ex in examples}),
            "snapshot_rows_with_labels": len(examples),
            "folds": fold_summaries,
        },
    )
    _write_loss_history(Path(output_dir), fold_summaries)
    return result


def train_artifact(
    dataset: BacktestDataset,
    artifact_dir: str | Path,
    train_start: date,
    train_end: date,
    min_target_date: date | None,
    mode: str,
    max_seq_len: int,
    hidden_size: int,
    layers: int,
    dropout: float,
    epochs: int,
    patience: int,
    learning_rate: float,
    weight_decay: float,
    min_training_examples: int,
    seed: int,
    device: str = "auto",
    source_export_id: str | None = None,
) -> dict[str, Any]:
    torch_device = _resolve_torch_device(device)
    if train_start > train_end:
        raise ValueError("train-start must be on or before train-end")
    rows = build_feature_rows(dataset)
    rows_by_event = _rows_by_event(rows)
    market_lookup = _market_lookup(dataset)
    examples = _examples(rows, rows_by_event, market_lookup, mode, max_seq_len)
    target_dates = post_settlement_target_dates(
        (example.row.target_date for example in examples),
        min_target_date or POST_SETTLEMENT_SYSTEM_START_DATE,
    )
    train_set = {value for value in target_dates if train_start <= value <= train_end}
    if not train_set:
        raise ValueError("artifact train window contains no labeled target dates")
    train_examples = [example for example in examples if example.row.target_date in train_set]
    _seed_everything(seed)
    model, normalizer, fold_summary = _fit_fold(
        train_examples,
        market_lookup,
        mode,
        max_seq_len,
        hidden_size,
        layers,
        dropout,
        epochs,
        patience,
        learning_rate,
        weight_decay,
        min_training_examples,
        seed,
        torch_device,
    )
    train_dates = sorted(train_set)
    fold_summary.update(
        {
            "train_start_date": train_dates[0].isoformat(),
            "train_end_date": train_dates[-1].isoformat(),
        }
    )
    artifact = Path(artifact_dir)
    artifact.mkdir(parents=True, exist_ok=True)
    payload = {
        "artifact_type": "model_artifact",
        "model_artifact_type": "neuralcaster_v2_frozen_model",
        "contract": "neuralcaster_v2_frozen_model.v1",
        "model_name": MODEL_NAME,
        "model_family": "pytorch_gru_gaussian_residual",
        "neural_mode": mode,
        "source_export_id": source_export_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "train_start_date": train_dates[0].isoformat(),
        "train_end_date": train_dates[-1].isoformat(),
        "min_target_date": (min_target_date or POST_SETTLEMENT_SYSTEM_START_DATE).isoformat(),
        "labeled_target_dates": [value.isoformat() for value in target_dates],
        "independent_city_days": len({(ex.row.city, ex.row.target_date) for ex in examples}),
        "snapshot_rows_with_labels": len(examples),
        "config": {
            "mode": mode,
            "max_seq_len": max_seq_len,
            "hidden_size": hidden_size,
            "layers": layers,
            "dropout": dropout,
            "epochs": epochs,
            "patience": patience,
            "learning_rate": learning_rate,
            "weight_decay": weight_decay,
            "min_training_examples": min_training_examples,
            "seed": seed,
        },
        "feature_names": _feature_names(mode),
        "city_values": CITY_VALUES,
        "normalizer": asdict(normalizer),
        "fold": _summary_without_loss_history(fold_summary),
        "model_state": (
            {key: value.detach().cpu() for key, value in model.state_dict().items()}
            if model is not None
            else None
        ),
    }
    torch.save(payload, artifact / "model.pt")
    manifest = {key: value for key, value in payload.items() if key != "model_state"}
    (artifact / "artifact.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _write_loss_history(artifact, [fold_summary])
    return {**manifest, "artifact_dir": str(artifact)}


def score_artifact(
    dataset: BacktestDataset,
    artifact_dir: str | Path,
    output_dir: str | Path,
    start_date: date,
    end_date: date,
    min_target_date: date | None,
    probability_floor: float,
    market_probability_blend: float | None = None,
    device: str = "auto",
    source_export_id: str | None = None,
) -> dict[str, Any]:
    if start_date > end_date:
        raise ValueError("start-date must be on or before end-date")
    if min_target_date is not None:
        start_date = max(start_date, min_target_date)
    if start_date > end_date:
        raise ValueError("score window contains no post-settlement-system target dates")
    artifact = _load_artifact(Path(artifact_dir))
    artifact_kind = artifact.get("model_artifact_type") or artifact.get("artifact_type")
    if artifact_kind != "neuralcaster_v2_frozen_model":
        raise ValueError("artifact is not a neuralcaster_v2 frozen model")
    config = artifact["config"]
    mode = str(artifact["neural_mode"])
    torch_device = _resolve_torch_device(device)
    normalizer = Normalizer(**artifact["normalizer"])
    model = _model_from_artifact(artifact, torch_device)
    rows = build_feature_rows(dataset)
    rows_by_event = _rows_by_event(rows)
    market_lookup = _market_lookup(dataset)
    markets_grouped = markets_by_snapshot(dataset)
    examples = _scoring_examples(
        rows,
        rows_by_event,
        market_lookup,
        mode,
        int(config["max_seq_len"]),
        start_date,
        end_date,
    )
    blend = (
        float(market_probability_blend)
        if market_probability_blend is not None
        else 0.0
    )
    predictions, distributions = _predict_examples(
        examples,
        model,
        normalizer,
        market_lookup,
        markets_grouped,
        mode,
        int(config["max_seq_len"]),
        probability_floor,
        blend,
        torch_device,
    )
    diagnostics = [
        {
            "target_date": example.row.target_date.isoformat(),
            "city": example.row.city,
            "event_ticker": example.row.event_ticker,
            "snapshot_hour_utc": example.row.snapshot_hour_utc.isoformat(),
            "mode": "frozen_artifact_score",
            "artifact_dir": str(artifact_dir),
            "artifact_train_start_date": artifact["train_start_date"],
            "artifact_train_end_date": artifact["train_end_date"],
            "neural_mode": mode,
            "has_label": example.row.settlement_temperature_f is not None,
        }
        for example in examples
    ]
    raycaster_evaluate.MODEL_NAME = MODEL_NAME
    result = raycaster_evaluate.write_evaluation_outputs(
        dataset,
        output_dir,
        predictions,
        distributions,
        diagnostics,
        mode=(
            f"{mode}_frozen_artifact_train_{artifact['train_start_date']}_"
            f"{artifact['train_end_date']}_score_{start_date.isoformat()}_{end_date.isoformat()}"
        ),
        source_export_id=source_export_id,
    )
    _update_summary(
        Path(output_dir),
        {
            "model_name": MODEL_NAME,
            "model_family": "pytorch_gru_gaussian_residual",
            "neural_mode": mode,
            "scoring_mode": "frozen_artifact_no_training",
            "artifact_dir": str(artifact_dir),
            "artifact_train_start_date": artifact["train_start_date"],
            "artifact_train_end_date": artifact["train_end_date"],
            "score_start_date": start_date.isoformat(),
            "score_end_date": end_date.isoformat(),
            "min_target_date": (min_target_date or POST_SETTLEMENT_SYSTEM_START_DATE).isoformat(),
            "max_seq_len": config["max_seq_len"],
            "hidden_size": config["hidden_size"],
            "layers": config["layers"],
            "dropout": config["dropout"],
            "market_probability_blend": blend,
            "seed": config["seed"],
            "device": str(torch_device),
            "scored_snapshot_rows": len(examples),
            "scored_labeled_snapshot_rows": sum(
                example.row.settlement_temperature_f is not None for example in examples
            ),
        },
    )
    return result


def _load_artifact(artifact_dir: Path) -> dict[str, Any]:
    path = artifact_dir / "model.pt"
    if not path.exists():
        raise FileNotFoundError(f"missing neuralcaster artifact model file: {path}")
    try:
        return torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        return torch.load(path, map_location="cpu")


def _model_from_artifact(
    artifact: dict[str, Any],
    device: torch.device,
) -> TemporalDistributionNet | None:
    state = artifact.get("model_state")
    if state is None:
        return None
    config = artifact["config"]
    mode = str(artifact["neural_mode"])
    model = TemporalDistributionNet(
        sequence_dim=len(_feature_names(mode)) * 2,
        static_dim=len(CITY_VALUES) + 3,
        hidden_size=int(config["hidden_size"]),
        layers=int(config["layers"]),
        dropout=float(config["dropout"]),
    )
    model.load_state_dict(state)
    model.to(device)
    model.eval()
    return model


def _summary_without_loss_history(summary: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in summary.items() if key != "loss_history"}


def _fit_fold(
    train_examples: list[Example],
    market_lookup,
    mode: str,
    max_seq_len: int,
    hidden_size: int,
    layers: int,
    dropout: float,
    epochs: int,
    patience: int,
    learning_rate: float,
    weight_decay: float,
    min_training_examples: int,
    seed: int,
    device: torch.device,
) -> tuple[TemporalDistributionNet | None, Normalizer, dict[str, Any]]:
    feature_names = _feature_names(mode)
    normalizer = Normalizer.fit(train_examples, feature_names, market_lookup)
    if len(train_examples) < min_training_examples:
        return (
            None,
            normalizer,
            {
                "model_mode": "fallback_baseline",
                "training_examples": len(train_examples),
                "epochs_run": 0,
                "best_validation_loss": "",
            },
        )
    train_dates = sorted({example.row.target_date for example in train_examples})
    validation_date = train_dates[-1] if len(train_dates) >= 3 else None
    fit_examples = (
        [example for example in train_examples if example.row.target_date != validation_date]
        if validation_date is not None
        else train_examples
    )
    validation_examples = (
        [example for example in train_examples if example.row.target_date == validation_date]
        if validation_date is not None
        else train_examples
    )
    fit_tensors = _to_tensors(
        fit_examples,
        normalizer,
        market_lookup,
        mode,
        max_seq_len=max_seq_len,
        device=device,
    )
    validation_tensors = _to_tensors(
        validation_examples,
        normalizer,
        market_lookup,
        mode,
        max_seq_len=max_seq_len,
        device=device,
    )
    model = TemporalDistributionNet(
        sequence_dim=fit_tensors.sequence.shape[-1],
        static_dim=fit_tensors.static.shape[-1],
        hidden_size=hidden_size,
        layers=layers,
        dropout=dropout,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    best_state = None
    best_loss = float("inf")
    epochs_without_improvement = 0
    epochs_run = 0
    loss_history: list[dict[str, Any]] = []
    for epoch in range(1, epochs + 1):
        epochs_run = epoch
        model.train()
        optimizer.zero_grad()
        mean_residual, sigma = model(
            fit_tensors.sequence,
            fit_tensors.lengths,
            fit_tensors.static,
        )
        loss = _gaussian_nll(
            fit_tensors.target_residual,
            mean_residual,
            sigma,
            fit_tensors.weights,
        )
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 2.0)
        optimizer.step()
        model.eval()
        with torch.no_grad():
            val_mean, val_sigma = model(
                validation_tensors.sequence,
                validation_tensors.lengths,
                validation_tensors.static,
            )
            validation_loss = float(
                _gaussian_nll(
                    validation_tensors.target_residual,
                    val_mean,
                    val_sigma,
                    validation_tensors.weights,
                )
            )
        loss_history.append(
            {
                "epoch": epoch,
                "train_loss": float(loss.detach()),
                "validation_loss": validation_loss,
            }
        )
        if validation_loss + 1e-5 < best_loss:
            best_loss = validation_loss
            best_state = {
                key: value.detach().cpu().clone() for key, value in model.state_dict().items()
            }
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
        if epochs_without_improvement >= patience:
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    return (
        model,
        normalizer,
        {
            "model_mode": "trained_gru_gaussian_residual",
            "training_examples": len(train_examples),
            "fit_examples": len(fit_examples),
            "validation_examples": len(validation_examples),
            "epochs_run": epochs_run,
            "best_validation_loss": best_loss,
            "seed": seed,
            "device": str(device),
            "loss_history": loss_history,
        },
    )


def _write_loss_history(output_dir: Path, fold_summaries: list[dict[str, Any]]) -> None:
    import csv

    rows = []
    for fold_index, fold in enumerate(fold_summaries, start=1):
        for item in fold.get("loss_history", []):
            rows.append(
                {
                    "fold": fold_index,
                    "train_start_date": fold.get("train_start_date", ""),
                    "train_end_date": fold.get("train_end_date", ""),
                    "test_start_date": fold.get("test_start_date", ""),
                    "test_end_date": fold.get("test_end_date", ""),
                    **item,
                }
            )
    if not rows:
        return
    path = output_dir / "loss_history.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    try:
        import matplotlib
    except ImportError:
        return
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    charts = output_dir / "charts"
    charts.mkdir(exist_ok=True)
    plt.figure(figsize=(9, 5))
    for fold_index in sorted({int(row["fold"]) for row in rows}):
        fold_rows = [row for row in rows if int(row["fold"]) == fold_index]
        epochs = [int(row["epoch"]) for row in fold_rows]
        train_loss = [float(row["train_loss"]) for row in fold_rows]
        validation_loss = [float(row["validation_loss"]) for row in fold_rows]
        label = f"fold {fold_index}"
        plt.plot(epochs, train_loss, linestyle="--", alpha=0.75, label=f"{label} train")
        plt.plot(epochs, validation_loss, linewidth=2, label=f"{label} validation")
    plt.xlabel("Epoch")
    plt.ylabel("Gaussian NLL")
    plt.title("Neuralcaster v2 Training Loss")
    plt.legend()
    plt.tight_layout()
    plt.savefig(charts / "training_loss.png")
    plt.close()


def _predict_examples(
    examples: list[Example],
    model: TemporalDistributionNet | None,
    normalizer: Normalizer,
    market_lookup,
    markets_grouped,
    mode: str,
    max_seq_len: int,
    probability_floor: float,
    market_probability_blend: float,
    device: torch.device,
) -> tuple[list[TemperaturePrediction], list[BracketDistribution]]:
    tensors = _to_tensors(
        examples,
        normalizer,
        market_lookup,
        mode,
        max_seq_len=max_seq_len,
        device=device,
    )
    if model is None:
        expected = tensors.baseline.cpu().numpy()
        sigma = np.full(len(examples), 3.0)
    else:
        model.to(device)
        model.eval()
        with torch.no_grad():
            residual, sigma_tensor = model(tensors.sequence, tensors.lengths, tensors.static)
        expected = (tensors.baseline + residual).cpu().numpy()
        sigma = sigma_tensor.cpu().numpy()
    predictions: list[TemperaturePrediction] = []
    distributions: list[BracketDistribution] = []
    for example, expected_high, sigma_value in zip(examples, expected, sigma, strict=True):
        expected_high = _respect_observed_floor(float(expected_high), example.row)
        quantiles = _quantiles(expected_high, float(sigma_value), example.row)
        predictions.append(
            TemperaturePrediction(
                city=example.row.city,
                event_ticker=example.row.event_ticker,
                snapshot_hour_utc=example.row.snapshot_hour_utc,
                model_name=MODEL_NAME,
                expected_high_f=expected_high,
                quantiles=quantiles,
            )
        )
        brackets = brackets_for_snapshot(
            markets_grouped,
            example.row.city,
            example.row.event_ticker,
            example.row.snapshot_hour_utc,
        )
        if brackets:
            neural_probabilities = bracket_distribution(
                brackets,
                expected_high_f=expected_high,
                quantiles=quantiles,
                observed_high_so_far_f=_observed(example.row),
                probability_floor=probability_floor,
            )
            probabilities = _blend_market_probabilities(
                neural_probabilities,
                brackets,
                example.row,
                market_lookup,
                market_probability_blend if mode == "market" else 0.0,
                probability_floor,
            )
            distributions.append(
                BracketDistribution(
                    city=example.row.city,
                    event_ticker=example.row.event_ticker,
                    snapshot_hour_utc=example.row.snapshot_hour_utc,
                    model_name=MODEL_NAME,
                    probabilities=probabilities,
                )
            )
    return predictions, distributions


def _to_tensors(
    examples: list[Example],
    normalizer: Normalizer,
    market_lookup,
    mode: str,
    max_seq_len: int,
    device: torch.device | None = None,
) -> FoldTensors:
    active_device = device or torch.device("cpu")
    feature_names = _feature_names(mode)
    city_index = {value: index for index, value in enumerate(CITY_VALUES)}
    sequence_dim = len(feature_names) * 2
    static_dim = len(CITY_VALUES) + 3
    sequence = np.zeros((len(examples), max_seq_len, sequence_dim), dtype=np.float32)
    static = np.zeros((len(examples), static_dim), dtype=np.float32)
    lengths = np.ones(len(examples), dtype=np.int64)
    baseline = np.zeros(len(examples), dtype=np.float32)
    target_residual = np.zeros(len(examples), dtype=np.float32)
    weights = np.zeros(len(examples), dtype=np.float32)
    for row_index, example in enumerate(examples):
        rows = example.sequence_rows[-max_seq_len:]
        lengths[row_index] = max(1, len(rows))
        for seq_index, row in enumerate(rows):
            values = _sequence_features(row, market_lookup)
            for feature_index, name in enumerate(feature_names):
                normalized, present = normalizer.transform(name, values.get(name))
                sequence[row_index, seq_index, feature_index] = normalized
                sequence[row_index, seq_index, feature_index + len(feature_names)] = present
        offset = 0
        static[row_index, offset + city_index.get(example.row.city, 0)] = 1.0
        offset += len(CITY_VALUES)
        static[row_index, offset] = float(example.row.target_date.timetuple().tm_yday) / 366.0
        static[row_index, offset + 1] = float(example.row.snapshot_hour_utc.hour) / 23.0
        static[row_index, offset + 2] = 1.0 if mode == "market" else 0.0
        baseline[row_index] = example.baseline_high_f
        target_residual[row_index] = example.target_high_f - example.baseline_high_f
        weights[row_index] = example.weight
    return FoldTensors(
        sequence=torch.tensor(sequence, device=active_device),
        lengths=torch.tensor(lengths, device=active_device),
        static=torch.tensor(static, device=active_device),
        baseline=torch.tensor(baseline, device=active_device),
        target_residual=torch.tensor(target_residual, device=active_device),
        weights=torch.tensor(weights, device=active_device),
    )


def _examples(
    rows: list[FeatureRow],
    rows_by_event: dict[tuple[str, str], list[FeatureRow]],
    market_lookup,
    mode: str,
    max_seq_len: int,
) -> list[Example]:
    labeled = rows_with_temperature(rows)
    city_day_counts = Counter((row.city, row.target_date) for row in labeled)
    examples = []
    for row in labeled:
        history = [
            item
            for item in rows_by_event.get((row.city, row.event_ticker), [])
            if item.snapshot_hour_utc <= row.snapshot_hour_utc
        ][-max_seq_len:]
        if not history or row.settlement_temperature_f is None:
            continue
        examples.append(
            Example(
                row=row,
                sequence_rows=history,
                target_high_f=float(row.settlement_temperature_f),
                winner_ticker=row.winner_ticker,
                baseline_high_f=_baseline(row, market_lookup, mode),
                weight=1.0 / city_day_counts[(row.city, row.target_date)],
            )
        )
    return examples


def _scoring_examples(
    rows: list[FeatureRow],
    rows_by_event: dict[tuple[str, str], list[FeatureRow]],
    market_lookup,
    mode: str,
    max_seq_len: int,
    start_date: date,
    end_date: date,
) -> list[Example]:
    examples = []
    for row in rows:
        if row.target_date < start_date or row.target_date > end_date:
            continue
        history = [
            item
            for item in rows_by_event.get((row.city, row.event_ticker), [])
            if item.snapshot_hour_utc <= row.snapshot_hour_utc
        ][-max_seq_len:]
        if not history:
            continue
        baseline_high_f = _baseline(row, market_lookup, mode)
        examples.append(
            Example(
                row=row,
                sequence_rows=history,
                target_high_f=(
                    float(row.settlement_temperature_f)
                    if row.settlement_temperature_f is not None
                    else baseline_high_f
                ),
                winner_ticker=row.winner_ticker,
                baseline_high_f=baseline_high_f,
                weight=1.0,
            )
        )
    return examples


def _rows_by_event(rows: list[FeatureRow]) -> dict[tuple[str, str], list[FeatureRow]]:
    grouped: dict[tuple[str, str], list[FeatureRow]] = {}
    for row in rows:
        grouped.setdefault((row.city, row.event_ticker), []).append(row)
    for items in grouped.values():
        items.sort(key=lambda item: item.snapshot_hour_utc)
    return grouped


def _market_lookup(dataset: BacktestDataset):
    grouped = markets_by_snapshot(dataset)
    lookup = {key: _market_features(markets) for key, markets in grouped.items()}
    for key, markets in grouped.items():
        city, event_ticker, snapshot_hour_utc = key
        lookup[(city, event_ticker, snapshot_hour_utc, "markets")] = markets
    return lookup


def _market_features(markets) -> dict[str, float]:
    if not markets:
        return {}
    raw_probs = {}
    spreads = []
    for market in markets:
        probability = market.normalized_market_midpoint_probability
        if probability is None and market.yes_bid is not None and market.yes_ask is not None:
            probability = (float(market.yes_bid) + float(market.yes_ask)) / 2.0
        if probability is None:
            continue
        raw_probs[market.bracket.ticker] = max(0.0, float(probability))
        if market.yes_bid is not None and market.yes_ask is not None:
            spreads.append(max(0.0, float(market.yes_ask) - float(market.yes_bid)))
    total = sum(raw_probs.values())
    if total <= 0:
        return {}
    probs = {ticker: probability / total for ticker, probability in raw_probs.items()}
    representatives = {
        market.bracket.ticker: _bracket_representative(market.bracket) for market in markets
    }
    expected = sum(probs[ticker] * representatives[ticker] for ticker in probs)
    entropy = -sum(prob * math.log(max(prob, 1e-9)) for prob in probs.values())
    return {
        "market_expected_high_f": expected,
        "market_top_probability": max(probs.values()),
        "market_entropy": entropy,
        "market_avg_spread": mean(spreads) if spreads else 0.0,
        "market_probability_mass": total,
    }


def _blend_market_probabilities(
    neural_probabilities: dict[str, float],
    brackets: list[Bracket],
    row: FeatureRow,
    market_lookup,
    blend_weight: float,
    probability_floor: float,
) -> dict[str, float]:
    blend_weight = min(1.0, max(0.0, blend_weight))
    if blend_weight <= 0:
        return neural_probabilities
    market_probabilities = _market_probability_distribution(brackets, row, market_lookup)
    if not market_probabilities:
        return neural_probabilities
    raw = {
        ticker: (1.0 - blend_weight) * neural_probabilities.get(ticker, 0.0)
        + blend_weight * market_probabilities.get(ticker, 0.0)
        for ticker in neural_probabilities
    }
    total = sum(max(0.0, value) for value in raw.values())
    if total <= 0:
        return neural_probabilities
    normalized = {ticker: max(0.0, value) / total for ticker, value in raw.items()}
    if probability_floor <= 0:
        return normalized
    floored = {ticker: max(value, probability_floor) for ticker, value in normalized.items()}
    floor_total = sum(floored.values())
    return {ticker: value / floor_total for ticker, value in floored.items()}


def _market_probability_distribution(
    brackets: list[Bracket],
    row: FeatureRow,
    market_lookup,
) -> dict[str, float]:
    markets = market_lookup.get((row.city, row.event_ticker, row.snapshot_hour_utc, "markets"))
    if not markets:
        return {}
    bracket_tickers = {bracket.ticker for bracket in brackets}
    raw = {}
    for market in markets:
        probability = market.normalized_market_midpoint_probability
        if probability is None and market.yes_bid is not None and market.yes_ask is not None:
            probability = (float(market.yes_bid) + float(market.yes_ask)) / 2.0
        if probability is not None and market.market_ticker in bracket_tickers:
            raw[market.market_ticker] = max(0.0, float(probability))
    total = sum(raw.values())
    if total <= 0:
        return {}
    return {ticker: value / total for ticker, value in raw.items()}


def _bracket_representative(bracket: Bracket) -> float:
    if bracket.lower_f is None and bracket.upper_f is not None:
        return float(bracket.upper_f - 1)
    if bracket.upper_f is None and bracket.lower_f is not None:
        return float(bracket.lower_f + 1)
    if bracket.lower_f is not None and bracket.upper_f is not None:
        return (float(bracket.lower_f) + float(bracket.upper_f)) / 2.0
    return 75.0


def _baseline(row: FeatureRow, market_lookup, mode: str) -> float:
    weather = baseline_prediction(row, "weather_only")
    if mode != "market":
        return weather
    market = _market_for_row(row, market_lookup).get("market_expected_high_f")
    market_value = _finite_float(market)
    if market_value is None:
        return weather
    observed = _observed(row)
    blended = 0.70 * market_value + 0.30 * weather
    return max(blended, observed) if observed is not None else blended


def _sequence_features(row: FeatureRow, market_lookup) -> dict[str, float | str | None]:
    values = dict(row.features)
    values.update(_market_for_row(row, market_lookup))
    return values


def _market_for_row(row: FeatureRow, market_lookup) -> dict[str, float]:
    return market_lookup.get((row.city, row.event_ticker, row.snapshot_hour_utc), {})


def _feature_names(mode: str) -> list[str]:
    return (
        WEATHER_SEQUENCE_FEATURES + MARKET_SEQUENCE_FEATURES
        if mode == "market"
        else WEATHER_SEQUENCE_FEATURES
    )


def _gaussian_nll(
    target: torch.Tensor,
    mean_prediction: torch.Tensor,
    sigma: torch.Tensor,
    weights: torch.Tensor,
) -> torch.Tensor:
    variance = sigma.square()
    per_row = 0.5 * ((target - mean_prediction).square() / variance) + torch.log(sigma)
    normalized_weights = weights / torch.clamp(weights.sum(), min=1e-9)
    return (per_row * normalized_weights).sum()


def _quantiles(expected_high_f: float, sigma: float, row: FeatureRow) -> dict[float, float]:
    sigma = max(1.0, min(8.0, sigma))
    raw = {level: expected_high_f + z_score * sigma for level, z_score in QUANTILE_Z.items()}
    observed = _observed(row)
    if observed is not None:
        raw = {level: max(value, observed) for level, value in raw.items()}
    return monotonic_quantiles(raw)


def _respect_observed_floor(value: float, row: FeatureRow) -> float:
    observed = _observed(row)
    return max(value, observed) if observed is not None else value


def _observed(row: FeatureRow) -> float | None:
    observed = _finite_float(row.features.get("settlement_observed_high_so_far_f"))
    return (
        observed
        if observed is not None
        else _finite_float(row.features.get("observed_high_so_far_f"))
    )


def _finite_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(1)


def _update_summary(output_dir: Path, payload: dict[str, Any]) -> None:
    summary_path = output_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.update(payload)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _report(args) -> int:
    summary_path = Path(args.run) / "summary.json"
    if not summary_path.exists():
        raise SystemExit(f"missing summary.json in {args.run}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    print(f"model: {summary.get('model_name')}")
    print(f"mode: {summary.get('mode')}")
    print(f"source_export_id: {summary.get('source_export_id')}")
    print(f"independent_city_days: {summary.get('independent_city_days')}")
    for section in ("temperature_metrics", "bracket_metrics"):
        print(section)
        for row in summary.get(section, []):
            print(f"  {row['metric']}: {float(row['value']):.4f} n={row['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
