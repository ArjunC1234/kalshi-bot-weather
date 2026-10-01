from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from reliable_model_v1.model import (
    Context,
    ContractQuote,
    Policy,
    Quote,
    Settlement,
    WeatherFact,
    align_contexts,
    candidate_for_context,
    fee,
    intentions_for_policy,
    score,
    select_policy,
    success_checks,
)

UTC = timezone.utc
BASE = datetime(2026, 9, 13, 18, tzinfo=UTC)
POLICY = Policy(1, 0.95, 0.05, 18, 75)


def contracts(no_ask_size: float = 2.0) -> tuple[ContractQuote, ...]:
    result = []
    for index in range(6):
        lower = None if index == 0 else 80 + 2 * index
        upper = 81 + 2 * index if index < 5 else None
        result.append(
            ContractQuote(
                ticker=f"T{index}",
                index=index,
                lower=lower,
                upper=upper,
                no_bid=0.87 if index == 0 else 0.92,
                no_ask=0.89 if index == 0 else 0.94,
                no_ask_size=no_ask_size,
            )
        )
    return tuple(result)


def quote(at: datetime, event: str = "EVENT", depth: float = 2.0) -> Quote:
    return Quote(
        city="nyc",
        event=event,
        target_date="2026-09-13",
        raw_id=f"quote-{at.minute}",
        requested=at,
        received=at + timedelta(seconds=1),
        close_time=at + timedelta(hours=10),
        hours_since_climate_start=13,
        contracts=contracts(depth),
        complete=True,
        integrity_reason="ok",
    )


def weather(available: datetime, event: str = "EVENT") -> WeatherFact:
    return WeatherFact(
        city="nyc",
        event=event,
        target_date="2026-09-13",
        source_raw_id="obs",
        available=available,
        observation_time=available - timedelta(minutes=10),
        observed_high=83,
    )


class ReliableModelTests(unittest.TestCase):
    def test_same_snapshot_quote_before_observation_is_not_eligible(self):
        contexts = align_contexts([quote(BASE)], [weather(BASE + timedelta(seconds=5))])
        candidate, reason = candidate_for_context(contexts[0], POLICY)
        self.assertIsNone(candidate)
        self.assertEqual(reason, "no_prior_observation_receipt")

    def test_first_later_quote_is_used_once(self):
        observations = [weather(BASE)]
        contexts = align_contexts(
            [
                quote(BASE - timedelta(seconds=1)),
                quote(BASE + timedelta(minutes=5)),
                quote(BASE + timedelta(minutes=10)),
            ],
            observations,
        )
        intentions, audit = intentions_for_policy(contexts, POLICY, "2026-09-13", "2026-09-13")
        self.assertEqual(len(intentions), 1)
        self.assertEqual(
            intentions[0]["market_requested_at"], (BASE + timedelta(minutes=5)).isoformat()
        )
        self.assertIn("later_eligible_after_event_trade", {row["reason"] for row in audit})

    def test_missing_one_contract_of_depth_blocks_entry(self):
        context = Context(
            quote=quote(BASE + timedelta(minutes=5), depth=0.9),
            weather=weather(BASE),
            availability_valid=True,
            observation_age_minutes=15,
        )
        candidate, reason = candidate_for_context(context, POLICY)
        self.assertIsNone(candidate)
        self.assertEqual(reason, "eliminated_but_not_executable")

    def test_fee_is_full_cent_and_stress_is_not_smaller(self):
        self.assertEqual(fee(0.5), 0.02)
        self.assertGreaterEqual(fee(0.5, 2), fee(0.5))

    def test_loss_and_observation_label_contradiction_are_visible(self):
        context = Context(quote(BASE + timedelta(minutes=5)), weather(BASE), True, 15)
        candidate, _ = candidate_for_context(context, POLICY)
        rows = score(
            [candidate],
            {"EVENT": Settlement("nyc", "EVENT", "2026-09-13", candidate["contract_ticker"], 81)},
        )
        self.assertFalse(rows[0]["hit"])
        self.assertTrue(rows[0]["observation_label_contradiction"])
        self.assertLess(rows[0]["net_pnl"], 0)

    def test_policy_selection_uses_only_supplied_selection_summaries(self):
        strict = Policy(2, 0.90, 0.03, 12, 45)
        loose = POLICY
        base = {
            "settled_trades": 12,
            "hit_rate": 1.0,
            "net_pnl": 1.0,
            "stress_net_pnl": 0.5,
            "observation_label_contradictions": 0,
        }
        requirements = {
            "selection_requirements": {
                "minimum_trades": 12,
                "minimum_hit_rate": 0.8,
                "positive_net_pnl": True,
                "positive_fee_stress_pnl": True,
                "zero_observation_label_contradictions": True,
            }
        }
        selected = select_policy(
            [(loose, base), (strict, {**base, "stress_net_pnl": 0.6})], requirements
        )
        self.assertEqual(selected, strict)

    def test_incomplete_prospective_window_cannot_pass(self):
        summary = {
            "settled_trades": 20,
            "active_days": 14,
            "cities": 6,
            "maximum_city_trade_fraction": 0.2,
            "hit_rate": 1.0,
            "iid_exact_95_hit_lower_bound": 0.8,
            "net_pnl": 1.0,
            "stress_net_pnl": 1.0,
            "day_bootstrap_net_pnl_95_lower": 0.1,
            "seven_day_halves": [{"net_pnl": 0.5}, {"net_pnl": 0.5}],
            "leave_one_city_out_net_pnl": {"nyc": 0.8},
            "label_coverage": 1.0,
            "observation_label_contradictions": 0,
            "availability_violations": 0,
        }
        gates = {
            "prospective_test_end": "2026-09-26",
            "success_requirements": {
                "minimum_trades": 20,
                "minimum_active_days": 7,
                "minimum_cities": 4,
                "maximum_city_trade_fraction": 0.5,
                "minimum_hit_rate": 0.8,
                "minimum_iid_exact_95_hit_lower_bound": 0.6,
                "minimum_label_coverage": 0.95,
            },
        }
        checks = success_checks(summary, gates, date(2026, 9, 18))
        self.assertFalse(checks["full_14_day_window_complete"])
        self.assertFalse(all(checks.values()))


if __name__ == "__main__":
    unittest.main()
