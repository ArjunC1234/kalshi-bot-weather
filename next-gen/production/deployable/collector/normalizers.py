"""Normalize provider payloads into immutable collector fact rows."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from config import City
from ids import deterministic_id
from source_families import source_family_features
from time_utils import SnapshotClock, parse_datetime

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

FULL_MONTHS = {
    "JANUARY": 1,
    "FEBRUARY": 2,
    "MARCH": 3,
    "APRIL": 4,
    "MAY": 5,
    "JUNE": 6,
    "JULY": 7,
    "AUGUST": 8,
    "SEPTEMBER": 9,
    "OCTOBER": 10,
    "NOVEMBER": 11,
    "DECEMBER": 12,
}


@dataclass(frozen=True)
class Bracket:
    ticker: str
    label: str
    lower_f: int | None
    upper_f: int | None


@dataclass(frozen=True)
class EnsembleMember:
    model: str
    full_high_f: float
    remaining_high_f: float | None


ENSEMBLE_MODELS = {
    "GEFS": ("gfs_seamless", "ncep_gefs_seamless"),
    "ECMWF IFS": ("ecmwf_ifs025", "ecmwf_ifs025_ensemble"),
    "ICON EPS": ("icon_seamless", "icon_seamless_eps"),
    "GEM": ("gem_global", "gem_global_ensemble"),
}


def parse_event_date(event_ticker: str) -> date:
    match = re.search(
        r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})(?:-|$)",
        event_ticker.upper(),
    )
    if not match:
        raise ValueError(f"cannot parse event date from {event_ticker!r}")
    return date(2000 + int(match.group(1)), MONTHS[match.group(2)], int(match.group(3)))


def parse_bracket(market: dict[str, Any]) -> Bracket:
    ticker = str(market.get("ticker") or "")
    raw_label = market.get("yes_sub_title") or market.get("subtitle") or market.get("title")
    if not ticker or not isinstance(raw_label, str):
        raise ValueError("market is missing ticker or bracket label")
    label = raw_label.replace("Ãƒâ€š", "").strip()
    cleaned = (
        label.lower()
        .replace("fahrenheit", "")
        .replace("�", "")
        .replace("°", "")
        .replace("Ã‚Â°", "")
        .replace("Â°", "")
        .replace("ï¿½", "")
        .replace("Ã¢â‚¬â€œ", "-")
        .replace("Ã¢â‚¬â€", "-")
    )
    match = re.search(r"(-?\d+)\s*(?:to|-)\s*(-?\d+)", cleaned)
    if match:
        lower, upper = int(match.group(1)), int(match.group(2))
        if lower > upper:
            raise ValueError(f"invalid bracket range {label!r}")
        return Bracket(ticker, label, lower, upper)
    match = re.search(r"(-?\d+)\s*(?:or\s*)?(?:below|lower|less)", cleaned)
    if match:
        return Bracket(ticker, label, None, int(match.group(1)))
    match = re.search(r"(-?\d+)\s*(?:or\s*)?(?:above|higher|more)", cleaned)
    if match:
        return Bracket(ticker, label, int(match.group(1)), None)
    match = re.search(r"(?:less than|below|under)\s*(-?\d+)", cleaned)
    if match:
        return Bracket(ticker, label, None, int(match.group(1)) - 1)
    match = re.search(r"(?:greater than|above|over)\s*(-?\d+)", cleaned)
    if match:
        return Bracket(ticker, label, int(match.group(1)) + 1, None)
    raise ValueError(f"cannot parse bracket label {label!r} for {ticker}")


def validate_brackets(brackets: list[Bracket]) -> tuple[Bracket, ...]:
    ordered = tuple(
        sorted(brackets, key=lambda item: float("-inf") if item.lower_f is None else item.lower_f)
    )
    if len(ordered) < 2:
        raise ValueError("event must contain at least two brackets")
    if ordered[0].lower_f is not None or ordered[-1].upper_f is not None:
        raise ValueError("event brackets must have lower and upper tails")
    for left, right in zip(ordered, ordered[1:], strict=False):
        if left.upper_f is None or right.lower_f is None or left.upper_f + 1 != right.lower_f:
            raise ValueError(f"bracket gap or overlap between {left.label!r} and {right.label!r}")
    return ordered


def select_event(
    markets: list[dict[str, Any]], city_local_date: date
) -> tuple[date, list[dict[str, Any]]]:
    grouped: dict[date, list[dict[str, Any]]] = {}
    for market in markets:
        event_ticker = market.get("event_ticker")
        if not isinstance(event_ticker, str):
            continue
        try:
            grouped.setdefault(parse_event_date(event_ticker), []).append(market)
        except ValueError:
            continue
    candidates = sorted(event_date for event_date in grouped if event_date >= city_local_date)
    if not candidates:
        raise ValueError(f"no current or future event for local date {city_local_date.isoformat()}")
    selected = candidates[0]
    return selected, grouped[selected]


def event_row(
    collector_run_id: str,
    city: City,
    target_date: date,
    event_ticker: str,
    clock: SnapshotClock,
    raw_payload_id: str | None,
    markets: list[dict[str, Any]],
    brackets: tuple[Bracket, ...],
) -> dict[str, Any]:
    close_time = _common_close_time(markets)
    settlement_sources = _settlement_sources(markets)
    rules_primary = _common_text(markets, "rules_primary")
    rules_secondary = _common_text(markets, "rules_secondary")
    return {
        "event_id": deterministic_id(
            "event", city.key, event_ticker, clock.snapshot_time_utc.isoformat()
        ),
        "collector_run_id": collector_run_id,
        "city": city.key,
        "city_name": city.name,
        "station_id": city.station_id,
        "latitude": city.latitude,
        "longitude": city.longitude,
        "city_timezone": city.timezone_name,
        "series_ticker": city.series_ticker,
        "event_ticker": event_ticker,
        "target_date": target_date.isoformat(),
        "target_date_local": clock.target_date_local.isoformat(),
        **clock_fields(clock),
        "market_close_time_utc": close_time.isoformat() if close_time else None,
        "is_active_climate_window": (
            clock.climate_day_start_utc <= clock.snapshot_time_utc < clock.climate_day_end_utc
        ),
        "settlement_sources": settlement_sources,
        "rules_primary": rules_primary,
        "rules_secondary": rules_secondary,
        "settlement_source_provider": _infer_market_settlement_source(
            settlement_sources,
            rules_primary,
            rules_secondary,
        ),
        "settlement_station_id": city.station_id,
        "raw_payload_id": raw_payload_id,
        "metadata": {
            "settlement_aliases": city.settlement_aliases,
            "settlement_sources": settlement_sources,
            "rules_primary": rules_primary,
            "rules_secondary": rules_secondary,
            "brackets": [asdict(bracket) for bracket in brackets],
        },
    }


def market_rows(
    collector_run_id: str,
    city: City,
    target_date: date,
    event_ticker: str,
    clock: SnapshotClock,
    brackets: tuple[Bracket, ...],
    markets: list[dict[str, Any]],
    raw_payload_id: str | None,
) -> list[dict[str, Any]]:
    by_ticker = {str(market.get("ticker")): market for market in markets}
    midpoints: list[float] = []
    asks: list[float] = []
    for bracket in brackets:
        market = by_ticker.get(bracket.ticker, {})
        bid = market_float(market, "yes_bid_dollars", "yes_bid")
        ask = market_float(market, "yes_ask_dollars", "yes_ask")
        asks.append(ask or 0.0)
        midpoints.append((bid + ask) / 2.0 if bid is not None and ask is not None else 0.0)
    total_midpoint = sum(midpoints)
    normalized = [value / total_midpoint if total_midpoint > 0 else 0.0 for value in midpoints]
    top_index = (
        max(range(len(normalized)), key=lambda index: normalized[index]) if normalized else 0
    )
    sorted_probs = sorted(normalized, reverse=True)
    top_gap = sorted_probs[0] - sorted_probs[1] if len(sorted_probs) > 1 else 0.0
    sum_asks = sum(asks)
    rows: list[dict[str, Any]] = []
    for index, bracket in enumerate(brackets):
        market = by_ticker.get(bracket.ticker, {})
        bid = market_float(market, "yes_bid_dollars", "yes_bid")
        ask = market_float(market, "yes_ask_dollars", "yes_ask")
        no_bid = market_float(market, "no_bid_dollars", "no_bid")
        no_ask = market_float(market, "no_ask_dollars", "no_ask")
        yes_bid_size = market_float(market, "yes_bid_size", "yes_bid_size_fp")
        yes_ask_size = market_float(market, "yes_ask_size", "yes_ask_size_fp")
        no_bid_size = market_float(market, "no_bid_size", "no_bid_size_fp")
        no_ask_size = market_float(market, "no_ask_size", "no_ask_size_fp")
        if no_bid is None and ask is not None:
            no_bid = max(0.0, 1.0 - ask)
        if no_ask is None and bid is not None:
            no_ask = max(0.0, 1.0 - bid)
        if no_bid_size is None:
            no_bid_size = yes_ask_size
        if no_ask_size is None:
            no_ask_size = yes_bid_size
        rows.append(
            {
                "market_snapshot_id": deterministic_id(
                    "market",
                    city.key,
                    event_ticker,
                    clock.snapshot_time_utc.isoformat(),
                    bracket.ticker,
                ),
                "collector_run_id": collector_run_id,
                "city": city.key,
                "target_date": target_date.isoformat(),
                "target_date_local": clock.target_date_local.isoformat(),
                "event_ticker": event_ticker,
                "market_ticker": bracket.ticker,
                **clock_fields(clock),
                "city_timezone": city.timezone_name,
                "checkpoint_label": clock.checkpoint_label,
                "bracket_index": index,
                "bracket_label": bracket.label,
                "bracket_lower_f": bracket.lower_f,
                "bracket_upper_f": bracket.upper_f,
                "is_lower_tail": bracket.lower_f is None,
                "is_upper_tail": bracket.upper_f is None,
                "yes_bid_dollars": bid,
                "yes_ask_dollars": ask,
                "no_bid_dollars": no_bid,
                "no_ask_dollars": no_ask,
                "last_price_dollars": market_float(market, "last_price_dollars", "last_price"),
                "previous_yes_bid_dollars": market_float(market, "previous_yes_bid_dollars"),
                "previous_yes_ask_dollars": market_float(market, "previous_yes_ask_dollars"),
                "previous_price_dollars": market_float(market, "previous_price_dollars"),
                "volume": market_float(market, "volume", "volume_fp"),
                "volume_24h": market_float(market, "volume_24h", "volume_24h_fp"),
                "liquidity_dollars": market_float(market, "liquidity_dollars"),
                "open_interest": market_float(market, "open_interest", "open_interest_fp"),
                "yes_bid_size": yes_bid_size,
                "yes_ask_size": yes_ask_size,
                "no_bid_size": no_bid_size,
                "no_ask_size": no_ask_size,
                "yes_midpoint": (bid + ask) / 2.0 if bid is not None and ask is not None else None,
                "yes_spread": ask - bid if bid is not None and ask is not None else None,
                "normalized_market_midpoint_probability": normalized[index],
                "market_top_ticker": brackets[top_index].ticker if brackets else None,
                "market_top_probability": normalized[top_index] if normalized else None,
                "market_top_two_gap": top_gap,
                "sum_yes_asks": sum_asks,
                "market_overround_ask": sum_asks - 1.0,
                "settlement_sources": _settlement_sources([market]),
                "rules_primary": market.get("rules_primary"),
                "rules_secondary": market.get("rules_secondary"),
                "raw_payload_id": raw_payload_id,
                "metadata": {
                    "status": market.get("status"),
                    "open_time": market.get("open_time"),
                    "close_time": market.get("close_time"),
                    "expiration_time": market.get("expiration_time"),
                    "expected_expiration_time": market.get("expected_expiration_time"),
                    "rules_primary": market.get("rules_primary"),
                    "rules_secondary": market.get("rules_secondary"),
                    "settlement_sources": _settlement_sources([market]),
                },
            }
        )
    return rows


def weather_row(
    collector_run_id: str,
    city: City,
    target_date: date,
    event_ticker: str,
    clock: SnapshotClock,
    daily: dict[str, Any] | None,
    hourly: dict[str, Any] | None,
    observations: dict[str, Any] | None,
    ensemble: dict[str, Any] | None,
    hrrr: dict[str, Any] | None,
    nbm: dict[str, Any] | None,
    source_payload_ids: dict[str, str],
    settlement_observation_payloads: dict[str, Any] | None = None,
) -> dict[str, Any]:
    daily_high = daily_high_from_payload(daily or {}, clock)
    hourly_values = hourly_rows_from_payload(hourly or {}, clock)
    hourly_high = max((value for _, value in hourly_values), default=None)
    nws_anchor = max(
        [value for value in (daily_high, hourly_high) if value is not None], default=None
    )
    observation_values = observations_from_payload(observations or {}, clock)
    observed_high = max((value for _, value in observation_values), default=None)
    latest_obs = observation_values[-1] if observation_values else None
    settlement_observed = settlement_observation_features(
        {"nws_observations": observation_values},
        clock,
        settlement_observation_payloads,
    )
    settlement_observed_high = settlement_observed.get("settlement_observed_high_so_far_f")
    observed_floor = (
        settlement_observed_high if settlement_observed_high is not None else observed_high
    )
    ensemble_features = ensemble_summary(
        ensemble or {}, clock, latest_obs[0] if latest_obs else None
    )
    hrrr_values = open_meteo_rows(hrrr or {}, clock)
    nbm_values = open_meteo_rows(nbm or {}, clock)
    hrrr_features = time_series_features(
        "hrrr", hrrr_values, clock.snapshot_time_utc, observed_floor
    )
    nbm_features = time_series_features("nbm", nbm_values, clock.snapshot_time_utc, observed_floor)
    source_highs = [
        value
        for value in (
            nws_anchor,
            ensemble_features.get("ensemble_raw_median_high_f"),
            hrrr_features.get("hrrr_projected_high_f"),
            nbm_features.get("nbm_projected_high_f"),
        )
        if isinstance(value, (int, float))
    ]
    family_features = source_family_features(
        {
            "nws_anchor_high_f": nws_anchor,
            "nws_daily_daytime_high_f": daily_high,
            "nws_hourly_window_max_f": hourly_high,
            "observed_high_so_far_f": observed_high,
            "settlement_observed_high_so_far_f": settlement_observed_high,
            "hrrr_projected_high_f": hrrr_features.get("hrrr_projected_high_f"),
            "nbm_projected_high_f": nbm_features.get("nbm_projected_high_f"),
            "ensemble_raw_median_high_f": ensemble_features.get("ensemble_raw_median_high_f"),
        }
    )
    return {
        "weather_snapshot_id": deterministic_id(
            "weather", city.key, event_ticker, clock.snapshot_time_utc.isoformat()
        ),
        "collector_run_id": collector_run_id,
        "city": city.key,
        "target_date": target_date.isoformat(),
        "target_date_local": clock.target_date_local.isoformat(),
        "event_ticker": event_ticker,
        **clock_fields(clock),
        "city_timezone": city.timezone_name,
        "checkpoint_label": clock.checkpoint_label,
        "nws_anchor_high_f": nws_anchor,
        "nws_daily_daytime_high_f": daily_high,
        "nws_hourly_window_max_f": hourly_high,
        "nws_next_3h_max_f": max_in_next_hours(hourly_values, clock.snapshot_time_utc, 3),
        "nws_next_6h_max_f": max_in_next_hours(hourly_values, clock.snapshot_time_utc, 6),
        "nws_next_8h_max_f": max_in_next_hours(hourly_values, clock.snapshot_time_utc, 8),
        "nws_remaining_day_max_f": max(
            (value for ts, value in hourly_values if ts >= clock.snapshot_time_utc), default=None
        ),
        "observed_high_so_far_f": observed_high,
        **settlement_observed,
        "latest_observation_time_utc": latest_obs[0].isoformat() if latest_obs else None,
        "latest_observation_temp_f": latest_obs[1] if latest_obs else None,
        "observation_age_seconds": (clock.snapshot_time_utc - latest_obs[0]).total_seconds()
        if latest_obs
        else None,
        "warming_rate_last_1h_f_per_hour": observed_slope(
            observation_values, clock.snapshot_time_utc, 1
        ),
        "warming_rate_last_3h_f_per_hour": observed_slope(
            observation_values, clock.snapshot_time_utc, 3
        ),
        **{
            key: ensemble_features.get(key)
            for key in (
                "ensemble_raw_median_high_f",
                "ensemble_raw_mean_high_f",
                "ensemble_member_stddev_f",
                "ensemble_family_count",
                "ensemble_member_count",
            )
        },
        "hrrr_projected_high_f": hrrr_features.get("hrrr_projected_high_f"),
        "hrrr_next_3h_max_f": hrrr_features.get("hrrr_next_3h_max_f"),
        "hrrr_next_6h_max_f": hrrr_features.get("hrrr_next_6h_max_f"),
        "hrrr_next_8h_max_f": hrrr_features.get("hrrr_next_8h_max_f"),
        "hrrr_next_3h_slope_f_per_hour": hrrr_features.get("hrrr_next_3h_slope_f_per_hour"),
        "hrrr_next_6h_slope_f_per_hour": hrrr_features.get("hrrr_next_6h_slope_f_per_hour"),
        "hrrr_next_8h_slope_f_per_hour": hrrr_features.get("hrrr_next_8h_slope_f_per_hour"),
        "nbm_projected_high_f": nbm_features.get("nbm_projected_high_f"),
        "nbm_next_3h_max_f": nbm_features.get("nbm_next_3h_max_f"),
        "nbm_next_6h_max_f": nbm_features.get("nbm_next_6h_max_f"),
        "nbm_next_8h_max_f": nbm_features.get("nbm_next_8h_max_f"),
        "nbm_next_3h_slope_f_per_hour": nbm_features.get("nbm_next_3h_slope_f_per_hour"),
        "nbm_next_6h_slope_f_per_hour": nbm_features.get("nbm_next_6h_slope_f_per_hour"),
        "nbm_next_8h_slope_f_per_hour": nbm_features.get("nbm_next_8h_slope_f_per_hour"),
        "all_weather_sources_range_f": max(source_highs) - min(source_highs)
        if len(source_highs) >= 2
        else None,
        "weather_source_stddev_f": stddev(source_highs),
        "nws_hrrr_disagreement_f": diff(nws_anchor, hrrr_features.get("hrrr_projected_high_f")),
        "nws_nbm_disagreement_f": diff(nws_anchor, nbm_features.get("nbm_projected_high_f")),
        "hrrr_nbm_disagreement_f": diff(
            hrrr_features.get("hrrr_projected_high_f"), nbm_features.get("nbm_projected_high_f")
        ),
        **family_features,
        "source_payload_ids": source_payload_ids,
        "features": {
            "nws_daily_update_time": (daily or {}).get("properties", {}).get("updateTime"),
            "nws_daily_generated_at": (daily or {}).get("properties", {}).get("generatedAt"),
            "nws_hourly_update_time": (hourly or {}).get("properties", {}).get("updateTime"),
            "nws_hourly_generated_at": (hourly or {}).get("properties", {}).get("generatedAt"),
            "hrrr_peak_time_utc": hrrr_features.get("hrrr_peak_time_utc"),
            "hrrr_full_window_high_f": hrrr_features.get("hrrr_full_window_high_f"),
            "hrrr_remaining_forecast_high_f": hrrr_features.get("hrrr_remaining_forecast_high_f"),
            "nbm_peak_time_utc": nbm_features.get("nbm_peak_time_utc"),
            "nbm_full_window_high_f": nbm_features.get("nbm_full_window_high_f"),
            "nbm_remaining_forecast_high_f": nbm_features.get("nbm_remaining_forecast_high_f"),
            "ensemble_model_counts": ensemble_features.get("ensemble_model_counts"),
            "ensemble_error": ensemble_features.get("ensemble_error"),
            **settlement_observed,
        },
    }


def settlement_row(
    city: City,
    target_date: date,
    event_ticker: str,
    markets: list[dict[str, Any]],
    brackets: tuple[Bracket, ...],
    raw_payload_id: str | None,
    settled_at_utc: datetime,
) -> dict[str, Any]:
    resolved = [market for market in markets if market.get("result") in ("yes", "no")]
    if not resolved:
        raise ValueError("settlement has no resolved contracts")
    winners = [market for market in resolved if market.get("result") == "yes"]
    if len(winners) != 1:
        raise ValueError(f"settlement must contain exactly one winner, found {len(winners)}")
    winner = winners[0]
    winner_ticker = str(winner["ticker"])
    index = next(
        (idx for idx, bracket in enumerate(brackets) if bracket.ticker == winner_ticker), None
    )
    if index is None:
        raise ValueError("winner ticker is not in event brackets")
    settlement_sources = _settlement_sources(markets)
    rules_primary = _common_text(markets, "rules_primary")
    rules_secondary = _common_text(markets, "rules_secondary")
    market_settlement_source = _infer_market_settlement_source(
        settlement_sources,
        rules_primary,
        rules_secondary,
    )
    return {
        "settlement_id": deterministic_id("settlement", city.key, event_ticker),
        "city": city.key,
        "target_date": target_date.isoformat(),
        "event_ticker": event_ticker,
        "settled_at_utc": settled_at_utc.astimezone(UTC).isoformat(),
        "winner_ticker": winner_ticker,
        "winner_label": winner.get("yes_sub_title") or winner.get("subtitle"),
        "settlement_temperature_f": parse_float(winner.get("expiration_value")),
        "settlement_bracket_index": index,
        "market_settlement_source": market_settlement_source,
        "rules_primary": rules_primary,
        "rules_secondary": rules_secondary,
        "source_provider": "kalshi",
        "raw_payload_id": raw_payload_id,
        "validation_status": "valid",
        "warnings": [],
    }


def final_temperature_label_row(
    city: City,
    target_date: date,
    event_ticker: str,
    station_id: str,
    final_high_f: float,
    product: dict[str, Any],
    raw_payload_id: str | None,
    created_at_utc: datetime,
    warnings: list[str] | None = None,
    source_provider: str = "nws_cli",
) -> dict[str, Any]:
    metadata = product.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    product_id = str(
        product.get("id")
        or product.get("product_id")
        or product.get("observationId")
        or metadata.get("id")
        or ""
    )
    issued_at = (
        product.get("issuanceTime")
        or product.get("issued_at_utc")
        or product.get("validTimeLocal")
        or product.get("validTimeUtc")
        or product.get("obsTimeUtc")
    )
    status = "valid_with_warnings" if warnings else "valid"
    return {
        "final_temperature_label_id": deterministic_id(
            "final_temperature_label", city.key, event_ticker, source_provider
        ),
        "city": city.key,
        "target_date": target_date.isoformat(),
        "event_ticker": event_ticker,
        "station_id": station_id,
        "final_high_f": final_high_f,
        "source_provider": source_provider,
        "product_id": product_id or None,
        "issued_at_utc": str(issued_at) if issued_at else None,
        "raw_payload_id": raw_payload_id,
        "validation_status": status,
        "warnings": warnings or [],
        "created_at_utc": created_at_utc.astimezone(UTC).isoformat(),
    }


def parse_nws_cli_final_high(product: dict[str, Any], target_date: date) -> float | None:
    text = _nws_cli_product_text(product)
    if not text:
        return None
    normalized = text.upper()
    if re.search(r"\bVALID(?:\s+TODAY)?\s+AS\s+OF\b", normalized):
        return None
    report_date = _nws_cli_report_date(normalized)
    if report_date != target_date:
        return None
    in_temperature_section = False
    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if line.startswith("TEMPERATURE"):
            in_temperature_section = True
            continue
        if in_temperature_section and line.startswith("PRECIPITATION"):
            break
        if in_temperature_section:
            match = re.match(r"MAXIMUM\s+(-?\d+(?:\.\d+)?)(?:[A-Z]+)?\b", line)
            if match:
                return float(match.group(1))
    match = re.search(
        r"^\s*MAXIMUM\s+(-?\d+(?:\.\d+)?)(?:[A-Z]+)?\b",
        normalized,
        flags=re.MULTILINE,
    )
    return float(match.group(1)) if match else None


def select_nws_cli_final_high_product(
    products: list[dict[str, Any]], target_date: date
) -> tuple[dict[str, Any], float] | None:
    candidates: list[tuple[datetime, dict[str, Any], float]] = []
    for product in products:
        final_high = parse_nws_cli_final_high(product, target_date)
        if final_high is None:
            continue
        try:
            issued_at = parse_datetime(str(product["issuanceTime"])).astimezone(UTC)
        except (KeyError, TypeError, ValueError):
            issued_at = datetime.min.replace(tzinfo=UTC)
        candidates.append((issued_at, product, final_high))
    if not candidates:
        return None
    _, product, final_high = max(candidates, key=lambda item: item[0])
    return product, final_high


def parse_weather_company_final_high(payload: dict[str, Any], target_date: date) -> float | None:
    """Parse daily final high from a Weather Company label payload.

    This accepts a deliberately small set of explicit field names rather than
    guessing from arbitrary temperatures. If the official payload shape changes,
    the collector should fail closed and record a provider error.
    """

    candidates = _weather_company_candidate_rows(payload)
    for row in candidates:
        if _payload_date(row) not in (None, target_date):
            continue
        value = _first_numeric(
            row,
            (
                "final_high_f",
                "finalHighF",
                "daily_high_f",
                "dailyHighF",
                "temperatureMaxF",
                "temperature_max_f",
                "maxTemperatureF",
                "max_temperature_f",
            ),
        )
        if value is not None:
            return value
        nested = row.get("temperatureMax") or row.get("temperature") or row.get("highTemperature")
        if isinstance(nested, dict):
            value = _first_numeric(nested, ("fahrenheit", "f", "value"))
            if value is not None:
                unit = str(nested.get("unit") or nested.get("units") or "F").upper()
                return value * 9.0 / 5.0 + 32.0 if unit == "C" else value
    return None


def final_high_validation_warnings(
    final_high_f: float,
    event_metadata: Any,
    winner_ticker: str | None,
) -> list[str]:
    if not winner_ticker:
        return []
    metadata = event_metadata if isinstance(event_metadata, dict) else {}
    brackets = metadata.get("brackets")
    if not isinstance(brackets, list):
        return ["cannot validate final high against winner bracket: missing bracket metadata"]
    winner = next(
        (
            item
            for item in brackets
            if isinstance(item, dict) and item.get("ticker") == winner_ticker
        ),
        None,
    )
    if not isinstance(winner, dict):
        return ["cannot validate final high against winner bracket: winner bracket missing"]
    bracket = Bracket(
        str(winner.get("ticker") or ""),
        str(winner.get("label") or ""),
        _optional_int(winner.get("lower_f")),
        _optional_int(winner.get("upper_f")),
    )
    return (
        []
        if bracket_contains_temperature(bracket, final_high_f)
        else [
            (
                f"final high {final_high_f:g}F does not fall inside "
                f"Kalshi winner bracket {winner_ticker}"
            )
        ]
    )


def bracket_contains_temperature(bracket: Bracket, temperature_f: float) -> bool:
    rounded = int(temperature_f + 0.5)
    if bracket.lower_f is not None and rounded < bracket.lower_f:
        return False
    return not (bracket.upper_f is not None and rounded > bracket.upper_f)


def settlement_observation_features(
    source_rows: dict[str, list[tuple[datetime, float]]],
    clock: SnapshotClock,
    extra_payloads: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows_by_source = {
        source: rows
        for source, rows in source_rows.items()
        if rows
    }
    for source, payload in (extra_payloads or {}).items():
        if source in rows_by_source or not isinstance(payload, dict):
            continue
        rows = observations_from_payload(payload, clock)
        if rows:
            rows_by_source[source] = rows

    source_summaries: dict[str, dict[str, Any]] = {}
    highs: list[float] = []
    latest_times: list[datetime] = []
    for source, rows in sorted(rows_by_source.items()):
        high = max((value for _, value in rows), default=None)
        latest = rows[-1][0] if rows else None
        if high is None or latest is None:
            continue
        highs.append(high)
        latest_times.append(latest)
        source_summaries[source] = {
            "high_f": high,
            "latest_time_utc": latest.isoformat(),
            "age_seconds": (clock.snapshot_time_utc - latest).total_seconds(),
            "sample_count": len(rows),
        }

    consensus_high = max(highs) if highs else None
    latest_time = max(latest_times) if latest_times else None
    nws_high = source_summaries.get("nws_observations", {}).get("high_f")
    age_seconds = (
        (clock.snapshot_time_utc - latest_time).total_seconds() if latest_time is not None else None
    )
    source_range = max(highs) - min(highs) if len(highs) >= 2 else 0.0 if highs else None
    source_stddev = stddev(highs) if len(highs) >= 2 else 0.0 if highs else None
    return {
        "settlement_observed_high_so_far_f": consensus_high,
        "settlement_observed_latest_time_utc": latest_time.isoformat() if latest_time else None,
        "settlement_observed_age_seconds": age_seconds,
        "settlement_observed_age_hours": age_seconds / 3600.0 if age_seconds is not None else None,
        "settlement_observed_source_count": len(source_summaries),
        "settlement_observed_source_range_f": source_range,
        "settlement_observed_source_stddev_f": source_stddev,
        "settlement_observed_nws_delta_f": diff(consensus_high, nws_high),
        "settlement_observed_sources": source_summaries,
    }


def clock_fields(clock: SnapshotClock) -> dict[str, Any]:
    return {
        "snapshot_time_utc": clock.snapshot_time_utc.isoformat(),
        "snapshot_time_local": clock.snapshot_time_local.isoformat(),
        "snapshot_local_date": clock.snapshot_local_date.isoformat(),
        "snapshot_local_hour": clock.snapshot_local_hour,
        "climate_day_start_utc": clock.climate_day_start_utc.isoformat(),
        "climate_day_end_utc": clock.climate_day_end_utc.isoformat(),
        "climate_day_start_local": clock.climate_day_start_local.isoformat(),
        "climate_day_end_local": clock.climate_day_end_local.isoformat(),
        "hours_since_climate_start": clock.hours_since_climate_start,
        "hours_until_climate_end": clock.hours_until_climate_end,
        "checkpoint_label": clock.checkpoint_label,
    }


def parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _weather_company_candidate_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [payload]
    for key in ("labels", "daily", "days", "observations", "data", "results"):
        value = payload.get(key)
        if isinstance(value, dict):
            rows.append(value)
        elif isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _payload_date(row: dict[str, Any]) -> date | None:
    for key in (
        "target_date",
        "targetDate",
        "validDate",
        "valid_date",
        "date",
        "obsDate",
        "observationDate",
    ):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            continue
    return None


def _first_numeric(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = parse_float(row.get(key))
        if value is not None:
            return value
    return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _nws_cli_product_text(product: dict[str, Any]) -> str:
    for key in ("productText", "text"):
        value = product.get(key)
        if isinstance(value, str):
            return value
    properties = product.get("properties")
    if isinstance(properties, dict) and isinstance(properties.get("productText"), str):
        return str(properties["productText"])
    return ""


def _nws_cli_report_date(text: str) -> date | None:
    match = re.search(
        r"CLIMATE SUMMARY FOR\s+([A-Z]+)\s+(\d{1,2})\s+(\d{4})",
        text,
    )
    if not match:
        return None
    month = FULL_MONTHS.get(match.group(1))
    if month is None:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(2)))
    except ValueError:
        return None


def market_float(market: dict[str, Any], *keys: str) -> float | None:
    cent_price_fields = {
        "yes_bid", "yes_ask", "no_bid", "no_ask", "last_price",
        "previous_yes_bid", "previous_yes_ask", "previous_price",
    }
    for key in keys:
        value = parse_float(market.get(key))
        if value is not None:
            # Contract quantities, including *_fp strings, are already in contracts.
            return value / 100.0 if key in cent_price_fields else value
    return None


def daily_high_from_payload(payload: dict[str, Any], clock: SnapshotClock) -> float | None:
    periods = payload.get("properties", {}).get("periods", [])
    values: list[float] = []
    for period in periods if isinstance(periods, list) else []:
        try:
            timestamp = parse_datetime(str(period["startTime"]))
            if not bool(period.get("isDaytime")):
                continue
            if not (clock.climate_day_start_utc <= timestamp < clock.climate_day_end_utc):
                continue
            value = float(period["temperature"])
            if str(period.get("temperatureUnit", "F")).upper() == "C":
                value = value * 9.0 / 5.0 + 32.0
            values.append(value)
        except (KeyError, TypeError, ValueError):
            continue
    return max(values) if values else None


def hourly_rows_from_payload(
    payload: dict[str, Any], clock: SnapshotClock
) -> list[tuple[datetime, float]]:
    periods = payload.get("properties", {}).get("periods", [])
    rows: list[tuple[datetime, float]] = []
    for period in periods if isinstance(periods, list) else []:
        try:
            timestamp = parse_datetime(str(period["startTime"]))
            if not (clock.climate_day_start_utc <= timestamp < clock.climate_day_end_utc):
                continue
            value = float(period["temperature"])
            if str(period.get("temperatureUnit", "F")).upper() == "C":
                value = value * 9.0 / 5.0 + 32.0
            rows.append((timestamp, value))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(rows)


def observations_from_payload(
    payload: dict[str, Any], clock: SnapshotClock
) -> list[tuple[datetime, float]]:
    rows: list[tuple[datetime, float]] = []
    for item in _observation_candidate_rows(payload):
        try:
            props = item.get("properties") if isinstance(item.get("properties"), dict) else item
            timestamp = _observation_timestamp(props)
            if timestamp is None:
                continue
            if not (clock.climate_day_start_utc <= timestamp <= clock.snapshot_time_utc):
                continue
            value = _observation_temperature_f(props)
            if value is not None:
                rows.append((timestamp, value))
        except (TypeError, ValueError):
            continue
    return sorted(rows)


def open_meteo_rows(payload: dict[str, Any], clock: SnapshotClock) -> list[tuple[datetime, float]]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return []
    times = hourly.get("time")
    temps = hourly.get("temperature_2m")
    if not isinstance(times, list) or not isinstance(temps, list):
        return []
    rows: list[tuple[datetime, float]] = []
    for raw_time, raw_temp in zip(times, temps, strict=False):
        if raw_temp is None:
            continue
        timestamp = parse_datetime(str(raw_time))
        if clock.climate_day_start_utc <= timestamp < clock.climate_day_end_utc:
            rows.append((timestamp, float(raw_temp)))
    return sorted(rows)


def ensemble_summary(
    payload: dict[str, Any], clock: SnapshotClock, observed_at: datetime | None
) -> dict[str, Any]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return {}
    try:
        members, counts = extract_ensemble_members(hourly, clock, observed_at)
        weights = model_balanced_weights(members, counts)
        highs = [member.full_high_f for member in members]
        mean = sum(value * weight for value, weight in zip(highs, weights, strict=True))
        variance = sum(
            weight * (value - mean) ** 2 for value, weight in zip(highs, weights, strict=True)
        )
        return {
            "ensemble_raw_median_high_f": weighted_quantile(highs, weights, 0.5),
            "ensemble_raw_mean_high_f": mean,
            "ensemble_member_stddev_f": math.sqrt(max(0.0, variance)),
            "ensemble_family_count": len(counts),
            "ensemble_member_count": len(members),
            "ensemble_model_counts": dict(counts),
        }
    except Exception as exc:
        return {"ensemble_error": str(exc)}


def extract_ensemble_members(
    hourly: dict[str, Any],
    clock: SnapshotClock,
    observed_at: datetime | None,
) -> tuple[list[EnsembleMember], tuple[tuple[str, int], ...]]:
    raw_times = hourly.get("time")
    if not isinstance(raw_times, list):
        raise ValueError("ensemble response is missing hourly timestamps")
    timestamps = [parse_datetime(str(value)) for value in raw_times]
    full_indexes = [
        index
        for index, timestamp in enumerate(timestamps)
        if clock.climate_day_start_utc <= timestamp < clock.climate_day_end_utc
    ]
    remaining_indexes = [
        index for index in full_indexes if observed_at is None or timestamps[index] > observed_at
    ]
    members: list[EnsembleMember] = []
    counts: list[tuple[str, int]] = []
    for model, (_, suffix) in ENSEMBLE_MODELS.items():
        fields = sorted(
            key for key in hourly if key.startswith("temperature_2m") and key.endswith(f"_{suffix}")
        )
        model_members = []
        for field in fields:
            values = hourly.get(field)
            if not isinstance(values, list) or len(values) != len(timestamps):
                continue
            full_values = [
                float(values[index]) for index in full_indexes if values[index] is not None
            ]
            remaining_values = [
                float(values[index]) for index in remaining_indexes if values[index] is not None
            ]
            if full_values:
                model_members.append(
                    EnsembleMember(
                        model=model,
                        full_high_f=max(full_values),
                        remaining_high_f=max(remaining_values) if remaining_values else None,
                    )
                )
        if model_members:
            members.extend(model_members)
            counts.append((model, len(model_members)))
    if not members:
        raise ValueError("ensemble response had no usable members")
    return members, tuple(counts)


def model_balanced_weights(
    members: list[EnsembleMember],
    model_counts: tuple[tuple[str, int], ...],
) -> list[float]:
    counts = dict(model_counts)
    model_weight = 1.0 / len(counts)
    return [model_weight / counts[member.model] for member in members]


def weighted_quantile(values: list[float], weights: list[float], quantile: float) -> float:
    pairs = sorted(zip(values, weights, strict=True), key=lambda pair: pair[0])
    threshold = quantile * sum(weight for _, weight in pairs)
    total = 0.0
    for value, weight in pairs:
        total += weight
        if total >= threshold:
            return value
    return pairs[-1][0]


def time_series_features(
    prefix: str,
    rows: list[tuple[datetime, float]],
    as_of: datetime,
    observed_high: float | None,
) -> dict[str, float | str | None]:
    remaining = [(timestamp, value) for timestamp, value in rows if timestamp >= as_of]
    full_high = max((value for _, value in rows), default=None)
    remaining_high = max((value for _, value in remaining), default=None)
    projected_candidates = [value for value in (observed_high, remaining_high) if value is not None]
    projected_high = max(projected_candidates) if projected_candidates else full_high
    peak_time = max(remaining, key=lambda item: item[1])[0].isoformat() if remaining else None
    return {
        f"{prefix}_full_window_high_f": full_high,
        f"{prefix}_remaining_forecast_high_f": remaining_high,
        f"{prefix}_projected_high_f": projected_high,
        f"{prefix}_next_3h_max_f": max_in_next_hours(rows, as_of, 3),
        f"{prefix}_next_6h_max_f": max_in_next_hours(rows, as_of, 6),
        f"{prefix}_next_8h_max_f": max_in_next_hours(rows, as_of, 8),
        f"{prefix}_next_3h_slope_f_per_hour": slope_in_next_hours(rows, as_of, 3),
        f"{prefix}_next_6h_slope_f_per_hour": slope_in_next_hours(rows, as_of, 6),
        f"{prefix}_next_8h_slope_f_per_hour": slope_in_next_hours(rows, as_of, 8),
        f"{prefix}_peak_time_utc": peak_time,
    }


def max_in_next_hours(
    rows: list[tuple[datetime, float]], as_of: datetime, hours: int
) -> float | None:
    end = as_of + _hours(hours)
    values = [value for timestamp, value in rows if as_of <= timestamp <= end]
    return max(values) if values else None


def slope_in_next_hours(
    rows: list[tuple[datetime, float]], as_of: datetime, hours: int
) -> float | None:
    end = as_of + _hours(hours)
    return linear_slope(
        [(timestamp, value) for timestamp, value in rows if as_of <= timestamp <= end]
    )


def observed_slope(rows: list[tuple[datetime, float]], as_of: datetime, hours: int) -> float | None:
    start = as_of - _hours(hours)
    return linear_slope(
        [(timestamp, value) for timestamp, value in rows if start <= timestamp <= as_of]
    )


def linear_slope(rows: list[tuple[datetime, float]]) -> float | None:
    if len(rows) < 2:
        return None
    origin = rows[0][0]
    xs = [(timestamp - origin).total_seconds() / 3600.0 for timestamp, _ in rows]
    ys = [value for _, value in rows]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denominator


def _hours(value: int):
    from datetime import timedelta

    return timedelta(hours=value)


def stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    avg = sum(values) / len(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / len(values))


def diff(left: Any, right: Any) -> float | None:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) - float(right)
    return None


def _observation_candidate_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("features", "observations", "data", "results", "hours", "hourly"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            nested = value.get("observations") or value.get("data") or value.get("results")
            if isinstance(nested, list):
                rows.extend(item for item in nested if isinstance(item, dict))
            else:
                rows.append(value)
    if not rows:
        rows.append(payload)
    return rows


def _observation_timestamp(row: dict[str, Any]) -> datetime | None:
    for key in (
        "timestamp",
        "valid",
        "validTimeUtc",
        "valid_time_utc",
        "validTimeLocal",
        "obsTimeUtc",
        "observation_time_utc",
        "time",
        "dateTime",
    ):
        value = row.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), UTC)
        try:
            return parse_datetime(str(value)).astimezone(UTC)
        except ValueError:
            continue
    return None


def _observation_temperature_f(row: dict[str, Any]) -> float | None:
    nested = row.get("temperature")
    if isinstance(nested, dict):
        value = parse_float(nested.get("value"))
        if value is not None:
            unit = str(nested.get("unitCode") or nested.get("unit") or "C").upper()
            return value if unit.endswith(":DEGF") or unit == "F" else value * 9.0 / 5.0 + 32.0
    for key in (
        "temp_f",
        "temperature_f",
        "temperatureF",
        "temperature",
        "temp",
        "tmpf",
        "airTemperatureF",
    ):
        value = parse_float(row.get(key))
        if value is not None:
            return value
    value = parse_float(row.get("airTemperatureC") or row.get("temperature_c") or row.get("temp_c"))
    return value * 9.0 / 5.0 + 32.0 if value is not None else None


def _settlement_sources(markets: list[dict[str, Any]]) -> dict[str, Any]:
    raw: list[Any] = []
    for market in markets:
        for key in (
            "settlement_sources",
            "settlement_source",
            "settlementSource",
            "settlement_source_id",
            "outcome_source",
        ):
            value = market.get(key)
            if value not in (None, "", [], {}):
                raw.append(value)
    return {"raw": raw} if raw else {}


def _common_text(markets: list[dict[str, Any]], key: str) -> str | None:
    values = sorted({str(market.get(key)).strip() for market in markets if market.get(key)})
    if not values:
        return None
    return values[0] if len(values) == 1 else "\n---\n".join(values)


def _infer_market_settlement_source(
    settlement_sources: dict[str, Any],
    rules_primary: str | None,
    rules_secondary: str | None,
) -> str:
    text = " ".join(
        (
            str(settlement_sources),
            str(rules_primary or ""),
            str(rules_secondary or ""),
        )
    ).lower()
    if "weather company" in text or "weather.com" in text or "weather_company" in text:
        return "weather_company_daily"
    if "daily climate" in text or "nws" in text or "noaa" in text or "cli" in text:
        return "nws_cli_daily"
    return "unknown"


def _common_close_time(markets: list[dict[str, Any]]) -> datetime | None:
    close_times = {
        parse_datetime(str(market["close_time"]))
        for market in markets
        if isinstance(market.get("close_time"), str)
    }
    return close_times.pop() if len(close_times) == 1 else None
