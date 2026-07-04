"""Backtest evaluation for stored model distributions."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from backtest.replay import settled_distributions
from libs.metrics import log_loss, multiclass_brier, ranked_probability_score, top_one_accuracy
from libs.models import BacktestDataset, BacktestResult, MetricSummary
from libs.probabilities import normalize


def evaluate_bracket_model(
    dataset: BacktestDataset,
    model_name: str,
    source_export_id: str | None = None,
) -> BacktestResult:
    pairs = settled_distributions(dataset, model_name)
    predictions: list[dict[str, Any]] = []
    metric_rows: list[dict[str, float]] = []
    market_order = _market_order(dataset)
    for distribution, settlement in pairs:
        probabilities = normalize(distribution.probabilities, distribution.model_name)
        winner_probability = probabilities.get(settlement.winner_ticker, 0.0)
        ordered = market_order.get(
            (distribution.city, distribution.event_ticker),
            list(probabilities),
        )
        row = {
            "city": distribution.city,
            "event_ticker": distribution.event_ticker,
            "snapshot_hour_utc": distribution.snapshot_hour_utc.isoformat(),
            "model_name": model_name,
            "winner_ticker": settlement.winner_ticker,
            "winner_probability": winner_probability,
            "top_one_accuracy": top_one_accuracy(probabilities, settlement.winner_ticker),
        }
        predictions.append(row)
        metric_rows.append(
            {
                "log_loss": log_loss(winner_probability),
                "brier": multiclass_brier(probabilities, settlement.winner_ticker),
                "rps": ranked_probability_score(ordered, probabilities, settlement.winner_ticker),
                "top_one_accuracy": row["top_one_accuracy"],
                "winner_probability": winner_probability,
            }
        )
    metrics = [_summary(metric, [row[metric] for row in metric_rows]) for metric in _METRICS]
    return BacktestResult(
        model_name=model_name,
        predictions=predictions,
        metrics=metrics,
        metadata={
            "forecast_count": len(predictions),
            "source_export_id": source_export_id,
            "generated_at_utc": datetime.now(UTC).isoformat(),
        },
    )


_METRICS = ("log_loss", "brier", "rps", "top_one_accuracy", "winner_probability")


def _summary(metric: str, values: list[float]) -> MetricSummary:
    return MetricSummary(
        metric=metric,
        value=sum(values) / len(values) if values else float("nan"),
        count=len(values),
    )


def _market_order(dataset: BacktestDataset) -> dict[tuple[str, str], list[str]]:
    grouped: dict[tuple[str, str], list[tuple[int, str]]] = defaultdict(list)
    for market in dataset.markets:
        grouped[(market.city, market.event_ticker)].append(
            (market.bracket.index, market.market_ticker)
        )
    return {key: [ticker for _, ticker in sorted(values)] for key, values in grouped.items()}
