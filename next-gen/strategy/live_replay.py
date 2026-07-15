"""Live-like strategy replay with side-aware entries and exits."""

from __future__ import annotations

import ast
import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backtest.data_sources import LocalExportSource
from backtest.load_dataset import load_dataset
from libs.models import (
    BacktestDataset,
    EventSnapshot,
    FinalTemperatureLabel,
    MarketSnapshot,
    Settlement,
    WeatherSnapshot,
)
from libs.source_families import family_support_count, weather_values
from strategy.metrics import (
    daily_pnl_rows,
    edge_bucket_rows,
    grouped_metric_rows,
    price_bucket_rows,
    probability_bucket_rows,
    summary_metrics,
)
from strategy.orders import PaperTrade


@dataclass(frozen=True)
class LiveReplayConfig:
    edge_threshold: float = 0.08
    max_edge: float | None = 0.12
    max_spread: float = 0.15
    min_model_probability: float = 0.20
    max_model_probability: float = 0.50
    enable_no_trading: bool = True
    no_entry_mode: str = "observed_only"
    min_no_model_probability: float = 0.75
    min_no_ask: float = 0.05
    max_contracts_per_order: int = 20
    max_no_contracts_per_order: int = 10
    min_entry_exit_bid: float = 0.01
    min_entry_quote_size: float = 1.0
    min_entry_price: float | None = None
    max_entry_price: float | None = None
    enable_early_entry_gate: bool = False
    early_entry_hours_elapsed: float = 6.0
    early_entry_edge_threshold: float = 0.18
    mid_entry_hours_elapsed: float = 10.0
    mid_entry_edge_threshold: float = 0.15
    early_entry_min_source_confirmations: int = 2
    observed_exclusion_margin_f: float = 1.0
    exit_edge_threshold: float = 0.08
    daily_budget: float | None = 40.0
    max_order_cost: float = 3.0
    base_budget_fraction: float = 0.10
    max_budget_fraction: float = 0.25
    sizing_policy: str = "cheap-tier"
    max_positions_per_event: int = 1
    max_open_positions: int = 12
    execution_lag_hours: int = 0
    block_worse_execution_price: bool = True
    min_exit_price: float = 0.01
    no_bid_exit_cooldown_hours: int = 1
    late_day_min_hours_elapsed: float = 10.0
    late_day_observed_gap_block_f: float = 2.0
    late_day_min_source_confirmations: int = 2
    slice_allowlist_path: str | None = None
    slice_key_fields: tuple[str, ...] = ("city", "checkpoint", "side", "bracket_type")


@dataclass(frozen=True)
class ReplaySignal:
    city: str
    event_ticker: str
    market_ticker: str
    side: str
    target_date: str
    snapshot_hour_utc: datetime
    checkpoint: str
    bracket_type: str
    model_probability: float
    entry_bid: float
    entry_ask: float
    spread: float
    edge: float
    contracts_requested: float
    order_cost_requested: float


@dataclass(frozen=True)
class ReplayFill:
    fill_id: str
    action: str
    reason: str
    city: str
    event_ticker: str
    market_ticker: str
    side: str
    target_date: str
    signal_time_utc: datetime
    execution_time_utc: datetime
    model_probability: float | None
    edge: float | None
    requested_contracts: float
    filled_contracts: float
    outcome_price: float
    exchange_price: float
    blocked: bool = False
    block_reason: str = ""


@dataclass
class ReplayPosition:
    order_id: str
    city: str
    event_ticker: str
    market_ticker: str
    side: str
    target_date: str
    entry_time_utc: datetime
    model_probability: float
    entry_price: float
    edge: float
    contracts: float
    checkpoint: str
    bracket_type: str


