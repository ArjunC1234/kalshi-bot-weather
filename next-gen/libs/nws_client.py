"""Read-only NWS API client helpers."""

from __future__ import annotations

from typing import Any

import requests

from libs.constants import NWS_API_BASE_URL
from libs.errors import SourceError


class NwsClient:
    def __init__(
        self,
        user_agent: str,
        base_url: str = NWS_API_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "application/geo+json, application/json",
            }
        )

    def get_json(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = (
            path_or_url
            if path_or_url.startswith("http")
            else f"{self.base_url}/{path_or_url.lstrip('/')}"
        )
        response = self.session.get(url, params=params or {}, timeout=self.timeout)
        if response.status_code >= 400:
            raise SourceError(f"NWS request failed {response.status_code}: {response.text}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise SourceError("NWS returned non-object payload")
        return payload
