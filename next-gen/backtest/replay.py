"""Point-in-time replay helpers."""

from __future__ import annotations

from collections import defaultdict

from backtest.settlements import settlement_by_event
from libs.models import BacktestDataset, BracketDistribution, EventSnapshot, Settlement


def settled_distributions(
    dataset: BacktestDataset,
    model_name: str,
) -> list[tuple[BracketDistribution, Settlement]]:
    settlements = settlement_by_event(dataset.settlements)
    rows: list[tuple[BracketDistribution, Settlement]] = []
    for distribution in dataset.model_outputs:
        if distribution.model_name != model_name:
            continue
        settlement = settlements.get((distribution.city, distribution.event_ticker))
        if settlement is not None:
            rows.append((distribution, settlement))
    return rows


def latest_event_snapshots(events: list[EventSnapshot]) -> list[EventSnapshot]:
    grouped: dict[tuple[str, str], list[EventSnapshot]] = defaultdict(list)
    for event in events:
        grouped[(event.city, event.event_ticker)].append(event)
    return [max(group, key=lambda item: item.snapshot_hour_utc) for group in grouped.values()]
