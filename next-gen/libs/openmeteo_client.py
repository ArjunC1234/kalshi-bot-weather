"""Read-only Open-Meteo API client helpers."""

from __future__ import annotations

from typing import Any

import requests

from libs.errors import SourceError


class OpenMeteoClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self.session = requests.Session()

    def get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        response = self.session.get(url, params=params, timeout=self.timeout)
        if response.status_code >= 400:
            raise SourceError(f"Open-Meteo request failed {response.status_code}: {response.text}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise SourceError("Open-Meteo returned non-object payload")
        return payload
