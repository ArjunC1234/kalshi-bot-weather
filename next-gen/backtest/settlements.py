"""Settlement validation helpers."""

from __future__ import annotations

from collections.abc import Iterable

from libs.errors import DataValidationError
from libs.models import Settlement


def require_valid_settlement(settlement: Settlement) -> None:
    if settlement.validation_status != "valid":
        raise DataValidationError(f"settlement {settlement.event_ticker} is not valid")
    if not settlement.winner_ticker:
        raise DataValidationError("settlement winner_ticker is required")


def settlement_by_event(settlements: Iterable[Settlement]) -> dict[tuple[str, str], Settlement]:
    result: dict[tuple[str, str], Settlement] = {}
    for settlement in settlements:
        require_valid_settlement(settlement)
        key = (settlement.city, settlement.event_ticker)
        if key in result:
            raise DataValidationError(f"duplicate settlement for {key}")
        result[key] = settlement
    return result
