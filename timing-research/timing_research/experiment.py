"""Fixed revision experiment: decisions first, outcomes in a separate artifact."""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal
from statistics import mean

from .reader import digest, stamp


def fee(price: float, multiplier: float) -> float:
    """Conservative full-cent taker fee proxy for one contract; no rebates."""
    p = Decimal(str(price))
    return float(
        (Decimal(".07") * Decimal(str(multiplier)) * p * (1 - p)).quantize(
            Decimal(".01"), rounding=ROUND_CEILING
        )
    )


def hit_lower_bound(wins: int, total: int):
    """One-sided 95% exact binomial bound; descriptive IID reference only."""
    if total == 0:
        return None
    if wins == 0:
        return 0.0
    low, high = 0.0, 1.0
    for _ in range(80):
        p = (low + high) / 2
        probability = sum(
            math.comb(total, k) * p**k * (1 - p) ** (total - k) for k in range(wins, total + 1)
        )
        if probability < 0.05:
            low = p
        else:
            high = p
    return (low + high) / 2


def quote_key(quote):
    return [(row["ticker"], row["lower"], row["upper"]) for row in quote["rows"]]


def exit_quote(entry: dict, quotes: list[dict], minutes: int, tolerance: int):
    target = stamp(entry["received"]) + timedelta(minutes=minutes)
    lower, upper = target - timedelta(seconds=tolerance), target + timedelta(seconds=tolerance)
    eligible = [
        quote
        for quote in quotes
        if quote["event"] == entry["event"]
        and stamp(quote["requested"]) > stamp(entry["received"])
        and lower <= stamp(quote["requested"]) <= stamp(quote["received"]) <= upper
    ]
    if not eligible:
        return None
    first = min(eligible, key=lambda quote: (stamp(quote["requested"]), quote["raw_id"]))
    return first if quote_key(first) == quote_key(entry) else None


def revision(previous, current, protocol):
    if not previous or not previous["signal_valid"] or not current["signal_valid"]:
        return None, "missing_adjacent_forecast"
    if (previous["city"], previous["event"], previous["target_date"]) != (
        current["city"],
        current["event"],
        current["target_date"],
    ):
        return None, "different_event"
    gap = (current["ready"] - previous["ready"]).total_seconds()
    low, high = protocol["adjacent_seconds"]
    if not low <= gap <= high:
        return None, "nonadjacent_receipts"
    if (
        max(previous["source_age_minutes"], current["source_age_minutes"])
        > protocol["max_source_age_minutes"]
    ):
        return None, "stale_forecast"
    if current["version"] < previous["version"] or current["generated"] < previous["generated"]:
        return None, "version_reversal"
    delta = current["high"] - previous["high"]
    if delta and current["version"] == previous["version"]:
        return None, "changed_without_new_version"
    return delta, "valid"


def choose_probe(entry, old_high, new_high, protocol):
    if not entry:
        return None, "missing_entry"
    rule = protocol["probe"]
    sigma = rule["sigma_f"]

    def cdf(x):
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    choices = []
    for row in entry["rows"]:
        lo = -math.inf if row["lower"] is None else row["lower"] - 0.5
        hi = math.inf if row["upper"] is None else row["upper"] + 0.5
        old = cdf((hi - old_high) / sigma) - cdf((lo - old_high) / sigma)
        new = cdf((hi - new_high) / sigma) - cdf((lo - new_high) / sigma)
        if (
            old - new >= rule["minimum_mass_reduction"]
            and rule["ask_range"][0] <= row["no_ask"] <= rule["ask_range"][1]
            and row["no_ask"] - row["no_bid"] <= rule["max_spread"] + 1e-9
        ):
            choices.append(
                {
                    "ticker": row["ticker"],
                    "side": "no",
                    "contracts": 1,
                    "ask": row["no_ask"],
                    "mass_reduction": old - new,
                    "entry_depth": row["no_ask_size"],
                }
            )
    if not choices:
        return None, "no_price_eligible_probe"
    # Rank before any future quote or settlement is seen. No replacement if the
    # chosen candidate lacks depth or later loses its quote.
    chosen = min(choices, key=lambda row: (-row["mass_reduction"], row["ticker"]))
    chosen["depth_verified"] = chosen["entry_depth"] is not None and chosen["entry_depth"] >= 1
    return chosen, "eligible" if chosen["depth_verified"] else "missing_or_insufficient_entry_depth"


