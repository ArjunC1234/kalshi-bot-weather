"""Read-only Kalshi API client helpers."""

from __future__ import annotations

from typing import Any

import requests

from libs.constants import KALSHI_API_BASE_URL
from libs.errors import SourceError


class KalshiClient:
    def __init__(self, base_url: str = KALSHI_API_BASE_URL, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def get_markets(self, params: dict[str, Any]) -> dict[str, Any]:
        response = self.session.get(f"{self.base_url}/markets", params=params, timeout=self.timeout)
        if response.status_code >= 400:
            raise SourceError(f"Kalshi get_markets failed {response.status_code}: {response.text}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise SourceError("Kalshi get_markets returned non-object payload")
        return payload
