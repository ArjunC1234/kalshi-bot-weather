"""Backtest dataset validators."""

from __future__ import annotations

from backtest.settlements import settlement_by_event
from libs.errors import DataValidationError
from libs.models import BacktestDataset
from libs.validation import validate_distribution


def validate_dataset(dataset: BacktestDataset, require_settlements: bool = False) -> None:
    if not dataset.events:
        raise DataValidationError("dataset has no events")
    if require_settlements and not dataset.settlements:
        raise DataValidationError("dataset has no settlements")
    settlement_by_event(dataset.settlements)
    for distribution in dataset.model_outputs:
        validate_distribution(distribution.probabilities)
