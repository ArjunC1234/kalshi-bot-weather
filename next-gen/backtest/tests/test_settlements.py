from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from backtest.settlements import settlement_by_event
from libs.errors import DataValidationError
from libs.models import Settlement


class SettlementTests(unittest.TestCase):
    def test_duplicate_settlements_rejected(self) -> None:
        settlement = Settlement(
            city="den",
            event_ticker="KXHIGHDEN-26JUL01",
            target_date=date(2026, 7, 1),
            settled_at_utc=datetime(2026, 7, 2, tzinfo=UTC),
            winner_ticker="A",
        )
        with self.assertRaises(DataValidationError):
            settlement_by_event([settlement, settlement])

    def test_invalid_settlement_rejected(self) -> None:
        settlement = Settlement(
            city="den",
            event_ticker="KXHIGHDEN-26JUL01",
            target_date=date(2026, 7, 1),
            settled_at_utc=datetime(2026, 7, 2, tzinfo=UTC),
            winner_ticker="A",
            validation_status="unresolved",
        )
        with self.assertRaises(DataValidationError):
            settlement_by_event([settlement])


if __name__ == "__main__":
    unittest.main()