def decide(observations: list[dict], protocol: dict) -> dict:
    previous, seen_revisions, seen_controls = {}, set(), set()
    transitions, intentions, controls = [], [], []
    quotes = [row["quote"] for row in observations if row["quote"]]
    start, end = stamp(protocol["start"]), stamp(protocol["end"])
    for current in sorted(
        observations, key=lambda row: (row["snapshot"], row["city"], row["run_id"])
    ):
        city = current["city"]
        prev, previous[city] = previous.get(city), current
        when = current.get("ready", current["snapshot"])
        if not start <= when < end:
            continue
        delta, reason = revision(prev, current, protocol)
        audit_row = {
            "city": city,
            "run_id": current["run_id"],
            "time": when,
            "reason": reason,
            "revision_f": delta,
        }
        transitions.append(audit_row)
        if delta is None:
            continue
        low, high = protocol["climate_hours"]
        if not low <= current["climate_hour"] <= high:
            audit_row["reason"] = "outside_climate_window"
            continue
        event_key = (city, current["event"], current["target_date"])
        entry = current["quote"]
        base = {
            "city": city,
            "event": current["event"],
            "target_date": current["target_date"],
            "signal_time": current["ready"],
            "climate_hour": current["climate_hour"],
            "revision_f": delta,
            "old_high": prev["high"],
            "new_high": current["high"],
            "old_version": prev["version"],
            "new_version": current["version"],
            "previous_raw_ids": prev["raw_ids"],
            "raw_ids": current["raw_ids"],
            "entry": entry,
        }
        base["intention_id"] = digest(
            {"protocol": digest(protocol), "run_id": current["run_id"], "city": city}
        )
        control_key = (*event_key, math.floor(current["climate_hour"]))
        if delta == 0 and control_key not in seen_controls:
            seen_controls.add(control_key)
            controls.append(base)
        if abs(delta) < protocol["material_revision_f"] or event_key in seen_revisions:
            continue
        seen_revisions.add(event_key)
        baseline = []
        if entry:
            for control in controls:
                if (
                    control["city"] != city
                    or control["target_date"] >= current["target_date"]
                    or not control["entry"]
                    or math.floor(control["climate_hour"]) != math.floor(current["climate_hour"])
                ):
                    continue
                age = (
                    date.fromisoformat(current["target_date"])
                    - date.fromisoformat(control["target_date"])
                ).days
                if not 1 <= age <= protocol["control_lookback_days"]:
                    continue
                if (
                    abs(control["entry"]["centroid"] - entry["centroid"])
                    > protocol["control_max_centroid_difference"]
                ):
                    continue
                past = exit_quote(
                    control["entry"],
                    quotes,
                    protocol["primary_horizon_minutes"],
                    protocol["exit_tolerance_seconds"],
                )
                # Availability is enforced at the signal, not the later entry.
                if past and stamp(past["received"]) < current["ready"]:
                    baseline.append(
                        {
                            "control_id": control["intention_id"],
                            "outcome_raw_id": past["raw_id"],
                            "available_at": past["received"],
                            "change": past["centroid"] - control["entry"]["centroid"],
                        }
                    )
        probe, probe_reason = choose_probe(entry, prev["high"], current["high"], protocol)
        intentions.append(
            {
                **base,
                "probe": probe,
                "probe_reason": probe_reason,
                "baseline_controls": baseline,
                "baseline_change": mean(row["change"] for row in baseline)
                if len(baseline) >= protocol["control_min_days"]
                else None,
            }
        )
    return {"intentions": intentions, "controls": controls, "transitions": transitions}


