from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kalshi_client import sign_request
from live_strategy import (
    LiveCloudcaster,
    MarketRow,
    OrderManager,
    RuntimeConfig,
    StrategyEngine,
    TradeSignal,
    _buy_payload,
    _client_order_id,
    _daily_budget,
    _sell_payload,
)


def test_strategy_uses_cheap_tier_budget_sizing() -> None:
    row = _market_row(ask=0.20)
    probabilities = {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.30}}
    signals = StrategyEngine(_config()).select_signals(
        [row],
        probabilities,
        daily_budget=10.0,
        existing_tickers=set(),
    )
    assert len(signals) == 1
    assert signals[0].contracts == 10
    assert signals[0].order_cost == 2.0


def test_strategy_filters_out_existing_exposure() -> None:
    row = _market_row(ask=0.20)
    probabilities = {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.30}}
    signals = StrategyEngine(_config()).select_signals(
        [row],
        probabilities,
        daily_budget=10.0,
        existing_tickers={"MKT"},
    )
    assert signals == []


def test_strategy_blocks_late_day_upper_tail_without_confirmation() -> None:
    row = replace(
        _market_row(ask=0.20),
        bracket_lower_f=77,
        bracket_upper_f=None,
        weather={
            "nws_anchor_high_f": 79.0,
            "nws_hourly_window_max_f": 76.0,
            "observed_high_so_far_f": 73.4,
            "hrrr_projected_high_f": 75.0,
            "nbm_projected_high_f": 73.9,
            "ensemble_raw_median_high_f": 76.0,
            "hours_elapsed": 11.0,
            "hours_remaining": 13.0,
        },
    )
    probabilities = {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.30}}
    signals = StrategyEngine(_config()).select_signals(
        [row],
        probabilities,
        daily_budget=10.0,
        existing_tickers=set(),
    )
    assert signals == []


def test_daily_budget_uses_twenty_percent_with_forty_dollar_cap() -> None:
    config = _config()
    assert round(_daily_budget(117.67, config), 3) == 23.534
    assert _daily_budget(500.00, config) == 40.0


def test_live_city_residual_is_disabled_by_default() -> None:
    model = LiveCloudcaster(_config())
    row = _market_row(ask=0.20)
    model.fit([row])
    assert model.residual_calibrator.correction(row) == 0.0


def test_existing_exposure_includes_executed_filled_relevant_orders() -> None:
    manager = OrderManager(_config(), _FakeClient())
    assert manager.existing_exposure({"LIVE"}) == {"LIVE"}


def test_existing_exposure_includes_market_positions_shape() -> None:
    manager = OrderManager(_config(), _FakeClientWithMarketPositions())
    assert manager.existing_exposure({"MKT"}) == {"MKT"}


def test_place_buys_blocks_when_live_ask_worsens() -> None:
    row = _market_row(ask=0.20)
    signal = TradeSignal(
        city="nyc",
        event_ticker="EVT",
        market_ticker="MKT",
        side="yes",
        target_date="2026-07-13",
        snapshot_time_utc=row.snapshot_time_utc.isoformat(),
        model_probability=0.30,
        yes_bid=0.10,
        yes_ask=0.20,
        no_bid=0.80,
        no_ask=0.90,
        entry_price=0.20,
        exchange_price=0.20,
        spread=0.10,
        edge=0.10,
        contracts=10,
        order_cost=2.0,
        client_order_id="cid",
    )
    config = replace(_config(), trade_enabled=True)
    results = OrderManager(config, _FakeClientWithWorseLiveAsk()).place_buys(
        [signal],
        dry_run=False,
    )
    assert results[0]["action"] == "blocked_buy"
    assert results[0]["reason"] == "live_price_worse_than_signal"


def test_place_buys_blocks_zero_live_ask() -> None:
    row = _market_row(ask=0.20)
    signal = TradeSignal(
        city="nyc",
        event_ticker="EVT",
        market_ticker="MKT",
        side="yes",
        target_date="2026-07-13",
        snapshot_time_utc=row.snapshot_time_utc.isoformat(),
        model_probability=0.30,
        yes_bid=0.10,
        yes_ask=0.20,
        no_bid=0.80,
        no_ask=0.90,
        entry_price=0.20,
        exchange_price=0.20,
        spread=0.10,
        edge=0.10,
        contracts=10,
        order_cost=2.0,
        client_order_id="cid",
    )
    config = replace(_config(), trade_enabled=True)
    results = OrderManager(config, _FakeClientWithZeroLiveAsk()).place_buys(
        [signal],
        dry_run=False,
    )
    assert results[0]["action"] == "blocked_buy"
    assert results[0]["reason"] == "invalid_live_price"
    assert results[0]["live_ask"] == 0.0


