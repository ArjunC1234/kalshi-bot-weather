"""End-to-end paper strategy backtest."""

from __future__ import annotations

import ast
import csv
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backtest.data_sources import LocalExportSource
from backtest.load_dataset import load_dataset
from libs.models import BacktestDataset, MarketSnapshot
from strategy.fills import settle_order
from strategy.metrics import (
    daily_pnl_rows,
    edge_bucket_rows,
    grouped_metric_rows,
    price_bucket_rows,
    probability_bucket_rows,
    summary_metrics,
)
from strategy.orders import PaperOrder, PaperTrade, order_from_signal
from strategy.signals import Signal, StrategyConfig, signal_for_market


def run_backtest(
    data_path: str | Path,
    model_report: str | Path,
    output_dir: str | Path,
    config: StrategyConfig,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset(LocalExportSource(Path(data_path)))
    probabilities = _load_probabilities(Path(model_report))
    signals, orders = _generate_orders(dataset, probabilities, config)
    trades = _settle_orders(dataset, orders)
    summary = {
        "mode": "paper_strategy_backtest",
        "data_path": str(data_path),
        "model_report": str(model_report),
        "config": asdict(config),
        **summary_metrics(trades),
        "signals": len(signals),
        "orders": len(orders),
    }
    _write_outputs(output, summary, signals, orders, trades)
    return {**summary, "output_dir": str(output)}


def _generate_orders(
    dataset: BacktestDataset,
    probabilities: dict[tuple[str, str, object, str], float],
    config: StrategyConfig,
) -> tuple[list[Signal], list[PaperOrder]]:
    markets = sorted(
        dataset.markets,
        key=lambda market: (
            market.snapshot_hour_utc,
            market.city,
            market.event_ticker,
            market.market_ticker,
        ),
    )
    signals: list[Signal] = []
    eligible: list[Signal] = []
    for market in markets:
        key = (
            market.city,
            market.event_ticker,
            market.snapshot_hour_utc,
            market.market_ticker,
        )
        probability = probabilities.get(key)
        if probability is None:
            continue
        signal = signal_for_market(market, probability, config)
        signals.append(signal)
        if signal.decision != "buy_yes":
            continue
        eligible.append(signal)
    selected = _select_signals(eligible, config)
    orders = []
    budget_remaining = _daily_budget_remaining(config)
    sequence = 1
    for signal in selected:
        contracts = _contracts_for_signal(signal, config, budget_remaining)
        if contracts <= 0:
            continue
        if config.daily_budget is not None:
            budget_remaining[signal.target_date] -= contracts * float(signal.yes_ask or 0.0)
        orders.append(order_from_signal(signal, contracts, sequence))
        sequence += 1
    return signals, orders


def _select_signals(signals: list[Signal], config: StrategyConfig) -> list[Signal]:
    grouped: dict[str, list[Signal]] = {}
    for signal in signals:
        grouped.setdefault(signal.event_ticker, []).append(signal)
    selected: list[Signal] = []
    for event_ticker in sorted(grouped):
        event_signals = grouped[event_ticker]
        if config.entry_policy == "first":
            ordered = sorted(event_signals, key=lambda signal: signal.snapshot_hour_utc)
        elif config.entry_policy == "latest":
            ordered = sorted(
                event_signals,
                key=lambda signal: (signal.snapshot_hour_utc, signal.buy_edge or 0.0),
                reverse=True,
            )
        elif config.entry_policy == "best-edge":
            ordered = sorted(
                event_signals,
                key=lambda signal: (signal.buy_edge or 0.0, signal.snapshot_hour_utc),
                reverse=True,
            )
        else:
            raise ValueError(f"unknown entry_policy: {config.entry_policy}")
        selected.extend(ordered[: config.max_positions_per_event])
    return sorted(selected, key=lambda signal: (signal.snapshot_hour_utc, signal.event_ticker))


def _daily_budget_remaining(config: StrategyConfig) -> dict[str, float]:
    if config.daily_budget is None:
        return {}
    return {}


def _contracts_for_signal(
    signal: Signal,
    config: StrategyConfig,
    budget_remaining: dict[str, float],
) -> float:
    if signal.yes_ask is None:
        return 0.0
    if config.daily_budget is None:
        return _unbudgeted_contracts(signal, config)
    remaining = budget_remaining.setdefault(signal.target_date, config.daily_budget)
    if remaining <= 0:
        return 0.0
    desired_premium = config.daily_budget * _budget_fraction(signal, config)
    premium = min(remaining, desired_premium)
    return float(math.floor(premium / float(signal.yes_ask)))


def _unbudgeted_contracts(signal: Signal, config: StrategyConfig) -> float:
    multiplier = _sizing_multiplier(signal, config)
    return config.stake * multiplier


def _budget_fraction(signal: Signal, config: StrategyConfig) -> float:
    fraction = config.base_budget_fraction * _sizing_multiplier(signal, config)
    return min(config.max_budget_fraction, max(0.0, fraction))


def _sizing_multiplier(signal: Signal, config: StrategyConfig) -> float:
    if config.sizing_policy == "fixed":
        return 1.0
    if config.sizing_policy == "prob-tier":
        return 2.0 if signal.model_probability >= 0.35 else 1.0
    if config.sizing_policy == "edge-tier":
        return 2.0 if (signal.buy_edge or 0.0) >= 0.10 else 1.0
    if config.sizing_policy == "cheap-tier":
        return 2.0 if (signal.yes_ask or 1.0) < 0.25 else 1.0
    if config.sizing_policy == "fractional-kelly":
        return _fractional_kelly_multiplier(signal, config)
    raise ValueError(f"unknown sizing_policy: {config.sizing_policy}")


def _fractional_kelly_multiplier(signal: Signal, config: StrategyConfig) -> float:
    if signal.yes_ask is None:
        return 0.0
    price = float(signal.yes_ask)
    edge = max(0.0, signal.model_probability - price)
    fraction = config.kelly_fraction * (edge / max(1e-9, 1.0 - price))
    return fraction / max(1e-9, config.base_budget_fraction)


def _settle_orders(dataset: BacktestDataset, orders: list[PaperOrder]) -> list[PaperTrade]:
    settlements = {settlement.event_ticker: settlement for settlement in dataset.settlements}
    market_history = _markets_by_ticker(dataset.markets)
    return [
        settle_order(
            order,
            settlements.get(order.event_ticker),
            market_history.get((order.event_ticker, order.market_ticker), []),
        )
        for order in orders
    ]


def _load_probabilities(path: Path) -> dict[tuple[str, str, object, str], float]:
    distributions_path = path / "bracket_distributions.csv"
    output = {}
    with distributions_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            probabilities = ast.literal_eval(row["probabilities"])
            snapshot = _parse_datetime(row["snapshot_hour_utc"])
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


def _markets_by_ticker(
    markets: list[MarketSnapshot],
) -> dict[tuple[str, str], list[MarketSnapshot]]:
    output: dict[tuple[str, str], list[MarketSnapshot]] = {}
    for market in markets:
        output.setdefault((market.event_ticker, market.market_ticker), []).append(market)
    return output


def _write_outputs(
    output: Path,
    summary: dict[str, Any],
    signals: list[Signal],
    orders: list[PaperOrder],
    trades: list[PaperTrade],
) -> None:
    _write_dict_rows(output / "signals.csv", [asdict(row) for row in signals])
    _write_dict_rows(output / "orders.csv", [asdict(row) for row in orders])
    _write_dict_rows(output / "trades.csv", [asdict(row) for row in trades])
    _write_dict_rows(output / "positions.csv", [asdict(row) for row in trades])
    _write_dict_rows(output / "daily_pnl.csv", daily_pnl_rows(trades))
    edge_rows = edge_bucket_rows(trades)
    daily_rows = daily_pnl_rows(trades)
    _write_dict_rows(output / "edge_buckets.csv", edge_rows)
    _write_dict_rows(output / "probability_buckets.csv", probability_bucket_rows(trades))
    _write_dict_rows(output / "price_buckets.csv", price_bucket_rows(trades))
    _write_dict_rows(output / "city_metrics.csv", grouped_metric_rows(trades, "city"))
    _write_dict_rows(output / "checkpoint_metrics.csv", grouped_metric_rows(trades, "checkpoint"))
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    _write_report(output / "strategy_report.md", summary)
    _write_charts(output, daily_rows, edge_rows)


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Paper Strategy Backtest",
        "",
        f"- Trades: {summary['trades']}",
        f"- Total contracts: {float(summary['total_contracts']):.4f}",
        f"- Total premium risk: {float(summary['total_risk']):.4f}",
        f"- Total PnL: {float(summary['total_pnl']):.4f}",
        f"- ROI: {float(summary['roi']):.4f}",
        f"- Hit rate: {float(summary['hit_rate']):.4f}",
        f"- Max drawdown: {float(summary['max_drawdown']):.4f}",
        f"- Mean CLV: {_format_optional(summary.get('mean_clv'))}",
        f"- Positive CLV rate: {_format_optional(summary.get('positive_clv_rate'))}",
        "",
        "This is a paper-only conservative ask-fill backtest. It is not a live execution report.",
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


def _write_charts(
    output: Path,
    daily_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
) -> None:
    try:
        import matplotlib
    except ImportError:
        return
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    charts = output / "charts"
    charts.mkdir(exist_ok=True)
    if daily_rows:
        dates = [row["target_date"] for row in daily_rows]
        cumulative = [float(row["cumulative_pnl"]) for row in daily_rows]
        peaks = []
        peak = 0.0
        for value in cumulative:
            peak = max(peak, value)
            peaks.append(peak)
        drawdowns = [value - peak for value, peak in zip(cumulative, peaks, strict=True)]
        fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
        axes[0].plot(dates, cumulative, marker="o")
        axes[0].set_title("Paper Strategy Equity Curve")
        axes[0].set_ylabel("Cumulative PnL")
        axes[1].bar(dates, drawdowns)
        axes[1].set_title("Drawdown")
        axes[1].set_ylabel("PnL from peak")
        axes[1].tick_params(axis="x", rotation=35)
        fig.tight_layout()
        fig.savefig(charts / "equity_drawdown.png", dpi=180)
        plt.close(fig)
    if edge_rows:
        rows = [row for row in edge_rows if int(row["trades"]) > 0]
        if rows:
            labels = [row["group"] for row in rows]
            fig, axes = plt.subplots(1, 2, figsize=(12, 4))
            axes[0].bar(labels, [float(row["pnl"]) for row in rows])
            axes[0].set_title("PnL by Edge Bucket")
            axes[0].tick_params(axis="x", rotation=35)
            axes[1].bar(labels, [float(row["hit_rate"]) for row in rows])
            axes[1].set_title("Hit Rate by Edge Bucket")
            axes[1].set_ylim(0, 1)
            axes[1].tick_params(axis="x", rotation=35)
            fig.tight_layout()
            fig.savefig(charts / "edge_buckets.png", dpi=180)
            plt.close(fig)


def _parse_datetime(value: str):
    from libs.time_utils import parse_datetime

    return parse_datetime(value)


def _format_optional(value: Any) -> str:
    return "" if value is None else f"{float(value):.4f}"
