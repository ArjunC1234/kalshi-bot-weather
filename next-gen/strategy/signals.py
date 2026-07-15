"""Signal generation from model fair values and market quotes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from libs.models import MarketSnapshot


@dataclass(frozen=True)
class StrategyConfig:
    edge_threshold: float = 0.05
    max_spread: float = 0.15
    stake: float = 1.0
    max_positions_per_event: int = 1
    min_price: float = 0.02
    max_price: float = 0.98
    min_model_probability: float | None = None
    max_model_probability: float | None = None
    max_edge: float | None = None
    min_entry_hour_utc: int | None = None
    max_entry_hour_utc: int | None = None
    include_cities: tuple[str, ...] = ()
    exclude_cities: tuple[str, ...] = ()
    entry_policy: str = "first"
    sizing_policy: str = "fixed"
    daily_budget: float | None = None
    base_budget_fraction: float = 0.10
    max_budget_fraction: float = 0.25
    kelly_fraction: float = 0.25


@dataclass(frozen=True)
class Signal:
    city: str
    event_ticker: str
    market_ticker: str
    target_date: str
    snapshot_hour_utc: datetime
    model_probability: float
    yes_bid: float | None
    yes_ask: float | None
    market_mid: float | None
    spread: float | None
    buy_edge: float | None
    sell_edge: float | None
    decision: str
    skip_reason: str


def signal_for_market(
    market: MarketSnapshot,
    model_probability: float,
    config: StrategyConfig,
) -> Signal:
    market_mid = market_midpoint(market)
    spread = (
        float(market.yes_ask) - float(market.yes_bid)
        if market.yes_ask is not None and market.yes_bid is not None
        else None
    )
    buy_edge = (
        float(model_probability) - float(market.yes_ask)
        if market.yes_ask is not None
        else None
    )
    sell_edge = (
        float(market.yes_bid) - float(model_probability)
        if market.yes_bid is not None
        else None
    )
    decision, reason = _decision(market, model_probability, buy_edge, spread, config)
    return Signal(
        city=market.city,
        event_ticker=market.event_ticker,
        market_ticker=market.market_ticker,
        target_date=market.target_date.isoformat(),
        snapshot_hour_utc=market.snapshot_hour_utc,
        model_probability=float(model_probability),
        yes_bid=market.yes_bid,
        yes_ask=market.yes_ask,
        market_mid=market_mid,
        spread=spread,
        buy_edge=buy_edge,
        sell_edge=sell_edge,
        decision=decision,
        skip_reason=reason,
    )


def market_midpoint(market: MarketSnapshot) -> float | None:
    if market.normalized_market_midpoint_probability is not None:
        return float(market.normalized_market_midpoint_probability)
    if market.yes_bid is not None and market.yes_ask is not None:
        return (float(market.yes_bid) + float(market.yes_ask)) / 2.0
    if market.last_price is not None:
        return float(market.last_price)
    return None


def _decision(
    market: MarketSnapshot,
    model_probability: float,
    buy_edge: float | None,
    spread: float | None,
    config: StrategyConfig,
) -> tuple[str, str]:
    if market.yes_ask is None:
        return "skip", "missing_ask"
    if market.yes_bid is None:
        return "skip", "missing_bid"
    if spread is None:
        return "skip", "missing_spread"
    if spread > config.max_spread:
        return "skip", "spread_too_wide"
    if market.yes_ask < config.min_price or market.yes_ask > config.max_price:
        return "skip", "price_out_of_range"
    if (
        config.min_model_probability is not None
        and model_probability < config.min_model_probability
    ):
        return "skip", "model_probability_too_low"
    if (
        config.max_model_probability is not None
        and model_probability >= config.max_model_probability
    ):
        return "skip", "model_probability_too_high"
    if buy_edge is None or buy_edge < config.edge_threshold:
        return "skip", "edge_below_threshold"
    if config.max_edge is not None and buy_edge >= config.max_edge:
        return "skip", "edge_above_max"
    if (
        config.min_entry_hour_utc is not None
        and market.snapshot_hour_utc.hour < config.min_entry_hour_utc
    ):
        return "skip", "before_entry_window"
    if (
        config.max_entry_hour_utc is not None
        and market.snapshot_hour_utc.hour > config.max_entry_hour_utc
    ):
        return "skip", "after_entry_window"
    if config.include_cities and market.city not in config.include_cities:
        return "skip", "city_not_included"
    if config.exclude_cities and market.city in config.exclude_cities:
        return "skip", "city_excluded"
    return "buy_yes", ""