def test_strategy_can_select_no_when_bracket_is_unlikely() -> None:
    row = replace(_market_row(ask=0.20), no_bid=0.10, no_ask=0.20)
    probabilities = {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.10}}
    signals = StrategyEngine(replace(_config(), no_entry_mode="model")).select_signals(
        [row],
        probabilities,
        daily_budget=10.0,
        existing_tickers=set(),
    )
    assert len(signals) == 1
    assert signals[0].side == "no"
    assert signals[0].model_probability == 0.90
    assert signals[0].entry_price == 0.20
    assert signals[0].exchange_price == 0.80


def test_strategy_blocks_model_only_no_by_default() -> None:
    row = replace(_market_row(ask=0.20), no_bid=0.10, no_ask=0.20)
    probabilities = {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.10}}
    signals = StrategyEngine(_config()).select_signals(
        [row],
        probabilities,
        daily_budget=10.0,
        existing_tickers=set(),
    )
    assert signals == []


def test_strategy_blocks_no_when_exit_bid_is_zero() -> None:
    row = replace(
        _market_row(ask=0.20),
        bracket_lower_f=78,
        bracket_upper_f=79,
        no_bid=0.0,
        no_ask=0.20,
        weather={
            "nws_anchor_high_f": 83.0,
            "observed_high_so_far_f": 80.2,
            "hrrr_projected_high_f": 83.0,
            "nbm_projected_high_f": 82.0,
            "ensemble_raw_median_high_f": 83.0,
            "hours_elapsed": 12.0,
            "hours_remaining": 3.0,
        },
    )
    probabilities = {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.40}}
    signals = StrategyEngine(_config()).select_signals(
        [row],
        probabilities,
        daily_budget=10.0,
        existing_tickers=set(),
    )
    assert signals == []


def test_strategy_selects_no_when_observed_high_excludes_yes() -> None:
    row = replace(
        _market_row(ask=0.20),
        bracket_lower_f=78,
        bracket_upper_f=79,
        no_bid=0.80,
        no_ask=0.90,
        weather={
            "nws_anchor_high_f": 83.0,
            "observed_high_so_far_f": 80.2,
            "hrrr_projected_high_f": 83.0,
            "nbm_projected_high_f": 82.0,
            "ensemble_raw_median_high_f": 83.0,
            "hours_elapsed": 12.0,
            "hours_remaining": 3.0,
        },
    )
    probabilities = {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.40}}
    signals = StrategyEngine(_config()).select_signals(
        [row],
        probabilities,
        daily_budget=10.0,
        existing_tickers=set(),
    )
    assert len(signals) == 1
    assert signals[0].side == "no"
    assert signals[0].model_probability == 1.0
    assert round(signals[0].edge, 3) == 0.100


def test_close_positions_uses_market_positions_shape() -> None:
    row = _market_row(ask=0.20)
    manager = OrderManager(
        replace(_config(), exit_edge_threshold=0.0),
        _FakeClientWithMarketPositions(),
    )
    results = manager.close_positions(
        [row],
        {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.05}},
        dry_run=True,
    )
    assert len(results) == 1
    assert results[0]["action"] == "dry_run_sell"
    payload = results[0]["payload"]
    assert payload["ticker"] == "MKT"
    assert payload["client_order_id"].startswith("wx-")
    assert payload["side"] == "ask"
    assert payload["count"] == "4.00"
    assert payload["price"] == "0.0800"
    assert payload["time_in_force"] == "immediate_or_cancel"
    assert payload["reduce_only"] is True


def test_close_positions_holds_on_small_model_exit_edge() -> None:
    row = _market_row(ask=0.20)
    manager = OrderManager(_config(), _FakeClientWithMarketPositions())
    results = manager.close_positions(
        [row],
        {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.05}},
        dry_run=True,
    )
    assert results == []


def test_close_positions_sells_on_strong_model_exit_edge() -> None:
    row = _market_row(ask=0.20)
    manager = OrderManager(_config(), _FakeClientWithMarketPositions())
    results = manager.close_positions(
        [row],
        {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.0}},
        dry_run=True,
    )
    assert len(results) == 1
    assert results[0]["reason"] == "model_exit_edge"


def test_close_positions_closes_no_on_strong_model_exit_edge() -> None:
    row = replace(_market_row(ask=0.20), no_bid=0.08, no_ask=0.20)
    manager = OrderManager(_config(), _FakeClientWithNoMarketPosition())
    results = manager.close_positions(
        [row],
        {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 1.00}},
        dry_run=True,
    )
    assert len(results) == 1
    assert results[0]["reason"] == "model_exit_edge"
    payload = results[0]["payload"]
    assert payload["side"] == "bid"
    assert payload["price"] == "0.9200"
    assert payload["reduce_only"] is True


