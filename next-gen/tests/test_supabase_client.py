from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any

from libs.config import SupabaseConfig
from libs.supabase_client import SupabaseClient


@dataclass
class FakeResponse:
    payload: list[dict[str, Any]]
    status_code: int = 200
    text: str = ""

    def json(self) -> list[dict[str, Any]]:
        return self.payload


class FakeSession:
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.headers: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: float,
    ) -> FakeResponse:
        self.calls.append(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return FakeResponse(self.pages.pop(0))


class SupabaseClientTests(unittest.TestCase):
    def test_select_paginates_until_short_page(self) -> None:
        client = SupabaseClient(
            SupabaseConfig(
                url="https://example.supabase.co",
                service_role_key="test-key",
            )
        )
        client.page_size = 2
        fake_session = FakeSession(
            [
                [{"id": 1}, {"id": 2}],
                [{"id": 3}, {"id": 4}],
                [{"id": 5}],
            ]
        )
        client.session = fake_session  # type: ignore[assignment]

        rows = client.select("weather_snapshots", {"target_date": "gte.2026-07-01"})

        self.assertEqual(rows, [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}, {"id": 5}])
        self.assertEqual(
            [call["headers"]["Range"] for call in fake_session.calls],
            ["0-1", "2-3", "4-5"],
        )
        self.assertTrue(
            all(
                call["params"] == {"select": "*", "target_date": "gte.2026-07-01"}
                for call in fake_session.calls
            )
        )


if __name__ == "__main__":
    unittest.main()
