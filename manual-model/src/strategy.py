from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .manual_v1 import bracket_probability


@dataclass(frozen=True)
class StrategyConfig:
    min_edge: float = 0.03
    max_spread: float = 0.10
    min_entry_price: float = 0.35
    max_entry_price: float = 0.65
    market_weight: float = 0.35
    daily_budget: float = 40.0
    max_order_cost: float = 3.0
    contracts_per_trade: int = 5


def generate_trades(
    predictions: pd.DataFrame,
    markets: pd.DataFrame,
    settlements: pd.DataFrame,
    config: StrategyConfig,
) -> pd.DataFrame:
    pred_cols = [
        "city",
        "event_ticker",
        "target_date",
        "snapshot_time_utc",
        "checkpoint",
        "expected_high_f",
        "sigma_f",
    ]
    rows = markets.merge(predictions[pred_cols], on=["city", "event_ticker", "target_date", "snapshot_time_utc"], how="inner")
    rows = rows.merge(settlements[["event_ticker", "winner_ticker"]], on="event_ticker", how="left")
    candidates = []
    for _, row in rows.iterrows():
        yes_probability = bracket_probability(
            float(row["expected_high_f"]),
            float(row["sigma_f"]),
            row.get("bracket_lower_f"),
            row.get("bracket_upper_f"),
        )
        market_probability = _finite(row.get("normalized_market_midpoint_probability"))
        if market_probability is not None:
            weight = min(max(config.market_weight, 0.0), 1.0)
            yes_probability = (1.0 - weight) * yes_probability + weight * market_probability
        for side in ("yes", "no"):
            entry = _finite(row.get(f"{side}_ask_dollars"))
            opposite = _finite(row.get("no_bid_dollars" if side == "yes" else "yes_bid_dollars"))
            if entry is None or opposite is None:
                continue
            spread = entry - opposite
            model_probability = yes_probability if side == "yes" else 1.0 - yes_probability
            edge = model_probability - entry
            if spread > config.max_spread:
                continue
            if entry < config.min_entry_price or entry > config.max_entry_price:
                continue
            if edge < config.min_edge:
                continue
            candidates.append(
                {
                    "target_date": row["target_date"],
                    "city": row["city"],
                    "checkpoint": row["checkpoint"],
                    "snapshot_time_utc": row["snapshot_time_utc"],
                    "event_ticker": row["event_ticker"],
                    "market_ticker": row["market_ticker"],
                    "winner_ticker": row["winner_ticker"],
                    "side": side,
                    "entry_price": entry,
                    "model_probability": model_probability,
                    "edge": edge,
                    "expected_high_f": row["expected_high_f"],
                    "sigma_f": row["sigma_f"],
                }
            )
    if not candidates:
        return pd.DataFrame()
    candidate_frame = pd.DataFrame(candidates)
    selected = []
    for (_, event_ticker), group in candidate_frame.groupby(["target_date", "event_ticker"], sort=True):
        selected.append(group.sort_values(["edge", "model_probability"], ascending=False).iloc[0].to_dict())
    selected_frame = pd.DataFrame(selected).sort_values(["target_date", "snapshot_time_utc", "event_ticker"])
    return _apply_budget_and_settle(selected_frame, config)


def summarize_trades(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {
            "trades": 0,
            "total_risk": 0.0,
            "total_pnl": 0.0,
            "roi": 0.0,
            "hit_rate": 0.0,
        }
    risk = float((trades["entry_price"] * trades["contracts"]).sum())
    pnl = float(trades["pnl"].sum())
    return {
        "trades": int(len(trades)),
        "total_risk": risk,
        "total_pnl": pnl,
        "roi": pnl / risk if risk else 0.0,
        "hit_rate": float(trades["hit"].mean()),
    }


def daily_pnl(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["target_date", "trades", "pnl", "cumulative_pnl", "hit_rate"])
    daily = trades.groupby("target_date").agg(
        trades=("pnl", "size"),
        pnl=("pnl", "sum"),
        hit_rate=("hit", "mean"),
    ).reset_index()
    daily["cumulative_pnl"] = daily["pnl"].cumsum()
    return daily


def _apply_budget_and_settle(candidates: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    rows = []
    budget_remaining: dict[Any, float] = {}
    for _, row in candidates.iterrows():
        day = row["target_date"]
        budget_remaining.setdefault(day, config.daily_budget)
        contracts = min(config.contracts_per_trade, int(config.max_order_cost // float(row["entry_price"])))
        if contracts <= 0:
            continue
        cost = contracts * float(row["entry_price"])
        if cost > budget_remaining[day]:
            continue
        budget_remaining[day] -= cost
        settlement_value = 1.0 if _won(row) else 0.0
        pnl = contracts * (settlement_value - float(row["entry_price"]))
        output = row.to_dict()
        output.update(
            {
                "contracts": float(contracts),
                "settlement_value": settlement_value,
                "hit": 1.0 if pnl > 0 else 0.0,
                "pnl": pnl,
                "roi": pnl / cost if cost else 0.0,
            }
        )
        rows.append(output)
    return pd.DataFrame(rows)


def _won(row: pd.Series) -> bool:
    market_won = str(row["market_ticker"]) == str(row["winner_ticker"])
    return market_won if row["side"] == "yes" else not market_won


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed):
        return None
    return parsed
