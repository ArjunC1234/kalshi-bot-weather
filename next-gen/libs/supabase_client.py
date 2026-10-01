"""Thin Supabase REST and Storage client using requests."""

from __future__ import annotations

import gzip
import json
from typing import Any

import requests

from libs.config import SupabaseConfig
from libs.errors import SourceError


class SupabaseClient:
    page_size = 1000

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
        rows: list[dict[str, Any]] = []
        offset = 0
        primary_keys = {
            "events": "event_id", "market_snapshots": "market_snapshot_id",
            "weather_snapshots": "weather_snapshot_id", "settlements": "settlement_id",
            "final_temperature_labels": "final_temperature_label_id", "raw_payloads": "raw_payload_id",
        }
        query = {"select": "*", **(params or {})}
        if table in primary_keys:
            # Range pagination without a stable order can repeat or omit facts.
            query.setdefault("order", f"{primary_keys[table]}.asc")
        while True:
            response = self.session.get(
                f"{self.config.url}/rest/v1/{table}",
                params=query,
                headers={
                    "Accept": "application/json",
                    "Range": f"{offset}-{offset + self.page_size - 1}",
                },
                timeout=self.timeout,
            )
            if response.status_code >= 400:
                raise SourceError(
                    f"Supabase select {table} failed {response.status_code}: {response.text}"
                )
            payload = response.json()
            if not isinstance(payload, list):
                raise SourceError(f"Supabase select {table} returned non-list payload")
            rows.extend(row for row in payload if isinstance(row, dict))
            if len(payload) < self.page_size:
                return rows
            offset += self.page_size

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