def run_live_replay(
    data_path: str | Path,
    model_report: str | Path,
    output_dir: str | Path,
    config: LiveReplayConfig,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset(LocalExportSource(Path(data_path)))
    probabilities = _load_probabilities(Path(model_report))
    result = _run(dataset, probabilities, config)
    summary = {
        "mode": "live_like_replay",
        "data_path": str(data_path),
        "model_report": str(model_report),
        "config": asdict(config),
        "slice_gate_enabled": config.slice_allowlist_path is not None,
        "slice_allowlist_path": config.slice_allowlist_path,
        "slice_key_fields": list(config.slice_key_fields),
        **summary_metrics(result["trades"]),
        "signals": len(result["signals"]),
        "fills": len([fill for fill in result["fills"] if not fill.blocked]),
        "blocked_orders": len([fill for fill in result["fills"] if fill.blocked]),
        "open_positions_settled": result["open_positions_settled"],
        "open_positions_unsettled": result["open_positions_unsettled"],
    }
    _write_outputs(output, summary, result["signals"], result["fills"], result["trades"])
    return {**summary, "output_dir": str(output)}


def _run(
    dataset: BacktestDataset,
    probabilities: dict[tuple[str, str, datetime, str], float],
    config: LiveReplayConfig,
) -> dict[str, Any]:
    markets_by_snapshot = _markets_by_snapshot(dataset.markets)
    market_history = _market_history(dataset.markets)
    weather_by_key = _weather_by_key(dataset.weather)
    events = _events_by_key(dataset.events)
    settlements = {settlement.event_ticker: settlement for settlement in dataset.settlements}
    final_labels = {label.event_ticker: label for label in dataset.final_temperature_labels}
    allowed_slices = _load_slice_allowlist(config.slice_allowlist_path, config.slice_key_fields)
    positions: dict[str, ReplayPosition] = {}
    cooldown_until: dict[str, datetime] = {}
    budget_remaining: dict[str, float] = {}
    signals: list[ReplaySignal] = []
    fills: list[ReplayFill] = []
    trades: list[PaperTrade] = []
    sequence = 1

    for snapshot, markets in sorted(markets_by_snapshot.items()):
        probabilities_by_event = _probabilities_for_snapshot(markets, probabilities, snapshot)
        fills.extend(
            _exit_positions(
                positions,
                markets,
                market_history,
                probabilities_by_event,
                weather_by_key,
                settlements,
                snapshot,
                cooldown_until,
                config,
                trades,
            )
        )
        if len(positions) >= config.max_open_positions:
            continue
        candidates = _candidate_signals(
            markets,
            probabilities_by_event,
            weather_by_key,
            events,
            settlements,
            final_labels,
            snapshot,
            config,
            allowed_slices,
        )
        for signal in _select_signals(candidates, positions, config):
            target_day = signal.target_date
            requested = _contracts_for_signal(signal, config, budget_remaining)
            if requested <= 0:
                continue
            signal = ReplaySignal(
                **{
                    **asdict(signal),
                    "contracts_requested": requested,
                    "order_cost_requested": requested * signal.entry_ask,
                }
            )
            signals.append(signal)
            execution_market = _execution_market(signal, market_history, config)
            fill = _entry_fill(signal, execution_market, config, sequence)
            fills.append(fill)
            sequence += 1
            if fill.blocked or fill.filled_contracts <= 0:
                continue
            if config.daily_budget is not None:
                budget_remaining[target_day] = (
                    budget_remaining.get(
                        target_day,
                        config.daily_budget,
                    )
                    - fill.filled_contracts * fill.outcome_price
                )
            positions[signal.market_ticker] = ReplayPosition(
                order_id=fill.fill_id,
                city=signal.city,
                event_ticker=signal.event_ticker,
                market_ticker=signal.market_ticker,
                side=signal.side,
                target_date=target_day,
                entry_time_utc=fill.execution_time_utc,
                model_probability=signal.model_probability,
                entry_price=fill.outcome_price,
                edge=signal.edge,
                contracts=fill.filled_contracts,
                checkpoint=signal.checkpoint,
                bracket_type=signal.bracket_type,
            )

    open_positions_settled, open_positions_unsettled = _settle_remaining_positions(
        positions,
        settlements,
        market_history,
        trades,
    )
    return {
        "signals": signals,
        "fills": fills,
        "trades": trades,
        "open_positions_settled": open_positions_settled,
        "open_positions_unsettled": open_positions_unsettled,
    }


def _candidate_signals(
    markets: list[MarketSnapshot],
    probabilities_by_event: dict[str, dict[str, float]],
    weather_by_key: dict[tuple[str, str, datetime], WeatherSnapshot],
    events: dict[tuple[str, str], EventSnapshot],
    settlements: dict[str, Settlement],
    final_labels: dict[str, FinalTemperatureLabel],
    snapshot: datetime,
    config: LiveReplayConfig,
    allowed_slices: set[tuple[str, ...]] | None,
) -> list[ReplaySignal]:
    signals = []
    for market in markets:
        if not _entry_market_eligible(market, snapshot, events, settlements, final_labels):
            continue
        yes_probability = probabilities_by_event.get(market.event_ticker, {}).get(
            market.market_ticker
        )
        if yes_probability is None:
            continue
        weather = weather_by_key.get((market.city, market.event_ticker, market.snapshot_hour_utc))
        yes_probability = _effective_yes_probability(
            market,
            weather,
            yes_probability,
            config.observed_exclusion_margin_f,
        )
        for side, probability in (("yes", yes_probability), ("no", 1.0 - yes_probability)):
            quote = _quote(market, side)
            if quote is None:
                continue
            bid, ask, _ = quote
            if side == "yes":
                if _observed_high_excludes_yes(
                    market,
                    weather,
                    config.observed_exclusion_margin_f,
                ):
                    continue
                if not (config.min_model_probability <= probability < config.max_model_probability):
                    continue
            else:
                if not config.enable_no_trading or _observed_high_confirms_yes(market, weather):
                    continue
                if config.no_entry_mode == "disabled":
                    continue
                observed_excluded = _observed_high_excludes_yes(
                    market,
                    weather,
                    config.observed_exclusion_margin_f,
                )
                if config.no_entry_mode == "observed_only" and not observed_excluded:
                    continue
                if config.no_entry_mode not in {"observed_only", "model"}:
                    continue
                if probability < config.min_no_model_probability:
                    continue
                if ask < config.min_no_ask:
                    continue
            spread = ask - bid
            edge = probability - ask
            if spread > config.max_spread or edge < config.edge_threshold:
                continue
            if config.min_entry_price is not None and ask < config.min_entry_price:
                continue
            if config.max_entry_price is not None and ask > config.max_entry_price:
                continue
            if bid < config.min_entry_exit_bid:
                continue
            if not _has_entry_size(market, side, config):
                continue
            if not _passes_early_entry_gate(market, weather, side, ask, edge, config):
                continue
            if side == "yes" and config.max_edge is not None and edge >= config.max_edge:
                continue
            if side == "yes" and not _passes_late_day_sanity(market, weather, edge, config):
                continue
            checkpoint = _checkpoint(market.snapshot_hour_utc)
            bracket_type = _bracket_type(market)
            if not _slice_allowed(
                {
                    "city": market.city,
                    "checkpoint": checkpoint,
                    "side": side,
                    "bracket_type": bracket_type,
                },
                allowed_slices,
                config.slice_key_fields,
            ):
                continue
            signals.append(
                ReplaySignal(
                    city=market.city,
                    event_ticker=market.event_ticker,
                    market_ticker=market.market_ticker,
                    side=side,
                    target_date=market.target_date.isoformat(),
                    snapshot_hour_utc=market.snapshot_hour_utc,
                    checkpoint=checkpoint,
                    bracket_type=bracket_type,
                    model_probability=probability,
                    entry_bid=bid,
                    entry_ask=ask,
                    spread=spread,
                    edge=edge,
                    contracts_requested=0.0,
                    order_cost_requested=0.0,
                )
            )
    return signals


def _select_signals(
    signals: list[ReplaySignal],
    positions: dict[str, ReplayPosition],
    config: LiveReplayConfig,
) -> list[ReplaySignal]:
    open_events = {position.event_ticker for position in positions.values()}
    grouped: dict[str, list[ReplaySignal]] = {}
    for signal in signals:
        if signal.market_ticker in positions or signal.event_ticker in open_events:
            continue
        grouped.setdefault(signal.event_ticker, []).append(signal)
    selected = []
    for event_signals in grouped.values():
        selected.extend(
            sorted(
                event_signals,
                key=lambda signal: (signal.edge, signal.snapshot_hour_utc),
                reverse=True,
            )[: config.max_positions_per_event]
        )
    return sorted(selected, key=lambda signal: (signal.snapshot_hour_utc, signal.event_ticker))


def _entry_fill(
    signal: ReplaySignal,
    execution_market: MarketSnapshot | None,
    config: LiveReplayConfig,
    sequence: int,
) -> ReplayFill:
    fill_id = f"live-replay-{sequence:06d}"
    if execution_market is None:
        return _blocked_fill(signal, fill_id, "missing_execution_quote")
    quote = _quote(execution_market, signal.side)
    if quote is None:
        return _blocked_fill(signal, fill_id, "invalid_execution_quote")
    bid, ask, exchange_price = quote
    if ask <= 0.0 or exchange_price <= 0.0:
        return _blocked_fill(signal, fill_id, "invalid_execution_price")
    if config.block_worse_execution_price and ask > signal.entry_ask:
        return _blocked_fill(
            signal,
            fill_id,
            "execution_price_worse_than_signal",
            ask,
            exchange_price,
        )
    available = _available_size(execution_market, signal.side, "entry")
    filled = (
        min(signal.contracts_requested, available)
        if available is not None
        else signal.contracts_requested
    )
    if filled <= 0:
        return _blocked_fill(signal, fill_id, "zero_available_size", ask, exchange_price)
    return ReplayFill(
        fill_id=fill_id,
        action="buy",
        reason="entry",
        city=signal.city,
        event_ticker=signal.event_ticker,
        market_ticker=signal.market_ticker,
        side=signal.side,
        target_date=signal.target_date,
        signal_time_utc=signal.snapshot_hour_utc,
        execution_time_utc=execution_market.snapshot_hour_utc,
        model_probability=signal.model_probability,
        edge=signal.edge,
        requested_contracts=signal.contracts_requested,
        filled_contracts=filled,
        outcome_price=ask,
        exchange_price=exchange_price,
    )


def _blocked_fill(
    signal: ReplaySignal,
    fill_id: str,
    reason: str,
    outcome_price: float = 0.0,
    exchange_price: float = 0.0,
) -> ReplayFill:
    return ReplayFill(
        fill_id=fill_id,
        action="blocked_buy",
        reason="entry",
        city=signal.city,
        event_ticker=signal.event_ticker,
        market_ticker=signal.market_ticker,
        side=signal.side,
        target_date=signal.target_date,
        signal_time_utc=signal.snapshot_hour_utc,
        execution_time_utc=signal.snapshot_hour_utc,
        model_probability=signal.model_probability,
        edge=signal.edge,
        requested_contracts=signal.contracts_requested,
        filled_contracts=0.0,
        outcome_price=outcome_price,
        exchange_price=exchange_price,
        blocked=True,
        block_reason=reason,
    )


def _exit_positions(
    positions: dict[str, ReplayPosition],
    markets: list[MarketSnapshot],
    market_history: dict[tuple[str, str], list[MarketSnapshot]],
    probabilities_by_event: dict[str, dict[str, float]],
    weather_by_key: dict[tuple[str, str, datetime], WeatherSnapshot],
    settlements: dict[str, Settlement],
    snapshot: datetime,
    cooldown_until: dict[str, datetime],
    config: LiveReplayConfig,
    trades: list[PaperTrade],
) -> list[ReplayFill]:
    market_by_ticker = {market.market_ticker: market for market in markets}
    fills = []
    for ticker, position in list(positions.items()):
        market = market_by_ticker.get(ticker)
        if market is None:
            continue
        weather = weather_by_key.get((market.city, market.event_ticker, market.snapshot_hour_utc))
        yes_probability = probabilities_by_event.get(market.event_ticker, {}).get(ticker)
        if yes_probability is not None:
            yes_probability = _effective_yes_probability(
                market,
                weather,
                yes_probability,
                config.observed_exclusion_margin_f,
            )
        outcome_probability = (
            yes_probability if position.side == "yes" and yes_probability is not None else None
        )
        if position.side == "no" and yes_probability is not None:
            outcome_probability = 1.0 - yes_probability
        quote = _quote(market, position.side)
        if quote is None:
            continue
        bid, _, exchange_entry_price = quote
        outcome_bid = max(config.min_exit_price, bid)
        observed_exit = _observed_exit_for_side(
            market,
            weather,
            position.side,
            config.observed_exclusion_margin_f,
        )
        model_exit = (
            outcome_probability is not None
            and outcome_bid - outcome_probability >= config.exit_edge_threshold
        )
        settlement = settlements.get(position.event_ticker)
        if settlement is not None and settlement.settled_at_utc <= snapshot:
            _append_settled_trade(position, settlement, market_history, trades)
            del positions[ticker]
            continue
        if not observed_exit and not model_exit:
            continue
        if ticker in cooldown_until and snapshot < cooldown_until[ticker]:
            continue
        available = _available_size(market, position.side, "exit")
        filled = min(position.contracts, available) if available is not None else position.contracts
        if filled <= 0 or bid <= 0:
            cooldown_until[ticker] = snapshot + timedelta(hours=config.no_bid_exit_cooldown_hours)
            fills.append(
                ReplayFill(
                    fill_id=f"exit-{position.order_id}-{snapshot.isoformat()}",
                    action="blocked_sell",
                    reason="no_bid_exit" if observed_exit else "model_exit_no_bid",
                    city=position.city,
                    event_ticker=position.event_ticker,
                    market_ticker=position.market_ticker,
                    side=position.side,
                    target_date=position.target_date,
                    signal_time_utc=snapshot,
                    execution_time_utc=snapshot,
                    model_probability=outcome_probability,
                    edge=None,
                    requested_contracts=position.contracts,
                    filled_contracts=0.0,
                    outcome_price=outcome_bid,
                    exchange_price=_exchange_exit_price(position.side, outcome_bid),
                    blocked=True,
                    block_reason="no_exit_liquidity",
                )
            )
            continue
        reason = f"observed_high_excludes_{position.side}" if observed_exit else "model_exit_edge"
        fills.append(
            ReplayFill(
                fill_id=f"exit-{position.order_id}-{snapshot.isoformat()}",
                action="sell",
                reason=reason,
                city=position.city,
                event_ticker=position.event_ticker,
                market_ticker=position.market_ticker,
                side=position.side,
                target_date=position.target_date,
                signal_time_utc=snapshot,
                execution_time_utc=snapshot,
                model_probability=outcome_probability,
                edge=None,
                requested_contracts=position.contracts,
                filled_contracts=filled,
                outcome_price=outcome_bid,
                exchange_price=_exchange_exit_price(position.side, outcome_bid),
            )
        )
        _append_exit_trade(position, outcome_bid, market, trades)
        del positions[ticker]
    return fills


def _append_exit_trade(
    position: ReplayPosition,
    exit_price: float,
    market: MarketSnapshot,
    trades: list[PaperTrade],
) -> None:
    pnl = (exit_price - position.entry_price) * position.contracts
    trades.append(
        _paper_trade(
            position,
            winner_ticker=None,
            settlement_value=exit_price,
            pnl=pnl,
            hit=1.0 if pnl > 0 else 0.0,
            closing_mid=_outcome_midpoint(market, position.side),
        )
    )


def _append_settled_trade(
    position: ReplayPosition,
    settlement: Settlement,
    market_history: dict[tuple[str, str], list[MarketSnapshot]],
    trades: list[PaperTrade],
) -> None:
    yes_won = settlement.winner_ticker == position.market_ticker
    hit = yes_won if position.side == "yes" else not yes_won
    settlement_value = 1.0 if hit else 0.0
    pnl = (settlement_value - position.entry_price) * position.contracts
    history = market_history.get((position.event_ticker, position.market_ticker), [])
    closing = _outcome_midpoint(history[-1], position.side) if history else None
    trades.append(
        _paper_trade(
            position,
            winner_ticker=settlement.winner_ticker,
            settlement_value=settlement_value,
            pnl=pnl,
            hit=1.0 if hit else 0.0,
            closing_mid=closing,
        )
    )


def _paper_trade(
    position: ReplayPosition,
    winner_ticker: str | None,
    settlement_value: float,
    pnl: float,
    hit: float,
    closing_mid: float | None,
) -> PaperTrade:
    clv = closing_mid - position.entry_price if closing_mid is not None else None
    return PaperTrade(
        order_id=position.order_id,
        city=position.city,
        event_ticker=position.event_ticker,
        market_ticker=position.market_ticker,
        target_date=position.target_date,
        entry_time_utc=position.entry_time_utc,
        model_probability=position.model_probability,
        entry_price=position.entry_price,
        edge=position.edge,
        contracts=position.contracts,
        winner_ticker=winner_ticker,
        settlement_value=settlement_value,
        pnl=pnl,
        roi=pnl / max(1e-9, position.entry_price * position.contracts),
        hit=hit,
        closing_mid=closing_mid,
        clv=clv,
        checkpoint=position.checkpoint,
        side=position.side,
        bracket_type=position.bracket_type,
    )


def _settle_remaining_positions(
    positions: dict[str, ReplayPosition],
    settlements: dict[str, Settlement],
    market_history: dict[tuple[str, str], list[MarketSnapshot]],
    trades: list[PaperTrade],
) -> tuple[int, int]:
    settled = 0
    unsettled = 0
    for position in list(positions.values()):
        settlement = settlements.get(position.event_ticker)
        if settlement is None:
            unsettled += 1
            continue
        _append_settled_trade(position, settlement, market_history, trades)
        settled += 1
    return settled, unsettled


def _contracts_for_signal(
    signal: ReplaySignal,
    config: LiveReplayConfig,
    budget_remaining: dict[str, float],
) -> float:
    if signal.entry_ask <= 0:
        return 0.0
    if config.daily_budget is None:
        return float(_contract_cap(signal, config))
    remaining = budget_remaining.setdefault(signal.target_date, config.daily_budget)
    if remaining <= 0:
        return 0.0
    multiplier = 2.0 if config.sizing_policy == "cheap-tier" and signal.entry_ask < 0.25 else 1.0
    if config.sizing_policy == "edge-tier" and signal.edge >= 0.10:
        multiplier = 2.0
    if config.sizing_policy == "prob-tier" and signal.model_probability >= 0.35:
        multiplier = 2.0
    fraction = min(config.max_budget_fraction, config.base_budget_fraction * multiplier)
    premium = min(remaining, config.max_order_cost, config.daily_budget * fraction)
    return float(min(math.floor(premium / signal.entry_ask), _contract_cap(signal, config)))


def _contract_cap(signal: ReplaySignal, config: LiveReplayConfig) -> int:
    return (
        config.max_no_contracts_per_order if signal.side == "no" else config.max_contracts_per_order
    )


def _has_entry_size(market: MarketSnapshot, side: str, config: LiveReplayConfig) -> bool:
    ask_size = _available_size(market, side, "entry")
    bid_size = _available_size(market, side, "exit")
    if ask_size is not None and ask_size < config.min_entry_quote_size:
        return False
    if bid_size is not None and bid_size < config.min_entry_quote_size:
        return False
    return True


def _passes_early_entry_gate(
    market: MarketSnapshot,
    weather: WeatherSnapshot | None,
    side: str,
    ask: float,
    edge: float,
    config: LiveReplayConfig,
) -> bool:
    if not config.enable_early_entry_gate or side != "yes":
        return True
    hours_elapsed = _hours_elapsed(weather)
    if hours_elapsed is None or hours_elapsed >= config.mid_entry_hours_elapsed:
        return True
    required_edge = (
        config.early_entry_edge_threshold
        if hours_elapsed < config.early_entry_hours_elapsed
        else config.mid_entry_edge_threshold
    )
    if edge < required_edge:
        return False
    if config.min_entry_price is not None and ask < config.min_entry_price:
        return False
    if config.max_entry_price is not None and ask > config.max_entry_price:
        return False
    return (
        _source_confirmation_count(market, weather) >= config.early_entry_min_source_confirmations
    )


def _hours_elapsed(weather: WeatherSnapshot | None) -> float | None:
    if weather is None:
        return None
    value = _number(weather.features.get("hours_elapsed"))
    if value is None:
        value = _number(weather.features.get("hours_since_climate_start"))
    return value


def _source_confirmation_count(
    market: MarketSnapshot,
    weather: WeatherSnapshot | None,
) -> int:
    if weather is None:
        return 0
    return family_support_count(market.bracket, weather_values(weather))


def _source_values(weather: WeatherSnapshot) -> list[float | None]:
    """Legacy diagnostic values; entry gates use independent families above."""
    return [
        weather.nws_anchor_high_f,
        _number(weather.features.get("nws_hourly_window_max_f")),
        weather.hrrr_projected_high_f,
        weather.nbm_projected_high_f,
        weather.ensemble_raw_median_high_f,
    ]


def _quote(market: MarketSnapshot, side: str) -> tuple[float, float, float] | None:
    if side == "yes":
        if market.yes_bid is None or market.yes_ask is None:
            return None
        return float(market.yes_bid), float(market.yes_ask), float(market.yes_ask)
    if side == "no":
        no_bid = market.no_bid
        no_ask = market.no_ask
        if no_bid is None and market.yes_ask is not None:
            no_bid = max(0.0, 1.0 - float(market.yes_ask))
        if no_ask is None and market.yes_bid is not None:
            no_ask = max(0.0, 1.0 - float(market.yes_bid))
        if no_bid is None or no_ask is None:
            return None
        return float(no_bid), float(no_ask), _bounded_price(1.0 - float(no_ask))
    raise ValueError(f"unsupported side {side}")


def _exchange_exit_price(side: str, outcome_bid: float) -> float:
    return _bounded_price(outcome_bid if side == "yes" else 1.0 - outcome_bid)


def _bounded_price(price: float) -> float:
    return min(0.99, max(0.01, float(price)))


def _available_size(market: MarketSnapshot, side: str, action: str) -> float | None:
    if side == "yes" and action == "entry":
        return market.yes_ask_size
    if side == "yes" and action == "exit":
        return market.yes_bid_size
    if side == "no" and action == "entry":
        return market.no_ask_size
    if side == "no" and action == "exit":
        return market.no_bid_size
    return None


def _execution_market(
    signal: ReplaySignal,
    market_history: dict[tuple[str, str], list[MarketSnapshot]],
    config: LiveReplayConfig,
) -> MarketSnapshot | None:
    target = signal.snapshot_hour_utc + timedelta(hours=config.execution_lag_hours)
    history = market_history.get((signal.event_ticker, signal.market_ticker), [])
    for market in history:
        if market.snapshot_hour_utc >= target:
            return market
    return history[-1] if history and config.execution_lag_hours == 0 else None


def _entry_market_eligible(
    market: MarketSnapshot,
    snapshot: datetime,
    events: dict[tuple[str, str], EventSnapshot],
    settlements: dict[str, Settlement],
    final_labels: dict[str, FinalTemperatureLabel],
) -> bool:
    event = events.get((market.city, market.event_ticker))
    if event is not None and snapshot >= event.climate_window_end_utc:
        return False
    settlement = settlements.get(market.event_ticker)
    if settlement is not None and settlement.settled_at_utc <= snapshot:
        return False
    label = final_labels.get(market.event_ticker)
    return not (
        label is not None and label.issued_at_utc is not None and label.issued_at_utc <= snapshot
    )


def _probabilities_for_snapshot(
    markets: list[MarketSnapshot],
    probabilities: dict[tuple[str, str, datetime, str], float],
    snapshot: datetime,
) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for market in markets:
        probability = probabilities.get(
            (market.city, market.event_ticker, snapshot, market.market_ticker)
        )
        if probability is None:
            continue
        output.setdefault(market.event_ticker, {})[market.market_ticker] = probability
    return output


def _effective_yes_probability(
    market: MarketSnapshot,
    weather: WeatherSnapshot | None,
    probability: float,
    margin_f: float = 0.0,
) -> float:
    if _observed_high_excludes_yes(market, weather, margin_f):
        return 0.0
    if _observed_high_confirms_yes(market, weather):
        return 1.0
    return probability


def _observed_high_excludes_yes(
    market: MarketSnapshot,
    weather: WeatherSnapshot | None,
    margin_f: float = 0.0,
) -> bool:
    observed = weather.observed_high_so_far_f if weather is not None else None
    if observed is None or market.bracket.upper_f is None:
        return False
    return float(observed) >= float(market.bracket.upper_f) + margin_f


def _observed_high_confirms_yes(
    market: MarketSnapshot,
    weather: WeatherSnapshot | None,
) -> bool:
    observed = weather.observed_high_so_far_f if weather is not None else None
    if observed is None or market.bracket.lower_f is None or market.bracket.upper_f is not None:
        return False
    return int(float(observed) + 0.5) >= int(market.bracket.lower_f)


def _observed_exit_for_side(
    market: MarketSnapshot,
    weather: WeatherSnapshot | None,
    side: str,
    margin_f: float = 0.0,
) -> bool:
    return (
        _observed_high_excludes_yes(market, weather, margin_f)
        if side == "yes"
        else _observed_high_confirms_yes(market, weather)
    )


def _passes_late_day_sanity(
    market: MarketSnapshot,
    weather: WeatherSnapshot | None,
    edge: float,
    config: LiveReplayConfig,
) -> bool:
    if weather is None:
        return True
    observed = weather.observed_high_so_far_f
    hours_elapsed = _number(weather.features.get("hours_elapsed"))
    if hours_elapsed is None:
        hours_elapsed = _number(weather.features.get("hours_since_climate_start"))
    if (
        observed is None
        or hours_elapsed is None
        or hours_elapsed < config.late_day_min_hours_elapsed
        or market.bracket.lower_f is None
    ):
        return True
    lower = float(market.bracket.lower_f)
    if observed >= lower - config.late_day_observed_gap_block_f:
        return True
    confirmations = sum(
        1
        for value in (
            weather.nws_anchor_high_f,
            _number(weather.features.get("nws_hourly_window_max_f")),
            weather.hrrr_projected_high_f,
            weather.nbm_projected_high_f,
            weather.ensemble_raw_median_high_f,
        )
        if value is not None and value >= lower
    )
    if confirmations < config.late_day_min_source_confirmations:
        return False
    return edge >= config.edge_threshold


def _outcome_midpoint(market: MarketSnapshot, side: str) -> float | None:
    quote = _quote(market, side)
    if quote is None:
        return None
    bid, ask, _ = quote
    return (bid + ask) / 2.0


def _markets_by_snapshot(markets: list[MarketSnapshot]) -> dict[datetime, list[MarketSnapshot]]:
    output: dict[datetime, list[MarketSnapshot]] = {}
    for market in markets:
        output.setdefault(market.snapshot_hour_utc, []).append(market)
    return output


def _market_history(
    markets: list[MarketSnapshot],
) -> dict[tuple[str, str], list[MarketSnapshot]]:
    output: dict[tuple[str, str], list[MarketSnapshot]] = {}
    for market in markets:
        output.setdefault((market.event_ticker, market.market_ticker), []).append(market)
    return {
        key: sorted(value, key=lambda item: item.snapshot_hour_utc) for key, value in output.items()
    }


def _weather_by_key(
    weather: list[WeatherSnapshot],
) -> dict[tuple[str, str, datetime], WeatherSnapshot]:
    return {(row.city, row.event_ticker, row.snapshot_hour_utc): row for row in weather}


def _events_by_key(events: list[EventSnapshot]) -> dict[tuple[str, str], EventSnapshot]:
    output: dict[tuple[str, str], EventSnapshot] = {}
    for event in events:
        key = (event.city, event.event_ticker)
        current = output.get(key)
        if current is None or event.snapshot_hour_utc > current.snapshot_hour_utc:
            output[key] = event
    return output


def _load_probabilities(path: Path) -> dict[tuple[str, str, datetime, str], float]:
    distributions_path = path / "bracket_distributions.csv"
    output = {}
    with distributions_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            probabilities = ast.literal_eval(row["probabilities"])
            snapshot = datetime.fromisoformat(row["snapshot_hour_utc"])
            for market_ticker, probability in probabilities.items():
                output[
                    (
                        row["city"],
                        row["event_ticker"],
                        snapshot,
                        str(market_ticker),
                    )
                ] = float(probability)
    return output


def _write_outputs(
    output: Path,
    summary: dict[str, Any],
    signals: list[ReplaySignal],
    fills: list[ReplayFill],
    trades: list[PaperTrade],
) -> None:
    _write_dict_rows(output / "signals.csv", [asdict(row) for row in signals])
    _write_dict_rows(output / "fills.csv", [asdict(row) for row in fills])
    _write_dict_rows(output / "trades.csv", [asdict(row) for row in trades])
    _write_dict_rows(output / "daily_pnl.csv", daily_pnl_rows(trades))
    _write_dict_rows(output / "edge_buckets.csv", edge_bucket_rows(trades))
    _write_dict_rows(output / "probability_buckets.csv", probability_bucket_rows(trades))
    _write_dict_rows(output / "price_buckets.csv", price_bucket_rows(trades))
    _write_dict_rows(output / "city_metrics.csv", grouped_metric_rows(trades, "city"))
    _write_dict_rows(output / "checkpoint_metrics.csv", grouped_metric_rows(trades, "checkpoint"))
    _write_dict_rows(output / "side_metrics.csv", grouped_metric_rows(trades, "side"))
    _write_dict_rows(
        output / "bracket_type_metrics.csv",
        grouped_metric_rows(trades, "bracket_type"),
    )
    _write_dict_rows(output / "slice_metrics.csv", _slice_metric_rows(trades))
    _write_dict_rows(output / "side_edge_buckets.csv", _side_bucket_rows(trades, "edge"))
    _write_dict_rows(
        output / "side_probability_buckets.csv",
        _side_bucket_rows(trades, "model_probability"),
    )
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    _write_report(output / "live_replay_report.md", summary)


def _write_dict_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Live-Like Strategy Replay",
        "",
        f"- Trades: {summary['trades']}",
        f"- Fills: {summary['fills']}",
        f"- Blocked orders: {summary['blocked_orders']}",
        f"- Total PnL: {float(summary['total_pnl']):.4f}",
        f"- ROI: {float(summary['roi']):.4f}",
        f"- Hit rate: {float(summary['hit_rate']):.4f}",
        f"- Max drawdown: {float(summary['max_drawdown']):.4f}",
        f"- Open positions settled at end: {summary['open_positions_settled']}",
        f"- Open positions left unsettled: {summary['open_positions_unsettled']}",
        f"- Slice gate enabled: {summary['slice_gate_enabled']}",
        "",
        "This replay is intentionally stricter than the settlement-only paper backtest:",
        "it supports YES/NO sides, execution quote refresh, partial fills when quote size is "
        "present, model exits, observed-high forced exits, no-bid exit cooldowns, stale-market "
        "entry guards, and side-specific calibration outputs.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _side_bucket_rows(trades: list[PaperTrade], bucket_field: str) -> list[dict[str, Any]]:
    if bucket_field == "edge":
        rows = edge_bucket_rows
    elif bucket_field == "model_probability":
        rows = probability_bucket_rows
    else:
        raise ValueError(f"unsupported side bucket field {bucket_field}")
    output = []
    for side in ("yes", "no"):
        side_trades = [trade for trade in trades if trade.side == side]
        for row in rows(side_trades):
            output.append({"side": side, **row})
    return output


def build_slice_allowlist(
    trades_path: str | Path,
    output_path: str | Path,
    key_fields: tuple[str, ...] = ("city", "checkpoint", "side", "bracket_type"),
    min_trades: int = 2,
    min_pnl: float = 0.0,
    min_avg_clv: float = 0.0,
    min_roi: float | None = None,
    min_hit_rate: float | None = None,
) -> dict[str, Any]:
    rows = _read_trade_rows(Path(trades_path))
    grouped: dict[tuple[str, ...], list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(_row_slice_key(row, key_fields), []).append(row)
    output_rows = []
    allowed = 0
    for key, group_rows in sorted(grouped.items()):
        metrics = _slice_row_metrics(group_rows)
        is_allowed = (
            metrics["trades"] >= min_trades
            and metrics["pnl"] > min_pnl
            and metrics["avg_clv"] > min_avg_clv
            and (min_roi is None or metrics["roi"] >= min_roi)
            and (min_hit_rate is None or metrics["hit_rate"] >= min_hit_rate)
        )
        allowed += int(is_allowed)
        output_rows.append(
            {
                **{field: value for field, value in zip(key_fields, key, strict=True)},
                **metrics,
                "allowed": is_allowed,
            }
        )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_dict_rows(output, output_rows)
    return {
        "input_trades": str(trades_path),
        "output": str(output_path),
        "key_fields": list(key_fields),
        "groups": len(output_rows),
        "allowed_groups": allowed,
    }


def _slice_metric_rows(trades: list[PaperTrade]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[PaperTrade]] = {}
    for trade in trades:
        key = (trade.city, trade.checkpoint, trade.side, trade.bracket_type)
        grouped.setdefault(key, []).append(trade)
    output = []
    for key, group_trades in sorted(grouped.items()):
        row = grouped_metric_rows(group_trades, "side")[0]
        row.pop("group", None)
        output.append(
            {
                "city": key[0],
                "checkpoint": key[1],
                "side": key[2],
                "bracket_type": key[3],
                **row,
            }
        )
    return output


def _load_slice_allowlist(
    path: str | None,
    key_fields: tuple[str, ...],
) -> set[tuple[str, ...]] | None:
    if path is None:
        return None
    allowed: set[tuple[str, ...]] = set()
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("allowed", "true")).lower() not in {"1", "true", "yes"}:
                continue
            allowed.add(_row_slice_key(row, key_fields))
    return allowed


def _slice_allowed(
    values: dict[str, Any],
    allowed_slices: set[tuple[str, ...]] | None,
    key_fields: tuple[str, ...],
) -> bool:
    if allowed_slices is None:
        return True
    return tuple(str(values.get(field, "")) for field in key_fields) in allowed_slices


def _read_trade_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _row_slice_key(row: dict[str, Any], key_fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(row.get(field, "unknown") or "unknown") for field in key_fields)


def _slice_row_metrics(rows: list[dict[str, str]]) -> dict[str, Any]:
    pnl = sum(_float(row.get("pnl")) for row in rows)
    risk = sum(_float(row.get("entry_price")) * _float(row.get("contracts")) for row in rows)
    clv = [_float(row.get("clv")) for row in rows if row.get("clv") not in (None, "")]
    hits = [_float(row.get("hit")) for row in rows]
    return {
        "trades": len(rows),
        "pnl": pnl,
        "roi": pnl / max(1e-9, risk),
        "hit_rate": sum(hits) / len(hits) if hits else 0.0,
        "avg_clv": sum(clv) / len(clv) if clv else 0.0,
        "positive_clv_rate": (sum(1.0 for value in clv if value > 0) / len(clv) if clv else 0.0),
    }


def _float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def _bracket_type(market: MarketSnapshot) -> str:
    if market.bracket.lower_f is None:
        return "lower_tail"
    if market.bracket.upper_f is None:
        return "upper_tail"
    return "bounded"


def _checkpoint(snapshot: datetime) -> str:
    return f"utc_{snapshot.hour:02d}"


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None