def settlement_index(rows: list[dict], cutoff) -> dict:
    result = {}
    for row in rows:
        if row.get("validation_status") != "valid" or row.get("source_provider") != "kalshi":
            continue
        if stamp(row["settled_at_utc"]) > cutoff:
            continue
        event = row["event_ticker"]
        if event in result and result[event]["winner_ticker"] != row["winner_ticker"]:
            raise ValueError("conflicting_settlement_labels")
        result[event] = row
    return result


def attach_outcomes(
    decisions: dict, observations: list[dict], labels: list[dict], protocol: dict
) -> list[dict]:
    quotes = [row["quote"] for row in observations if row["quote"]]
    settlements = settlement_index(labels, stamp(protocol["score_not_before"]))
    scored = []
    for intention in decisions["intentions"] + decisions["controls"]:
        entry, probe = intention["entry"], intention.get("probe")
        record = {
            "intention_id": intention["intention_id"],
            "city": intention["city"],
            "target_date": intention["target_date"],
            "event": intention["event"],
            "kind": "revision" if intention["revision_f"] else "control",
            "revision_f": intention["revision_f"],
            "horizons": {},
            "settlement": None,
        }
        for horizon in protocol["horizons_minutes"]:
            later = (
                exit_quote(entry, quotes, horizon, protocol["exit_tolerance_seconds"])
                if entry
                else None
            )
            outcome = {
                "reason": "missing_entry" if not entry else "missing_exit",
                "price_change": None,
                "net_markout": None,
                "fee_stress_net_markout": None,
                "paired_signed_change": None,
            }
            if later:
                change = later["centroid"] - entry["centroid"]
                outcome.update(
                    reason="price_only",
                    price_change=change,
                    exit_raw_id=later["raw_id"],
                    exit_received=later["received"],
                    actual_horizon_minutes=(
                        stamp(later["received"]) - stamp(entry["received"])
                    ).total_seconds()
                    / 60,
                )
                if (
                    intention["revision_f"]
                    and intention.get("baseline_change") is not None
                    and horizon == protocol["primary_horizon_minutes"]
                ):
                    outcome["paired_signed_change"] = math.copysign(1, intention["revision_f"]) * (
                        change - intention["baseline_change"]
                    )
                if probe:
                    row = next(row for row in later["rows"] if row["ticker"] == probe["ticker"])
                    depth = row["no_bid_size"]
                    outcome["reason"] = "missing_or_insufficient_depth"
                    if probe["depth_verified"] and depth is not None and depth >= 1:
                        buy = probe["ask"] + protocol["adverse_execution_dollars"]
                        sell = max(0, row["no_bid"] - protocol["adverse_execution_dollars"])
                        outcome.update(
                            reason="hypothetical_depth_checked",
                            buy_price=buy,
                            sell_price=sell,
                            net_markout=sell
                            - buy
                            - fee(buy, protocol["taker_multiplier_assumption"])
                            - fee(sell, protocol["taker_multiplier_assumption"]),
                            fee_stress_net_markout=sell
                            - buy
                            - fee(buy, protocol["fee_multiplier_stress"])
                            - fee(sell, protocol["fee_multiplier_stress"]),
                        )
            record["horizons"][str(horizon)] = outcome
        label = settlements.get(intention["event"])
        if label and entry and probe and probe["depth_verified"]:
            if not label.get("raw_payload_id"):
                raise ValueError("settlement_missing_provenance")
            universe = {row["ticker"] for row in entry["rows"]}
            if label["winner_ticker"] not in universe or stamp(label["settled_at_utc"]) <= stamp(
                entry["received"]
            ):
                raise ValueError("settlement_universe_or_time_mismatch")
            buy = probe["ask"] + protocol["adverse_execution_dollars"]
            hit = int(label["winner_ticker"] != probe["ticker"])
            costs = fee(buy, protocol["taker_multiplier_assumption"])
            record["settlement"] = {
                "hit": hit,
                "debit": buy + costs,
                "fees": costs,
                "net_pnl": hit - buy - costs,
                "source_raw_id": label.get("raw_payload_id"),
                "settled_at": label["settled_at_utc"],
            }
        scored.append(record)
    return scored


