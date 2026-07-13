"""Paper strategy metrics."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from strategy.orders import PaperTrade

EDGE_BUCKETS = (
    (0.00, 0.03, "0.00-0.03"),
    (0.03, 0.05, "0.03-0.05"),
    (0.05, 0.08, "0.05-0.08"),
    (0.08, 0.12, "0.08-0.12"),
    (0.12, float("inf"), "0.12+"),
)


def summary_metrics(trades: list[PaperTrade]) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "total_pnl": 0.0,
            "roi": 0.0,
            "hit_rate": 0.0,
            "max_drawdown": 0.0,
            "mean_clv": None,
            "positive_clv_rate": None,
        }
    risk = sum(trade.entry_price * trade.contracts for trade in trades)
    clv_values = [trade.clv for trade in trades if trade.clv is not None]
    return {
        "trades": len(trades),
        "total_pnl": sum(trade.pnl for trade in trades),
        "roi": sum(trade.pnl for trade in trades) / max(1e-9, risk),
        "hit_rate": mean(trade.hit for trade in trades),
        "average_entry_price": mean(trade.entry_price for trade in trades),
        "average_model_probability": mean(trade.model_probability for trade in trades),
        "average_edge": mean(trade.edge for trade in trades),
        "max_drawdown": max_drawdown(trades),
        "mean_clv": mean(clv_values) if clv_values else None,
        "median_clv": _median(clv_values) if clv_values else None,
        "positive_clv_rate": (
            mean(1.0 if value > 0 else 0.0 for value in clv_values) if clv_values else None
        ),
    }


def daily_pnl_rows(trades: list[PaperTrade]) -> list[dict[str, Any]]:
    grouped: dict[str, list[PaperTrade]] = defaultdict(list)
    for trade in trades:
        grouped[trade.target_date].append(trade)
    cumulative = 0.0
    rows = []
    for day, day_trades in sorted(grouped.items()):
        pnl = sum(trade.pnl for trade in day_trades)
        cumulative += pnl
        rows.append(
            {
                "target_date": day,
                "trades": len(day_trades),
                "pnl": pnl,
                "cumulative_pnl": cumulative,
                "hit_rate": mean(trade.hit for trade in day_trades),
            }
        )
    return rows


def grouped_metric_rows(trades: list[PaperTrade], group_name: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[PaperTrade]] = defaultdict(list)
    for trade in trades:
        grouped[str(getattr(trade, group_name))].append(trade)
    return [
        _trade_group_row(group, group_trades)
        for group, group_trades in sorted(grouped.items())
    ]


def edge_bucket_rows(trades: list[PaperTrade]) -> list[dict[str, Any]]:
    grouped: dict[str, list[PaperTrade]] = {label: [] for _, _, label in EDGE_BUCKETS}
    for trade in trades:
        grouped[edge_bucket(trade.edge)].append(trade)
    return [_trade_group_row(bucket, bucket_trades) for bucket, bucket_trades in grouped.items()]


def edge_bucket(edge: float) -> str:
    for low, high, label in EDGE_BUCKETS:
        if low <= edge < high:
            return label
    return "unknown"


def max_drawdown(trades: list[PaperTrade]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for trade in sorted(trades, key=lambda item: item.entry_time_utc):
        equity += trade.pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _trade_group_row(group: str, trades: list[PaperTrade]) -> dict[str, Any]:
    if not trades:
        return {
            "group": group,
            "trades": 0,
            "pnl": 0.0,
            "roi": 0.0,
            "hit_rate": "",
            "avg_edge": "",
            "avg_model_probability": "",
            "realized_win_rate": "",
            "avg_clv": "",
        }
    risk = sum(trade.entry_price * trade.contracts for trade in trades)
    clv_values = [trade.clv for trade in trades if trade.clv is not None]
    return {
        "group": group,
        "trades": len(trades),
        "pnl": sum(trade.pnl for trade in trades),
        "roi": sum(trade.pnl for trade in trades) / max(1e-9, risk),
        "hit_rate": mean(trade.hit for trade in trades),
        "avg_edge": mean(trade.edge for trade in trades),
        "avg_model_probability": mean(trade.model_probability for trade in trades),
        "realized_win_rate": mean(trade.hit for trade in trades),
        "avg_entry_price": mean(trade.entry_price for trade in trades),
        "avg_clv": mean(clv_values) if clv_values else "",
        "positive_clv_rate": (
            mean(1.0 if value > 0 else 0.0 for value in clv_values) if clv_values else ""
        ),
    }


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