def test_close_positions_forces_no_exit_when_open_high_yes_confirmed() -> None:
    row = replace(
        _market_row(ask=0.20),
        bracket_lower_f=80,
        bracket_upper_f=None,
        no_bid=0.01,
        no_ask=0.20,
        weather={
            "nws_anchor_high_f": 83.0,
            "observed_high_so_far_f": 80.0,
            "hrrr_projected_high_f": 83.0,
            "nbm_projected_high_f": 82.0,
            "ensemble_raw_median_high_f": 83.0,
            "hours_elapsed": 12.0,
            "hours_remaining": 3.0,
        },
    )
    manager = OrderManager(_config(), _FakeClientWithNoMarketPosition())
    results = manager.close_positions(
        [row],
        {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.01}},
        dry_run=True,
    )
    assert len(results) == 1
    assert results[0]["reason"] == "observed_high_excludes_no"
    assert results[0]["payload"]["side"] == "bid"


def test_close_positions_sells_when_observed_high_excludes_bounded_yes() -> None:
    row = replace(
        _market_row(ask=0.20),
        bracket_lower_f=78,
        bracket_upper_f=79,
        weather={
            "nws_anchor_high_f": 80.0,
            "observed_high_so_far_f": 80.2,
            "hrrr_projected_high_f": 80.0,
            "nbm_projected_high_f": 80.0,
            "ensemble_raw_median_high_f": 80.0,
            "hours_elapsed": 12.0,
            "hours_remaining": 3.0,
        },
    )
    manager = OrderManager(_config(), _FakeClientWithMarketPositions())
    results = manager.close_positions(
        [row],
        {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.99}},
        dry_run=True,
    )
    assert len(results) == 1
    assert results[0]["action"] == "dry_run_sell"
    assert results[0]["reason"] == "observed_high_excludes_yes"


def test_forced_observed_exit_uses_one_cent_floor_when_bid_is_zero() -> None:
    row = replace(
        _market_row(ask=0.20),
        bracket_lower_f=78,
        bracket_upper_f=79,
        weather={
            "nws_anchor_high_f": 80.0,
            "observed_high_so_far_f": 80.2,
            "hrrr_projected_high_f": 80.0,
            "nbm_projected_high_f": 80.0,
            "ensemble_raw_median_high_f": 80.0,
            "hours_elapsed": 12.0,
            "hours_remaining": 3.0,
        },
    )
    manager = OrderManager(_config(), _FakeClientWithZeroBidPosition())
    results = manager.close_positions(
        [row],
        {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.99}},
        dry_run=True,
    )
    assert results[0]["payload"]["price"] == "0.0100"


def test_close_positions_keeps_open_high_yes_when_observed_high_rises() -> None:
    row = replace(
        _market_row(ask=0.20),
        bracket_lower_f=77,
        bracket_upper_f=None,
        weather={
            "nws_anchor_high_f": 80.0,
            "observed_high_so_far_f": 80.0,
            "hrrr_projected_high_f": 80.0,
            "nbm_projected_high_f": 80.0,
            "ensemble_raw_median_high_f": 80.0,
            "hours_elapsed": 12.0,
            "hours_remaining": 3.0,
        },
    )
    manager = OrderManager(_config(), _FakeClientWithMarketPositions())
    results = manager.close_positions(
        [row],
        {("nyc", "EVT", row.snapshot_time_utc): {"MKT": 0.99}},
        dry_run=True,
    )
    assert results == []


def test_client_order_id_is_stable() -> None:
    row = _market_row(ask=0.20)
    assert _client_order_id("buy", row, 0.30, 0.10) == _client_order_id(
        "buy",
        row,
        0.30,
        0.10,
    )


def test_buy_and_sell_payloads_use_v2_yes_side_shape() -> None:
    signal = TradeSignal(
        city="nyc",
        event_ticker="EVT",
        market_ticker="MKT",
        side="yes",
        target_date="2026-07-13",
        snapshot_time_utc="2026-07-13T12:00:00+00:00",
        model_probability=0.30,
        yes_bid=0.10,
        yes_ask=0.20,
        no_bid=0.80,
        no_ask=0.90,
        entry_price=0.20,
        exchange_price=0.20,
        spread=0.10,
        edge=0.10,
        contracts=10,
        order_cost=2.0,
        client_order_id="cid",
    )
    buy = _buy_payload(signal)
    sell = _sell_payload(_market_row(ask=0.20), 3)
    assert buy["side"] == "bid"
    assert buy["price"] == "0.2000"
    assert buy["reduce_only"] is False
    assert sell["side"] == "ask"
    assert sell["reduce_only"] is True
    assert sell["time_in_force"] == "immediate_or_cancel"


