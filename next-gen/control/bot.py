"""Deferred deployed bot monitoring API stubs."""

from __future__ import annotations

from typing import Any


def bot_stub(resource: str) -> dict[str, Any]:
    return {
        "resource": resource,
        "status": "not_configured",
        "message": "Deployed bot monitoring is reserved for a later Workbench phase.",
        "data": [],
    }
