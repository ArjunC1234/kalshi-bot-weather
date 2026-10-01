"""Runtime config and city definitions for collector v3."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BUCKET = "weather-research-raw"
DEFAULT_DATA_DIR = Path("collector_spool_v3")


@dataclass(frozen=True)
class City:
    key: str
    name: str
    series_ticker: str
    station_id: str
    latitude: float
    longitude: float
    timezone_name: str
    standard_utc_offset_hours: int
    settlement_aliases: tuple[str, ...]


CITIES: tuple[City, ...] = (
    City(
        "nyc",
        "New York City",
        "KXHIGHNY",
        "KNYC",
        40.77898,
        -73.96925,
        "America/New_York",
        -5,
        ("central park",),
    ),
    City(
        "mia",
        "Miami",
        "KXHIGHMIA",
        "KMIA",
        25.79536,
        -80.29012,
        "America/New_York",
        -5,
        ("miami international airport",),
    ),
    City(
        "la",
        "Los Angeles",
        "KXHIGHLAX",
        "KLAX",
        33.93817,
        -118.38660,
        "America/Los_Angeles",
        -8,
        ("los angeles airport",),
    ),
    City(
        "den",
        "Denver",
        "KXHIGHDEN",
        "KDEN",
        39.84657,
        -104.65623,
        "America/Denver",
        -7,
        ("denver international airport",),
    ),
    City(
        "aus",
        "Austin",
        "KXHIGHAUS",
        "KAUS",
        30.19453,
        -97.66988,
        "America/Chicago",
        -6,
        ("austin bergstrom",),
    ),
    City(
        "okc",
        "Oklahoma City",
        "KXHIGHTOKC",
        "KOKC",
        35.39309,
        -97.60073,
        "America/Chicago",
        -6,
        ("will rogers",),
    ),
)

CITY_BY_KEY = {city.key: city for city in CITIES}


@dataclass(frozen=True)
class Settings:
    nws_user_agent: str
    supabase_url: str | None
    supabase_service_role_key: str | None
    supabase_storage_bucket: str
    database_url: str | None
    collector_data_dir: Path
    weather_company_api_key: str | None
    weather_company_daily_label_url_template: str | None


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def settings_from_env() -> Settings:
    return Settings(
        nws_user_agent=require_env("NWS_USER_AGENT"),
        supabase_url=os.environ.get("SUPABASE_URL"),
        supabase_service_role_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
        supabase_storage_bucket=os.environ.get("SUPABASE_STORAGE_BUCKET", DEFAULT_BUCKET),
        database_url=os.environ.get("DATABASE_URL"),
        collector_data_dir=Path(os.environ.get("COLLECTOR_DATA_DIR", DEFAULT_DATA_DIR)),
        weather_company_api_key=os.environ.get("WEATHER_COMPANY_API_KEY"),
        weather_company_daily_label_url_template=os.environ.get(
            "WEATHER_COMPANY_DAILY_LABEL_URL_TEMPLATE"
        ),
    )


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


def parse_cities(value: str | None) -> tuple[City, ...]:
    if not value:
        return CITIES
    keys = tuple(part.strip().lower() for part in value.split(",") if part.strip())
    unknown = sorted(set(keys) - set(CITY_BY_KEY))
    if unknown:
        raise RuntimeError(f"unknown cities: {', '.join(unknown)}")
    return tuple(CITY_BY_KEY[key] for key in keys)
