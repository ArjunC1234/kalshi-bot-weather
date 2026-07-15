"""Authenticated Kalshi REST client for demo trading."""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class KalshiApiError(RuntimeError):
    def __init__(self, method: str, path: str, status_code: int, body: str) -> None:
        super().__init__(f"Kalshi {method} {path} failed {status_code}: {body[:1000]}")
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body


class KalshiTradingClient:
    def __init__(
        self,
        base_url: str,
        api_key_id: str,
        private_key_path: str | Path,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key_id = api_key_id
        self.private_key = serialization.load_pem_private_key(
            _resolve_private_key_path(private_key_path).read_bytes(),
            password=None,
        )
        self.timeout = timeout
        self.session = requests.Session()

    def get_balance(self) -> dict[str, Any]:
        return self.get("/portfolio/balance")

    def get_positions(self) -> dict[str, Any]:
        return self.get("/portfolio/positions")

    def get_orders(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.get("/portfolio/orders", params=params)

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/portfolio/events/orders", payload)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self.delete(f"/portfolio/events/orders/{order_id}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json=payload)

    def delete(self, path: str) -> dict[str, Any]:
        return self._request("DELETE", path)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(method, path),
            timeout=self.timeout,
            **kwargs,
        )
        if response.status_code >= 400:
            raise KalshiApiError(method, path, response.status_code, response.text)
        if not response.text:
            return {}
        payload = response.json()
        return payload if isinstance(payload, dict) else {"payload": payload}

    def _headers(self, method: str, path: str) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        sign_path = urlparse(f"{self.base_url}{path}").path
        signature = sign_request(self.private_key, timestamp, method.upper(), sign_path)
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }


def sign_request(private_key: Any, timestamp: str, method: str, path: str) -> str:
    message = f"{timestamp}{method}{path.split('?')[0]}".encode()
    signature = private_key.sign(
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def _resolve_private_key_path(path: str | Path) -> Path:
    configured = Path(path)
    if configured.exists():
        return configured
    local = Path(__file__).resolve().parent / configured.name
    if local.exists():
        return local
    return configured