def test_no_buy_payload_uses_ask_side_and_exchange_price() -> None:
    signal = TradeSignal(
        city="nyc",
        event_ticker="EVT",
        market_ticker="MKT",
        side="no",
        target_date="2026-07-13",
        snapshot_time_utc="2026-07-13T12:00:00+00:00",
        model_probability=0.90,
        yes_bid=0.80,
        yes_ask=0.90,
        no_bid=0.10,
        no_ask=0.20,
        entry_price=0.20,
        exchange_price=0.80,
        spread=0.10,
        edge=0.10,
        contracts=10,
        order_cost=2.0,
        client_order_id="cid",
    )
    payload = _buy_payload(signal)
    assert payload["side"] == "ask"
    assert payload["price"] == "0.8000"
    assert payload["reduce_only"] is False


def test_sign_request_returns_base64_signature() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signature = sign_request(
        private_key,
        "1703123456789",
        "GET",
        "/trade-api/v2/portfolio/balance?ignored=true",
    )
    assert isinstance(signature, str)
    assert len(signature) > 100


class _FakeClient:
    def get_positions(self) -> dict[str, list[dict[str, str]]]:
        return {"positions": []}

    def get_orders(self, params: dict[str, str]) -> dict[str, list[dict[str, str]]]:
        if params["status"] == "executed":
            return {
                "orders": [
                    {"ticker": "LIVE", "fill_count_fp": "2.00"},
                    {"ticker": "OLD", "fill_count_fp": "1.00"},
                    {"ticker": "UNFILLED", "fill_count_fp": "0.00"},
                ]
            }
        return {"orders": []}


class _FakeClientWithMarketPositions:
    def get_positions(self) -> dict[str, list[dict[str, str]]]:
        return {"market_positions": [{"ticker": "MKT", "position_fp": "4.00"}]}

    def get_orders(self, params: dict[str, str]) -> dict[str, list[dict[str, str]]]:
        return {"orders": []}

    def get(self, path: str) -> dict[str, dict[str, str]]:
        assert path == "/markets/MKT"
        return {"market": {"yes_bid_dollars": "0.0800"}}


class _FakeClientWithNoMarketPosition:
    def get_positions(self) -> dict[str, list[dict[str, str]]]:
        return {"market_positions": [{"ticker": "MKT", "position_fp": "-4.00"}]}

    def get_orders(self, params: dict[str, str]) -> dict[str, list[dict[str, str]]]:
        return {"orders": []}

    def get(self, path: str) -> dict[str, dict[str, str]]:
        assert path == "/markets/MKT"
        return {
            "market": {
                "yes_bid_dollars": "0.8000",
                "yes_ask_dollars": "0.9200",
                "no_bid_dollars": "0.0800",
                "no_ask_dollars": "0.2000",
            }
        }


class _FakeClientWithWorseLiveAsk:
    def get(self, path: str) -> dict[str, dict[str, str]]:
        assert path == "/markets/MKT"
        return {"market": {"yes_bid_dollars": "0.1000", "yes_ask_dollars": "0.2200"}}

    def create_order(self, payload):
        raise AssertionError("worse live price should block order placement")


class _FakeClientWithZeroLiveAsk:
    def get(self, path: str) -> dict[str, dict[str, str]]:
        assert path == "/markets/MKT"
        return {"market": {"yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.0000"}}

    def create_order(self, payload):
        raise AssertionError("zero live ask should block order placement")


class _FakeClientWithZeroBidPosition(_FakeClientWithMarketPositions):
    def get(self, path: str) -> dict[str, dict[str, str]]:
        assert path == "/markets/MKT"
        return {"market": {"yes_bid_dollars": "0.0000"}}


def _config() -> RuntimeConfig:
    return RuntimeConfig(
        database_url="postgresql://example",
        kalshi_base_url="https://external-api.demo.kalshi.co/trade-api/v2",
        kalshi_api_key_id="key",
        kalshi_private_key_path="key.pem",
        max_order_cost=3.0,
    )


def _market_row(ask: float) -> MarketRow:
    return MarketRow(
        city="nyc",
        event_ticker="EVT",
        market_ticker="MKT",
        target_date=date(2026, 7, 13),
        snapshot_time_utc=datetime(2026, 7, 13, 12, tzinfo=UTC),
        checkpoint="utc_12",
        yes_bid=0.10,
        yes_ask=ask,
        no_bid=max(0.0, 1.0 - ask),
        no_ask=0.90,
        last_price=None,
        market_probability=0.15,
        bracket_index=1,
        bracket_lower_f=80,
        bracket_upper_f=81,
        weather={
            "nws_anchor_high_f": 80.0,
            "observed_high_so_far_f": 79.0,
            "hrrr_projected_high_f": 81.0,
            "nbm_projected_high_f": 80.0,
            "ensemble_raw_median_high_f": 80.0,
            "hours_elapsed": 10.0,
            "hours_remaining": 4.0,
        },
        settlement_temperature_f=80.0,
    )
