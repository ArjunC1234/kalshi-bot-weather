"""Environment and runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from libs.constants import DEFAULT_SUPABASE_BUCKET
from libs.errors import ConfigError


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    service_role_key: str
    storage_bucket: str = DEFAULT_SUPABASE_BUCKET

    @classmethod
    def from_env(cls) -> SupabaseConfig:
        load_dotenv()
        return cls(
            url=require_env("SUPABASE_URL").rstrip("/"),
            service_role_key=require_env("SUPABASE_SERVICE_ROLE_KEY"),
            storage_bucket=os.environ.get("SUPABASE_STORAGE_BUCKET", DEFAULT_SUPABASE_BUCKET),
        )


@dataclass(frozen=True)
class ApiConfig:
    nws_user_agent: str

    @classmethod
    def from_env(cls) -> ApiConfig:
        load_dotenv()
        return cls(nws_user_agent=require_env("NWS_USER_AGENT"))
