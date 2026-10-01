"""Neuralcaster EV strategy backtest."""

from __future__ import annotations

import ast
import csv
import json
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from backtest.data_sources import LocalExportSource
from backtest.load_dataset import load_dataset
from libs.models import BacktestDataset, MarketSnapshot, Settlement, WeatherSnapshot
from libs.settlement_policy import POST_SETTLEMENT_SYSTEM_START
from libs.time_utils import parse_datetime
from strategy.metrics import (
    daily_pnl_rows,
    edge_bucket_rows,
    grouped_metric_rows,
    price_bucket_rows,
    probability_bucket_rows,
    summary_metrics,
)
from strategy.orders import PaperOrder, PaperTrade


@dataclass(frozen=True)
class NeuralEvConfig:
    start_date: str | None = None
    end_date: str | None = None
    min_target_date: str | None = POST_SETTLEMENT_SYSTEM_START
    min_ev: float = 0.03
    max_ev: float | None = None
    max_spread: float = 0.15
    min_entry_price: float = 0.02
    max_entry_price: float = 0.80
    daily_budget: float = 40.0
    max_order_cost: float = 3.0
    max_contracts_per_order: int = 20
    max_no_contracts_per_order: int = 10
    max_positions_per_event: int = 1
    allow_yes: bool = True
    allow_no: bool = True
    entry_policy: str = "best-ev"
    min_hours_elapsed: float | None = None


@dataclass(frozen=True)
class EvSignal:
    city: str
    event_ticker: str
    market_ticker: str
    target_date: str
    snapshot_hour_utc: Any
    hours_elapsed: float | None
    side: str
    model_probability: float
    entry_price: float | None
    opposite_bid: float | None
    market_mid: float | None
    spread: float | None
    ev: float | None
    decision: str
    skip_reason: str
    bracket_type: str


@dataclass(frozen=True)
class NeuralLearnedGateConfig(NeuralEvConfig):
    train_start: str | None = None
    train_end: str | None = None
    test_start: str | None = None
    test_end: str | None = None
    gate_model_type: str = "hist_gradient_boosting"
    min_training_examples: int = 400
    min_training_dates: int = 5
    min_predicted_reward: float = 0.0
    min_trade_probability: float = 0.55
    selection_score: str = "predicted_reward"
    gate_application: str = "fixed_gate_veto"
    target: str = "reward"


@dataclass(frozen=True)
class LearnedGateCandidate:
    signal: EvSignal
    features: dict[str, Any]
    reward: float | None
    positive_reward: int | None
    positive_clv: int | None
    shorted_winner: int | None
    winner_ticker: str | None
    closing_mid: float | None
    clv: float | None
    final_high_f: float | None
    selected: bool = False
    learned_decision: str = "unscored"
    learned_skip_reason: str = ""
    predicted_reward: float | None = None
    trade_probability: float | None = None
    model_mode: str = "unscored"
    training_examples: int = 0
    training_dates: int = 0


