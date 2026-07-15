"""Paper order and trade records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from strategy.signals import Signal


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    side: str
    city: str
    event_ticker: str
    market_ticker: str
    target_date: str
    snapshot_hour_utc: datetime
    model_probability: float
    entry_price: float
    edge: float
    contracts: float


@dataclass(frozen=True)
class PaperTrade:
    order_id: str
    city: str
    event_ticker: str
    market_ticker: str
    target_date: str
    entry_time_utc: datetime
    model_probability: float
    entry_price: float
    edge: float
    contracts: float
    winner_ticker: str | None
    settlement_value: float
    pnl: float
    roi: float
    hit: float
    closing_mid: float | None
    clv: float | None
    checkpoint: str
    side: str = "yes"
    bracket_type: str = "unknown"


def order_from_signal(
    signal: Signal,
    contracts: float,
    sequence: int,
) -> PaperOrder:
    if signal.yes_ask is None or signal.buy_edge is None:
        raise ValueError("buy order requires ask and buy edge")
    return PaperOrder(
        order_id=f"paper-{sequence:06d}",
        side="buy_yes",
        city=signal.city,
        event_ticker=signal.event_ticker,
        market_ticker=signal.market_ticker,
        target_date=signal.target_date,
        snapshot_hour_utc=signal.snapshot_hour_utc,
        model_probability=signal.model_probability,
        entry_price=float(signal.yes_ask),
        edge=float(signal.buy_edge),
        contracts=contracts,
    )
