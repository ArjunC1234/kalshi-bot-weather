"""Conservative paper fill and settlement simulation."""

from __future__ import annotations

from collections.abc import Iterable

from libs.models import MarketSnapshot, Settlement
from strategy.orders import PaperOrder, PaperTrade
from strategy.signals import market_midpoint


def settle_order(
    order: PaperOrder,
    settlement: Settlement | None,
    market_history: Iterable[MarketSnapshot],
) -> PaperTrade:
    winner_ticker = settlement.winner_ticker if settlement is not None else None
    hit = 1.0 if winner_ticker == order.market_ticker else 0.0
    settlement_value = hit
    pnl = (settlement_value - order.entry_price) * order.contracts
    roi = pnl / max(1e-9, order.entry_price * order.contracts)
    closing_mid = _closing_mid(market_history)
    clv = closing_mid - order.entry_price if closing_mid is not None else None
    return PaperTrade(
        order_id=order.order_id,
        city=order.city,
        event_ticker=order.event_ticker,
        market_ticker=order.market_ticker,
        target_date=order.target_date,
        entry_time_utc=order.snapshot_hour_utc,
        model_probability=order.model_probability,
        entry_price=order.entry_price,
        edge=order.edge,
        contracts=order.contracts,
        winner_ticker=winner_ticker,
        settlement_value=settlement_value,
        pnl=pnl,
        roi=roi,
        hit=hit,
        closing_mid=closing_mid,
        clv=clv,
        checkpoint=_checkpoint(order.snapshot_hour_utc),
    )


def _closing_mid(markets: Iterable[MarketSnapshot]) -> float | None:
    ordered = sorted(markets, key=lambda market: market.snapshot_hour_utc)
    for market in reversed(ordered):
        midpoint = market_midpoint(market)
        if midpoint is not None:
            return midpoint
    return None


def _checkpoint(snapshot_hour_utc) -> str:
    return f"utc_{snapshot_hour_utc.hour:02d}"
