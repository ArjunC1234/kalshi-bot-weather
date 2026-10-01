"""Read immutable timing spools without treating nominal snapshots as availability."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

from .contract import (
    CITY_BY_KEY,
    city_clock,
    daily_high_from_payload,
    market_float,
    parse_bracket,
    parse_event_date,
    validate_brackets,
)

DAILY = "timing_nws_daily_forecast"
HOURLY = "timing_nws_hourly_forecast"
MARKET = "timing_kalshi_post_forecast_markets"


def stamp(value) -> datetime:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None:
        raise ValueError("naive timestamp")
    return parsed.astimezone(UTC)


def canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
    ).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def quote_from_raw(raw: dict) -> dict:
    if not raw["success"] or raw["status_code"] != 200:
        raise ValueError("quote_request_failed")
    requested, received = stamp(raw["requested_at_utc"]), stamp(raw["received_at_utc"])
    if requested > received or (received - requested).total_seconds() > 15:
        raise ValueError("quote_latency_or_time_order")
    body = raw["payload"]
    if body.get("cursor"):
        raise ValueError("paginated_quote")
    event = raw["event_ticker"]
    city = raw["city"]
    if city not in CITY_BY_KEY or not event.startswith(CITY_BY_KEY[city].series_ticker + "-"):
        raise ValueError("quote_city_mismatch")
    if parse_event_date(event).isoformat() != raw["target_date"]:
        raise ValueError("quote_target_mismatch")
    selected = [market for market in body["markets"] if market.get("event_ticker") == event]
    brackets = validate_brackets([parse_bracket(market) for market in selected])
    by_ticker = {market["ticker"]: market for market in selected}
    if len(by_ticker) != 6 or len(selected) != 6:
        raise ValueError("duplicate_or_missing_brackets")
    rows = []
    for bracket in brackets:
        market = by_ticker[bracket.ticker]
        if market.get("status") not in ("active", "open"):
            raise ValueError("inactive_market")
        if received >= stamp(market["close_time"]):
            raise ValueError("post_close_quote")
        row = {"ticker": bracket.ticker, "lower": bracket.lower_f, "upper": bracket.upper_f}
        for side in ("yes", "no"):
            for kind in ("bid", "ask"):
                key = f"{side}_{kind}"
                row[key] = number(market_float(market, f"{key}_dollars", key))
                row[f"{key}_size"] = number(market_float(market, f"{key}_size_fp", f"{key}_size"))
                if row[key] is None or not 0 <= row[key] <= 1:
                    raise ValueError("missing_or_invalid_quote_price")
                if row[f"{key}_size"] is not None and row[f"{key}_size"] < 0:
                    raise ValueError("negative_quote_size")
            if row[f"{side}_ask"] < row[f"{side}_bid"]:
                raise ValueError("crossed_quote")
        if (
            abs(row["yes_bid"] + row["no_ask"] - 1) > 0.000001
            or abs(row["yes_ask"] + row["no_bid"] - 1) > 0.000001
        ):
            raise ValueError("inconsistent_binary_quotes")
        rows.append(row)
    weights = [(row["yes_bid"] + row["yes_ask"]) / 2 for row in rows]
    if sum(weights) <= 0:
        raise ValueError("zero_market_mass")
    return {
        "requested": requested,
        "received": received,
        "event": event,
        "raw_id": raw["raw_payload_id"],
        "rows": rows,
        "centroid": sum(i * weight for i, weight in enumerate(weights)) / sum(weights),
    }


def observation(run: dict, city: str, records: list[dict]) -> dict:
    result = {
        "run_id": run["collector_run_id"],
        "city": city,
        "snapshot": stamp(run["snapshot_time_utc"]),
        "quote": None,
        "signal_valid": False,
        "capture_valid": False,
        "issues": [],
        "raw_ids": {},
        "source_age_minutes": None,
    }
    by_endpoint = {}
    for raw in records:
        if raw["endpoint_name"] in by_endpoint:
            result["issues"].append("duplicate_endpoint")
        by_endpoint[raw["endpoint_name"]] = raw
        result["raw_ids"][raw["endpoint_name"]] = raw["raw_payload_id"]
    try:
        result["quote"] = quote_from_raw(by_endpoint[MARKET])
    except (KeyError, TypeError, ValueError) as error:
        result["issues"].append(f"quote:{error}")
    try:
        daily, hourly = by_endpoint[DAILY], by_endpoint[HOURLY]
        event, target = daily["event_ticker"], daily["target_date"]
        if parse_event_date(event).isoformat() != target:
            raise ValueError("event_target_mismatch")
        if not event.startswith(CITY_BY_KEY[city].series_ticker + "-"):
            raise ValueError("city_event_mismatch")
        clock = city_clock(
            CITY_BY_KEY[city], stamp(daily["received_at_utc"]), date.fromisoformat(target)
        )
        ready = max(stamp(daily["received_at_utc"]), stamp(hourly["received_at_utc"]))
        for raw in (daily, hourly):
            if not raw["success"] or raw["status_code"] != 200:
                raise ValueError("forecast_request_failed")
            if raw["event_ticker"] != event or raw["target_date"] != target:
                raise ValueError("source_event_mismatch")
            props = raw["payload"]["properties"]
            if not props.get("periods"):
                raise ValueError("missing_forecast_periods")
            for key in ("updateTime", "generatedAt"):
                if stamp(props[key]) > stamp(raw["received_at_utc"]):
                    raise ValueError("future_forecast_version")
        quote = result["quote"]
        if quote and quote["event"] != event:
            result["issues"].append("quote_event_mismatch")
            result["quote"] = None
        if quote and (
            quote["requested"] < ready or (quote["received"] - ready).total_seconds() > 60
        ):
            result["issues"].append("quote_not_timely_after_forecast")
            result["quote"] = None
        result["capture_valid"] = (
            result["quote"] is not None and len(records) == 4 and not result["issues"]
        )
        props = daily["payload"]["properties"]
        result["source_age_minutes"] = (ready - stamp(props["updateTime"])).total_seconds() / 60
        high = number(daily_high_from_payload(daily["payload"], clock))
        if high is None:
            raise ValueError("missing_target_daytime_high")
        version = stamp(props["updateTime"])
        result.update(
            event=event,
            target_date=target,
            high=high,
            version=version,
            generated=stamp(props["generatedAt"]),
            ready=ready,
            daily_received=stamp(daily["received_at_utc"]),
            climate_hour=(ready - clock.climate_day_start_utc).total_seconds() / 3600,
            source_age_minutes=(ready - version).total_seconds() / 60,
            body_hash=digest(daily["payload"]),
        )
        result["signal_valid"] = "duplicate_endpoint" not in result["issues"]
    except (KeyError, TypeError, ValueError) as error:
        result["issues"].append(f"forecast:{error}")
    return result


def read_captures(path: Path, as_of: datetime) -> dict:
    runs, raw_ids, observations, issues, files = {}, {}, [], [], []
    duplicates = 0
    for source in sorted(path.rglob("*.json.gz")):
        try:
            compressed = source.read_bytes()
            payload = json.loads(gzip.decompress(compressed))
            run = payload["tables"]["collector_runs"][0]
            if run.get("collector_version") != "timing-v1":
                continue
            if stamp(run["completed_at_utc"]) > as_of:
                continue
            key, fingerprint = run["collector_run_id"], digest(payload)
            if key in runs:
                if runs[key][1] != fingerprint:
                    raise ValueError("conflicting_duplicate_run")
                duplicates += 1
                continue
            raw = payload["raw_payloads"]
            local_ids = set()
            for row in raw:
                raw_id = row["raw_payload_id"]
                if raw_id in raw_ids or raw_id in local_ids:
                    raise ValueError("duplicate_receipt_id")
                local_ids.add(raw_id)
                if row["collector_run_id"] != key:
                    raise ValueError("receipt_run_mismatch")
                if not (
                    stamp(run["started_at_utc"])
                    <= stamp(row["requested_at_utc"])
                    <= stamp(row["received_at_utc"])
                    <= stamp(run["completed_at_utc"])
                ):
                    raise ValueError("receipt_time_order")
            normalized = payload["tables"]["raw_payloads"]
            if {row["raw_payload_id"] for row in normalized} != local_ids or len(normalized) != len(
                raw
            ):
                raise ValueError("raw_table_membership_mismatch")
            if run["raw_payload_count"] != len(raw):
                raise ValueError("raw_count_mismatch")
            normalized_by_id = {row["raw_payload_id"]: row for row in normalized}
            for row in raw:
                if normalized_by_id[row["raw_payload_id"]] != {
                    k: v for k, v in row.items() if k != "payload"
                }:
                    raise ValueError("raw_table_metadata_mismatch")
            parsed_observations = []
            for city in CITY_BY_KEY:
                parsed_observations.append(
                    observation(run, city, [row for row in raw if row.get("city") == city])
                )
            runs[key] = (run, fingerprint)
            raw_ids.update({row["raw_payload_id"]: row for row in raw})
            observations.extend(parsed_observations)
            files.append(
                {
                    "path": str(source.resolve()),
                    "sha256": hashlib.sha256(compressed).hexdigest(),
                    "bytes": len(compressed),
                }
            )
        except (
            KeyError,
            ValueError,
            TypeError,
            OSError,
            IndexError,
            EOFError,
            AttributeError,
        ) as error:
            issues.append({"path": str(source), "error": str(error)})
    return {
        "runs": [value[0] for value in runs.values()],
        "observations": observations,
        "raw": list(raw_ids.values()),
        "integrity_errors": issues,
        "files": files,
        "duplicate_copies_ignored": duplicates,
    }


def audit(data: dict, start: datetime, end: datetime) -> dict:
    if end <= start:
        raise ValueError("audit end must follow start")
    # Timer starts at UTC second 30. Include leading/trailing missing slots, not
    # just gaps between successful captures. Explicit bounds are mandatory.
    first = math.ceil((start.timestamp() - 30) / 300) * 300 + 30
    slots = list(range(int(first), math.ceil(end.timestamp()), 300))
    run_slots = {}
    for run in data["runs"]:
        timestamp = stamp(run["snapshot_time_utc"]).timestamp()
        slot = math.floor((timestamp - 30) / 300) * 300 + 30
        if 0 <= timestamp - slot <= 90:
            run_slots.setdefault(slot, []).append(run["collector_run_id"])
    available = {slot: ids[0] for slot, ids in run_slots.items() if slot in slots}
    included = set(available.values())
    rows = [row for row in data["observations"] if row["run_id"] in included]
    raw = [row for row in data["raw"] if row["collector_run_id"] in included]
    counts = Counter(issue for row in rows for issue in row["issues"])
    ages = sorted(
        row["source_age_minutes"] for row in rows if row["source_age_minutes"] is not None
    )
    market_rows = [market for row in rows if row["quote"] for market in row["quote"]["rows"]]
    sizes = sum(market.get("no_ask_size") is not None for market in market_rows)
    by_day = []
    for day in sorted({datetime.fromtimestamp(slot, UTC).date().isoformat() for slot in slots}):
        expected = [
            slot for slot in slots if datetime.fromtimestamp(slot, UTC).date().isoformat() == day
        ]
        ids = {available[slot] for slot in expected if slot in available}
        daily_rows = [row for row in rows if row["run_id"] in ids]
        by_day.append(
            {
                "date": day,
                "expected_cycles": len(expected),
                "captured_cycles": len(ids),
                "valid_city_cycles": sum(row["capture_valid"] for row in daily_rows),
                "expected_city_cycles": len(expected) * 6,
            }
        )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "expected_cycles": len(slots),
        "captured_cycles": len(available),
        "missing_cycles": len(slots) - len(available),
        "duplicate_slot_runs": sum(max(0, len(run_slots.get(slot, [])) - 1) for slot in slots),
        "cycle_coverage": len(available) / len(slots) if slots else 0,
        "valid_city_cycle_coverage": sum(row["capture_valid"] for row in rows) / (len(slots) * 6)
        if slots
        else 0,
        "provider_failures": sum(not row["success"] for row in raw),
        "http_429": sum(row.get("status_code") == 429 for row in raw),
        "integrity_errors": data["integrity_errors"],
        "observation_issues": dict(counts),
        "quote_brackets": len(market_rows),
        "brackets_with_no_ask_size": sizes,
        "source_age_median_minutes": ages[len(ages) // 2] if ages else None,
        "source_age_over_90_minutes": sum(age > 90 for age in ages),
        "immutable_input_bytes": sum(file["bytes"] for file in data["files"]),
        "daily": by_day,
        "duplicate_copies_ignored": data["duplicate_copies_ignored"],
        "note": "Archive presence is not proof of database sync. Missing sizes are never inferred.",
    }