def run_neural_ev_backtest(
    data_path: str | Path,
    model_report: str | Path,
    output_dir: str | Path,
    config: NeuralEvConfig,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset(LocalExportSource(Path(data_path)))
    probabilities = _load_probabilities(Path(model_report))
    signals, orders = _generate_orders(dataset, probabilities, config)
    trades = _settle_orders(dataset, orders)
    summary = {
        "mode": "neuralcaster_ev_strategy",
        "data_path": str(data_path),
        "model_report": str(model_report),
        "config": asdict(config),
        "signals": len(signals),
        "orders": len(orders),
        **summary_metrics(trades),
    }
    _write_outputs(output, summary, signals, orders, trades)
    return {**summary, "output_dir": str(output)}


def run_neural_ev_validation_fixed_window(
    data_path: str | Path,
    model_report: str | Path,
    output_dir: str | Path,
    base_config: NeuralEvConfig,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    min_validation_trades: int = 5,
    validation_objective: str = "robust",
    min_validation_positive_clv: float = 0.50,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset(LocalExportSource(Path(data_path)))
    probabilities = _load_probabilities(Path(model_report))
    train_start = _effective_window_start(train_start, base_config.min_target_date)
    test_start = _effective_window_start(test_start, base_config.min_target_date)
    if train_start > train_end:
        raise ValueError("validation window contains no post-settlement-system target dates")
    if test_start > test_end:
        raise ValueError("test window contains no post-settlement-system target dates")
    sweep_rows: list[dict[str, Any]] = []
    best_row: dict[str, Any] | None = None
    best_config: NeuralEvConfig | None = None
    for candidate in _ev_policy_grid(base_config, train_start, train_end):
        signals, orders = _generate_orders(dataset, probabilities, candidate)
        trades = _settle_orders(dataset, orders)
        metrics = summary_metrics(trades)
        row = {
            "train_start": train_start,
            "train_end": train_end,
            "side_mode": _side_mode(candidate),
            **asdict(candidate),
            **metrics,
        }
        sweep_rows.append(row)
        if not _passes_validation_constraints(
            metrics,
            validation_objective=validation_objective,
            min_validation_trades=min_validation_trades,
            min_validation_positive_clv=min_validation_positive_clv,
        ):
            continue
        candidate_key = _validation_sort_key(row, validation_objective)
        best_key = _validation_sort_key(best_row, validation_objective) if best_row else None
        if best_key is None or candidate_key > best_key:
            best_row = row
            best_config = candidate
    if best_config is None:
        summary = {
            "mode": "neuralcaster_ev_validation_fixed_window",
            "data_path": str(data_path),
            "model_report": str(model_report),
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
            "min_validation_trades": min_validation_trades,
            "validation_objective": validation_objective,
            "min_validation_positive_clv": min_validation_positive_clv,
            "selected_validation": None,
            "selection_status": "abstained_no_validation_policy_passed",
            "config": asdict(base_config),
            "signals": 0,
            "orders": 0,
            **summary_metrics([]),
        }
        _write_outputs(output, summary, [], [], [])
        _write_dict_rows(output / "validation_policy_sweep.csv", sweep_rows)
        return {**summary, "output_dir": str(output)}
    test_config = replace(best_config, start_date=test_start, end_date=test_end)
    signals, orders = _generate_orders(dataset, probabilities, test_config)
    trades = _settle_orders(dataset, orders)
    summary = {
        "mode": "neuralcaster_ev_validation_fixed_window",
        "data_path": str(data_path),
        "model_report": str(model_report),
        "train_start": train_start,
        "train_end": train_end,
        "test_start": test_start,
        "test_end": test_end,
        "min_validation_trades": min_validation_trades,
        "validation_objective": validation_objective,
        "min_validation_positive_clv": min_validation_positive_clv,
        "selected_validation": best_row,
        "selection_status": "selected",
        "config": asdict(test_config),
        "signals": len(signals),
        "orders": len(orders),
        **summary_metrics(trades),
    }
    _write_outputs(output, summary, signals, orders, trades)
    _write_dict_rows(output / "validation_policy_sweep.csv", sweep_rows)
    return {**summary, "output_dir": str(output)}


def run_neural_ev_learned_gate(
    data_path: str | Path,
    model_report: str | Path,
    output_dir: str | Path,
    config: NeuralLearnedGateConfig,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if config.train_start is None or config.train_end is None:
        raise ValueError("learned gate requires train_start and train_end")
    if config.test_start is None or config.test_end is None:
        raise ValueError("learned gate requires test_start and test_end")
    effective_train_start = _effective_window_start(config.train_start, config.min_target_date)
    effective_test_start = _effective_window_start(config.test_start, config.min_target_date)
    if effective_train_start > config.train_end:
        raise ValueError(
            "learned gate train window contains no post-settlement-system target dates"
        )
    if effective_test_start > config.test_end:
        raise ValueError("learned gate test window contains no post-settlement-system target dates")
    config = replace(
        config,
        train_start=effective_train_start,
        test_start=effective_test_start,
    )
    dataset = load_dataset(LocalExportSource(Path(data_path)))
    probabilities = _load_probabilities(Path(model_report))
    model_predictions = _load_temperature_predictions(Path(model_report))
    candidate_config = replace(
        config,
        start_date=config.train_start,
        end_date=config.test_end,
    )
    candidates = _generate_learned_gate_candidates(
        dataset,
        probabilities,
        model_predictions,
        candidate_config,
    )
    baseline_all_signals, baseline_all_orders = _generate_orders(
        dataset,
        probabilities,
        replace(
            config,
            start_date=config.train_start,
            end_date=config.test_end,
        ),
    )
    baseline_order_keys = {_order_identity(order) for order in baseline_all_orders}
    all_scored: list[LearnedGateCandidate] = []
    selected_signals: list[EvSignal] = []
    diagnostics: list[dict[str, Any]] = []
    test_dates = _date_range(config.test_start, config.test_end)
    for target_day in test_dates:
        train_end = min(_parse_iso_date(config.train_end), target_day - timedelta(days=1))
        train_candidates = [
            row
            for row in candidates
            if row.reward is not None
            and row.signal.target_date >= config.train_start
            and row.signal.target_date <= train_end.isoformat()
            and row.signal.target_date < target_day.isoformat()
            and row.signal.decision == "buy"
            and (
                config.gate_application != "fixed_gate_veto"
                or _signal_identity(row.signal) in baseline_order_keys
            )
        ]
        day_candidates = [
            row
            for row in candidates
            if row.signal.target_date == target_day.isoformat()
            and row.signal.decision == "buy"
        ]
        model = _fit_learned_gate(train_candidates, config)
        scored = _score_learned_gate_candidates(day_candidates, model, config)
        selected = _select_learned_gate_signals(scored, config, baseline_order_keys)
        selected_ids = {
            (
                signal.city,
                signal.event_ticker,
                signal.market_ticker,
                signal.snapshot_hour_utc,
                signal.side,
            )
            for signal in selected
        }
        for row in scored:
            key = (
                row.signal.city,
                row.signal.event_ticker,
                row.signal.market_ticker,
                row.signal.snapshot_hour_utc,
                row.signal.side,
            )
            all_scored.append(replace(row, selected=key in selected_ids))
        selected_signals.extend(selected)
        diagnostics.append(
            {
                "target_date": target_day.isoformat(),
                "training_examples": model["training_examples"],
                "training_dates": model["training_dates"],
                "model_mode": model["model_mode"],
                "candidate_count": len(day_candidates),
                "selected_count": len(selected),
                "mean_training_reward": model["mean_training_reward"],
                "positive_training_rate": model["positive_training_rate"],
            }
        )
    selected_signal_keys = {_signal_identity(signal) for signal in selected_signals}
    baseline_orders = [
        order
        for order in baseline_all_orders
        if order.target_date >= config.test_start
        and order.target_date <= config.test_end
    ]
    if config.gate_application == "fixed_gate_veto":
        orders = [
            replace(order, order_id=f"neural-gate-{index:06d}")
            for index, order in enumerate(
                (
                    order
                    for order in baseline_orders
                    if _order_identity(order) in selected_signal_keys
                ),
                start=1,
            )
        ]
    else:
        orders = _orders_from_signals(selected_signals, config, prefix="neural-gate")
    trades = _settle_orders(dataset, orders)
    baseline_trades = _settle_orders(dataset, baseline_orders)
    summary = {
        "mode": "neuralcaster_ev_learned_gate",
        "data_path": str(data_path),
        "model_report": str(model_report),
        "config": asdict(config),
        "train_start": config.train_start,
        "train_end": config.train_end,
        "test_start": config.test_start,
        "test_end": config.test_end,
        "candidate_count": len(candidates),
        "scored_candidates": len(all_scored),
        "signals": len(all_scored),
        "orders": len(orders),
        "baseline_fixed_gate": {
            "signals": len(
                [
                    signal
                    for signal in baseline_all_signals
                    if signal.target_date >= config.test_start
                    and signal.target_date <= config.test_end
                ]
            ),
            "orders": len(baseline_orders),
            **summary_metrics(baseline_trades),
        },
        **summary_metrics(trades),
    }
    _write_outputs(output, summary, [row.signal for row in all_scored], orders, trades)
    _write_dict_rows(output / "learned_candidates.csv", [_candidate_row(row) for row in all_scored])
    _write_dict_rows(output / "learned_gate_diagnostics.csv", diagnostics)
    _write_dict_rows(output / "baseline_trades.csv", [asdict(row) for row in baseline_trades])
    _write_learned_gate_report(output / "learned_gate_report.md", summary)
    return {**summary, "output_dir": str(output)}


def _generate_orders(
    dataset: BacktestDataset,
    probabilities: dict[tuple[str, str, Any, str], float],
    config: NeuralEvConfig,
) -> tuple[list[EvSignal], list[PaperOrder]]:
    signals = []
    eligible = []
    event_lookup = _event_lookup(dataset)
    for market in sorted(
        dataset.markets,
        key=lambda item: (
            item.snapshot_hour_utc,
            item.city,
            item.event_ticker,
            item.market_ticker,
        ),
    ):
        if not _within_date_window(market.target_date.isoformat(), config):
            continue
        probability = probabilities.get(
            (market.city, market.event_ticker, market.snapshot_hour_utc, market.market_ticker)
        )
        if probability is None:
            continue
        hours_elapsed = _hours_elapsed(market, event_lookup)
        for signal in _signals_for_market(market, probability, config, hours_elapsed):
            signals.append(signal)
            if signal.decision == "buy":
                eligible.append(signal)
    selected = _select_signals(eligible, config)
    budget_remaining: dict[str, float] = {}
    orders = []
    sequence = 1
    for signal in selected:
        contracts = _contracts_for_signal(signal, config, budget_remaining)
        if contracts <= 0:
            continue
        budget_remaining[signal.target_date] = (
            budget_remaining.setdefault(signal.target_date, config.daily_budget)
            - contracts * float(signal.entry_price or 0.0)
        )
        orders.append(_order_from_signal(signal, contracts, sequence))
        sequence += 1
    return signals, orders


def _signals_for_market(
    market: MarketSnapshot,
    yes_probability: float,
    config: NeuralEvConfig,
    hours_elapsed: float | None,
) -> list[EvSignal]:
    output = []
    if config.allow_yes:
        output.append(
            _signal(
                market,
                side="yes",
                model_probability=yes_probability,
                entry_price=market.yes_ask,
                opposite_bid=market.yes_bid,
                config=config,
                hours_elapsed=hours_elapsed,
            )
        )
    if config.allow_no:
        output.append(
            _signal(
                market,
                side="no",
                model_probability=1.0 - yes_probability,
                entry_price=_no_ask(market),
                opposite_bid=_no_bid(market),
                config=config,
                hours_elapsed=hours_elapsed,
            )
        )
    return output


def _signal(
    market: MarketSnapshot,
    side: str,
    model_probability: float,
    entry_price: float | None,
    opposite_bid: float | None,
    config: NeuralEvConfig,
    hours_elapsed: float | None,
) -> EvSignal:
    spread = (
        float(entry_price) - float(opposite_bid)
        if entry_price is not None and opposite_bid is not None
        else None
    )
    ev = float(model_probability) - float(entry_price) if entry_price is not None else None
    decision, reason = _decision(entry_price, spread, ev, config, hours_elapsed)
    return EvSignal(
        city=market.city,
        event_ticker=market.event_ticker,
        market_ticker=market.market_ticker,
        target_date=market.target_date.isoformat(),
        snapshot_hour_utc=market.snapshot_hour_utc,
        hours_elapsed=hours_elapsed,
        side=side,
        model_probability=float(model_probability),
        entry_price=entry_price,
        opposite_bid=opposite_bid,
        market_mid=_side_midpoint(entry_price, opposite_bid),
        spread=spread,
        ev=ev,
        decision=decision,
        skip_reason=reason,
        bracket_type=_bracket_type(market),
    )


def _decision(
    entry_price: float | None,
    spread: float | None,
    ev: float | None,
    config: NeuralEvConfig,
    hours_elapsed: float | None,
) -> tuple[str, str]:
    if config.min_hours_elapsed is not None:
        if hours_elapsed is None:
            return "skip", "missing_hours_elapsed"
        if hours_elapsed < config.min_hours_elapsed:
            return "skip", "before_min_hours_elapsed"
    if entry_price is None:
        return "skip", "missing_ask"
    if spread is None:
        return "skip", "missing_spread"
    if spread > config.max_spread:
        return "skip", "spread_too_wide"
    if entry_price < config.min_entry_price or entry_price > config.max_entry_price:
        return "skip", "price_out_of_range"
    if ev is None or ev < config.min_ev:
        return "skip", "ev_below_threshold"
    if config.max_ev is not None and ev > config.max_ev:
        return "skip", "ev_above_threshold"
    return "buy", ""


def _select_signals(signals: list[EvSignal], config: NeuralEvConfig) -> list[EvSignal]:
    grouped: dict[str, list[EvSignal]] = {}
    for signal in signals:
        grouped.setdefault(signal.event_ticker, []).append(signal)
    selected = []
    for event_ticker in sorted(grouped):
        ordered = sorted(
            grouped[event_ticker],
            key=lambda item: (
                item.ev or -999.0,
                item.model_probability,
                item.snapshot_hour_utc,
            ),
            reverse=True,
        )
        if config.entry_policy == "first":
            ordered = sorted(ordered, key=lambda item: item.snapshot_hour_utc)
        elif config.entry_policy == "latest":
            ordered = sorted(ordered, key=lambda item: item.snapshot_hour_utc, reverse=True)
        elif config.entry_policy != "best-ev":
            raise ValueError(f"unknown entry_policy: {config.entry_policy}")
        selected.extend(ordered[: config.max_positions_per_event])
    return sorted(selected, key=lambda item: (item.snapshot_hour_utc, item.event_ticker))


def _within_date_window(target_date: str, config: NeuralEvConfig) -> bool:
    if config.min_target_date is not None and target_date < config.min_target_date:
        return False
    if config.start_date is not None and target_date < config.start_date:
        return False
    if config.end_date is not None and target_date > config.end_date:
        return False
    return True


def _effective_window_start(start_date: str, min_target_date: str | None) -> str:
    if min_target_date is None:
        return start_date
    return max(start_date, min_target_date)


def _ev_policy_grid(
    base_config: NeuralEvConfig,
    start_date: str,
    end_date: str,
) -> list[NeuralEvConfig]:
    candidates = []
    hour_gates = (
        (base_config.min_hours_elapsed,)
        if base_config.min_hours_elapsed is not None
        else (None, 6.0, 10.0, 14.0)
    )
    for side_mode in ("all", "yes_only", "no_only"):
        for min_hours_elapsed in hour_gates:
            for min_ev in (0.02, 0.05, 0.08, 0.12):
                for max_ev in (None, 0.08, 0.12, 0.18):
                    if max_ev is not None and min_ev > max_ev:
                        continue
                    for max_spread in (0.10, 0.15):
                        for min_entry_price in (0.35, 0.50, 0.55, 0.60):
                            for max_entry_price in (0.60, 0.65, 0.80):
                                if min_entry_price > max_entry_price:
                                    continue
                                candidates.append(
                                    replace(
                                        base_config,
                                        start_date=start_date,
                                        end_date=end_date,
                                        min_ev=min_ev,
                                        max_ev=max_ev,
                                        max_spread=max_spread,
                                        min_entry_price=min_entry_price,
                                        max_entry_price=max_entry_price,
                                        min_hours_elapsed=min_hours_elapsed,
                                        allow_yes=side_mode != "no_only",
                                        allow_no=side_mode != "yes_only",
                                    )
                                )
    return candidates


def _side_mode(config: NeuralEvConfig) -> str:
    if config.allow_yes and config.allow_no:
        return "all"
    if config.allow_yes:
        return "yes_only"
    if config.allow_no:
        return "no_only"
    return "disabled"


def _validation_sort_key(row: dict[str, Any], objective: str = "robust") -> tuple[float, ...]:
    if objective == "pnl":
        return (
            float(row.get("total_pnl") or 0.0),
            float(row.get("roi") or 0.0),
            int(row.get("trades") or 0),
            float(row.get("hit_rate") or 0.0),
        )
    if objective == "hit_rate":
        trades = int(row.get("trades") or 0)
        hit_rate = float(row.get("hit_rate") or 0.0)
        positive_clv_rate = float(row.get("positive_clv_rate") or 0.0)
        total_pnl = float(row.get("total_pnl") or 0.0)
        risk = float(row.get("total_risk") or 0.0)
        drawdown = abs(float(row.get("max_drawdown") or 0.0))
        pnl_per_risk = total_pnl / max(1e-9, risk)
        return (
            hit_rate,
            positive_clv_rate,
            pnl_per_risk,
            -drawdown,
            min(trades, 60),
            total_pnl,
        )
    if objective != "robust":
        raise ValueError(f"unknown validation objective: {objective}")
    trades = int(row.get("trades") or 0)
    hit_rate = float(row.get("hit_rate") or 0.0)
    positive_clv_rate = float(row.get("positive_clv_rate") or 0.0)
    total_pnl = float(row.get("total_pnl") or 0.0)
    risk = float(row.get("total_risk") or 0.0)
    drawdown = abs(float(row.get("max_drawdown") or 0.0))
    pnl_per_risk = total_pnl / max(1e-9, risk)
    hit_lcb = _wilson_lower_bound(hit_rate, trades)
    clv_lcb = _wilson_lower_bound(positive_clv_rate, trades)
    drawdown_penalty = drawdown / max(1.0, abs(total_pnl) + risk)
    return (
        hit_lcb,
        clv_lcb,
        pnl_per_risk - drawdown_penalty,
        min(trades, 60),
        total_pnl,
    )


def _passes_validation_constraints(
    metrics: dict[str, Any],
    *,
    validation_objective: str,
    min_validation_trades: int,
    min_validation_positive_clv: float,
) -> bool:
    if int(metrics["trades"]) < min_validation_trades:
        return False
    if (
        metrics.get("positive_clv_rate") is not None
        and float(metrics["positive_clv_rate"]) < min_validation_positive_clv
    ):
        return False
    if validation_objective == "hit_rate" and float(metrics.get("total_pnl") or 0.0) <= 0.0:
        return False
    return True


def _wilson_lower_bound(rate: float, n: int, z: float = 1.28) -> float:
    if n <= 0:
        return 0.0
    denominator = 1.0 + z * z / n
    center = rate + z * z / (2.0 * n)
    margin = z * ((rate * (1.0 - rate) + z * z / (4.0 * n)) / n) ** 0.5
    return (center - margin) / denominator


def _contracts_for_signal(
    signal: EvSignal,
    config: NeuralEvConfig,
    budget_remaining: dict[str, float],
) -> float:
    if signal.entry_price is None:
        return 0.0
    remaining = budget_remaining.setdefault(signal.target_date, config.daily_budget)
    premium = min(remaining, config.max_order_cost)
    cap = (
        config.max_no_contracts_per_order
        if signal.side == "no"
        else config.max_contracts_per_order
    )
    return float(max(0, min(cap, int(premium // max(0.01, signal.entry_price)))))


def _order_from_signal(signal: EvSignal, contracts: float, sequence: int) -> PaperOrder:
    if signal.entry_price is None or signal.ev is None:
        raise ValueError("EV order requires entry price and EV")
    return PaperOrder(
        order_id=f"neural-ev-{sequence:06d}",
        side=f"buy_{signal.side}",
        city=signal.city,
        event_ticker=signal.event_ticker,
        market_ticker=signal.market_ticker,
        target_date=signal.target_date,
        snapshot_hour_utc=signal.snapshot_hour_utc,
        model_probability=signal.model_probability,
        entry_price=float(signal.entry_price),
        edge=float(signal.ev),
        contracts=contracts,
        hours_elapsed=signal.hours_elapsed,
    )


def _settle_orders(dataset: BacktestDataset, orders: list[PaperOrder]) -> list[PaperTrade]:
    settlements = {settlement.event_ticker: settlement for settlement in dataset.settlements}
    market_history = _markets_by_ticker(dataset.markets)
    trades = []
    for order in orders:
        settlement = settlements.get(order.event_ticker)
        markets = market_history.get((order.event_ticker, order.market_ticker), [])
        trades.append(_settle_order(order, settlement, markets))
    return trades


def _event_lookup(dataset: BacktestDataset):
    return {
        (event.city, event.event_ticker, event.snapshot_hour_utc): event
        for event in dataset.events
    }


def _hours_elapsed(market: MarketSnapshot, event_lookup) -> float | None:
    event = event_lookup.get((market.city, market.event_ticker, market.snapshot_hour_utc))
    if event is None:
        return None
    return (market.snapshot_hour_utc - event.climate_window_start_utc).total_seconds() / 3600.0


def _settle_order(
    order: PaperOrder,
    settlement: Settlement | None,
    markets: list[MarketSnapshot],
) -> PaperTrade:
    side = order.side.removeprefix("buy_")
    winner_ticker = settlement.winner_ticker if settlement is not None else None
    yes_hit = winner_ticker == order.market_ticker
    hit = False if winner_ticker is None else (yes_hit if side == "yes" else not yes_hit)
    settlement_value = 1.0 if hit else 0.0
    pnl = (settlement_value - order.entry_price) * order.contracts
    closing_mid = _closing_mid(markets, side)
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
        roi=pnl / max(1e-9, order.entry_price * order.contracts),
        hit=1.0 if hit else 0.0,
        closing_mid=closing_mid,
        clv=closing_mid - order.entry_price if closing_mid is not None else None,
        checkpoint=f"utc_{order.snapshot_hour_utc.hour:02d}",
        side=side,
        bracket_type=_bracket_type(markets[0]) if markets else "unknown",
        hours_elapsed=order.hours_elapsed,
    )


_LEARNED_GATE_CATEGORICAL_FEATURES = ["city", "side", "checkpoint", "bracket_type"]
_LEARNED_GATE_NUMERIC_FEATURES = [
    "model_probability",
    "raw_ev",
    "entry_price",
    "opposite_bid",
    "market_mid",
    "spread",
    "side_market_probability",
    "hours_elapsed",
    "hour_utc",
    "target_day_of_year",
    "bracket_lower_f",
    "bracket_upper_f",
    "bracket_width_f",
    "is_lower_tail",
    "is_upper_tail",
    "is_tail",
    "expected_high_f",
    "q05",
    "q25",
    "q50",
    "q75",
    "q95",
    "q90_width_f",
    "q50_width_f",
    "expected_distance_outside_contract_f",
    "q05_distance_outside_contract_f",
    "q95_distance_outside_contract_f",
    "expected_inside_contract",
    "q_band_overlaps_contract",
    "nws_anchor_high_f",
    "hrrr_projected_high_f",
    "nbm_projected_high_f",
    "ensemble_raw_median_high_f",
    "observed_high_so_far_f",
    "source_range_f",
    "source_std_f",
    "hrrr_nbm_gap_f",
    "hrrr_nws_gap_f",
    "nbm_nws_gap_f",
    "ensemble_nbm_gap_f",
    "any_source_inside_contract",
    "any_source_near_contract_1f",
    "any_source_near_contract_2f",
    "hrrr_remaining_forecast_high_f",
    "nbm_remaining_forecast_high_f",
    "hrrr_full_window_high_f",
    "nbm_full_window_high_f",
    "nws_hourly_window_max_f",
]


def _generate_learned_gate_candidates(
    dataset: BacktestDataset,
    probabilities: dict[tuple[str, str, Any, str], float],
    model_predictions: dict[tuple[str, str, Any], dict[str, float]],
    config: NeuralEvConfig,
) -> list[LearnedGateCandidate]:
    event_lookup = _event_lookup(dataset)
    weather_lookup = _weather_lookup(dataset)
    settlement_lookup = {row.event_ticker: row for row in dataset.settlements}
    market_history = _markets_by_ticker(dataset.markets)
    candidates: list[LearnedGateCandidate] = []
    for market in sorted(
        dataset.markets,
        key=lambda item: (
            item.snapshot_hour_utc,
            item.city,
            item.event_ticker,
            item.market_ticker,
        ),
    ):
        if not _within_date_window(market.target_date.isoformat(), config):
            continue
        probability = probabilities.get(
            (market.city, market.event_ticker, market.snapshot_hour_utc, market.market_ticker)
        )
        if probability is None:
            continue
        prediction = model_predictions.get(
            (market.city, market.event_ticker, market.snapshot_hour_utc)
        )
        hours_elapsed = _hours_elapsed(market, event_lookup)
        weather = weather_lookup.get((market.city, market.event_ticker, market.snapshot_hour_utc))
        for signal in _signals_for_market(market, probability, config, hours_elapsed):
            if signal.decision != "buy":
                continue
            candidates.append(
                _candidate_from_signal(
                    signal,
                    market,
                    settlement_lookup.get(market.event_ticker),
                    market_history.get((market.event_ticker, market.market_ticker), []),
                    weather,
                    prediction,
                )
            )
    return candidates


def _candidate_from_signal(
    signal: EvSignal,
    market: MarketSnapshot,
    settlement: Settlement | None,
    markets: list[MarketSnapshot],
    weather: WeatherSnapshot | None,
    prediction: dict[str, float] | None,
) -> LearnedGateCandidate:
    side = signal.side
    winner_ticker = settlement.winner_ticker if settlement is not None else None
    yes_hit = winner_ticker == signal.market_ticker
    hit = None if winner_ticker is None else (yes_hit if side == "yes" else not yes_hit)
    settlement_value = None if hit is None else (1.0 if hit else 0.0)
    reward = (
        None
        if settlement_value is None or signal.entry_price is None
        else settlement_value - float(signal.entry_price)
    )
    closing_mid = _closing_mid(markets, side)
    clv = (
        None
        if closing_mid is None or signal.entry_price is None
        else closing_mid - float(signal.entry_price)
    )
    final_high_f = settlement.settlement_temperature_f if settlement is not None else None
    shorted_winner = (
        None
        if winner_ticker is None
        else int(side == "no" and winner_ticker == signal.market_ticker)
    )
    features = _learned_gate_features(signal, market, weather, prediction, final_high_f)
    return LearnedGateCandidate(
        signal=signal,
        features=features,
        reward=reward,
        positive_reward=None if reward is None else int(reward > 0.0),
        positive_clv=None if clv is None else int(clv > 0.0),
        shorted_winner=shorted_winner,
        winner_ticker=winner_ticker,
        closing_mid=closing_mid,
        clv=clv,
        final_high_f=final_high_f,
    )


def _learned_gate_features(
    signal: EvSignal,
    market: MarketSnapshot,
    weather: WeatherSnapshot | None,
    prediction: dict[str, float] | None,
    final_high_f: float | None,
) -> dict[str, Any]:
    forecast_values = _weather_source_values(weather)
    expected_high = _prediction_value(prediction, "expected_high_f")
    q05 = _prediction_value(prediction, "q05")
    q25 = _prediction_value(prediction, "q25")
    q50 = _prediction_value(prediction, "q50")
    q75 = _prediction_value(prediction, "q75")
    q95 = _prediction_value(prediction, "q95")
    source_range = _range_or_none(forecast_values)
    hrrr = _finite(weather.hrrr_projected_high_f if weather is not None else None)
    nbm = _finite(weather.nbm_projected_high_f if weather is not None else None)
    nws = _finite(weather.nws_anchor_high_f if weather is not None else None)
    ensemble = _finite(weather.ensemble_raw_median_high_f if weather is not None else None)
    features = {
        "city": signal.city,
        "side": signal.side,
        "checkpoint": f"utc_{signal.snapshot_hour_utc.hour:02d}",
        "bracket_type": signal.bracket_type,
        "model_probability": signal.model_probability,
        "raw_ev": signal.ev,
        "entry_price": signal.entry_price,
        "opposite_bid": signal.opposite_bid,
        "market_mid": signal.market_mid,
        "spread": signal.spread,
        "side_market_probability": _side_market_probability(market, signal.side),
        "hours_elapsed": signal.hours_elapsed,
        "hour_utc": signal.snapshot_hour_utc.hour,
        "target_day_of_year": signal.snapshot_hour_utc.timetuple().tm_yday,
        "bracket_lower_f": market.bracket.lower_f,
        "bracket_upper_f": market.bracket.upper_f,
        "bracket_width_f": _bracket_width(market),
        "is_lower_tail": int(market.bracket.lower_f is None),
        "is_upper_tail": int(market.bracket.upper_f is None),
        "is_tail": int(market.bracket.lower_f is None or market.bracket.upper_f is None),
        "expected_high_f": expected_high,
        "q05": q05,
        "q25": q25,
        "q50": q50,
        "q75": q75,
        "q95": q95,
        "q90_width_f": _sub_or_none(q95, q05),
        "q50_width_f": _sub_or_none(q75, q25),
        "expected_distance_outside_contract_f": _distance_outside_contract(market, expected_high),
        "q05_distance_outside_contract_f": _distance_outside_contract(market, q05),
        "q95_distance_outside_contract_f": _distance_outside_contract(market, q95),
        "expected_inside_contract": _inside_contract_flag(market, expected_high),
        "q_band_overlaps_contract": _intervals_overlap_flag(
            market.bracket.lower_f,
            market.bracket.upper_f,
            q05,
            q95,
        ),
        "nws_anchor_high_f": nws,
        "hrrr_projected_high_f": hrrr,
        "nbm_projected_high_f": nbm,
        "ensemble_raw_median_high_f": ensemble,
        "observed_high_so_far_f": _finite(
            weather.observed_high_so_far_f if weather is not None else None
        ),
        "source_range_f": source_range,
        "source_std_f": _std_or_none(forecast_values),
        "hrrr_nbm_gap_f": _abs_sub_or_none(hrrr, nbm),
        "hrrr_nws_gap_f": _abs_sub_or_none(hrrr, nws),
        "nbm_nws_gap_f": _abs_sub_or_none(nbm, nws),
        "ensemble_nbm_gap_f": _abs_sub_or_none(ensemble, nbm),
        "any_source_inside_contract": int(
            any(_inside_contract(market, value) for value in forecast_values)
        ),
        "any_source_near_contract_1f": int(
            any(_distance_outside_contract(market, value) <= 1.0 for value in forecast_values)
        ),
        "any_source_near_contract_2f": int(
            any(_distance_outside_contract(market, value) <= 2.0 for value in forecast_values)
        ),
        "final_high_f": final_high_f,
    }
    if weather is not None:
        for key in (
            "hrrr_remaining_forecast_high_f",
            "nbm_remaining_forecast_high_f",
            "hrrr_full_window_high_f",
            "nbm_full_window_high_f",
            "nws_hourly_window_max_f",
        ):
            features[key] = _finite(weather.features.get(key))
    return features


def _load_probabilities(path: Path) -> dict[tuple[str, str, Any, str], float]:
    output = {}
    with (path / "bracket_distributions.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            probabilities = ast.literal_eval(row["probabilities"])
            snapshot = parse_datetime(row["snapshot_hour_utc"])
            for market_ticker, probability in probabilities.items():
                output[(row["city"], row["event_ticker"], snapshot, str(market_ticker))] = float(
                    probability
                )
    return output


def _load_temperature_predictions(path: Path) -> dict[tuple[str, str, Any], dict[str, float]]:
    output: dict[tuple[str, str, Any], dict[str, float]] = {}
    predictions_path = path / "predictions.csv"
    if not predictions_path.exists():
        return output
    with predictions_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            snapshot = parse_datetime(row["snapshot_hour_utc"])
            output[(row["city"], row["event_ticker"], snapshot)] = {
                "expected_high_f": _finite(row.get("expected_high_f")),
                "q05": _finite(row.get("q05")),
                "q25": _finite(row.get("q25")),
                "q50": _finite(row.get("q50")),
                "q75": _finite(row.get("q75")),
                "q95": _finite(row.get("q95")),
            }
    return output


def _fit_learned_gate(
    candidates: list[LearnedGateCandidate],
    config: NeuralLearnedGateConfig,
) -> dict[str, Any]:
    training_dates = sorted({row.signal.target_date for row in candidates})
    rewards = [float(row.reward) for row in candidates if row.reward is not None]
    positives = [int(row.positive_reward or 0) for row in candidates if row.reward is not None]
    if (
        len(candidates) < config.min_training_examples
        or len(training_dates) < config.min_training_dates
        or not rewards
    ):
        return {
            "model_mode": "fallback_raw_ev",
            "reward_model": None,
            "probability_model": None,
            "training_examples": len(candidates),
            "training_dates": len(training_dates),
            "mean_training_reward": _mean_or_none(rewards),
            "positive_training_rate": _mean_or_none(positives),
            "feature_columns": [],
        }
    frame = _candidate_frame(candidates)
    feature_columns = _LEARNED_GATE_NUMERIC_FEATURES + _LEARNED_GATE_CATEGORICAL_FEATURES
    reward_model = _gate_reward_model(config.gate_model_type)
    reward_model.fit(frame, rewards)
    probability_model = None
    if len(set(positives)) > 1:
        probability_model = _gate_probability_model()
        probability_model.fit(frame, positives)
    return {
        "model_mode": f"trained_{config.gate_model_type}",
        "reward_model": reward_model,
        "probability_model": probability_model,
        "training_examples": len(candidates),
        "training_dates": len(training_dates),
        "mean_training_reward": _mean_or_none(rewards),
        "positive_training_rate": _mean_or_none(positives),
        "feature_columns": feature_columns,
    }


def _score_learned_gate_candidates(
    candidates: list[LearnedGateCandidate],
    model: dict[str, Any],
    config: NeuralLearnedGateConfig,
) -> list[LearnedGateCandidate]:
    if not candidates:
        return []
    reward_model = model["reward_model"]
    probability_model = model["probability_model"]
    if reward_model is None:
        return [
            _score_candidate_with_values(
                row,
                predicted_reward=float(row.signal.ev or 0.0),
                trade_probability=1.0 if float(row.signal.ev or 0.0) > 0 else 0.0,
                model=model,
                config=config,
            )
            for row in candidates
        ]
    frame = _candidate_frame(candidates, model["feature_columns"])
    predicted_rewards = [float(value) for value in reward_model.predict(frame)]
    if probability_model is not None:
        probabilities = [float(value) for value in probability_model.predict_proba(frame)[:, 1]]
    else:
        probabilities = [1.0 if value > 0 else 0.0 for value in predicted_rewards]
    return [
        _score_candidate_with_values(
            row,
            predicted_reward=predicted_reward,
            trade_probability=trade_probability,
            model=model,
            config=config,
        )
        for row, predicted_reward, trade_probability in zip(
            candidates,
            predicted_rewards,
            probabilities,
            strict=True,
        )
    ]


def _score_candidate_with_values(
    candidate: LearnedGateCandidate,
    predicted_reward: float,
    trade_probability: float,
    model: dict[str, Any],
    config: NeuralLearnedGateConfig,
) -> LearnedGateCandidate:
    if predicted_reward < config.min_predicted_reward:
        decision = "skip"
        reason = "predicted_reward_below_threshold"
    elif trade_probability < config.min_trade_probability:
        decision = "skip"
        reason = "trade_probability_below_threshold"
    else:
        decision = "buy"
        reason = ""
    return replace(
        candidate,
        learned_decision=decision,
        learned_skip_reason=reason,
        predicted_reward=predicted_reward,
        trade_probability=trade_probability,
        model_mode=model["model_mode"],
        training_examples=model["training_examples"],
        training_dates=model["training_dates"],
    )


def _select_learned_gate_signals(
    candidates: list[LearnedGateCandidate],
    config: NeuralLearnedGateConfig,
    baseline_signal_keys: set[tuple[str, str, str, Any, str]] | None = None,
) -> list[EvSignal]:
    eligible = [row for row in candidates if row.learned_decision == "buy"]
    if config.gate_application == "fixed_gate_veto":
        if baseline_signal_keys is None:
            baseline_signal_keys = set()
        eligible = [row for row in eligible if _signal_identity(row.signal) in baseline_signal_keys]
    elif config.gate_application != "rerank_candidates":
        raise ValueError(f"unknown gate_application: {config.gate_application}")
    grouped: dict[str, list[LearnedGateCandidate]] = {}
    for candidate in eligible:
        grouped.setdefault(candidate.signal.event_ticker, []).append(candidate)
    selected: list[EvSignal] = []
    for event_ticker in sorted(grouped):
        ordered = sorted(
            grouped[event_ticker],
            key=lambda item: (
                _selection_score(item, config),
                item.trade_probability or 0.0,
                item.signal.ev or -999.0,
                item.signal.snapshot_hour_utc,
            ),
            reverse=True,
        )
        if config.entry_policy == "first":
            ordered = sorted(ordered, key=lambda item: item.signal.snapshot_hour_utc)
        elif config.entry_policy == "latest":
            ordered = sorted(ordered, key=lambda item: item.signal.snapshot_hour_utc, reverse=True)
        elif config.entry_policy != "best-ev":
            raise ValueError(f"unknown entry_policy: {config.entry_policy}")
        selected.extend(row.signal for row in ordered[: config.max_positions_per_event])
    return sorted(selected, key=lambda item: (item.snapshot_hour_utc, item.event_ticker))


def _signal_identity(signal: EvSignal) -> tuple[str, str, str, Any, str]:
    return (
        signal.city,
        signal.event_ticker,
        signal.market_ticker,
        signal.snapshot_hour_utc,
        signal.side,
    )


def _order_identity(order: PaperOrder) -> tuple[str, str, str, Any, str]:
    return (
        order.city,
        order.event_ticker,
        order.market_ticker,
        order.snapshot_hour_utc,
        order.side.replace("buy_", ""),
    )


def _selection_score(candidate: LearnedGateCandidate, config: NeuralLearnedGateConfig) -> float:
    if config.selection_score == "trade_probability":
        return float(candidate.trade_probability or 0.0)
    if config.selection_score != "predicted_reward":
        raise ValueError(f"unknown learned gate selection score: {config.selection_score}")
    return float(candidate.predicted_reward or -999.0)


def _orders_from_signals(
    signals: list[EvSignal],
    config: NeuralEvConfig,
    prefix: str = "neural-ev",
) -> list[PaperOrder]:
    budget_remaining: dict[str, float] = {}
    orders = []
    sequence = 1
    for signal in signals:
        contracts = _contracts_for_signal(signal, config, budget_remaining)
        if contracts <= 0:
            continue
        budget_remaining[signal.target_date] = (
            budget_remaining.setdefault(signal.target_date, config.daily_budget)
            - contracts * float(signal.entry_price or 0.0)
        )
        order = _order_from_signal(signal, contracts, sequence)
        orders.append(replace(order, order_id=f"{prefix}-{sequence:06d}"))
        sequence += 1
    return orders


def _gate_reward_model(model_type: str) -> Pipeline:
    if model_type == "ridge":
        estimator = Ridge(alpha=2.0)
    elif model_type == "hist_gradient_boosting":
        estimator = HistGradientBoostingRegressor(
            max_iter=80,
            learning_rate=0.05,
            l2_regularization=0.02,
            min_samples_leaf=20,
            random_state=29,
        )
    else:
        raise ValueError(f"unknown learned gate model type: {model_type}")
    return Pipeline(
        [
            ("features", _feature_transformer(scale_numeric=model_type == "ridge")),
            ("model", estimator),
        ]
    )


def _gate_probability_model() -> Pipeline:
    return Pipeline(
        [
            ("features", _feature_transformer(scale_numeric=True)),
            (
                "model",
                LogisticRegression(
                    max_iter=1000,
                    class_weight="balanced",
                    solver="lbfgs",
                ),
            ),
        ]
    )


def _feature_transformer(scale_numeric: bool) -> ColumnTransformer:
    numeric_steps: list[tuple[str, Any]] = [
        ("imputer", SimpleImputer(strategy="median", keep_empty_features=True))
    ]
    if scale_numeric:
        numeric_steps.append(("scaler", StandardScaler()))
    numeric_pipeline = Pipeline(numeric_steps)
    categorical_pipeline = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]
    )
    return ColumnTransformer(
        [
            ("numeric", numeric_pipeline, _LEARNED_GATE_NUMERIC_FEATURES),
            ("categorical", categorical_pipeline, _LEARNED_GATE_CATEGORICAL_FEATURES),
        ],
        remainder="drop",
    )


def _candidate_frame(
    candidates: list[LearnedGateCandidate],
    columns: list[str] | None = None,
) -> pd.DataFrame:
    active_columns = columns or (
        _LEARNED_GATE_NUMERIC_FEATURES + _LEARNED_GATE_CATEGORICAL_FEATURES
    )
    rows = []
    for candidate in candidates:
        row = {column: candidate.features.get(column) for column in active_columns}
        for column in _LEARNED_GATE_CATEGORICAL_FEATURES:
            if column in row and row[column] in (None, ""):
                row[column] = "unknown"
        rows.append(row)
    return pd.DataFrame(rows, columns=active_columns)


def _candidate_row(candidate: LearnedGateCandidate) -> dict[str, Any]:
    signal = candidate.signal
    return {
        "target_date": signal.target_date,
        "snapshot_hour_utc": signal.snapshot_hour_utc.isoformat(),
        "city": signal.city,
        "event_ticker": signal.event_ticker,
        "market_ticker": signal.market_ticker,
        "side": signal.side,
        "bracket_type": signal.bracket_type,
        "entry_price": signal.entry_price,
        "spread": signal.spread,
        "raw_ev": signal.ev,
        "model_probability": signal.model_probability,
        "predicted_reward": candidate.predicted_reward,
        "trade_probability": candidate.trade_probability,
        "learned_decision": candidate.learned_decision,
        "learned_skip_reason": candidate.learned_skip_reason,
        "selected": candidate.selected,
        "model_mode": candidate.model_mode,
        "training_examples": candidate.training_examples,
        "training_dates": candidate.training_dates,
        "reward": candidate.reward,
        "positive_reward": candidate.positive_reward,
        "positive_clv": candidate.positive_clv,
        "shorted_winner": candidate.shorted_winner,
        "winner_ticker": candidate.winner_ticker,
        "closing_mid": candidate.closing_mid,
        "clv": candidate.clv,
        **candidate.features,
    }


def _weather_lookup(
    dataset: BacktestDataset,
) -> dict[tuple[str, str, Any], WeatherSnapshot]:
    return {
        (row.city, row.event_ticker, row.snapshot_hour_utc): row
        for row in dataset.weather
    }


def _date_range(start: str, end: str) -> list[date]:
    start_date = _parse_iso_date(start)
    end_date = _parse_iso_date(end)
    if start_date > end_date:
        raise ValueError("start date must be on or before end date")
    days = []
    current = start_date
    while current <= end_date:
        days.append(current)
        current += timedelta(days=1)
    return days


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _prediction_value(prediction: dict[str, float] | None, key: str) -> float | None:
    if prediction is None:
        return None
    return _finite(prediction.get(key))


def _weather_source_values(weather: WeatherSnapshot | None) -> list[float]:
    if weather is None:
        return []
    return [
        value
        for value in [
            _finite(weather.nws_anchor_high_f),
            _finite(weather.hrrr_projected_high_f),
            _finite(weather.nbm_projected_high_f),
            _finite(weather.ensemble_raw_median_high_f),
        ]
        if value is not None
    ]


def _side_market_probability(market: MarketSnapshot, side: str) -> float | None:
    probability = _finite(market.normalized_market_midpoint_probability)
    if probability is None:
        return None
    if side == "no":
        return 1.0 - probability
    return probability


def _bracket_width(market: MarketSnapshot) -> float | None:
    if market.bracket.lower_f is None or market.bracket.upper_f is None:
        return None
    return float(market.bracket.upper_f - market.bracket.lower_f)


def _distance_outside_contract(market: MarketSnapshot, value: float | None) -> float | None:
    if value is None:
        return None
    lower = market.bracket.lower_f
    upper = market.bracket.upper_f
    if lower is not None and value < lower:
        return float(lower - value)
    if upper is not None and value > upper:
        return float(value - upper)
    return 0.0


def _inside_contract(market: MarketSnapshot, value: float | None) -> bool:
    if value is None:
        return False
    lower = market.bracket.lower_f
    upper = market.bracket.upper_f
    if lower is not None and value < lower:
        return False
    if upper is not None and value > upper:
        return False
    return True


def _inside_contract_flag(market: MarketSnapshot, value: float | None) -> int | None:
    if value is None:
        return None
    return int(_inside_contract(market, value))


def _intervals_overlap_flag(
    contract_lower: int | None,
    contract_upper: int | None,
    value_lower: float | None,
    value_upper: float | None,
) -> int | None:
    if value_lower is None or value_upper is None:
        return None
    lower = float("-inf") if contract_lower is None else float(contract_lower)
    upper = float("inf") if contract_upper is None else float(contract_upper)
    return int(max(lower, value_lower) <= min(upper, value_upper))


def _finite(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _range_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return max(values) - min(values)


def _std_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    avg = sum(values) / len(values)
    return (sum((value - avg) ** 2 for value in values) / len(values)) ** 0.5


def _sub_or_none(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _abs_sub_or_none(left: float | None, right: float | None) -> float | None:
    value = _sub_or_none(left, right)
    return None if value is None else abs(value)


def _mean_or_none(values: list[float] | list[int]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))


def _write_outputs(
    output: Path,
    summary: dict[str, Any],
    signals: list[EvSignal],
    orders: list[PaperOrder],
    trades: list[PaperTrade],
) -> None:
    _write_dict_rows(output / "signals.csv", [asdict(row) for row in signals])
    _write_dict_rows(output / "orders.csv", [asdict(row) for row in orders])
    _write_dict_rows(output / "trades.csv", [asdict(row) for row in trades])
    _write_dict_rows(output / "positions.csv", [asdict(row) for row in trades])
    _write_dict_rows(output / "daily_pnl.csv", daily_pnl_rows(trades))
    _write_dict_rows(output / "edge_buckets.csv", edge_bucket_rows(trades))
    _write_dict_rows(output / "probability_buckets.csv", probability_bucket_rows(trades))
    _write_dict_rows(output / "price_buckets.csv", price_bucket_rows(trades))
    _write_dict_rows(output / "city_metrics.csv", grouped_metric_rows(trades, "city"))
    _write_dict_rows(output / "side_metrics.csv", grouped_metric_rows(trades, "side"))
    _write_dict_rows(output / "checkpoint_metrics.csv", grouped_metric_rows(trades, "checkpoint"))
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    _write_report(output / "strategy_report.md", summary)


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Neuralcaster EV Strategy",
        "",
        f"- Start date: {summary['config'].get('start_date') or 'all'}",
        f"- End date: {summary['config'].get('end_date') or 'all'}",
        f"- Trades: {summary['trades']}",
        f"- Total contracts: {float(summary['total_contracts']):.4f}",
        f"- Total risk: {float(summary['total_risk']):.4f}",
        f"- Total PnL: {float(summary['total_pnl']):.4f}",
        f"- ROI: {float(summary['roi']):.4f}",
        f"- Hit rate: {float(summary['hit_rate']):.4f}",
        f"- Max drawdown: {float(summary['max_drawdown']):.4f}",
        "",
        "This is a paper-only EV backtest from Neuralcaster bracket probabilities.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_learned_gate_report(path: Path, summary: dict[str, Any]) -> None:
    baseline = summary.get("baseline_fixed_gate", {})
    lines = [
        "# Neuralcaster EV Learned Gate",
        "",
        f"- Train window: {summary.get('train_start')} through {summary.get('train_end')}",
        f"- Test window: {summary.get('test_start')} through {summary.get('test_end')}",
        f"- Gate model: {summary['config'].get('gate_model_type')}",
        f"- Candidates scored: {summary.get('scored_candidates', 0)}",
        "",
        "## Learned Gate Result",
        "",
        f"- Trades: {summary['trades']}",
        f"- Total risk: {float(summary['total_risk']):.4f}",
        f"- Total PnL: {float(summary['total_pnl']):.4f}",
        f"- ROI: {float(summary['roi']):.4f}",
        f"- Hit rate: {float(summary['hit_rate']):.4f}",
        f"- Max drawdown: {float(summary['max_drawdown']):.4f}",
        "",
        "## Baseline Fixed Gate",
        "",
        f"- Trades: {baseline.get('trades', 0)}",
        f"- Total risk: {float(baseline.get('total_risk') or 0.0):.4f}",
        f"- Total PnL: {float(baseline.get('total_pnl') or 0.0):.4f}",
        f"- ROI: {float(baseline.get('roi') or 0.0):.4f}",
        f"- Hit rate: {float(baseline.get('hit_rate') or 0.0):.4f}",
        "",
        (
            "This is a paper-only learned gate over Neuralcaster EV candidates. "
            "The gate trains only on target dates before each scored test date."
        ),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_dict_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _markets_by_ticker(
    markets: list[MarketSnapshot],
) -> dict[tuple[str, str], list[MarketSnapshot]]:
    output: dict[tuple[str, str], list[MarketSnapshot]] = {}
    for market in markets:
        output.setdefault((market.event_ticker, market.market_ticker), []).append(market)
    return output


def _closing_mid(markets: list[MarketSnapshot], side: str) -> float | None:
    for market in sorted(markets, key=lambda item: item.snapshot_hour_utc, reverse=True):
        if side == "no":
            midpoint = _side_midpoint(_no_ask(market), _no_bid(market))
        else:
            midpoint = _side_midpoint(market.yes_ask, market.yes_bid)
        if midpoint is not None:
            return midpoint
    return None


def _side_midpoint(ask: float | None, bid: float | None) -> float | None:
    if ask is not None and bid is not None:
        return (float(ask) + float(bid)) / 2.0
    return None


def _no_ask(market: MarketSnapshot) -> float | None:
    if market.no_ask is not None:
        return float(market.no_ask)
    if market.yes_bid is not None:
        return max(0.0, 1.0 - float(market.yes_bid))
    return None


def _no_bid(market: MarketSnapshot) -> float | None:
    if market.no_bid is not None:
        return float(market.no_bid)
    if market.yes_ask is not None:
        return max(0.0, 1.0 - float(market.yes_ask))
    return None


def _bracket_type(market: MarketSnapshot) -> str:
    if market.bracket.lower_f is None:
        return "lower_tail"
    if market.bracket.upper_f is None:
        return "upper_tail"
    return "bounded"