def interval(rows: list[dict], field: str, protocol: dict) -> dict:
    good = [row for row in rows if row.get(field) is not None]
    if not good:
        return {"n": 0, "days": 0, "mean": None, "interval_95": [None, None]}
    days = defaultdict(list)
    for row in good:
        days[row["target_date"]].append(row[field])
    groups = [days[key] for key in sorted(days)]
    rng = random.Random(protocol["bootstrap_seed"])
    draws = []
    for _ in range(protocol["bootstrap_draws"]):
        values = [value for _ in groups for value in groups[rng.randrange(len(groups))]]
        draws.append(mean(values))
    draws.sort()
    return {
        "n": len(good),
        "days": len(days),
        "mean": mean(row[field] for row in good),
        "interval_95": [
            draws[int(0.025 * len(draws))],
            draws[min(len(draws) - 1, int(0.975 * len(draws)))],
        ],
    }


def summarize(decisions: dict, outcomes: list[dict], quality: dict, protocol: dict) -> dict:
    primary = str(protocol["primary_horizon_minutes"])
    revisions = [row for row in outcomes if row["kind"] == "revision"]
    flat = [{**row, **row["horizons"][primary]} for row in revisions]
    paired = interval(flat, "paired_signed_change", protocol)
    net = interval(flat, "net_markout", protocol)
    stress = interval(flat, "fee_stress_net_markout", protocol)
    gate = protocol["gate"]
    cities = Counter(row["city"] for row in revisions)
    probes = [row for row in decisions["intentions"] if row["probe"]]
    daily = []
    start, end = stamp(protocol["start"]).date(), stamp(protocol["end"]).date()
    for index in range((end - start).days):
        day = (start + timedelta(days=index)).isoformat()
        rows = [row for row in revisions if row["target_date"] == day]
        settled = [row["settlement"] for row in rows if row["settlement"] is not None]
        daily.append(
            {
                "date": day,
                "revisions": len(rows),
                "price_pairs": sum(
                    row["horizons"][primary]["price_change"] is not None for row in rows
                ),
                "settled_probes": len(settled),
                "wins": sum(row["hit"] for row in settled),
                "net_pnl": sum(row["net_pnl"] for row in settled),
                "fees": sum(row["fees"] for row in settled),
                "cash_debit": sum(row["debit"] for row in settled),
            }
        )
    checks = {
        "input_integrity": not quality["integrity_errors"],
        "cycle_coverage": quality["cycle_coverage"] >= gate["minimum_cycle_coverage"],
        "city_cycle_coverage": quality["valid_city_cycle_coverage"]
        >= gate["minimum_city_cycle_coverage"],
        "enough_revision_events": len(revisions) >= gate["minimum_revision_events"],
        "enough_revision_days": len({row["target_date"] for row in revisions})
        >= gate["minimum_revision_days"],
        "enough_cities": len(cities) >= gate["minimum_cities"],
        "city_concentration": bool(cities)
        and max(cities.values()) / len(revisions) <= gate["max_city_fraction"],
        "enough_paired_controls": paired["n"] >= gate["minimum_paired_events"],
        "paired_lower_bound_positive": paired["interval_95"][0] is not None
        and paired["interval_95"][0] > 0,
        "price_outcome_coverage": bool(revisions)
        and sum(row["price_change"] is not None for row in flat) / len(revisions)
        >= gate["minimum_outcome_coverage"],
        "enough_depth_checked_probes": net["n"] >= gate["minimum_probes"],
        "net_lower_bound_positive": net["interval_95"][0] is not None and net["interval_95"][0] > 0,
        "fee_stress_lower_bound_positive": stress["interval_95"][0] is not None
        and stress["interval_95"][0] > 0,
        "probe_outcome_coverage": bool(probes)
        and net["n"] / len(probes) >= gate["minimum_outcome_coverage"],
    }
    weeks = []
    for week in (0, 1):
        values = [
            row["net_markout"]
            for row in flat
            if row["net_markout"] is not None
            and (date.fromisoformat(row["target_date"]) - start).days // 7 == week
        ]
        weeks.append(
            {
                "week": week + 1,
                "n": len(values),
                "mean_net_markout": mean(values) if values else None,
            }
        )
    leave_city_out = {}
    for city in protocol["cities"]:
        values = [
            row["net_markout"]
            for row in flat
            if row["city"] != city and row["net_markout"] is not None
        ]
        leave_city_out[city] = mean(values) if values else None
    checks["both_weeks_positive"] = all(
        row["mean_net_markout"] is not None and row["mean_net_markout"] > 0 for row in weeks
    )
    checks["leave_one_city_out_positive"] = all(
        value is not None and value > 0 for value in leave_city_out.values()
    )
    mechanism_keys = [
        key
        for key in checks
        if key
        not in (
            "enough_depth_checked_probes",
            "net_lower_bound_positive",
            "fee_stress_lower_bound_positive",
            "probe_outcome_coverage",
            "both_weeks_positive",
            "leave_one_city_out_positive",
        )
    ]
    settled = [row["settlement"] for row in revisions if row["settlement"] is not None]
    settlement_stats = interval(
        [
            {**row, "pnl": row["settlement"]["net_pnl"]}
            for row in revisions
            if row["settlement"] is not None
        ],
        "pnl",
        protocol,
    )
    hit_stats = interval(
        [
            {**row, "hit": row["settlement"]["hit"]}
            for row in revisions
            if row["settlement"] is not None
        ],
        "hit",
        protocol,
    )
    horizons = {}
    for horizon in protocol["horizons_minutes"]:
        rows = [
            {
                **row,
                "signed_change": math.copysign(1, row["revision_f"])
                * row["horizons"][str(horizon)]["price_change"],
            }
            for row in revisions
            if row["horizons"][str(horizon)]["price_change"] is not None
        ]
        horizons[str(horizon)] = interval(rows, "signed_change", protocol)
    return {
        "status": "eligible_for_further_paper_research" if all(checks.values()) else "not_promoted",
        "live_trading_authorized": False,
        "gate_checks": checks,
        "mechanism_gate_passed": all(checks[key] for key in mechanism_keys),
        "revision_events": len(revisions),
        "controls": len(decisions["controls"]),
        "probe_candidates": len(probes),
        "paired_signed_change": paired,
        "net_markout": net,
        "fee_stress_markout": stress,
        "settlement_mean_pnl": settlement_stats,
        "hit_rate_day_bootstrap": hit_stats,
        "signed_price_change_by_horizon": horizons,
        "weekly_markouts": weeks,
        "leave_one_city_out_mean_markout": leave_city_out,
        "settled_probes": len(settled),
        "hit_rate": mean(row["hit"] for row in settled) if settled else None,
        "hit_rate_iid_exact_95_lower_bound": hit_lower_bound(
            sum(row["hit"] for row in settled), len(settled)
        ),
        "settlement_net_pnl": sum(row["net_pnl"] for row in settled),
        "daily": daily,
        "exclusions": dict(Counter(row["reason"] for row in decisions["transitions"])),
        "limitations": [
            "Five-minute samples are not executions or evidence of queue priority.",
            "Missing depth blocks economic scoring; no size is inferred.",
            "Controls are observational, not randomized; city weather may be correlated.",
            "The mechanism gate does not certify an 80% hit rate.",
            "The exact hit-rate bound assumes IID outcomes; the day bootstrap is empirical "
            "and can degenerate at all wins/losses.",
        ],
    }
