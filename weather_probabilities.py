#!/usr/bin/env python3
"""Compute and plot temperature-bracket probabilities for six Kalshi series."""

from __future__ import annotations

import argparse
import math
import os
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import requests


KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
NWS_BASE_URL = "https://api.weather.gov"
OPEN_METEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
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


class DataError(RuntimeError):
    """Raised when a remote response cannot produce a trustworthy distribution."""


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


CITIES = (
    City("nyc", "New York City", "KXHIGHNY", "KNYC", 40.77898, -73.96925, "America/New_York", -5, ("central park",)),
    City("mia", "Miami", "KXHIGHMIA", "KMIA", 25.79536, -80.29012, "America/New_York", -5, ("miami international airport",)),
    City("la", "Los Angeles", "KXHIGHLAX", "KLAX", 33.93817, -118.38660, "America/Los_Angeles", -8, ("los angeles airport",)),
    City("den", "Denver", "KXHIGHDEN", "KDEN", 39.84657, -104.65623, "America/Denver", -7, ("denver, co", "denver international airport")),
    City("aus", "Austin", "KXHIGHAUS", "KAUS", 30.19453, -97.66988, "America/Chicago", -6, ("austin bergstrom",)),
    City("okc", "Oklahoma City", "KXHIGHTOKC", "KOKC", 35.39309, -97.60073, "America/Chicago", -6, ("oklahoma city", "will rogers")),
)

ENSEMBLE_MODELS = {
    "GEFS": ("gfs_seamless", "ncep_gefs_seamless"),
    "ECMWF IFS": ("ecmwf_ifs025", "ecmwf_ifs025_ensemble"),
    "ICON EPS": ("icon_seamless", "icon_seamless_eps"),
    "GEM": ("gem_global", "gem_global_ensemble"),
}


@dataclass(frozen=True)
class Bracket:
    ticker: str
    label: str
    lower: int | None
    upper: int | None


@dataclass(frozen=True)
class Distribution:
    city: City
    target_date: date
    brackets: tuple[Bracket, ...]
    probabilities: tuple[float, ...]
    nws_high_f: float
    observed_high_f: float | None
    observed_at: datetime | None
    member_highs_f: tuple[float, ...]
    member_weights: tuple[float, ...]
    bandwidth_f: float
    raw_consensus_high_f: float
    center_shift_f: float
    model_counts: tuple[tuple[str, int], ...]
    window_start: datetime
    window_end: datetime
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class NwsForecast:
    high_f: float
    daytime_high_f: float | None
    hourly_high_f: float | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ObservationSummary:
    high_f: float
    latest_at: datetime


@dataclass(frozen=True)
class EnsembleMember:
    model: str
    full_high_f: float
    remaining_high_f: float | None


