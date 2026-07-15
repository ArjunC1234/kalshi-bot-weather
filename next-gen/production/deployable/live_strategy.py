"""Autonomous demo-trading strategy runtime for Kalshi weather markets."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
from kalshi_client import KalshiApiError, KalshiTradingClient
from psycopg.rows import dict_row
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from libs.source_families import family_at_or_above_count

FEATURE_COLUMNS = [
    "expected_high_f",
    "source_std_f",
    "source_range_f",
    "observed_high_so_far_f",
    "hours_elapsed",
    "hours_remaining",
    "bracket_index",
    "bracket_width_f",
    "bracket_center_f",
    "distance_to_center_f",
    "distance_to_lower_f",
    "distance_to_upper_f",
    "expected_inside_bracket",
    "market_implied_probability",
    "city",
    "checkpoint",
    "is_open_low",
    "is_open_high",
]
CATEGORICAL_FEATURES = ["city", "checkpoint", "is_open_low", "is_open_high"]
NUMERIC_FEATURES = [column for column in FEATURE_COLUMNS if column not in CATEGORICAL_FEATURES]


@dataclass(frozen=True)
class RuntimeConfig:
    database_url: str
    kalshi_base_url: str
    kalshi_api_key_id: str
    kalshi_private_key_path: str
    trade_enabled: bool = False
    kill_switch: bool = False
    max_data_age_minutes: int = 150
    account_budget_fraction: float = 0.20
    max_daily_budget: float = 40.0
    max_order_cost: float = 3.0
    max_open_positions: int = 12
    edge_threshold: float = 0.08
    max_edge: float = 0.12
    min_model_probability: float = 0.20
    max_model_probability: float = 0.50
    max_spread: float = 0.15
    enable_no_trading: bool = True
    no_entry_mode: str = "observed_only"
    min_no_model_probability: float = 0.75
    min_no_ask: float = 0.05
    max_contracts_per_order: int = 20
    max_no_contracts_per_order: int = 10
    min_entry_exit_bid: float = 0.01
    observed_exclusion_margin_f: float = 1.0
    enable_city_residual: bool = False
    base_budget_fraction: float = 0.10
    max_budget_fraction: float = 0.25
    sizing_policy: str = "cheap-tier"
    exit_edge_threshold: float = 0.08
    min_exit_price: float = 0.01
    late_day_min_hours_elapsed: float = 10.0
    late_day_observed_gap_block_f: float = 2.0
    late_day_min_source_confirmations: int = 2
    min_training_rows: int = 120
    temperature: float = 0.35
    probability_floor: float = 0.001
    audit_log_path: Path = Path("logs/trading_audit.jsonl")
    loop_seconds: int = 300


@dataclass(frozen=True)
class MarketRow:
    city: str
    event_ticker: str
    market_ticker: str
    target_date: date
    snapshot_time_utc: datetime
    checkpoint: str
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None
    no_ask: float | None
    last_price: float | None
    market_probability: float | None
    bracket_index: int
    bracket_lower_f: int | None
    bracket_upper_f: int | None
    weather: dict[str, Any]
    winner_ticker: str | None = None
    settlement_temperature_f: float | None = None


@dataclass(frozen=True)
class TradeSignal:
    city: str
    event_ticker: str
    market_ticker: str
    side: str
    target_date: str
    snapshot_time_utc: str
    model_probability: float
    yes_bid: float
    yes_ask: float
    no_bid: float | None
    no_ask: float | None
    entry_price: float
    exchange_price: float
    spread: float
    edge: float
    contracts: int
    order_cost: float
    client_order_id: str


@dataclass(frozen=True)
class TradeCandidate:
    row: MarketRow
    side: str
    probability: float
    bid: float
    ask: float
    exchange_price: float
    edge: float


@dataclass(frozen=True)
class OutcomeQuote:
    bid: float | None
    ask: float | None
    exchange_price: float | None


@dataclass(frozen=True)
class ResidualOffset:
    offset_f: float
    count: int


@dataclass
class LiveResidualCalibrator:
    alpha: float = 12.0
    global_offset_f: float = 0.0
    city_checkpoint_offsets: dict[tuple[str, str], ResidualOffset] | None = None
    city_offsets: dict[str, ResidualOffset] | None = None
    checkpoint_offsets: dict[str, ResidualOffset] | None = None

    def correction(self, row: MarketRow) -> float:
        for store, key in (
            (self.city_checkpoint_offsets or {}, (row.city, row.checkpoint)),
            (self.city_offsets or {}, row.city),
            (self.checkpoint_offsets or {}, row.checkpoint),
        ):
            offset = store.get(key)
            if offset is not None:
                return offset.offset_f
        return self.global_offset_f


class TradingStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def fetch_training_rows(self, days: int = 60) -> list[MarketRow]:
        since = datetime.now(UTC) - timedelta(days=days)
        sql = _base_market_sql(
            """
            join settlements s on s.city = m.city and s.event_ticker = m.event_ticker
            where m.snapshot_time_utc >= %s
              and m.yes_ask_dollars is not null
              and m.yes_bid_dollars is not null
            """,
            "s.winner_ticker",
        )
        return self._fetch_rows(sql, (since,))

    def fetch_latest_open_rows(self, max_age_minutes: int) -> list[MarketRow]:
        since = datetime.now(UTC) - timedelta(minutes=max_age_minutes)
        sql = _base_market_sql(
            """
            left join settlements s on s.city = m.city and s.event_ticker = m.event_ticker
            left join final_temperature_labels f
              on f.city = m.city and f.event_ticker = m.event_ticker
            join events e
              on e.city = m.city
             and e.event_ticker = m.event_ticker
             and e.snapshot_time_utc = m.snapshot_time_utc
            where m.snapshot_time_utc >= %s
              and s.settlement_id is null
              and f.final_temperature_label_id is null
              and m.snapshot_time_utc < coalesce(e.market_close_time_utc, e.climate_day_end_utc)
              and m.yes_ask_dollars is not null
              and m.yes_bid_dollars is not null
              and m.no_ask_dollars is not null
              and m.no_bid_dollars is not null
            """,
            "null::text as winner_ticker",
        )
        rows = self._fetch_rows(sql, (since,))
        latest_by_event: dict[tuple[str, str], datetime] = {}
        for row in rows:
            key = (row.city, row.event_ticker)
            latest_by_event[key] = max(
                latest_by_event.get(key, row.snapshot_time_utc),
                row.snapshot_time_utc,
            )
        return [
            row
            for row in rows
            if row.snapshot_time_utc == latest_by_event[(row.city, row.event_ticker)]
        ]

    def _fetch_rows(self, sql: str, params: tuple[Any, ...]) -> list[MarketRow]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_market_row(dict(row)) for row in rows]


class LiveCloudcaster:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.model: Pipeline | None = None
        self.training_rows = 0
        self.mode = "unfit"
        self.residual_calibrator = LiveResidualCalibrator()

    def fit(self, rows: list[MarketRow]) -> None:
        labeled = [row for row in rows if row.winner_ticker]
        self.residual_calibrator = (
            _fit_live_residual_calibrator(labeled)
            if self.config.enable_city_residual
            else LiveResidualCalibrator()
        )
        targets = [1 if row.market_ticker == row.winner_ticker else 0 for row in labeled]
        self.training_rows = len(labeled)
        if len(labeled) < self.config.min_training_rows or len(set(targets)) < 2:
            self.model = None
            self.mode = "fallback_source_distribution"
            return
        x = pd.DataFrame(
            [_features(row, self.residual_calibrator) for row in labeled],
            columns=FEATURE_COLUMNS,
        )
        model = _cloudcaster_pipeline()
        model.fit(x, targets, model__sample_weight=_sample_weights(labeled))
        self.model = model
        self.mode = "trained_live_cloudcaster"

    def predict_probabilities(
        self,
        rows: list[MarketRow],
    ) -> dict[tuple[str, str, datetime], dict[str, float]]:
        grouped: dict[tuple[str, str, datetime], list[MarketRow]] = defaultdict(list)
        for row in rows:
            grouped[(row.city, row.event_ticker, row.snapshot_time_utc)].append(row)
        output = {}
        for key, event_rows in grouped.items():
            if self.model is None:
                raw = {
                    row.market_ticker: _source_probability(row, self.residual_calibrator)
                    for row in event_rows
                }
            else:
                x = pd.DataFrame(
                    [_features(row, self.residual_calibrator) for row in event_rows],
                    columns=FEATURE_COLUMNS,
                )
                predictions = self.model.predict_proba(x)
                positive_index = list(self.model.named_steps["model"].classes_).index(1)
                raw = {
                    row.market_ticker: max(1e-12, float(predictions[index][positive_index]))
                    for index, row in enumerate(event_rows)
                }
            output[key] = _normalize(
                _apply_temperature(raw, self.config.temperature),
                self.config.probability_floor,
            )
        return output


class StrategyEngine:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def select_signals(
        self,
        rows: list[MarketRow],
        probabilities: dict[tuple[str, str, datetime], dict[str, float]],
        daily_budget: float,
        existing_tickers: set[str],
    ) -> list[TradeSignal]:
        candidates: list[TradeCandidate] = []
        for row in rows:
            if row.market_ticker in existing_tickers:
                continue
            if row.yes_bid is None or row.yes_ask is None:
                continue
            yes_probability = probabilities.get(
                (row.city, row.event_ticker, row.snapshot_time_utc),
                {},
            ).get(row.market_ticker)
            if yes_probability is None:
                continue
            yes_probability = _effective_yes_probability(
                row,
                yes_probability,
                self.config.observed_exclusion_margin_f,
            )
            yes_candidate = self._candidate(row, "yes", yes_probability)
            if yes_candidate is not None:
                candidates.append(yes_candidate)
            no_candidate = self._candidate(row, "no", 1.0 - yes_probability)
            if no_candidate is not None:
                candidates.append(no_candidate)

        selected = [
            max(items, key=lambda item: item.edge) for items in _group_by_event(candidates).values()
        ]
        budget_remaining: dict[str, float] = defaultdict(lambda: daily_budget)
        signals = []
        for candidate in sorted(selected, key=lambda item: item.row.snapshot_time_utc):
            row = candidate.row
            target_day = row.target_date.isoformat()
            contracts = self._contracts(candidate, daily_budget, budget_remaining[target_day])
            if contracts <= 0:
                continue
            order_cost = contracts * candidate.ask
            budget_remaining[target_day] -= order_cost
            signals.append(
                TradeSignal(
                    city=row.city,
                    event_ticker=row.event_ticker,
                    market_ticker=row.market_ticker,
                    side=candidate.side,
                    target_date=target_day,
                    snapshot_time_utc=row.snapshot_time_utc.isoformat(),
                    model_probability=candidate.probability,
                    yes_bid=float(row.yes_bid),
                    yes_ask=float(row.yes_ask),
                    no_bid=row.no_bid,
                    no_ask=row.no_ask,
                    entry_price=candidate.ask,
                    exchange_price=candidate.exchange_price,
                    spread=float(candidate.ask - candidate.bid),
                    edge=candidate.edge,
                    contracts=contracts,
                    order_cost=order_cost,
                    client_order_id=_client_order_id(
                        f"buy-{candidate.side}",
                        row,
                        candidate.probability,
                        candidate.edge,
                    ),
                )
            )
        return signals

    def _candidate(
        self,
        row: MarketRow,
        side: str,
        probability: float,
    ) -> TradeCandidate | None:
        if side == "yes":
            if _observed_high_excludes_yes(row, self.config.observed_exclusion_margin_f):
                return None
            bid, ask, exchange_price = row.yes_bid, row.yes_ask, row.yes_ask
            if not (
                self.config.min_model_probability <= probability < self.config.max_model_probability
            ):
                return None
        elif side == "no":
            if not self.config.enable_no_trading or _observed_high_confirms_yes(row):
                return None
            if self.config.no_entry_mode == "disabled":
                return None
            observed_excluded = _observed_high_excludes_yes(
                row,
                self.config.observed_exclusion_margin_f,
            )
            if self.config.no_entry_mode == "observed_only" and not observed_excluded:
                return None
            if self.config.no_entry_mode not in {"observed_only", "model"}:
                return None
            bid, ask = row.no_bid, row.no_ask
            exchange_price = _no_exchange_price(ask)
            if probability < self.config.min_no_model_probability:
                return None
            if ask is not None and ask < self.config.min_no_ask:
                return None
        else:
            raise ValueError(f"unsupported trade side {side}")
        if bid is None or ask is None or exchange_price is None or ask <= 0:
            return None
        if bid < self.config.min_entry_exit_bid:
            return None
        spread = ask - bid
        edge = probability - ask
        if spread > self.config.max_spread:
            return None
        if edge < self.config.edge_threshold:
            return None
        if side == "yes" and edge >= self.config.max_edge:
            return None
        if side == "yes" and not _passes_late_day_sanity(row, edge, probability, self.config):
            return None
        return TradeCandidate(row, side, probability, float(bid), float(ask), exchange_price, edge)

    def _contracts(
        self,
        candidate: TradeCandidate,
        daily_budget: float,
        remaining: float,
    ) -> int:
        if candidate.ask <= 0:
            return 0
        multiplier = (
            2.0 if self.config.sizing_policy == "cheap-tier" and candidate.ask < 0.25 else 1.0
        )
        fraction = min(
            self.config.max_budget_fraction,
            self.config.base_budget_fraction * multiplier,
        )
        premium = min(remaining, self.config.max_order_cost, daily_budget * fraction)
        contracts = max(0, math.floor(premium / candidate.ask))
        side_cap = (
            self.config.max_no_contracts_per_order
            if candidate.side == "no"
            else self.config.max_contracts_per_order
        )
        return min(contracts, side_cap)


class OrderManager:
    def __init__(self, config: RuntimeConfig, client: KalshiTradingClient) -> None:
        self.config = config
        self.client = client

    def account_balance_dollars(self) -> float:
        return float(self.client.get_balance().get("balance", 0)) / 100.0

    def existing_exposure(self, relevant_tickers: set[str] | None = None) -> set[str]:
        tickers: set[str] = set()

        def add_ticker(ticker: str) -> None:
            if ticker and (relevant_tickers is None or ticker in relevant_tickers):
                tickers.add(ticker)

        for item in _position_items(self.client.get_positions()):
            ticker = str(item.get("ticker") or item.get("market_ticker") or "")
            position = _position_side_count(item)
            if ticker and position is not None and position[1] > 0:
                add_ticker(ticker)
        for item in _items(self.client.get_orders({"status": "resting"}), "orders"):
            ticker = str(item.get("ticker") or item.get("market_ticker") or "")
            add_ticker(ticker)
        for item in _items(self.client.get_orders({"status": "executed"}), "orders"):
            ticker = str(item.get("ticker") or item.get("market_ticker") or "")
            fill_count = _number(item.get("fill_count") or item.get("fill_count_fp"))
            if ticker and fill_count and fill_count > 0:
                add_ticker(ticker)
        return tickers

    def place_buys(self, signals: list[TradeSignal], dry_run: bool) -> list[dict[str, Any]]:
        if self.config.kill_switch:
            return [
                {"action": "blocked_buy", "reason": "kill_switch", "signal": asdict(signal)}
                for signal in signals
            ]
        if not self.config.trade_enabled and not dry_run:
            raise RuntimeError("TRADE_ENABLED must be true to place demo orders")
        results = []
        for signal in signals:
            payload = _buy_payload(signal)
            if not dry_run:
                live = self._current_market(signal.market_ticker)
                quote = _live_outcome_quote(live, signal.side)
                if (
                    quote.ask is None
                    or quote.bid is None
                    or quote.exchange_price is None
                    or quote.ask <= 0.0
                    or quote.exchange_price <= 0.0
                ):
                    results.append(
                        {
                            "action": "blocked_buy",
                            "reason": "invalid_live_price",
                            "live_bid": quote.bid,
                            "live_ask": quote.ask,
                            "live_exchange_price": quote.exchange_price,
                            "signal": asdict(signal),
                        }
                    )
                    continue
                if quote.ask > signal.entry_price or quote.ask - quote.bid > self.config.max_spread:
                    results.append(
                        {
                            "action": "blocked_buy",
                            "reason": "live_price_worse_than_signal",
                            "live_bid": quote.bid,
                            "live_ask": quote.ask,
                            "live_exchange_price": quote.exchange_price,
                            "signal": asdict(signal),
                        }
                    )
                    continue
                payload["price"] = f"{quote.exchange_price:.4f}"
            if dry_run:
                results.append(
                    {
                        "action": "dry_run_buy",
                        "payload": payload,
                        "signal": asdict(signal),
                    }
                )
                continue
            try:
                response = self.client.create_order(payload)
                results.append(
                    {
                        "action": "placed_buy",
                        "payload": payload,
                        "response": response,
                        "signal": asdict(signal),
                    }
                )
            except KalshiApiError as exc:
                results.append(
                    {
                        "action": "buy_error",
                        "payload": payload,
                        "error": str(exc),
                        "signal": asdict(signal),
                    }
                )
        return results

    def close_positions(
        self,
        rows: list[MarketRow],
        probabilities: dict[tuple[str, str, datetime], dict[str, float]],
        dry_run: bool,
    ) -> list[dict[str, Any]]:
        market_by_ticker = {row.market_ticker: row for row in rows}
        results = []
        for item in _position_items(self.client.get_positions()):
            ticker = str(item.get("ticker") or item.get("market_ticker") or "")
            position = _position_side_count(item)
            side = position[0] if position is not None else ""
            contracts = int(position[1]) if position is not None else 0
            if contracts <= 0 or ticker not in market_by_ticker:
                continue
            row = market_by_ticker[ticker]
            probability = probabilities.get(
                (row.city, row.event_ticker, row.snapshot_time_utc), {}
            ).get(ticker)
            if probability is not None:
                probability = _effective_yes_probability(
                    row,
                    probability,
                    self.config.observed_exclusion_margin_f,
                )
            outcome_probability = probability if side == "yes" and probability is not None else None
            if side == "no" and probability is not None:
                outcome_probability = 1.0 - probability
            live = self._current_market(ticker)
            quote = _live_outcome_quote(live, side)
            fallback = _row_outcome_quote(row, side)
            exit_bid = quote.bid if quote.bid is not None else fallback.bid
            if exit_bid is None:
                continue
            exit_bid = max(float(exit_bid), self.config.min_exit_price)
            exchange_price = _exchange_exit_price(side, exit_bid)
            if exchange_price is None:
                continue
            observed_exit = _observed_exit_for_side(
                row,
                side,
                self.config.observed_exclusion_margin_f,
            )
            model_exit = (
                outcome_probability is not None
                and exit_bid - outcome_probability >= self.config.exit_edge_threshold
            )
            if not observed_exit and not model_exit:
                continue
            reason = f"observed_high_excludes_{side}" if observed_exit else "model_exit_edge"
            payload = _close_payload(
                row,
                side,
                contracts,
                exchange_price,
                _exit_client_order_id(row, side, exchange_price),
            )
            if dry_run:
                results.append({"action": "dry_run_sell", "reason": reason, "payload": payload})
            else:
                try:
                    results.append(
                        {
                            "action": "placed_sell",
                            "reason": reason,
                            "payload": payload,
                            "response": self.client.create_order(payload),
                        }
                    )
                except KalshiApiError as exc:
                    results.append(
                        {
                            "action": "sell_error",
                            "reason": reason,
                            "payload": payload,
                            "error": str(exc),
                        }
                    )
        return results

    def _current_market(self, ticker: str) -> dict[str, Any]:
        try:
            return self.client.get(f"/markets/{ticker}").get("market") or {}
        except KalshiApiError:
            return {}

    def cancel_stale_orders(
        self,
        active_client_order_ids: set[str],
        dry_run: bool,
    ) -> list[dict[str, Any]]:
        results = []
        for item in _items(self.client.get_orders({"status": "resting"}), "orders"):
            client_order_id = str(item.get("client_order_id") or "")
            order_id = str(item.get("order_id") or "")
            if not client_order_id.startswith("wx-"):
                continue
            if client_order_id in active_client_order_ids:
                continue
            if dry_run:
                results.append(
                    {
                        "action": "dry_run_cancel",
                        "order_id": order_id,
                        "client_order_id": client_order_id,
                    }
                )
            else:
                results.append(
                    {
                        "action": "cancelled_order",
                        "order_id": order_id,
                        "client_order_id": client_order_id,
                        "response": self.client.cancel_order(order_id),
                    }
                )
        return results


def run_once(config: RuntimeConfig, dry_run: bool) -> dict[str, Any]:
    _validate_config(config, dry_run)
    store = TradingStore(config.database_url)
    client = KalshiTradingClient(
        config.kalshi_base_url,
        config.kalshi_api_key_id,
        config.kalshi_private_key_path,
    )
    manager = OrderManager(config, client)
    training_rows = store.fetch_training_rows()
    live_rows = store.fetch_latest_open_rows(config.max_data_age_minutes)
    model = LiveCloudcaster(config)
    model.fit(training_rows)
    probabilities = model.predict_probabilities(live_rows)
    balance = manager.account_balance_dollars()
    daily_budget = _daily_budget(balance, config)
    existing = manager.existing_exposure({row.market_ticker for row in live_rows})
    signals = StrategyEngine(config).select_signals(
        live_rows, probabilities, daily_budget, existing
    )
    if len(existing) >= config.max_open_positions:
        signals = []
    cancels = manager.cancel_stale_orders(
        {signal.client_order_id for signal in signals},
        dry_run,
    )
    sells = manager.close_positions(live_rows, probabilities, dry_run)
    buys = manager.place_buys(signals, dry_run)
    result = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "mode": "dry_run" if dry_run else "demo_trading",
        "model_mode": model.mode,
        "city_residual_enabled": config.enable_city_residual,
        "training_rows": model.training_rows,
        "live_market_rows": len(live_rows),
        "balance_dollars": balance,
        "daily_budget": daily_budget,
        "existing_exposure_count": len(existing),
        "signals": [asdict(signal) for signal in signals],
        "cancel_actions": cancels,
        "sell_actions": sells,
        "buy_actions": buys,
    }
    write_audit(config.audit_log_path, result)
    return result


def run_daemon(config: RuntimeConfig, dry_run: bool) -> None:
    while True:
        try:
            print(json.dumps(_summary(run_once(config, dry_run=dry_run)), indent=2), flush=True)
        except Exception as exc:  # noqa: BLE001 - keep supervised loop alive.
            write_audit(
                config.audit_log_path,
                {"timestamp_utc": datetime.now(UTC).isoformat(), "error": str(exc)},
            )
            print(f"ERROR: {exc}", flush=True)
        time.sleep(config.loop_seconds)


def write_audit(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def config_from_env() -> RuntimeConfig:
    load_dotenv()
    return RuntimeConfig(
        database_url=_required("DATABASE_URL"),
        kalshi_base_url=os.environ.get(
            "KALSHI_API_BASE_URL", "https://external-api.demo.kalshi.co/trade-api/v2"
        ),
        kalshi_api_key_id=_required("KALSHI_API_KEY_ID"),
        kalshi_private_key_path=_required("KALSHI_PRIVATE_KEY_PATH"),
        trade_enabled=_bool_env("TRADE_ENABLED", False),
        kill_switch=_bool_env("KALSHI_KILL_SWITCH", False),
        account_budget_fraction=_float_env("KALSHI_ACCOUNT_BUDGET_FRACTION", 0.20),
        max_daily_budget=_float_env("KALSHI_MAX_DAILY_BUDGET", 40.0),
        max_order_cost=_float_env("KALSHI_MAX_ORDER_COST", 3.0),
        max_open_positions=_int_env("KALSHI_MAX_OPEN_POSITIONS", 12),
        sizing_policy=os.environ.get("KALSHI_SIZING_POLICY", "cheap-tier"),
        enable_no_trading=_bool_env("KALSHI_ENABLE_NO_TRADING", True),
        no_entry_mode=os.environ.get("KALSHI_NO_ENTRY_MODE", "observed_only"),
        min_no_model_probability=_float_env("KALSHI_MIN_NO_MODEL_PROBABILITY", 0.75),
        min_no_ask=_float_env("KALSHI_MIN_NO_ASK", 0.05),
        max_contracts_per_order=_int_env("KALSHI_MAX_CONTRACTS_PER_ORDER", 20),
        max_no_contracts_per_order=_int_env("KALSHI_MAX_NO_CONTRACTS_PER_ORDER", 10),
        min_entry_exit_bid=_float_env("KALSHI_MIN_ENTRY_EXIT_BID", 0.01),
        observed_exclusion_margin_f=_float_env("KALSHI_OBSERVED_EXCLUSION_MARGIN_F", 1.0),
        enable_city_residual=_bool_env("KALSHI_ENABLE_CITY_RESIDUAL", False),
        exit_edge_threshold=_float_env("KALSHI_EXIT_EDGE_THRESHOLD", 0.08),
        min_exit_price=_float_env("KALSHI_MIN_EXIT_PRICE", 0.01),
        late_day_min_hours_elapsed=_float_env("KALSHI_LATE_DAY_MIN_HOURS_ELAPSED", 10.0),
        late_day_observed_gap_block_f=_float_env("KALSHI_LATE_DAY_OBSERVED_GAP_BLOCK_F", 2.0),
        late_day_min_source_confirmations=_int_env(
            "KALSHI_LATE_DAY_MIN_SOURCE_CONFIRMATIONS",
            2,
        ),
        loop_seconds=_int_env("KALSHI_BOT_LOOP_SECONDS", 300),
    )


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def add_cli_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="score and log without orders")
    parser.add_argument("--place-orders", action="store_true", help="allow demo order placement")


def _base_market_sql(extra_join_where: str, winner_expr: str) -> str:
    return f"""
        select
          m.city, m.event_ticker, m.market_ticker, m.target_date,
          m.snapshot_time_utc, m.checkpoint_label,
          m.yes_bid_dollars, m.yes_ask_dollars, m.no_bid_dollars, m.no_ask_dollars,
          m.last_price_dollars,
          m.normalized_market_midpoint_probability,
          m.bracket_index, m.bracket_lower_f, m.bracket_upper_f,
          w.nws_anchor_high_f, w.nws_hourly_window_max_f, w.observed_high_so_far_f,
          w.hrrr_projected_high_f, w.nbm_projected_high_f, w.ensemble_raw_median_high_f,
          w.hours_since_climate_start, w.hours_until_climate_end,
          s.settlement_temperature_f,
          {winner_expr}
        from market_snapshots m
        join weather_snapshots w
          on w.city = m.city
         and w.event_ticker = m.event_ticker
         and w.snapshot_time_utc = m.snapshot_time_utc
        {extra_join_where}
    """


def _market_row(row: dict[str, Any]) -> MarketRow:
    return MarketRow(
        city=str(row["city"]),
        event_ticker=str(row["event_ticker"]),
        market_ticker=str(row["market_ticker"]),
        target_date=_date(row["target_date"]),
        snapshot_time_utc=_datetime(row["snapshot_time_utc"]),
        checkpoint=str(row.get("checkpoint_label") or "unknown"),
        yes_bid=_number(row.get("yes_bid_dollars")),
        yes_ask=_number(row.get("yes_ask_dollars")),
        no_bid=_number(row.get("no_bid_dollars")),
        no_ask=_number(row.get("no_ask_dollars")),
        last_price=_number(row.get("last_price_dollars")),
        market_probability=_number(row.get("normalized_market_midpoint_probability")),
        bracket_index=int(row.get("bracket_index") or 0),
        bracket_lower_f=_optional_int(row.get("bracket_lower_f")),
        bracket_upper_f=_optional_int(row.get("bracket_upper_f")),
        weather={
            "nws_anchor_high_f": _number(row.get("nws_anchor_high_f")),
            "nws_hourly_window_max_f": _number(row.get("nws_hourly_window_max_f")),
            "observed_high_so_far_f": _number(row.get("observed_high_so_far_f")),
            "hrrr_projected_high_f": _number(row.get("hrrr_projected_high_f")),
            "nbm_projected_high_f": _number(row.get("nbm_projected_high_f")),
            "ensemble_raw_median_high_f": _number(row.get("ensemble_raw_median_high_f")),
            "hours_elapsed": _number(row.get("hours_since_climate_start")),
            "hours_remaining": _number(row.get("hours_until_climate_end")),
        },
        winner_ticker=str(row["winner_ticker"]) if row.get("winner_ticker") else None,
        settlement_temperature_f=_number(row.get("settlement_temperature_f")),
    )


def _features(
    row: MarketRow,
    residual_calibrator: LiveResidualCalibrator | None = None,
) -> dict[str, Any]:
    expected = _expected_high(row, residual_calibrator)
    lower = row.bracket_lower_f - 0.5 if row.bracket_lower_f is not None else expected - 5.5
    upper = row.bracket_upper_f + 0.5 if row.bracket_upper_f is not None else expected + 5.5
    center = (lower + upper) / 2.0
    return {
        "expected_high_f": expected,
        "source_std_f": _source_std(row),
        "source_range_f": _source_range(row),
        "observed_high_so_far_f": row.weather.get("observed_high_so_far_f"),
        "hours_elapsed": row.weather.get("hours_elapsed"),
        "hours_remaining": row.weather.get("hours_remaining"),
        "bracket_index": float(row.bracket_index),
        "bracket_width_f": max(1.0, upper - lower),
        "bracket_center_f": center,
        "distance_to_center_f": expected - center,
        "distance_to_lower_f": expected - lower,
        "distance_to_upper_f": upper - expected,
        "expected_inside_bracket": 1.0 if lower <= expected <= upper else 0.0,
        "market_implied_probability": row.market_probability,
        "city": row.city,
        "checkpoint": row.checkpoint,
        "is_open_low": str(row.bracket_lower_f is None),
        "is_open_high": str(row.bracket_upper_f is None),
    }


def _expected_high(
    row: MarketRow,
    residual_calibrator: LiveResidualCalibrator | None = None,
) -> float:
    values = sorted(value for value in _source_values(row) if value is not None)
    if not values:
        expected = row.weather.get("observed_high_so_far_f") or 75.0
    elif len(values) % 2:
        expected = values[len(values) // 2]
    else:
        expected = (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2.0
    observed = row.weather.get("observed_high_so_far_f")
    if residual_calibrator is not None:
        expected += residual_calibrator.correction(row)
    return max(expected, observed) if observed is not None else expected


def _source_values(row: MarketRow) -> list[float]:
    return [
        value
        for value in (
            row.weather.get("nws_anchor_high_f"),
            row.weather.get("nws_hourly_window_max_f"),
            row.weather.get("hrrr_projected_high_f"),
            row.weather.get("nbm_projected_high_f"),
            row.weather.get("ensemble_raw_median_high_f"),
        )
        if value is not None
    ]


def _source_probability(
    row: MarketRow,
    residual_calibrator: LiveResidualCalibrator | None = None,
) -> float:
    features = _features(row, residual_calibrator)
    distance = abs(float(features["distance_to_center_f"]))
    width = max(1.0, float(features["bracket_width_f"]))
    return max(
        math.exp(-0.5 * (distance / max(width, 2.5)) ** 2),
        row.market_probability or 0.001,
    )


def _cloudcaster_pipeline() -> Pipeline:
    return Pipeline(
        steps=[
            (
                "preprocessor",
                ColumnTransformer(
                    transformers=[
                        (
                            "numeric",
                            Pipeline(
                                [
                                    (
                                        "imputer",
                                        SimpleImputer(
                                            strategy="median",
                                            keep_empty_features=True,
                                        ),
                                    ),
                                    ("scaler", StandardScaler()),
                                ]
                            ),
                            NUMERIC_FEATURES,
                        ),
                        (
                            "categorical",
                            Pipeline(
                                [
                                    ("imputer", SimpleImputer(strategy="most_frequent")),
                                    (
                                        "encoder",
                                        OneHotEncoder(
                                            handle_unknown="ignore",
                                            sparse_output=False,
                                        ),
                                    ),
                                ]
                            ),
                            CATEGORICAL_FEATURES,
                        ),
                    ],
                    remainder="drop",
                ),
            ),
            (
                "model",
                HistGradientBoostingClassifier(
                    learning_rate=0.04,
                    max_leaf_nodes=15,
                    min_samples_leaf=20,
                    l2_regularization=0.2,
                    random_state=17,
                ),
            ),
        ]
    )


def _sample_weights(rows: list[MarketRow]) -> list[float]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row.target_date.isoformat()] += 1
    return [
        (1.0 / counts[row.target_date.isoformat()])
        * (5.0 if row.market_ticker == row.winner_ticker else 1.0)
        for row in rows
    ]


def _normalize(values: dict[str, float], floor: float) -> dict[str, float]:
    floored = {key: max(floor, float(value)) for key, value in values.items()}
    total = sum(floored.values())
    return {key: value / total for key, value in floored.items()} if total else {}


def _apply_temperature(values: dict[str, float], temperature: float) -> dict[str, float]:
    power = 1.0 / max(temperature, 1e-6)
    return {key: max(1e-12, value) ** power for key, value in values.items()}


def _group_by_event(items: list[TradeCandidate]) -> dict[str, list[TradeCandidate]]:
    grouped: dict[str, list[TradeCandidate]] = defaultdict(list)
    for item in items:
        grouped[item.row.event_ticker].append(item)
    return grouped


def _client_order_id(action: str, row: MarketRow, probability: float, edge: float) -> str:
    raw = "|".join(
        [
            "weather-cloudcaster-v1",
            action,
            row.event_ticker,
            row.market_ticker,
            row.snapshot_time_utc.strftime("%Y%m%dT%H"),
            f"{probability:.4f}",
            f"{edge:.4f}",
        ]
    )
    return "wx-" + hashlib.sha256(raw.encode()).hexdigest()[:29]


def _exit_client_order_id(row: MarketRow, side: str, price: float) -> str:
    raw = "|".join(
        [
            "weather-cloudcaster-v1",
            f"sell-{side}",
            row.event_ticker,
            row.market_ticker,
            datetime.now(UTC).strftime("%Y%m%dT%H%M%S"),
            f"{price:.4f}",
        ]
    )
    return "wx-" + hashlib.sha256(raw.encode()).hexdigest()[:29]


def _buy_payload(signal: TradeSignal) -> dict[str, Any]:
    return {
        "ticker": signal.market_ticker,
        "client_order_id": signal.client_order_id,
        "side": "bid" if signal.side == "yes" else "ask",
        "count": f"{signal.contracts:.2f}",
        "price": f"{signal.exchange_price:.4f}",
        "time_in_force": "good_till_canceled",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": False,
    }


def _sell_payload(
    row: MarketRow,
    contracts: int,
    price: float | None = None,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    return _close_payload(
        row,
        "yes",
        contracts,
        float(price if price is not None else row.yes_bid),
        client_order_id,
    )


def _close_payload(
    row: MarketRow,
    side: str,
    contracts: int,
    price: float,
    client_order_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ticker": row.market_ticker,
        "client_order_id": client_order_id or _client_order_id(f"sell-{side}", row, 0.0, 0.0),
        "side": "ask" if side == "yes" else "bid",
        "count": f"{contracts:.2f}",
        "price": f"{price:.4f}",
        "time_in_force": "immediate_or_cancel",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": True,
    }


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": result["mode"],
        "model_mode": result["model_mode"],
        "training_rows": result["training_rows"],
        "live_market_rows": result["live_market_rows"],
        "daily_budget": result["daily_budget"],
        "signals": len(result["signals"]),
        "cancels": len(result["cancel_actions"]),
        "buys": len(result["buy_actions"]),
        "sells": len(result["sell_actions"]),
    }


def _validate_config(config: RuntimeConfig, dry_run: bool) -> None:
    if config.no_entry_mode not in {"disabled", "observed_only", "model"}:
        raise RuntimeError("KALSHI_NO_ENTRY_MODE must be disabled, observed_only, or model")
    if "demo" not in config.kalshi_base_url and not dry_run:
        raise RuntimeError("refusing non-demo Kalshi URL without code change")
    if config.kill_switch and not dry_run:
        raise RuntimeError("KALSHI_KILL_SWITCH is active")


def _daily_budget(balance_dollars: float, config: RuntimeConfig) -> float:
    liquid_budget = balance_dollars * config.account_budget_fraction
    return max(0.0, min(liquid_budget, config.max_daily_budget))


def _items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = payload.get(key) or payload.get("payload") or []
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _position_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = _items(payload, "positions")
    items.extend(_items(payload, "market_positions"))
    return items


def _position_count(item: dict[str, Any]) -> float | None:
    return _number(
        item.get("position")
        or item.get("position_fp")
        or item.get("yes_count")
        or item.get("count")
    )


def _position_side_count(item: dict[str, Any]) -> tuple[str, float] | None:
    yes_count = _number(item.get("yes_count") or item.get("yes_count_fp"))
    if yes_count is not None and yes_count > 0:
        return ("yes", yes_count)
    no_count = _number(item.get("no_count") or item.get("no_count_fp"))
    if no_count is not None and no_count > 0:
        return ("no", no_count)
    count = _position_count(item)
    if count is None or count == 0:
        return None
    return ("yes", count) if count > 0 else ("no", abs(count))


def _row_outcome_quote(row: MarketRow, side: str) -> OutcomeQuote:
    if side == "yes":
        return OutcomeQuote(row.yes_bid, row.yes_ask, row.yes_ask)
    if side == "no":
        return OutcomeQuote(row.no_bid, row.no_ask, _no_exchange_price(row.no_ask))
    raise ValueError(f"unsupported trade side {side}")


def _live_outcome_quote(market: dict[str, Any], side: str) -> OutcomeQuote:
    yes_bid = _number(market.get("yes_bid_dollars"))
    yes_ask = _number(market.get("yes_ask_dollars"))
    no_bid = _number(market.get("no_bid_dollars"))
    no_ask = _number(market.get("no_ask_dollars"))
    if no_bid is None and yes_ask is not None:
        no_bid = max(0.0, 1.0 - yes_ask)
    if no_ask is None and yes_bid is not None:
        no_ask = max(0.0, 1.0 - yes_bid)
    if side == "yes":
        return OutcomeQuote(yes_bid, yes_ask, yes_ask)
    if side == "no":
        return OutcomeQuote(no_bid, no_ask, _no_exchange_price(no_ask))
    raise ValueError(f"unsupported trade side {side}")


def _no_exchange_price(no_price: float | None) -> float | None:
    if no_price is None:
        return None
    return _bounded_order_price(1.0 - float(no_price))


def _exchange_exit_price(side: str, outcome_bid: float) -> float | None:
    if side == "yes":
        return _bounded_order_price(outcome_bid)
    if side == "no":
        return _bounded_order_price(1.0 - outcome_bid)
    raise ValueError(f"unsupported trade side {side}")


def _bounded_order_price(price: float | None) -> float | None:
    if price is None:
        return None
    return min(0.99, max(0.01, float(price)))


def _source_std(row: MarketRow) -> float | None:
    values = _source_values(row)
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _source_range(row: MarketRow) -> float | None:
    values = _source_values(row)
    return max(values) - min(values) if len(values) >= 2 else None


def _fit_live_residual_calibrator(
    rows: list[MarketRow],
    alpha: float = 12.0,
) -> LiveResidualCalibrator:
    by_snapshot: dict[tuple[str, str, datetime], MarketRow] = {}
    for row in rows:
        if row.settlement_temperature_f is None:
            continue
        by_snapshot.setdefault((row.city, row.event_ticker, row.snapshot_time_utc), row)
    residual_rows = [
        (row, float(row.settlement_temperature_f) - _expected_high(row))
        for row in by_snapshot.values()
    ]
    global_offset = (
        sum(residual for _, residual in residual_rows) / len(residual_rows)
        if residual_rows
        else 0.0
    )
    return LiveResidualCalibrator(
        alpha=alpha,
        global_offset_f=_shrunk_mean([residual for _, residual in residual_rows], alpha),
        city_checkpoint_offsets=_live_offsets(
            residual_rows,
            lambda row: (row.city, row.checkpoint),
            alpha,
            global_offset,
        ),
        city_offsets=_live_offsets(residual_rows, lambda row: row.city, alpha, global_offset),
        checkpoint_offsets=_live_offsets(
            residual_rows,
            lambda row: row.checkpoint,
            alpha,
            global_offset,
        ),
    )


def _live_offsets(
    residual_rows: list[tuple[MarketRow, float]],
    key_func,
    alpha: float,
    global_offset: float,
) -> dict[Any, ResidualOffset]:
    grouped: dict[Any, list[float]] = defaultdict(list)
    for row, residual in residual_rows:
        grouped[key_func(row)].append(residual)
    return {
        key: ResidualOffset(
            offset_f=_shrunk_mean(values, alpha, global_offset),
            count=len(values),
        )
        for key, values in grouped.items()
    }


def _shrunk_mean(values: list[float], alpha: float, prior: float = 0.0) -> float:
    if not values:
        return prior
    weight = len(values) / (len(values) + alpha)
    return prior + weight * ((sum(values) / len(values)) - prior)


def _passes_late_day_sanity(
    row: MarketRow,
    edge: float,
    probability: float,
    config: RuntimeConfig,
) -> bool:
    del probability
    observed = row.weather.get("observed_high_so_far_f")
    hours_elapsed = row.weather.get("hours_elapsed")
    if (
        observed is None
        or hours_elapsed is None
        or hours_elapsed < config.late_day_min_hours_elapsed
        or row.bracket_lower_f is None
    ):
        return True
    lower = float(row.bracket_lower_f)
    if observed >= lower - config.late_day_observed_gap_block_f:
        return True
    confirmations = family_at_or_above_count(lower, row.weather)
    if confirmations < config.late_day_min_source_confirmations:
        return False
    return edge >= config.edge_threshold


def _observed_high_excludes_yes(row: MarketRow, margin_f: float = 0.0) -> bool:
    observed = row.weather.get("observed_high_so_far_f")
    if observed is None or row.bracket_upper_f is None:
        return False
    return float(observed) >= float(row.bracket_upper_f) + margin_f


def _observed_high_confirms_yes(row: MarketRow) -> bool:
    observed = row.weather.get("observed_high_so_far_f")
    if observed is None or row.bracket_lower_f is None or row.bracket_upper_f is not None:
        return False
    return int(float(observed) + 0.5) >= int(row.bracket_lower_f)


def _effective_yes_probability(row: MarketRow, probability: float, margin_f: float = 0.0) -> float:
    if _observed_high_excludes_yes(row, margin_f):
        return 0.0
    if _observed_high_confirms_yes(row):
        return 1.0
    return probability


def _observed_exit_for_side(row: MarketRow, side: str, margin_f: float = 0.0) -> bool:
    if side == "yes":
        return _observed_high_excludes_yes(row, margin_f)
    if side == "no":
        return _observed_high_confirms_yes(row)
    raise ValueError(f"unsupported trade side {side}")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    return default if value in (None, "") else float(value)


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value in (None, "") else int(value)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def _date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
