"""Leakage-resistant monotone-barrier weather market model.

The model buys NO only after an official observation has made a bounded YES
temperature bracket physically impossible.  Policy selection is confined to the
frozen selection interval; prospective outcomes never influence policy choice.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Iterable

UTC = timezone.utc


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def optional_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def fee(price: float, multiplier: float = 1.0) -> float:
    """One-contract Kalshi general taker fee, rounded up to a full cent."""
    p = Decimal(str(price))
    raw = Decimal("0.07") * Decimal(str(multiplier)) * p * (Decimal(1) - p)
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_CEILING))


def hit_lower_bound(wins: int, total: int) -> float | None:
    """One-sided 95% Clopper-Pearson binomial lower bound."""
    if total == 0:
        return None
    if wins == 0:
        return 0.0
    low, high = 0.0, 1.0
    for _ in range(80):
        p = (low + high) / 2
        upper_tail = sum(
            math.comb(total, k) * p**k * (1 - p) ** (total - k) for k in range(wins, total + 1)
        )
        if upper_tail < 0.05:
            low = p
        else:
            high = p
    return (low + high) / 2


@dataclass(frozen=True)
class RawFact:
    requested: datetime
    received: datetime
    endpoint: str


@dataclass(frozen=True)
class WeatherFact:
    city: str
    event: str
    target_date: str
    source_raw_id: str
    available: datetime
    observation_time: datetime
    observed_high: float


@dataclass(frozen=True)
class ContractQuote:
    ticker: str
    index: int
    lower: float | None
    upper: float | None
    no_bid: float | None
    no_ask: float | None
    no_ask_size: float | None


@dataclass(frozen=True)
class Quote:
    city: str
    event: str
    target_date: str
    raw_id: str
    requested: datetime
    received: datetime
    close_time: datetime
    hours_since_climate_start: float
    contracts: tuple[ContractQuote, ...]
    complete: bool
    integrity_reason: str


@dataclass(frozen=True)
class Context:
    quote: Quote
    weather: WeatherFact | None
    availability_valid: bool
    observation_age_minutes: float | None


@dataclass(frozen=True)
class Settlement:
    city: str
    event: str
    target_date: str
    winner_ticker: str
    temperature: float | None


@dataclass(frozen=True)
class Policy:
    minimum_observed_gap_f: int
    maximum_no_ask: float
    maximum_spread: float
    maximum_climate_hour: int
    maximum_observation_age_minutes: int

    @property
    def policy_id(self) -> str:
        return (
            f"gap{self.minimum_observed_gap_f}_ask{self.maximum_no_ask:.2f}_"
            f"spr{self.maximum_spread:.2f}_hr{self.maximum_climate_hour}_"
            f"age{self.maximum_observation_age_minutes}"
        )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_requirements(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_raw_facts(path: Path) -> tuple[dict[str, RawFact], list[str]]:
    facts: dict[str, RawFact] = {}
    errors: list[str] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw_id = row["raw_payload_id"]
            if row.get("success") != "True":
                continue
            fact = RawFact(
                requested=parse_time(row["requested_at_utc"]),
                received=parse_time(row["received_at_utc"]),
                endpoint=row["endpoint_name"],
            )
            previous = facts.get(raw_id)
            if previous is not None and previous != fact:
                errors.append(f"raw payload {raw_id} has conflicting timestamps")
            facts[raw_id] = fact
    return facts, errors


def load_weather(path: Path, raw: dict[str, RawFact]) -> tuple[list[WeatherFact], list[str]]:
    facts: list[WeatherFact] = []
    errors: list[str] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            high = optional_float(row.get("observed_high_so_far_f"))
            observation_time = row.get("latest_observation_time_utc")
            if high is None or not observation_time:
                continue
            try:
                source_ids = json.loads(row["source_payload_ids"])
            except (json.JSONDecodeError, KeyError) as exc:
                errors.append(
                    f"weather {row.get('weather_snapshot_id')} has invalid source IDs: {exc}"
                )
                continue
            source_id = source_ids.get("nws_observations")
            source = raw.get(source_id)
            if source is None or source.endpoint != "nws_observations":
                errors.append(
                    f"weather {row.get('weather_snapshot_id')} lacks its NWS observation receipt"
                )
                continue
            observed_at = parse_time(observation_time)
            if observed_at > source.received:
                snapshot_id = row.get("weather_snapshot_id")
                errors.append(
                    f"weather {snapshot_id} observation timestamp is after receipt"
                )
                continue
            facts.append(
                WeatherFact(
                    city=row["city"],
                    event=row["event_ticker"],
                    target_date=row["target_date"],
                    source_raw_id=source_id,
                    available=source.received,
                    observation_time=observed_at,
                    observed_high=high,
                )
            )
    return facts, errors


def _valid_contracts(contracts: list[ContractQuote]) -> tuple[bool, str]:
    if len(contracts) != 6:
        return False, "not_six_contracts"
    indexes = [contract.index for contract in contracts]
    if indexes != list(range(6)):
        return False, "noncontiguous_brackets"
    for contract in contracts:
        prices = [contract.no_bid, contract.no_ask]
        if any(value is None or not 0 <= value <= 1 for value in prices):
            return False, "missing_or_invalid_price"
        if contract.no_bid > contract.no_ask:
            return False, "crossed_no_market"
        if contract.lower is not None and contract.upper is not None:
            if contract.lower > contract.upper:
                return False, "reversed_bracket"
    for left, right in zip(contracts, contracts[1:]):
        if left.upper is not None and right.lower is not None:
            if abs((left.upper + 1) - right.lower) > 1e-9:
                return False, "noncontiguous_temperature_bounds"
    return True, "ok"


def load_quotes(path: Path, raw: dict[str, RawFact]) -> tuple[list[Quote], list[str]]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            groups[(row["event_ticker"], row["raw_payload_id"])].append(row)

    quotes: list[Quote] = []
    errors: list[str] = []
    for (event, raw_id), rows in groups.items():
        raw_fact = raw.get(raw_id)
        if raw_fact is None or raw_fact.endpoint != "kalshi_open_markets":
            errors.append(f"quote {event}/{raw_id} lacks its market request receipt")
            continue
        first = rows[0]
        try:
            metadata = json.loads(first["metadata"])
            close_time = parse_time(metadata["close_time"])
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            errors.append(f"quote {event}/{raw_id} has invalid close time: {exc}")
            continue
        contracts = sorted(
            (
                ContractQuote(
                    ticker=row["market_ticker"],
                    index=int(row["bracket_index"]),
                    lower=optional_float(row.get("bracket_lower_f")),
                    upper=optional_float(row.get("bracket_upper_f")),
                    no_bid=optional_float(row.get("no_bid_dollars")),
                    no_ask=optional_float(row.get("no_ask_dollars")),
                    no_ask_size=optional_float(row.get("no_ask_size")),
                )
                for row in rows
            ),
            key=lambda contract: contract.index,
        )
        complete, reason = _valid_contracts(contracts)
        quotes.append(
            Quote(
                city=first["city"],
                event=event,
                target_date=first["target_date"],
                raw_id=raw_id,
                requested=raw_fact.requested,
                received=raw_fact.received,
                close_time=close_time,
                hours_since_climate_start=float(first["hours_since_climate_start"]),
                contracts=tuple(contracts),
                complete=complete,
                integrity_reason=reason,
            )
        )
    quotes.sort(key=lambda quote: (quote.requested, quote.event, quote.raw_id))
    return quotes, errors


def load_settlements(path: Path) -> tuple[dict[str, Settlement], list[str]]:
    settlements: dict[str, Settlement] = {}
    errors: list[str] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("validation_status") != "valid" or row.get("source_provider") != "kalshi":
                continue
            settlement = Settlement(
                city=row["city"],
                event=row["event_ticker"],
                target_date=row["target_date"],
                winner_ticker=row["winner_ticker"],
                temperature=optional_float(row.get("settlement_temperature_f")),
            )
            previous = settlements.get(settlement.event)
            if previous is not None and previous != settlement:
                errors.append(f"event {settlement.event} has conflicting valid settlements")
            settlements[settlement.event] = settlement
    return settlements, errors


def align_contexts(quotes: Iterable[Quote], weather: Iterable[WeatherFact]) -> list[Context]:
    by_event: dict[tuple[str, str], list[WeatherFact]] = defaultdict(list)
    for fact in weather:
        by_event[(fact.city, fact.event)].append(fact)
    for facts in by_event.values():
        facts.sort(key=lambda fact: (fact.available, fact.source_raw_id))

    pointers: dict[tuple[str, str], int] = defaultdict(lambda: -1)
    contexts: list[Context] = []
    for quote in sorted(quotes, key=lambda item: (item.requested, item.event, item.raw_id)):
        key = (quote.city, quote.event)
        facts = by_event.get(key, [])
        pointer = pointers[key]
        while pointer + 1 < len(facts) and facts[pointer + 1].available <= quote.requested:
            pointer += 1
        pointers[key] = pointer
        matched = facts[pointer] if pointer >= 0 else None
        availability_valid = matched is None or matched.available <= quote.requested
        age = None
        if matched is not None:
            age = (quote.requested - matched.observation_time).total_seconds() / 60
        contexts.append(
            Context(
                quote=quote,
                weather=matched,
                availability_valid=availability_valid,
                observation_age_minutes=age,
            )
        )
    return contexts


def candidate_for_context(context: Context, policy: Policy) -> tuple[dict | None, str]:
    quote = context.quote
    weather = context.weather
    if not quote.complete:
        return None, quote.integrity_reason
    if quote.requested >= quote.close_time:
        return None, "at_or_after_close"
    if weather is None:
        return None, "no_prior_observation_receipt"
    if not context.availability_valid:
        return None, "availability_violation"
    if context.observation_age_minutes is None or context.observation_age_minutes < 0:
        return None, "invalid_observation_age"
    if context.observation_age_minutes > policy.maximum_observation_age_minutes:
        return None, "stale_observation"
    if quote.hours_since_climate_start > policy.maximum_climate_hour:
        return None, "after_entry_hour"

    eligible: list[tuple[float, str, ContractQuote]] = []
    saw_eliminated = False
    for contract in quote.contracts:
        if contract.upper is None:
            continue
        if weather.observed_high - contract.upper < policy.minimum_observed_gap_f:
            continue
        saw_eliminated = True
        if contract.no_ask is None or contract.no_bid is None:
            continue
        spread = contract.no_ask - contract.no_bid
        if contract.no_ask > policy.maximum_no_ask or spread > policy.maximum_spread:
            continue
        if contract.no_ask_size is None or contract.no_ask_size < 1:
            continue
        execution_price = round(contract.no_ask + 0.01, 10)
        if execution_price >= 1:
            continue
        eligible.append((execution_price, contract.ticker, contract))
    if not eligible:
        return None, "eliminated_but_not_executable" if saw_eliminated else "no_eliminated_bracket"

    execution_price, _, selected = min(eligible, key=lambda item: (item[0], item[1]))
    return {
        "policy_id": policy.policy_id,
        "city": quote.city,
        "event": quote.event,
        "target_date": quote.target_date,
        "market_raw_id": quote.raw_id,
        "market_requested_at": quote.requested.isoformat(),
        "market_received_at": quote.received.isoformat(),
        "observation_raw_id": weather.source_raw_id,
        "observation_received_at": weather.available.isoformat(),
        "observation_time": weather.observation_time.isoformat(),
        "observation_age_minutes": context.observation_age_minutes,
        "observed_high_f": weather.observed_high,
        "contract_ticker": selected.ticker,
        "contract_upper_f": selected.upper,
        "observed_gap_f": weather.observed_high - selected.upper,
        "no_bid": selected.no_bid,
        "no_ask": selected.no_ask,
        "no_ask_size": selected.no_ask_size,
        "spread": selected.no_ask - selected.no_bid,
        "execution_price": execution_price,
        "fee": fee(execution_price, 1),
        "stress_fee": fee(execution_price, 2),
        "hours_since_climate_start": quote.hours_since_climate_start,
        "availability_violation": weather.available > quote.requested,
    }, "eligible"


def intentions_for_policy(
    contexts: Iterable[Context], policy: Policy, start: str, end: str
) -> tuple[list[dict], list[dict]]:
    """Take the first eligible quote per event; later eligibility cannot replace it."""
    intentions: list[dict] = []
    audit: list[dict] = []
    traded_events: set[str] = set()
    for context in contexts:
        target_date = context.quote.target_date
        if not start <= target_date <= end:
            continue
        candidate, reason = candidate_for_context(context, policy)
        status = reason
        if candidate is not None:
            if context.quote.event in traded_events:
                status = "later_eligible_after_event_trade"
            else:
                traded_events.add(context.quote.event)
                intentions.append(candidate)
                status = "selected"
        audit.append(
            {
                "policy_id": policy.policy_id,
                "city": context.quote.city,
                "event": context.quote.event,
                "target_date": target_date,
                "market_raw_id": context.quote.raw_id,
                "market_requested_at": context.quote.requested.isoformat(),
                "observation_raw_id": context.weather.source_raw_id if context.weather else "",
                "observation_received_at": (
                    context.weather.available.isoformat() if context.weather else ""
                ),
                "reason": status,
            }
        )
    return intentions, audit


def score(intentions: Iterable[dict], settlements: dict[str, Settlement]) -> list[dict]:
    scored: list[dict] = []
    for intention in intentions:
        row = dict(intention)
        settlement = settlements.get(row["event"])
        if settlement is None:
            row.update(
                {
                    "label_available": False,
                    "winner_ticker": "",
                    "settlement_temperature_f": "",
                    "hit": "",
                    "gross_pnl": "",
                    "net_pnl": "",
                    "stress_net_pnl": "",
                    "observation_label_contradiction": "",
                }
            )
        else:
            hit = row["contract_ticker"] != settlement.winner_ticker
            gross = (1.0 if hit else 0.0) - row["execution_price"]
            contradiction = (
                settlement.temperature is not None
                and row["observed_high_f"] > settlement.temperature + 0.51
            )
            row.update(
                {
                    "label_available": True,
                    "winner_ticker": settlement.winner_ticker,
                    "settlement_temperature_f": settlement.temperature,
                    "hit": hit,
                    "gross_pnl": gross,
                    "net_pnl": gross - row["fee"],
                    "stress_net_pnl": gross - row["stress_fee"],
                    "observation_label_contradiction": contradiction,
                }
            )
        scored.append(row)
    return scored


def day_bootstrap_lower(rows: list[dict], field: str, draws: int = 10_000) -> float | None:
    settled = [row for row in rows if row["label_available"]]
    if not settled:
        return None
    by_day: dict[str, float] = defaultdict(float)
    for row in settled:
        by_day[row["target_date"]] += float(row[field])
    days = sorted(by_day)
    rng = random.Random(17)
    totals = []
    for _ in range(draws):
        totals.append(sum(by_day[days[rng.randrange(len(days))]] for _ in days))
    totals.sort()
    return totals[int(0.025 * len(totals))]


def summarize(rows: list[dict], test_start: str | None = None) -> dict:
    settled = [row for row in rows if row["label_available"]]
    wins = sum(bool(row["hit"]) for row in settled)
    cities = Counter(row["city"] for row in settled)
    net_pnl = sum(float(row["net_pnl"]) for row in settled)
    stress_pnl = sum(float(row["stress_net_pnl"]) for row in settled)
    leave_one_city_out = {
        city: sum(float(row["net_pnl"]) for row in settled if row["city"] != city)
        for city in sorted(cities)
    }
    halves = []
    if test_start is not None:
        origin = date.fromisoformat(test_start)
        for half in range(2):
            low = origin + timedelta(days=7 * half)
            high = low + timedelta(days=6)
            half_rows = [
                row for row in settled if low <= date.fromisoformat(row["target_date"]) <= high
            ]
            halves.append(
                {
                    "half": half + 1,
                    "start": low.isoformat(),
                    "end": high.isoformat(),
                    "trades": len(half_rows),
                    "net_pnl": sum(float(row["net_pnl"]) for row in half_rows),
                }
            )
    return {
        "intentions": len(rows),
        "settled_trades": len(settled),
        "label_coverage": len(settled) / len(rows) if rows else 0.0,
        "wins": wins,
        "losses": len(settled) - wins,
        "hit_rate": wins / len(settled) if settled else None,
        "iid_exact_95_hit_lower_bound": hit_lower_bound(wins, len(settled)),
        "gross_pnl": sum(float(row["gross_pnl"]) for row in settled),
        "fees": sum(float(row["fee"]) for row in settled),
        "net_pnl": net_pnl,
        "stress_fees": sum(float(row["stress_fee"]) for row in settled),
        "stress_net_pnl": stress_pnl,
        "day_bootstrap_net_pnl_95_lower": day_bootstrap_lower(settled, "net_pnl"),
        "active_days": len({row["target_date"] for row in settled}),
        "cities": len(cities),
        "city_counts": dict(sorted(cities.items())),
        "maximum_city_trade_fraction": max(cities.values()) / len(settled) if settled else None,
        "availability_violations": sum(bool(row["availability_violation"]) for row in rows),
        "observation_label_contradictions": sum(
            bool(row["observation_label_contradiction"]) for row in settled
        ),
        "leave_one_city_out_net_pnl": leave_one_city_out,
        "seven_day_halves": halves,
    }


def policy_grid(requirements: dict) -> list[Policy]:
    grid = requirements["candidate_grid"]
    return [
        Policy(*values)
        for values in itertools.product(
            grid["minimum_observed_gap_f"],
            grid["maximum_no_ask"],
            grid["maximum_spread"],
            grid["maximum_climate_hour"],
            grid["maximum_observation_age_minutes"],
        )
    ]


def selection_passes(summary: dict, requirements: dict) -> bool:
    gate = requirements["selection_requirements"]
    return (
        summary["settled_trades"] >= gate["minimum_trades"]
        and summary["hit_rate"] is not None
        and summary["hit_rate"] >= gate["minimum_hit_rate"]
        and (not gate["positive_net_pnl"] or summary["net_pnl"] > 0)
        and (not gate["positive_fee_stress_pnl"] or summary["stress_net_pnl"] > 0)
        and (
            not gate["zero_observation_label_contradictions"]
            or summary["observation_label_contradictions"] == 0
        )
    )


def select_policy(results: list[tuple[Policy, dict]], requirements: dict) -> Policy | None:
    passing = [
        (policy, summary) for policy, summary in results if selection_passes(summary, requirements)
    ]
    if not passing:
        return None
    # Profit is primary. Remaining terms prefer stricter, cheaper, earlier policies.
    passing.sort(
        key=lambda item: (
            -item[1]["stress_net_pnl"],
            -item[0].minimum_observed_gap_f,
            item[0].maximum_no_ask,
            item[0].maximum_spread,
            item[0].maximum_observation_age_minutes,
            item[0].maximum_climate_hour,
            item[0].policy_id,
        )
    )
    return passing[0][0]


def success_checks(summary: dict, requirements: dict, as_of: date) -> dict[str, bool]:
    gate = requirements["success_requirements"]
    full_window = as_of > date.fromisoformat(requirements["prospective_test_end"])
    lower = summary["iid_exact_95_hit_lower_bound"]
    bootstrap = summary["day_bootstrap_net_pnl_95_lower"]
    half_pnls = [half["net_pnl"] for half in summary["seven_day_halves"]]
    loo = list(summary["leave_one_city_out_net_pnl"].values())
    return {
        "full_14_day_window_complete": full_window,
        "minimum_trades": summary["settled_trades"] >= gate["minimum_trades"],
        "minimum_active_days": summary["active_days"] >= gate["minimum_active_days"],
        "minimum_cities": summary["cities"] >= gate["minimum_cities"],
        "maximum_city_trade_fraction": summary["maximum_city_trade_fraction"] is not None
        and summary["maximum_city_trade_fraction"] <= gate["maximum_city_trade_fraction"],
        "minimum_hit_rate": summary["hit_rate"] is not None
        and summary["hit_rate"] >= gate["minimum_hit_rate"],
        "minimum_iid_exact_95_hit_lower_bound": lower is not None
        and lower >= gate["minimum_iid_exact_95_hit_lower_bound"],
        "positive_net_pnl": summary["net_pnl"] > 0,
        "positive_fee_stress_pnl": summary["stress_net_pnl"] > 0,
        "positive_day_bootstrap_95_lower_bound": bootstrap is not None and bootstrap > 0,
        "positive_pnl_in_each_seven_day_half": len(half_pnls) == 2
        and all(value > 0 for value in half_pnls),
        "nonnegative_leave_one_city_out_pnl": bool(loo) and min(loo) >= 0,
        "minimum_label_coverage": summary["label_coverage"] >= gate["minimum_label_coverage"],
        "zero_observation_label_contradictions": summary["observation_label_contradictions"] == 0,
        "zero_availability_violations": summary["availability_violations"] == 0,
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_report(result: dict) -> str:
    selected = result.get("selected_policy")
    prospective = result.get("prospective_interim", {})
    checks = result.get("success_checks", {})
    status = result["status"]
    lines = [
        "# Monotone Barrier v1 Result",
        "",
        f"**Status: {status}**",
        "",
        result["status_reason"],
        "",
        "## Selected policy",
        "",
    ]
    if selected is None:
        lines.append("No candidate met the frozen selection requirements.")
    else:
        lines.extend(
            [
                f"- Policy: `{selected['policy_id']}`",
                f"- Minimum observed gap: {selected['minimum_observed_gap_f']} F",
                f"- Maximum displayed NO ask: ${selected['maximum_no_ask']:.2f}",
                f"- Maximum spread: ${selected['maximum_spread']:.2f}",
                f"- Latest climate hour: {selected['maximum_climate_hour']}",
                f"- Maximum observation age: {selected['maximum_observation_age_minutes']} minutes",
            ]
        )
    lines.extend(["", "## Prospective interim", ""])
    if prospective:
        lines.extend(
            [
                f"- Window observed: {result['prospective_observed_start']} through "
                f"{result['prospective_observed_end']}",
                f"- Settled trades: {prospective['settled_trades']}",
                f"- Hit rate: {_fmt(prospective['hit_rate'])}",
                f"- Exact one-sided 95% hit lower bound: "
                f"{_fmt(prospective['iid_exact_95_hit_lower_bound'])}",
                f"- Net PnL, one contract per event: ${prospective['net_pnl']:.2f}",
                f"- Doubled-fee stress PnL: ${prospective['stress_net_pnl']:.2f}",
                f"- Day-bootstrap 95% lower PnL: "
                f"${_fmt(prospective['day_bootstrap_net_pnl_95_lower'], 2)}",
                f"- Active days / cities: {prospective['active_days']} / {prospective['cities']}",
                f"- Label coverage: {prospective['label_coverage']:.1%}",
            ]
        )
    lines.extend(["", "## Frozen success checks", ""])
    for name, passed in checks.items():
        lines.append(f"- {'PASS' if passed else 'FAIL'}: `{name}`")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The rule is structurally constrained and the joins pass the recorded receipt-time "
            "boundary, but that does not prove future fill quality or continued profitability. "
            "The selection period was examined in earlier research and is not independent "
            "evidence. "
            "The complete September 13-26 window is the first promotion test.",
            "",
            "No live orders were placed and this report does not authorize deployment.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(data_dir: Path, output_dir: Path, requirements_path: Path, as_of: date) -> dict:
    requirements = load_requirements(requirements_path)
    files = {
        name: data_dir / filename
        for name, filename in {
            "raw": "raw_payloads.csv",
            "weather": "weather_snapshots.csv",
            "market": "market_snapshots.csv",
            "settlements": "settlements.csv",
        }.items()
    }
    raw, raw_errors = load_raw_facts(files["raw"])
    weather, weather_errors = load_weather(files["weather"], raw)
    quotes, quote_errors = load_quotes(files["market"], raw)
    settlements, settlement_errors = load_settlements(files["settlements"])
    integrity_errors = raw_errors + weather_errors + quote_errors + settlement_errors
    contexts = align_contexts(quotes, weather)

    selection_results: list[tuple[Policy, dict]] = []
    grid_rows: list[dict] = []
    for policy in policy_grid(requirements):
        intentions, _ = intentions_for_policy(
            contexts,
            policy,
            requirements["selection_start"],
            requirements["selection_end"],
        )
        summary = summarize(score(intentions, settlements))
        passed = selection_passes(summary, requirements)
        selection_results.append((policy, summary))
        grid_rows.append(
            {**asdict(policy), "policy_id": policy.policy_id, **summary, "passes": passed}
        )

    selected = select_policy(selection_results, requirements)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "policy_grid.csv", grid_rows)

    result = {
        "model": requirements["name"],
        "as_of": as_of.isoformat(),
        "integrity_errors": integrity_errors,
        "selected_policy": None,
        "status": "FAIL",
        "status_reason": "No policy met the frozen selection requirements.",
        "prospective_interim": {},
        "success_checks": {},
    }
    if selected is not None:
        result["selected_policy"] = {**asdict(selected), "policy_id": selected.policy_id}
        windows = {
            "development": (requirements["development_start"], requirements["development_end"]),
            "selection": (requirements["selection_start"], requirements["selection_end"]),
            "prospective_interim": (
                requirements["prospective_test_start"],
                min(requirements["prospective_test_end"], requirements["available_interim_end"]),
            ),
        }
        for name, (start, end) in windows.items():
            intentions, audit = intentions_for_policy(contexts, selected, start, end)
            scored = score(intentions, settlements)
            result[name] = summarize(
                scored,
                requirements["prospective_test_start"] if name == "prospective_interim" else None,
            )
            write_csv(output_dir / f"{name}_trades.csv", scored)
            if name == "prospective_interim":
                write_csv(output_dir / "prospective_interim_quote_audit.csv", audit)
        result["prospective_observed_start"] = requirements["prospective_test_start"]
        result["prospective_observed_end"] = min(
            requirements["prospective_test_end"], requirements["available_interim_end"]
        )
        checks = success_checks(result["prospective_interim"], requirements, as_of)
        if integrity_errors:
            checks["input_integrity"] = False
        else:
            checks["input_integrity"] = True
        result["success_checks"] = checks
        if as_of <= date.fromisoformat(requirements["prospective_test_end"]):
            result["status"] = "PENDING"
            result["status_reason"] = (
                "The frozen 14-day prospective window ends September 26; future outcomes are "
                "unavailable, so success cannot yet be claimed."
            )
        elif all(checks.values()):
            result["status"] = "PASS"
            result["status_reason"] = "Every frozen success requirement passed."
        else:
            result["status"] = "FAIL"
            result["status_reason"] = "At least one frozen success requirement failed."

    provenance = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "as_of": as_of.isoformat(),
        "requirements_sha256": sha256(requirements_path),
        "model_sha256": sha256(Path(__file__)),
        "inputs": {
            name: {"path": str(path), "sha256": sha256(path)} for name, path in files.items()
        },
        "policy_selected_before_prospective_scoring": True,
        "live_trading": False,
    }
    (output_dir / "selected_policy.json").write_text(
        json.dumps(result["selected_policy"], indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "REPORT.md").write_text(render_report(result), encoding="utf-8")
    return result
