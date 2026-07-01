#!/usr/bin/env python3
"""Standalone Kalshi demo weather trading bot.

This script intentionally does not import anything from the parent project.
It is designed to be copied to a server as a self-contained demo trader.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import math
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
except ImportError:  # pragma: no cover - exercised only on minimal dry-run installs.
    hashes = None  # type: ignore[assignment]
    serialization = None  # type: ignore[assignment]
    padding = None  # type: ignore[assignment]


NWS_BASE_URL = "https://api.weather.gov"
OPEN_METEO_HRRR_URL = "https://api.open-meteo.com/v1/gfs"
MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


class BotError(RuntimeError):
    """Raised when a recoverable bot operation cannot complete."""


def cents_to_fixed_dollars(cents: int) -> str:
    return f"{max(1, min(99, int(cents))) / 100:.4f}"


def contracts_to_fixed_count(count: int) -> str:
    return f"{max(1, int(count)):.2f}"


@dataclass(frozen=True)
class City:
    key: str
    name: str
    series_ticker: str
    station_id: str
    latitude: float
    longitude: float
    standard_utc_offset_hours: int


@dataclass(frozen=True)
class Bracket:
    ticker: str
    label: str
    lower: int | None
    upper: int | None
    yes_ask: float | None


@dataclass(frozen=True)
class EventMarket:
    event_ticker: str
    target_date: date
    close_time: datetime | None
    brackets: tuple[Bracket, ...]


@dataclass(frozen=True)
class NwsSnapshot:
    high_f: float
    signature: str
    daily_update_time: str | None
    daily_generated_at: str | None
    hourly_update_time: str | None
    hourly_generated_at: str | None


@dataclass(frozen=True)
class HrrrSnapshot:
    projected_high_f: float
    full_window_high_f: float
    remaining_forecast_high_f: float | None
    hour_count: int
    remaining_hour_count: int


@dataclass(frozen=True)
class ObservationSnapshot:
    observed_high_f: float | None
    latest_at: datetime | None


@dataclass(frozen=True)
class Decision:
    city: City
    event: EventMarket
    selected: Bracket
    probability: float
    fair_edge: float
    nws: NwsSnapshot
    hrrr: HrrrSnapshot | None
    observed: ObservationSnapshot
    probabilities: dict[str, float]
    reason: str


def parse_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_datetime(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_event_date(event_ticker: str) -> date:
    match = re.search(
        r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(?:-|$)",
        event_ticker.upper(),
    )
    if not match:
        raise BotError(f"cannot parse event date from {event_ticker!r}")
    return date(2000 + int(match.group(1)), MONTHS[match.group(2)], int(match.group(3)))


def parse_bracket(market: dict[str, Any]) -> Bracket:
    ticker = str(market.get("ticker") or "")
    raw_label = market.get("yes_sub_title") or market.get("subtitle") or market.get("title")
    if not ticker or not isinstance(raw_label, str):
        raise BotError("market is missing ticker or bracket label")
    label = raw_label.replace("Ã‚", "").replace("Â°", "").replace("�", "").strip()
    cleaned = (
        label.lower()
        .replace("fahrenheit", "")
        .replace("°", "")
        .replace("�", "")
        .replace("â€“", "-")
        .replace("â€”", "-")
    )

    match = re.search(r"(-?\d+)\s*(?:to|-)\s*(-?\d+)", cleaned)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        return Bracket(ticker, label, lower, upper, yes_ask_dollars(market))
    match = re.search(r"(-?\d+)\s*(?:or\s*)?(?:below|lower|less)", cleaned)
    if match:
        return Bracket(ticker, label, None, int(match.group(1)), yes_ask_dollars(market))
    match = re.search(r"(-?\d+)\s*(?:or\s*)?(?:above|higher|more)", cleaned)
    if match:
        return Bracket(ticker, label, int(match.group(1)), None, yes_ask_dollars(market))
    match = re.search(r"(?:less than|below|under)\s*(-?\d+)", cleaned)
    if match:
        return Bracket(ticker, label, None, int(match.group(1)) - 1, yes_ask_dollars(market))
    match = re.search(r"(?:greater than|above|over)\s*(-?\d+)", cleaned)
    if match:
        return Bracket(ticker, label, int(match.group(1)) + 1, None, yes_ask_dollars(market))
    raise BotError(f"cannot parse bracket label {label!r}")


def yes_ask_dollars(market: dict[str, Any]) -> float | None:
    for key in ("yes_ask_dollars", "yes_ask"):
        value = market.get(key)
        if value in (None, ""):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        return parsed / 100.0 if parsed > 1.0 else parsed
    return None


def bracket_sort_key(bracket: Bracket) -> tuple[float, float]:
    return (
        float("-inf") if bracket.lower is None else float(bracket.lower),
        float("inf") if bracket.upper is None else float(bracket.upper),
    )


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise BotError(f"{path} is not a JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def setup_logging(log_file: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=handlers,
    )
    logging.Formatter.converter = time.gmtime


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Http:
    def __init__(self, user_agent: str, timeout: float = 20.0) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        self.timeout = timeout

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise BotError(f"GET {url} returned non-object JSON")
        return payload


class KalshiClient:
    def __init__(
        self,
        base_url: str,
        key_id: str | None,
        private_key_file: Path | None,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.key_id = key_id
        self.private_key = None
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        self.timeout = timeout
        if private_key_file and private_key_file.exists():
            if serialization is None:
                raise BotError("cryptography is required when KALSHI_PRIVATE_KEY_FILE is set")
            self.private_key = serialization.load_pem_private_key(
                private_key_file.read_bytes(),
                password=None,
            )

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        if not self.key_id or self.private_key is None:
            raise BotError("Kalshi API credentials are not configured")
        if hashes is None or padding is None:
            raise BotError("cryptography is required to sign Kalshi API requests")
        timestamp = str(int(time.time() * 1000))
        parsed_path = urlparse(self._url(path)).path
        message = f"{timestamp}{method.upper()}{parsed_path}".encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
        }

    def get_markets(self, series_ticker: str) -> list[dict[str, Any]]:
        markets: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {
                "series_ticker": series_ticker,
                "status": "open",
                "limit": 1000,
            }
            if cursor:
                params["cursor"] = cursor
            response = self.session.get(self._url("/markets"), params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("markets")
            if not isinstance(rows, list):
                raise BotError("Kalshi markets response is missing markets")
            markets.extend(market for market in rows if isinstance(market, dict))
            cursor = payload.get("cursor")
            if not cursor:
                break
        return markets

    def get_positions(self) -> dict[str, int]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):
            params: dict[str, Any] = {"limit": 1000, "count_filter": "position"}
            if cursor:
                params["cursor"] = cursor
            payload = self._get("/portfolio/positions", params)
            market_positions = payload.get("market_positions")
            if isinstance(market_positions, list):
                rows.extend(row for row in market_positions if isinstance(row, dict))
            cursor = payload.get("cursor")
            if not cursor:
                break
        return parse_yes_positions({"market_positions": rows})

    def create_yes_order(
        self,
        event_ticker: str,
        ticker: str,
        yes_price_cents: int,
        count: int,
        client_order_id: str,
        time_in_force: str,
    ) -> dict[str, Any]:
        del event_ticker
        order = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": "bid",
            "price": cents_to_fixed_dollars(yes_price_cents),
            "count": contracts_to_fixed_count(count),
            "time_in_force": time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": False,
            "reduce_only": False,
        }
        return self._post("/portfolio/events/orders", order)

    def create_yes_sell_order(
        self,
        event_ticker: str,
        ticker: str,
        yes_price_cents: int,
        count: int,
        client_order_id: str,
        time_in_force: str,
    ) -> dict[str, Any]:
        del event_ticker
        order = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": "ask",
            "price": cents_to_fixed_dollars(yes_price_cents),
            "count": contracts_to_fixed_count(count),
            "time_in_force": time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False,
            "cancel_order_on_pause": False,
            "reduce_only": True,
        }
        return self._post("/portfolio/events/orders", order)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            self._url(path),
            data=json.dumps(payload, separators=(",", ":")),
            headers=self._auth_headers("POST", path),
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError:
            logging.error("Kalshi POST %s failed: %s %s", path, response.status_code, response.text[:1000])
            raise
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise BotError(f"POST {path} returned non-object JSON")
        return parsed

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(
            self._url(path),
            params=params,
            headers=self._auth_headers("GET", path),
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError:
            logging.error("Kalshi GET %s failed: %s %s", path, response.status_code, response.text[:1000])
            raise
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise BotError(f"GET {path} returned non-object JSON")
        return parsed


def parse_yes_positions(payload: dict[str, Any]) -> dict[str, int]:
    """Return positive YES positions by ticker from Kalshi portfolio payloads."""
    rows: list[Any] = []
    for key in ("market_positions", "positions", "markets"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(value)
    output: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = row.get("ticker") or row.get("market_ticker")
        if not ticker:
            continue
        count = first_int(
            row,
            (
                "position",
                "position_fp",
                "yes_position",
                "contracts",
                "count",
                "quantity",
            ),
        )
        if count is None:
            continue
        output[str(ticker)] = max(0, count)
    return output


def first_int(row: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return None


def load_config(path: Path) -> dict[str, Any]:
    config = load_json(path)
    required = ("cities", "poll_interval_minutes", "min_edge", "contracts")
    missing = [key for key in required if key not in config]
    if missing:
        raise BotError(f"config missing required keys: {', '.join(missing)}")
    return config


def load_cities(config: dict[str, Any]) -> tuple[City, ...]:
    cities = []
    for key, raw in config["cities"].items():
        cities.append(
            City(
                key=str(key),
                name=str(raw["name"]),
                series_ticker=str(raw["series_ticker"]),
                station_id=str(raw["station_id"]),
                latitude=float(raw["latitude"]),
                longitude=float(raw["longitude"]),
                standard_utc_offset_hours=int(raw["standard_utc_offset_hours"]),
            )
        )
    return tuple(cities)


def climate_window(city: City, target_date: date) -> tuple[datetime, datetime]:
    standard_zone = timezone(timedelta(hours=city.standard_utc_offset_hours))
    start = datetime.combine(target_date, dt_time.min, tzinfo=standard_zone).astimezone(UTC)
    return start, start + timedelta(days=1)


def fetch_event(kalshi: KalshiClient, city: City) -> EventMarket:
    markets = kalshi.get_markets(city.series_ticker)
    if not markets:
        raise BotError(f"no open markets for {city.series_ticker}")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for market in markets:
        event_ticker = str(market.get("event_ticker") or "")
        if event_ticker:
            grouped.setdefault(event_ticker, []).append(market)
    if not grouped:
        raise BotError(f"no event tickers in markets for {city.series_ticker}")
    events: list[EventMarket] = []
    for event_ticker, rows in grouped.items():
        try:
            target_date = parse_event_date(event_ticker)
            brackets = tuple(sorted((parse_bracket(row) for row in rows), key=bracket_sort_key))
        except BotError:
            continue
        close_values = [row.get("close_time") for row in rows if row.get("close_time")]
        close_time = parse_datetime(str(close_values[0])) if close_values else None
        events.append(EventMarket(event_ticker, target_date, close_time, brackets))
    if not events:
        raise BotError(f"no parseable events for {city.series_ticker}")
    return min(events, key=lambda event: (event.target_date, event.event_ticker))


def fetch_nws(http: Http, city: City, target_date: date, window_start: datetime, window_end: datetime) -> NwsSnapshot:
    point = http.get_json(f"{NWS_BASE_URL}/points/{city.latitude:.4f},{city.longitude:.4f}")
    properties = point.get("properties")
    if not isinstance(properties, dict):
        raise BotError("NWS points response is missing properties")
    forecast_url = properties.get("forecast")
    hourly_url = properties.get("forecastHourly")
    if not isinstance(forecast_url, str) or not isinstance(hourly_url, str):
        raise BotError("NWS points response is missing forecast URLs")

    daily = http.get_json(forecast_url)
    hourly = http.get_json(hourly_url)
    daily_props = daily.get("properties") if isinstance(daily.get("properties"), dict) else {}
    hourly_props = hourly.get("properties") if isinstance(hourly.get("properties"), dict) else {}

    highs: list[float] = []
    for period in daily_props.get("periods", []):
        if not isinstance(period, dict):
            continue
        try:
            start = parse_datetime(str(period["startTime"]))
            temperature = float(period["temperature"])
        except (KeyError, TypeError, ValueError):
            continue
        if start.date() == target_date:
            highs.append(temperature)
    for period in hourly_props.get("periods", []):
        if not isinstance(period, dict):
            continue
        try:
            start = parse_datetime(str(period["startTime"]))
            temperature = float(period["temperature"])
        except (KeyError, TypeError, ValueError):
            continue
        if window_start <= start < window_end:
            highs.append(temperature)
    if not highs:
        raise BotError(f"NWS has no usable high forecast for {city.key} {target_date}")

    daily_update = str(daily_props.get("updateTime") or "")
    daily_generated = str(daily_props.get("generatedAt") or "")
    hourly_update = str(hourly_props.get("updateTime") or "")
    hourly_generated = str(hourly_props.get("generatedAt") or "")
    signature = "|".join([daily_update, daily_generated, hourly_update, hourly_generated])
    return NwsSnapshot(
        high_f=max(highs),
        signature=signature,
        daily_update_time=daily_update or None,
        daily_generated_at=daily_generated or None,
        hourly_update_time=hourly_update or None,
        hourly_generated_at=hourly_generated or None,
    )


def fetch_observations(http: Http, city: City, window_start: datetime, as_of: datetime) -> ObservationSnapshot:
    payload = http.get_json(
        f"{NWS_BASE_URL}/stations/{city.station_id}/observations",
        {"start": window_start.isoformat(), "end": as_of.isoformat(), "limit": 500},
    )
    features = payload.get("features")
    if not isinstance(features, list):
        return ObservationSnapshot(None, None)
    values: list[tuple[datetime, float]] = []
    for feature in features:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict):
            continue
        raw_temp = props.get("temperature")
        if not isinstance(raw_temp, dict) or raw_temp.get("value") is None:
            continue
        try:
            observed_at = parse_datetime(str(props["timestamp"]))
            celsius = float(raw_temp["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if window_start <= observed_at <= as_of:
            values.append((observed_at, celsius * 9.0 / 5.0 + 32.0))
    if not values:
        return ObservationSnapshot(None, None)
    return ObservationSnapshot(max(value for _, value in values), max(timestamp for timestamp, _ in values))


def fetch_hrrr(
    http: Http,
    city: City,
    window_start: datetime,
    window_end: datetime,
    observed: ObservationSnapshot,
    as_of: datetime,
) -> HrrrSnapshot:
    days_needed = max(2, (window_end.date() - as_of.date()).days + 1)
    if days_needed > 2:
        raise BotError("HRRR is only available for short-range events")
    payload = http.get_json(
        OPEN_METEO_HRRR_URL,
        {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "hourly": "temperature_2m",
            "models": "gfs_hrrr",
            "forecast_days": days_needed,
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
        },
    )
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise BotError("Open-Meteo HRRR response is missing hourly data")
    times = hourly.get("time")
    temps = hourly.get("temperature_2m")
    if not isinstance(times, list) or not isinstance(temps, list) or len(times) != len(temps):
        raise BotError("Open-Meteo HRRR response has malformed hourly series")
    rows: list[tuple[datetime, float]] = []
    for raw_time, raw_temp in zip(times, temps, strict=True):
        if raw_temp is None:
            continue
        timestamp = parse_datetime(str(raw_time))
        if window_start <= timestamp < window_end:
            rows.append((timestamp, float(raw_temp)))
    if not rows:
        raise BotError("HRRR response does not cover the event window")
    remaining = [
        temp
        for timestamp, temp in rows
        if observed.latest_at is None or timestamp > observed.latest_at
    ]
    projected = list(remaining)
    if observed.observed_high_f is not None:
        projected.append(observed.observed_high_f)
    if not projected:
        projected = [temp for _, temp in rows]
    return HrrrSnapshot(
        projected_high_f=max(projected),
        full_window_high_f=max(temp for _, temp in rows),
        remaining_forecast_high_f=max(remaining) if remaining else None,
        hour_count=len(rows),
        remaining_hour_count=len(remaining),
    )


def normal_cdf(value: float, mean: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((value - mean) / (sigma * math.sqrt(2.0))))


def bracket_probability(bracket: Bracket, mean: float, sigma: float, observed_floor: float | None) -> float:
    lower = -math.inf if bracket.lower is None else bracket.lower - 0.5
    upper = math.inf if bracket.upper is None else bracket.upper + 0.5
    if observed_floor is not None and upper <= observed_floor - 0.5:
        return 0.0
    lower_cdf = 0.0 if math.isinf(lower) and lower < 0 else normal_cdf(lower, mean, sigma)
    upper_cdf = 1.0 if math.isinf(upper) and upper > 0 else normal_cdf(upper, mean, sigma)
    return max(0.0, upper_cdf - lower_cdf)


def normalize(values: list[float]) -> list[float]:
    total = sum(values)
    if total <= 0:
        return [1.0 / len(values) for _ in values]
    return [value / total for value in values]


def top3_hrrr_rerank(brackets: tuple[Bracket, ...], probabilities: list[float], hrrr_high: float, penalty: float) -> list[float]:
    hrrr_index = bracket_index_for_temperature(brackets, hrrr_high)
    top = sorted(range(len(probabilities)), key=lambda idx: probabilities[idx], reverse=True)[:3]
    if hrrr_index not in top:
        top[-1] = hrrr_index
        top = sorted(set(top))
    top_mass = sum(probabilities[index] for index in top)
    if top_mass <= 0:
        return probabilities
    scores = {
        index: probabilities[index] * math.exp(-penalty * abs(index - hrrr_index))
        for index in top
    }
    score_total = sum(scores.values())
    output = list(probabilities)
    if score_total <= 0:
        return output
    for index in top:
        output[index] = top_mass * scores[index] / score_total
    return normalize(output)


def bracket_index_for_temperature(brackets: tuple[Bracket, ...], value: float) -> int:
    for index, bracket in enumerate(brackets):
        lower = -math.inf if bracket.lower is None else bracket.lower - 0.5
        upper = math.inf if bracket.upper is None else bracket.upper + 0.5
        if lower <= value < upper:
            return index
    return min(range(len(brackets)), key=lambda idx: abs((brackets[idx].lower or brackets[idx].upper or value) - value))


def build_probabilities(
    event: EventMarket,
    nws: NwsSnapshot,
    hrrr: HrrrSnapshot | None,
    observed: ObservationSnapshot,
    config: dict[str, Any],
) -> dict[str, float]:
    sigma = float(config.get("distribution_sigma_f", 1.75))
    hrrr_weight = float(config.get("nws_hrrr_blend_weight", 0.35))
    mean = nws.high_f
    if hrrr is not None:
        mean = (1.0 - hrrr_weight) * nws.high_f + hrrr_weight * hrrr.projected_high_f
    if observed.observed_high_f is not None:
        mean = max(mean, observed.observed_high_f)
    values = [
        bracket_probability(bracket, mean, sigma, observed.observed_high_f)
        for bracket in event.brackets
    ]
    probabilities = normalize(values)
    if hrrr is not None:
        probabilities = top3_hrrr_rerank(
            event.brackets,
            probabilities,
            hrrr.projected_high_f,
            float(config.get("hrrr_top3_distance_penalty", 0.8)),
        )
    return {
        bracket.ticker: probability
        for bracket, probability in zip(event.brackets, probabilities, strict=True)
    }


def choose_decision(
    city: City,
    event: EventMarket,
    nws: NwsSnapshot,
    hrrr: HrrrSnapshot | None,
    observed: ObservationSnapshot,
    config: dict[str, Any],
) -> Decision:
    probabilities = build_probabilities(event, nws, hrrr, observed, config)
    selected = max(event.brackets, key=lambda bracket: probabilities.get(bracket.ticker, 0.0))
    probability = probabilities[selected.ticker]
    if selected.yes_ask is None:
        return Decision(city, event, selected, probability, 0.0, nws, hrrr, observed, probabilities, "missing yes ask")
    edge = probability - selected.yes_ask
    if selected.yes_ask > float(config.get("max_yes_ask", 0.95)):
        reason = f"yes ask {selected.yes_ask:.2f} above max"
    elif probability < float(config.get("min_probability", 0.0)):
        reason = f"probability {probability:.3f} below min"
    elif edge < float(config.get("min_edge", 0.02)):
        reason = f"edge {edge:.3f} below min"
    else:
        reason = "trade"
    return Decision(city, event, selected, probability, edge, nws, hrrr, observed, probabilities, reason)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": 1,
            "seen_nws_updates": {},
            "orders": [],
            "positions": [],
        }
    return load_json(path)


def update_seen(state: dict[str, Any], key: str, signature: str) -> bool:
    seen = state.setdefault("seen_nws_updates", {})
    if seen.get(key) == signature:
        return False
    seen[key] = signature
    return True


def already_ordered(state: dict[str, Any], order_key: str) -> bool:
    return any(order.get("order_key") == order_key for order in state.get("orders", []))


def record_order(state: dict[str, Any], row: dict[str, Any]) -> None:
    state.setdefault("orders", []).append(row)


def record_buy_position(
    state: dict[str, Any],
    decision: Decision,
    contracts: int,
    client_order_id: str,
    trade_enabled: bool,
) -> None:
    state.setdefault("positions", []).append(
        {
            "position_id": f"{decision.city.key}:{decision.event.event_ticker}:{decision.selected.ticker}:{client_order_id}",
            "status": "buy_submitted" if trade_enabled else "open",
            "city": decision.city.key,
            "event_ticker": decision.event.event_ticker,
            "ticker": decision.selected.ticker,
            "contracts": contracts,
            "opened_at": datetime.now(UTC).isoformat(),
            "open_probability": decision.probability,
            "open_yes_ask": decision.selected.yes_ask,
            "open_edge": decision.fair_edge,
            "open_client_order_id": client_order_id,
            "trade_enabled": trade_enabled,
            "source": "local_order_ledger",
            "exit_attempts": [],
        }
    )


def open_positions_for_city_event(
    state: dict[str, Any], city_key: str, event_ticker: str
) -> list[dict[str, Any]]:
    return [
        position
        for position in state.get("positions", [])
        if position.get("status") == "open"
        and position.get("city") == city_key
        and position.get("event_ticker") == event_ticker
    ]


def should_exit_position(
    position: dict[str, Any],
    decision: Decision,
    config: dict[str, Any],
) -> tuple[bool, str, float, float | None]:
    ticker = str(position["ticker"])
    probability = float(decision.probabilities.get(ticker, 0.0))
    bracket_by_ticker = {bracket.ticker: bracket for bracket in decision.event.brackets}
    bracket = bracket_by_ticker.get(ticker)
    current_ask = bracket.yes_ask if bracket else None
    edge = probability - current_ask if current_ask is not None else 0.0
    if bool(config.get("exit_when_not_top", True)) and ticker != decision.selected.ticker:
        return True, f"held ticker is no longer top; new_top={decision.selected.ticker}", probability, current_ask
    if probability < float(config.get("exit_probability_below", 0.25)):
        return True, f"probability {probability:.3f} below exit threshold", probability, current_ask
    if current_ask is not None and edge < float(config.get("exit_edge_below", -0.02)):
        return True, f"edge {edge:.3f} below exit threshold", probability, current_ask
    return False, "hold", probability, current_ask


def evaluate_exits(
    kalshi: KalshiClient,
    state: dict[str, Any],
    decision: Decision,
    config: dict[str, Any],
    trade_enabled: bool,
    live_positions: dict[str, int] | None,
) -> None:
    if not bool(config.get("exit_enabled", True)):
        return
    positions = positions_to_evaluate(state, decision, live_positions, trade_enabled)
    for position in positions:
        contracts = int(position.get("contracts") or 0)
        if contracts <= 0:
            logging.info("skip exit for %s: no live YES contracts", position.get("ticker"))
            continue
        should_exit, reason, probability, current_ask = should_exit_position(
            position, decision, config
        )
        if not should_exit:
            logging.info(
                "HOLD %s %s prob=%.3f reason=%s",
                decision.city.key,
                position["ticker"],
                probability,
                reason,
            )
            continue
        bracket_by_ticker = {bracket.ticker: bracket for bracket in decision.event.brackets}
        bracket = bracket_by_ticker.get(str(position["ticker"]))
        if bracket is None:
            logging.warning("cannot exit %s: ticker not present in event", position["ticker"])
            continue
        sell_price = bracket.yes_ask if bracket.yes_ask is not None else current_ask
        if sell_price is None:
            logging.warning("cannot exit %s: no current ask/limit proxy", position["ticker"])
            continue
        client_order_id = f"weather-exit-{decision.city.key}-{uuid.uuid4().hex[:12]}"
        exit_record = {
            "created_at": datetime.now(UTC).isoformat(),
            "client_order_id": client_order_id,
            "reason": reason,
            "probability": probability,
            "current_yes_ask": current_ask,
            "sell_limit": sell_price,
            "trade_enabled": trade_enabled,
            "response": None,
        }
        logging.info(
            "EXIT %s %s contracts=%s prob=%.3f sell_limit=%.2f reason=%s",
            decision.city.key,
            position["ticker"],
            contracts,
            probability,
            sell_price,
            reason,
        )
        if trade_enabled:
            response = kalshi.create_yes_sell_order(
                decision.event.event_ticker,
                str(position["ticker"]),
                max(1, min(99, round(float(sell_price) * 100))),
                contracts,
                client_order_id,
                str(config.get("sell_time_in_force", "immediate_or_cancel")),
            )
            exit_record["response"] = response
        position.setdefault("exit_attempts", []).append(exit_record)
        position["status"] = "exit_submitted" if trade_enabled else "exit_dry_run"
        position["closed_at"] = datetime.now(UTC).isoformat()


def positions_to_evaluate(
    state: dict[str, Any],
    decision: Decision,
    live_positions: dict[str, int] | None,
    trade_enabled: bool,
) -> list[dict[str, Any]]:
    if trade_enabled and live_positions is None:
        raise BotError("live position sync is required before live exits")
    if live_positions is not None:
        rows: list[dict[str, Any]] = []
        known = {
            str(position.get("ticker")): position
            for position in open_positions_for_city_event(
                state, decision.city.key, decision.event.event_ticker
            )
        }
        event_tickers = {bracket.ticker for bracket in decision.event.brackets}
        for ticker in sorted(event_tickers):
            contracts = int(live_positions.get(ticker, 0))
            if contracts <= 0:
                continue
            source = dict(known.get(ticker) or {})
            source.update(
                {
                    "status": "open",
                    "city": decision.city.key,
                    "event_ticker": decision.event.event_ticker,
                    "ticker": ticker,
                    "contracts": contracts,
                    "source": "kalshi_live_position",
                }
            )
            rows.append(source)
        return rows
    return open_positions_for_city_event(
        state, decision.city.key, decision.event.event_ticker
    )


def reconcile_local_positions(state: dict[str, Any], live_positions: dict[str, int]) -> None:
    now = datetime.now(UTC).isoformat()
    for position in state.get("positions", []):
        ticker = str(position.get("ticker") or "")
        if not ticker:
            continue
        live_count = int(live_positions.get(ticker, 0))
        position["last_live_contracts"] = live_count
        position["last_reconciled_at"] = now
        if live_count > 0 and position.get("status") in {"buy_submitted", "open"}:
            position["status"] = "open"
        elif live_count <= 0 and position.get("status") in {"buy_submitted", "open", "exit_submitted"}:
            position["status"] = "flat"


def run_once(
    kalshi: KalshiClient,
    http: Http,
    config: dict[str, Any],
    state_path: Path,
    trade_enabled: bool,
) -> None:
    state = load_state(state_path)
    cities = load_cities(config)
    excluded = {str(city).lower() for city in config.get("excluded_cities", [])}
    as_of = datetime.now(UTC)
    live_positions: dict[str, int] | None = None
    if bool(config.get("position_sync_enabled", True)):
        if trade_enabled:
            live_positions = kalshi.get_positions()
            reconcile_local_positions(state, live_positions)
            logging.info(
                "synced live positions: %s open YES markets",
                sum(1 for count in live_positions.values() if count > 0),
            )
        else:
            logging.info("position sync skipped in dry-run mode; using local ledger for exit tests")
    for city in cities:
        if city.key in excluded:
            logging.info("%s skipped: excluded by config", city.key)
            continue
        try:
            event = fetch_event(kalshi, city)
            window_start, window_end = climate_window(city, event.target_date)
            nws = fetch_nws(http, city, event.target_date, window_start, window_end)
            update_key = f"{city.key}:{event.event_ticker}"
            if not update_seen(state, update_key, nws.signature):
                logging.info("%s %s skipped: NWS signature unchanged", city.key, event.event_ticker)
                continue
            observed = fetch_observations(http, city, window_start, as_of)
            try:
                hrrr = fetch_hrrr(http, city, window_start, window_end, observed, as_of)
            except Exception as exc:
                logging.warning("%s HRRR unavailable: %s", city.key, exc)
                hrrr = None
            decision = choose_decision(city, event, nws, hrrr, observed, config)
            log_decision(decision)
            evaluate_exits(kalshi, state, decision, config, trade_enabled, live_positions)
            if decision.reason != "trade":
                continue
            order_key = f"{city.key}:{event.event_ticker}:{nws.signature}:{decision.selected.ticker}"
            if already_ordered(state, order_key):
                logging.info("%s duplicate order skipped for %s", city.key, decision.selected.ticker)
                continue
            client_order_id = f"weather-{city.key}-{event.target_date}-{uuid.uuid4().hex[:12]}"
            order_record = {
                "order_key": order_key,
                "client_order_id": client_order_id,
                "created_at": datetime.now(UTC).isoformat(),
                "city": city.key,
                "event_ticker": event.event_ticker,
                "ticker": decision.selected.ticker,
                "probability": decision.probability,
                "yes_ask": decision.selected.yes_ask,
                "edge": decision.fair_edge,
                "trade_enabled": trade_enabled,
                "response": None,
            }
            contracts = int(config.get("contracts", 1))
            if trade_enabled:
                yes_price_cents = max(1, min(99, round(float(decision.selected.yes_ask) * 100)))
                response = kalshi.create_yes_order(
                    event.event_ticker,
                    decision.selected.ticker,
                    yes_price_cents,
                    contracts,
                    client_order_id,
                    str(config.get("time_in_force", "immediate_or_cancel")),
                )
                order_record["response"] = response
                logging.info("ORDER SENT %s %s response=%s", city.key, decision.selected.ticker, response)
            else:
                logging.info("DRY RUN order %s %s", city.key, order_record)
            record_order(state, order_record)
            record_buy_position(
                state,
                decision,
                contracts,
                client_order_id,
                trade_enabled,
            )
        except Exception as exc:
            logging.exception("%s failed: %s", city.key, exc)
    write_json(state_path, state)


def log_decision(decision: Decision) -> None:
    ask = decision.selected.yes_ask
    hrrr_high = decision.hrrr.projected_high_f if decision.hrrr else None
    logging.info(
        (
            "%s %s selected=%s prob=%.3f ask=%s edge=%.3f reason=%s "
            "nws=%.1f hrrr=%s observed=%s"
        ),
        decision.city.key,
        decision.event.event_ticker,
        decision.selected.ticker,
        decision.probability,
        f"{ask:.2f}" if ask is not None else "none",
        decision.fair_edge,
        decision.reason,
        decision.nws.high_f,
        f"{hrrr_high:.1f}" if hrrr_high is not None else "none",
        f"{decision.observed.observed_high_f:.1f}" if decision.observed.observed_high_f is not None else "none",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone Kalshi demo weather trading bot.")
    parser.add_argument("--config", type=Path, default=Path(os.getenv("CONFIG_FILE", "config.json")))
    parser.add_argument("--state", type=Path, default=Path(os.getenv("STATE_FILE", "state/state.json")))
    parser.add_argument("--log-file", type=Path, default=Path(os.getenv("LOG_FILE", "logs/bot.log")))
    parser.add_argument("--once", action="store_true", help="Run one polling cycle and exit.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--interval-minutes", type=float, help="Override config poll interval.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_dotenv(args.env_file)
    setup_logging(args.log_file)
    try:
        config = load_config(args.config)
        user_agent = os.getenv("NWS_USER_AGENT")
        if not user_agent:
            raise BotError("NWS_USER_AGENT is required")
        kalshi = KalshiClient(
            os.getenv("KALSHI_API_BASE_URL", "https://external-api.demo.kalshi.co/trade-api/v2"),
            os.getenv("KALSHI_API_KEY_ID"),
            Path(os.environ["KALSHI_PRIVATE_KEY_FILE"]) if os.getenv("KALSHI_PRIVATE_KEY_FILE") else None,
        )
        http = Http(user_agent)
        trade_enabled = parse_bool(os.getenv("TRADE_ENABLED")) or bool(config.get("trade_enabled_default", False))
        interval = float(args.interval_minutes or config.get("poll_interval_minutes", 20))
        logging.info("bot started trade_enabled=%s interval_minutes=%.2f", trade_enabled, interval)
        while True:
            started = time.monotonic()
            run_once(kalshi, http, config, args.state, trade_enabled)
            if args.once:
                break
            elapsed = time.monotonic() - started
            time.sleep(max(5.0, interval * 60.0 - elapsed))
        return 0
    except Exception as exc:
        logging.exception("fatal error: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