class HttpClient:
    def __init__(self, user_agent: str, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": user_agent, "Accept": "application/json"}
        )

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise DataError(f"GET {url} failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise DataError(f"GET {url} returned a non-object JSON response")
        return payload


def parse_event_date(event_ticker: str) -> date:
    match = re.search(
        r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(?:-|$)",
        event_ticker.upper(),
    )
    if not match:
        raise DataError(f"cannot parse event date from {event_ticker!r}")
    return date(2000 + int(match.group(1)), MONTHS[match.group(2)], int(match.group(3)))


def parse_bracket(market: dict[str, Any]) -> Bracket:
    ticker = str(market.get("ticker", ""))
    raw_label = market.get("yes_sub_title") or market.get("subtitle")
    if not ticker or not isinstance(raw_label, str):
        raise DataError("market is missing ticker or bracket label")

    label = raw_label.replace("Â", "").strip()
    cleaned = label.lower().replace("fahrenheit", "").replace("°", "")
    cleaned = cleaned.replace("–", "-").replace("—", "-")

    match = re.search(r"(-?\d+)\s*(?:to|-)\s*(-?\d+)", cleaned)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise DataError(f"invalid bracket range {label!r}")
        return Bracket(ticker, label, lower, upper)

    match = re.search(r"(-?\d+)\s*(?:or\s*)?(?:below|lower|less)", cleaned)
    if match:
        return Bracket(ticker, label, None, int(match.group(1)))

    match = re.search(r"(-?\d+)\s*(?:or\s*)?(?:above|higher|more)", cleaned)
    if match:
        return Bracket(ticker, label, int(match.group(1)), None)

    match = re.search(r"(?:less than|below)\s*(-?\d+)", cleaned)
    if match:
        return Bracket(ticker, label, None, int(match.group(1)) - 1)

    match = re.search(r"(?:greater than|above)\s*(-?\d+)", cleaned)
    if match:
        return Bracket(ticker, label, int(match.group(1)) + 1, None)

    raise DataError(f"cannot parse bracket label {label!r} for {ticker}")


def validate_brackets(brackets: Iterable[Bracket]) -> tuple[Bracket, ...]:
    ordered = tuple(
        sorted(brackets, key=lambda item: float("-inf") if item.lower is None else item.lower)
    )
    if len(ordered) < 2:
        raise DataError("event must contain at least two brackets")
    if ordered[0].lower is not None or ordered[-1].upper is not None:
        raise DataError("event brackets must have lower and upper tails")
    if sum(item.lower is None for item in ordered) != 1:
        raise DataError("event must contain exactly one lower-tail bracket")
    if sum(item.upper is None for item in ordered) != 1:
        raise DataError("event must contain exactly one upper-tail bracket")

    for left, right in zip(ordered, ordered[1:]):
        if left.upper is None or right.lower is None or left.upper + 1 != right.lower:
            raise DataError(f"bracket gap or overlap between {left.label!r} and {right.label!r}")
    return ordered


def select_event(
    markets: list[dict[str, Any]], requested_date: date | None, today: date | None = None
) -> tuple[date, list[dict[str, Any]]]:
    grouped: dict[date, list[dict[str, Any]]] = {}
    for market in markets:
        event_ticker = market.get("event_ticker")
        if not isinstance(event_ticker, str):
            raise DataError("market is missing event_ticker")
        grouped.setdefault(parse_event_date(event_ticker), []).append(market)

    if requested_date is not None:
        if requested_date not in grouped:
            raise DataError(f"no open event exists for {requested_date.isoformat()}")
        return requested_date, grouped[requested_date]

    current = today or date.today()
    upcoming = sorted(event_date for event_date in grouped if event_date >= current)
    if not upcoming:
        raise DataError("no current or future open event exists")
    selected = upcoming[0]
    return selected, grouped[selected]


def fetch_open_markets(http: HttpClient, series_ticker: str) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {
            "series_ticker": series_ticker,
            "status": "open",
            "limit": 1000,
        }
        if cursor:
            params["cursor"] = cursor
        payload = http.get_json(f"{KALSHI_BASE_URL}/markets", params)
        batch = payload.get("markets")
        if not isinstance(batch, list):
            raise DataError("Kalshi response is missing its markets list")
        markets.extend(item for item in batch if isinstance(item, dict))
        cursor = payload.get("cursor")
        if not cursor:
            break
    if not markets:
        raise DataError(f"Kalshi returned no open markets for {series_ticker}")
    return markets


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def climate_day_window(city: City, target_date: date) -> tuple[datetime, datetime]:
    standard_zone = timezone(timedelta(hours=city.standard_utc_offset_hours))
    start = datetime.combine(target_date, time.min, tzinfo=standard_zone)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def c_to_f(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


def fetch_nws_high(
    http: HttpClient,
    city: City,
    target_date: date,
    window_start: datetime,
    window_end: datetime,
) -> float:
    point = http.get_json(f"{NWS_BASE_URL}/points/{city.latitude:.4f},{city.longitude:.4f}")
    properties = point.get("properties")
    if not isinstance(properties, dict):
        raise DataError("NWS point response is missing properties")

    forecast_url = properties.get("forecast")
    if isinstance(forecast_url, str):
        forecast = http.get_json(forecast_url)
        periods = forecast.get("properties", {}).get("periods", [])
        for period in periods if isinstance(periods, list) else []:
            try:
                start = parse_datetime(period["startTime"])
                period_date = start.astimezone(timezone(timedelta(hours=city.standard_utc_offset_hours))).date()
                if period_date == target_date and bool(period.get("isDaytime")):
                    value = float(period["temperature"])
                    return c_to_f(value) if str(period.get("temperatureUnit", "F")).upper() == "C" else value
            except (KeyError, TypeError, ValueError):
                continue

    hourly_url = properties.get("forecastHourly")
    if isinstance(hourly_url, str):
        hourly = http.get_json(hourly_url)
        periods = hourly.get("properties", {}).get("periods", [])
        values: list[float] = []
        for period in periods if isinstance(periods, list) else []:
            try:
                timestamp = parse_datetime(period["startTime"]).astimezone(UTC)
                if window_start <= timestamp < window_end:
                    value = float(period["temperature"])
                    if str(period.get("temperatureUnit", "F")).upper() == "C":
                        value = c_to_f(value)
                    values.append(value)
            except (KeyError, TypeError, ValueError):
                continue
        if values:
            return max(values)

    raise DataError(f"NWS has no forecast high for {city.name} on {target_date}")


def fetch_observed_high(
    http: HttpClient, city: City, window_start: datetime, window_end: datetime
) -> float | None:
    now = datetime.now(UTC)
    if now <= window_start:
        return None
    payload = http.get_json(
        f"{NWS_BASE_URL}/stations/{city.station_id}/observations",
        {
            "start": window_start.isoformat().replace("+00:00", "Z"),
            "end": min(now, window_end).isoformat().replace("+00:00", "Z"),
            "limit": 500,
        },
    )
    features = payload.get("features")
    if not isinstance(features, list):
        raise DataError("NWS observations response is missing features")
    values: list[float] = []
    for feature in features:
        try:
            value = feature["properties"]["temperature"]["value"]
            if value is not None:
                values.append(c_to_f(float(value)))
        except (KeyError, TypeError, ValueError):
            continue
    return max(values) if values else None


def extract_member_highs(
    hourly: dict[str, Any], window_start: datetime, window_end: datetime
) -> list[float]:
    raw_times = hourly.get("time")
    if not isinstance(raw_times, list):
        raise DataError("ensemble response is missing hourly timestamps")
    timestamps = [parse_datetime(str(value)).astimezone(UTC) for value in raw_times]
    indexes = [
        index for index, timestamp in enumerate(timestamps) if window_start <= timestamp < window_end
    ]
    if not indexes:
        raise DataError("ensemble response does not cover the climate-day window")

    member_fields = sorted(
        key for key in hourly if key == "temperature_2m" or key.startswith("temperature_2m_member")
    )
    highs: list[float] = []
    for field in member_fields:
        values = hourly.get(field)
        if not isinstance(values, list) or len(values) != len(timestamps):
            continue
        usable = [float(values[index]) for index in indexes if values[index] is not None]
        if usable:
            highs.append(max(usable))
    if len(highs) < 10:
        raise DataError(f"ensemble returned only {len(highs)} usable members")
    return highs


def fetch_ensemble_highs(
    http: HttpClient,
    city: City,
    window_start: datetime,
    window_end: datetime,
) -> list[float]:
    days_needed = max(3, (window_end.date() - datetime.now(UTC).date()).days + 1)
    if days_needed > 16:
        raise DataError("target date is outside the 16-day ensemble forecast horizon")
    payload = http.get_json(
        OPEN_METEO_ENSEMBLE_URL,
        {
            "latitude": city.latitude,
            "longitude": city.longitude,
            "hourly": "temperature_2m",
            "models": "gfs_seamless",
            "forecast_days": days_needed,
            "temperature_unit": "fahrenheit",
            "timezone": "UTC",
        },
    )
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise DataError("ensemble response is missing hourly data")
    return extract_member_highs(hourly, window_start, window_end)


def adjust_member_highs(
    member_highs: Iterable[float], nws_high: float, observed_high: float | None
) -> list[float]:
    values = list(member_highs)
    if not values:
        raise DataError("cannot adjust an empty ensemble")
    offset = nws_high - statistics.median(values)
    adjusted = [value + offset for value in values]
    if observed_high is not None:
        adjusted = [max(value, observed_high) for value in adjusted]
    return adjusted


def kernel_bandwidth(values: Iterable[float]) -> float:
    samples = sorted(values)
    if len(samples) < 2:
        return 1.0
    stddev = statistics.stdev(samples)
    quartiles = statistics.quantiles(samples, n=4, method="inclusive")
    robust_scale = min(stddev, (quartiles[2] - quartiles[0]) / 1.34)
    silverman = 0.9 * robust_scale * len(samples) ** (-0.2)
    return max(1.0, silverman)


def normal_cdf(value: float, mean: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((value - mean) / (sigma * math.sqrt(2.0))))


def mixture_cdf(value: float, member_highs: Iterable[float], bandwidth: float) -> float:
    members = list(member_highs)
    if not members or bandwidth <= 0:
        raise ValueError("mixture requires members and a positive bandwidth")
    return sum(normal_cdf(value, member, bandwidth) for member in members) / len(members)


def bracket_probability(bracket: Bracket, members: Iterable[float], bandwidth: float) -> float:
    if bracket.lower is None:
        return mixture_cdf(bracket.upper + 0.5, members, bandwidth)  # type: ignore[operator]
    if bracket.upper is None:
        return 1.0 - mixture_cdf(bracket.lower - 0.5, members, bandwidth)
    return mixture_cdf(bracket.upper + 0.5, members, bandwidth) - mixture_cdf(
        bracket.lower - 0.5, members, bandwidth
    )


def build_distribution(
    http: HttpClient, city: City, requested_date: date | None
) -> Distribution:
    markets = fetch_open_markets(http, city.series_ticker)
    target_date, event_markets = select_event(markets, requested_date)
    brackets = validate_brackets(parse_bracket(market) for market in event_markets)
    window_start, window_end = climate_day_window(city, target_date)
    nws_high = fetch_nws_high(http, city, target_date, window_start, window_end)
    observed_high = fetch_observed_high(http, city, window_start, window_end)
    raw_members = fetch_ensemble_highs(http, city, window_start, window_end)
    members = adjust_member_highs(raw_members, nws_high, observed_high)
    bandwidth = kernel_bandwidth(members)
    probabilities = tuple(bracket_probability(item, members, bandwidth) for item in brackets)
    if not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise DataError(f"bracket probabilities sum to {sum(probabilities):.12f}, not 1")
    return Distribution(
        city,
        target_date,
        brackets,
        probabilities,
        nws_high,
        observed_high,
        tuple(members),
        bandwidth,
    )


def print_distributions(distributions: Iterable[Distribution]) -> None:
    for distribution in distributions:
        observed = (
            "none" if distribution.observed_high_f is None else f"{distribution.observed_high_f:.1f}°F"
        )
        print(
            f"\n{distribution.city.name} ({distribution.city.series_ticker}) "
            f"- {distribution.target_date.isoformat()}"
        )
        print(
            f"NWS high: {distribution.nws_high_f:.1f}°F | observed high: {observed} | "
            f"ensemble range: {min(distribution.member_highs_f):.1f}–"
            f"{max(distribution.member_highs_f):.1f}°F | bandwidth: {distribution.bandwidth_f:.2f}°F"
        )
        print(f"{'BRACKET':<20} {'PROBABILITY':>11}")
        for bracket, probability in zip(distribution.brackets, distribution.probabilities):
            print(f"{bracket.label:<20} {probability:>10.2%}")


def plot_distributions(distributions: list[Distribution], output_path: Path, show: bool) -> None:
    figure, axes = plt.subplots(2, 3, figsize=(17, 9), constrained_layout=True)
    figure.suptitle("Kalshi Daily High Temperature Probabilities", fontsize=18, fontweight="bold")
    for axis, distribution in zip(axes.flat, distributions):
        x_values = range(len(distribution.brackets))
        percentages = [value * 100 for value in distribution.probabilities]
        bars = axis.bar(x_values, percentages, color="#167D8D", edgecolor="#0B3D46", linewidth=0.8)
        axis.set_title(
            f"{distribution.city.name} | {distribution.target_date.isoformat()}\n"
            f"NWS {distribution.nws_high_f:.0f}°F · σkernel {distribution.bandwidth_f:.1f}°F",
            fontsize=11,
        )
        axis.set_xticks(list(x_values), [item.label for item in distribution.brackets], rotation=28, ha="right")
        axis.set_ylabel("Probability (%)")
        axis.set_ylim(0, max(10.0, max(percentages) * 1.22))
        axis.grid(axis="y", alpha=0.22)
        axis.bar_label(bars, labels=[f"{value:.1f}%" for value in percentages], padding=3, fontsize=8)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=160, bbox_inches="tight")
    print(f"\nSaved dashboard: {output_path.resolve()}")
    if show:
        plt.show()
    else:
        plt.close(figure)


def default_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("output") / f"temperature_probabilities_{timestamp}.png"


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calculate and plot bracket probabilities for six Kalshi temperature markets."
    )
    parser.add_argument("--date", type=parse_date, help="Specific open event date (YYYY-MM-DD).")
    parser.add_argument("--output", type=Path, help="PNG output path.")
    parser.add_argument("--no-show", action="store_true", help="Save the chart without opening a window.")
    return parser


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    user_agent = os.getenv(
        "NWS_USER_AGENT", "kalshi-weather-probabilities/0.1 contact@example.com"
    )
    http = HttpClient(user_agent)
    distributions: list[Distribution] = []
    failures: list[str] = []
    for city in CITIES:
        print(f"Fetching {city.name}...", flush=True)
        try:
            distributions.append(build_distribution(http, city, args.date))
        except Exception as exc:
            failures.append(f"{city.name}: {exc}")

    if failures:
        print("\nUnable to produce all six distributions:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print_distributions(distributions)
    plot_distributions(distributions, args.output or default_output_path(), not args.no_show)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
