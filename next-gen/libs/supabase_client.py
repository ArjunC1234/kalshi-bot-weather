"""Thin Supabase REST and Storage client using requests."""

from __future__ import annotations

import gzip
import json
from typing import Any

import requests

from libs.config import SupabaseConfig
from libs.errors import SourceError


class SupabaseClient:
    def __init__(self, config: SupabaseConfig, timeout: float = 30.0) -> None:
        self.config = config
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "apikey": config.service_role_key,
                "Authorization": f"Bearer {config.service_role_key}",
            }
        )

    @classmethod
    def from_env(cls) -> SupabaseClient:
        return cls(SupabaseConfig.from_env())

    def select(self, table: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        response = self.session.get(
            f"{self.config.url}/rest/v1/{table}",
            params={"select": "*", **(params or {})},
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise SourceError(
                f"Supabase select {table} failed {response.status_code}: {response.text}"
            )
        payload = response.json()
        if not isinstance(payload, list):
            raise SourceError(f"Supabase select {table} returned non-list payload")
        return [row for row in payload if isinstance(row, dict)]

    def download_json_gz(self, storage_path: str) -> Any:
        response = self.session.get(
            f"{self.config.url}/storage/v1/object/{self.config.storage_bucket}/{storage_path}",
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise SourceError(
                f"Supabase storage download failed {response.status_code}: {response.text}"
            )
        return json.loads(gzip.decompress(response.content))
