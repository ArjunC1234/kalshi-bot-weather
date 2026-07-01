"""Shared constants and city definitions."""

from __future__ import annotations

from libs.models import City

DEFAULT_SUPABASE_BUCKET = "weather-research-raw"
SUPABASE_TABLES = (
    "collector_runs",
    "raw_payloads",
    "events",
    "market_snapshots",
    "weather_snapshots",
    "model_outputs",
    "settlements",
    "provider_errors",
)

KALSHI_API_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
NWS_API_BASE_URL = "https://api.weather.gov"
OPEN_METEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
OPEN_METEO_GFS_URL = "https://api.open-meteo.com/v1/gfs"

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

MODEL_MARKET_MIDPOINT = "market_midpoint"
MODEL_BASELINE = "baseline"
