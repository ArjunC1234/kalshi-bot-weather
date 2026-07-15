from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from edgecaster.model import (
    EdgecasterConfig,
    build_examples,
    select_trades,
    walk_forward_predictions,
)
from libs.models import BacktestDataset, Bracket, MarketSnapshot, Settlement


def test_build_examples_labels_yes_and_no_rewards() -> None:
    snapshot = datetime(2026, 7, 10, 12, tzinfo=UTC)
    market = MarketSnapshot(
        city="la",
        event_ticker="KXHIGHLAX-26JUL10",
        market_ticker="KXHIGHLAX-26JUL10-B76.5",
        target_date=date(2026, 7, 10),
        snapshot_hour_utc=snapshot,
        bracket=Bracket(
            ticker="KXHIGHLAX-26JUL10-B76.5",
            label="75 to 76",
            lower_f=75,
            upper_f=76,
        ),
        yes_bid=0.20,
        yes_ask=0.25,
        no_bid=0.70,
        no_ask=0.75,
    )
    dataset = BacktestDataset(
        markets=[market],
        settlements=[
            Settlement(
                city="la",
                event_ticker="KXHIGHLAX-26JUL10",
                target_date=date(2026, 7, 10),
                settled_at_utc=snapshot + timedelta(days=1),
                winner_ticker=market.market_ticker,
            )
        ],
    )
    examples = build_examples(
        dataset,
        {
            ("la", "KXHIGHLAX-26JUL10", snapshot): {
                "KXHIGHLAX-26JUL10-B76.5": 0.40
            }
        },
    )

    rewards = {(example.side, example.entry_ask): example.reward for example in examples}
    assert rewards[("yes", 0.25)] == 0.75
    assert rewards[("no", 0.75)] == -0.75


def test_walk_forward_falls_back_to_cloud_edge_with_tiny_training_set() -> None:
    snapshot = datetime(2026, 7, 10, 12, tzinfo=UTC)
    market = MarketSnapshot(
        city="nyc",
        event_ticker="KXHIGHNY-26JUL10",
        market_ticker="KXHIGHNY-26JUL10-B90.5",
        target_date=date(2026, 7, 10),
        snapshot_hour_utc=snapshot,
        bracket=Bracket(
            ticker="KXHIGHNY-26JUL10-B90.5",
            label="90 to 91",
            lower_f=90,
            upper_f=91,
        ),
        yes_bid=0.20,
        yes_ask=0.30,
        no_bid=0.60,
        no_ask=0.70,
    )
    dataset = BacktestDataset(
        markets=[market],
        settlements=[
            Settlement(
                city="nyc",
                event_ticker="KXHIGHNY-26JUL10",
                target_date=date(2026, 7, 10),
                settled_at_utc=snapshot + timedelta(days=1),
                winner_ticker="other",
            )
        ],
    )
    examples = build_examples(
        dataset,
        {("nyc", "KXHIGHNY-26JUL10", snapshot): {"KXHIGHNY-26JUL10-B90.5": 0.20}},
    )
    predictions = walk_forward_predictions(
        examples,
        EdgecasterConfig(min_training_days=5, min_training_examples=400),
    )
    trades = select_trades(
        predictions,
        dataset,
        EdgecasterConfig(
            min_predicted_reward=0.05,
            min_cloud_edge=0.0,
            max_spread=0.15,
            max_entry_price=0.90,
            allow_fallback_trades=True,
        ),
    )

    assert {prediction.model_mode for prediction in predictions} == {"fallback_cloud_edge"}
    assert len(trades) == 1
    assert trades[0].side == "no"
