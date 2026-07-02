"""Dataset loading helpers for TheTemp v1."""

from __future__ import annotations

from pathlib import Path

from backtest.data_sources import LocalExportSource
from backtest.load_dataset import load_dataset
from libs.models import BacktestDataset, Bracket, MarketSnapshot


def load_local_dataset(path: str | Path) -> BacktestDataset:
    return load_dataset(LocalExportSource(Path(path)))


def markets_by_snapshot(
    dataset: BacktestDataset,
) -> dict[tuple[str, str, object], list[MarketSnapshot]]:
    grouped: dict[tuple[str, str, object], list[MarketSnapshot]] = {}
    for market in dataset.markets:
        key = market.city, market.event_ticker, market.snapshot_hour_utc
        grouped.setdefault(key, []).append(market)
    for markets in grouped.values():
        markets.sort(key=lambda market: market.bracket.index)
    return grouped


def brackets_for_snapshot(
    grouped_markets: dict[tuple[str, str, object], list[MarketSnapshot]],
    city: str,
    event_ticker: str,
    snapshot_hour_utc: object,
) -> list[Bracket]:
    return [
        market.bracket
        for market in grouped_markets.get((city, event_ticker, snapshot_hour_utc), [])
    ]
