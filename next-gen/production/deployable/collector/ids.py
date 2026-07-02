"""Deterministic IDs and content hashes."""

from __future__ import annotations

import gzip
import hashlib
import json
from typing import Any


def deterministic_id(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:32]


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gzip_json_bytes(payload: Any) -> bytes:
    return gzip.compress(canonical_json_bytes(payload), compresslevel=6)
