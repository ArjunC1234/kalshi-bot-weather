"""Report writers for backtest results."""

from __future__ import annotations

from pathlib import Path

from libs.io_utils import write_csv
from libs.json_utils import write_json
from libs.models import BacktestDataset, BacktestResult


def write_dataset_summary(dataset: BacktestDataset, output: Path) -> None:
    write_json(
        output / "dataset_summary.json",
        {
            "collector_runs": len(dataset.collector_runs),
            "events": len(dataset.events),
            "markets": len(dataset.markets),
            "weather": len(dataset.weather),
            "settlements": len(dataset.settlements),
            "model_outputs": len(dataset.model_outputs),
        },
    )


def write_result(result: BacktestResult, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "config.json", result.metadata | {"model_name": result.model_name})
    write_csv(output / "predictions.csv", result.predictions)
    write_csv(output / "bracket_metrics.csv", [metric.__dict__ for metric in result.metrics])
    # Placeholders keep the v1 report contract stable for downstream tooling.
    for name in (
        "temperature_metrics.csv",
        "by_city.csv",
        "by_checkpoint.csv",
        "latest_per_event.csv",
        "calibration.csv",
        "errors.csv",
    ):
        write_csv(output / name, [])
    (output / "charts").mkdir(exist_ok=True)
