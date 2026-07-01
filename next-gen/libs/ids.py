"""Deterministic identifiers for rows and report artifacts."""

from __future__ import annotations

import hashlib

from libs.json_utils import canonical_json_bytes


def sha256_hex(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def deterministic_id(prefix: str, *parts: object, length: int = 32) -> str:
    digest = sha256_hex([prefix, *[str(part) for part in parts]])
    return digest[:length]
