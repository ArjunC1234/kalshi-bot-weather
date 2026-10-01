"""Normalize and summarize settlement-source metadata.

The trading target is the source named in the market rules. The training label
source is the source used to grade final temperatures. Those are deliberately
tracked separately so old NWS-labeled research is not silently treated as
Weather Company-labeled research.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

NWS_CLI_DAILY = "nws_cli_daily"
WEATHER_COMPANY_DAILY = "weather_company_daily"
WEATHER_COMPANY_HOURLY = "weather_company_hourly"
UNKNOWN = "unknown"

KNOWN_SOURCES = {
    NWS_CLI_DAILY,
    WEATHER_COMPANY_DAILY,
    WEATHER_COMPANY_HOURLY,
    UNKNOWN,
}

_SOURCE_COLUMNS = (
    "market_settlement_source",
    "settlement_source",
    "settlement_source_id",
    "settlement_provider",
    "settlement_source_provider",
    "outcome_source",
)

_RULE_COLUMNS = (
    "settlement_rule_text",
    "market_rule_text",
    "rules_text",
    "rule_text",
    "settlement_rules",
    "market_rules",
    "rules_primary",
    "rules_secondary",
)


def normalize_label_source(value: Any) -> str:
    text = _clean(value)
    if not text:
        return UNKNOWN
    if text in KNOWN_SOURCES:
        return text
    if "weather_company" in text or "the_weather_company" in text or text in {"twc", "weathercom"}:
        return WEATHER_COMPANY_DAILY
    if text in {"nws", "noaa", "nws_cli", "daily_climate_report", "cli", "cf6"}:
        return NWS_CLI_DAILY
    if "nws" in text or "noaa" in text or "climate_report" in text:
        return NWS_CLI_DAILY
    if "weather company" in text or "weather.com" in text:
        return WEATHER_COMPANY_DAILY
    return text


def normalize_market_source(value: Any, rule_text: Any = None) -> str:
    explicit = normalize_label_source(value)
    if explicit != UNKNOWN:
        return explicit
    rule = _clean(rule_text)
    if not rule:
        return UNKNOWN
    if ("weather_company" in rule or "weather company" in rule or "weather.com" in rule) and (
        "hourly" in rule or "exact time" in rule
    ):
        return WEATHER_COMPANY_HOURLY
    if "weather_company" in rule or "weather company" in rule or "weather.com" in rule:
        return WEATHER_COMPANY_DAILY
    if "nws" in rule or "noaa" in rule or "daily climate report" in rule or "cli" in rule:
        return NWS_CLI_DAILY
    return UNKNOWN


def infer_market_source(row: Mapping[str, Any]) -> str:
    for column in _SOURCE_COLUMNS:
        value = row.get(column)
        source = normalize_market_source(value)
        if source != UNKNOWN:
            return source
    rule_text = " ".join(str(row.get(column) or "") for column in _RULE_COLUMNS)
    return normalize_market_source(None, rule_text)


def annotate_table_rows(table_name: str, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        enriched = dict(row)
        if table_name == "final_temperature_labels":
            enriched.setdefault(
                "label_source",
                normalize_label_source(enriched.get("source_provider")),
            )
        elif table_name in {"events", "market_snapshots", "settlements"}:
            enriched.setdefault("market_settlement_source", infer_market_source(enriched))
        output.append(enriched)
    return output


def settlement_source_summary(tables: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict[str, Any]:
    label_sources = Counter[str]()
    market_sources = Counter[str]()
    settlement_sources = Counter[str]()

    for row in tables.get("final_temperature_labels", []):
        label_sources[
            normalize_label_source(row.get("label_source") or row.get("source_provider"))
        ] += 1
    for table_name in ("events", "market_snapshots"):
        seen_events: set[str] = set()
        for row in tables.get(table_name, []):
            event_ticker = str(row.get("event_ticker") or "")
            if event_ticker and event_ticker in seen_events:
                continue
            if event_ticker:
                seen_events.add(event_ticker)
            market_sources[infer_market_source(row)] += 1
    for row in tables.get("settlements", []):
        settlement_sources[infer_market_source(row)] += 1

    label_source = _dominant(label_sources)
    market_source = _dominant_without_unknown(market_sources)
    settlement_source = _dominant_without_unknown(settlement_sources)
    warnings = _compatibility_warnings(label_source, market_source, settlement_source)
    return {
        "label_sources": dict(sorted(label_sources.items())),
        "market_settlement_sources": dict(sorted(market_sources.items())),
        "settlement_row_sources": dict(sorted(settlement_sources.items())),
        "label_source": label_source,
        "market_settlement_source": market_source or UNKNOWN,
        "settlement_source": settlement_source or UNKNOWN,
        "compatible": not warnings,
        "warnings": warnings,
    }


def compatibility_summary(
    *,
    dataset_source: str | None,
    model_label_source: str | None,
    market_source: str | None = None,
) -> dict[str, Any]:
    dataset = dataset_source or UNKNOWN
    model = model_label_source or UNKNOWN
    market = market_source or UNKNOWN
    warnings: list[str] = []
    if dataset == UNKNOWN:
        warnings.append("Dataset label source is unknown.")
    if model == UNKNOWN:
        warnings.append("Model label source is unknown.")
    if market == UNKNOWN:
        warnings.append("Market settlement source is unknown; verify Kalshi rules before trading.")
    if dataset != UNKNOWN and model != UNKNOWN and dataset != model:
        warnings.append(
            f"Model label source {model} does not match dataset label source {dataset}."
        )
    if market != UNKNOWN and dataset != UNKNOWN and market != dataset:
        warnings.append(
            f"Market settlement source {market} does not match dataset label source {dataset}."
        )
    return {
        "dataset_label_source": dataset,
        "model_label_source": model,
        "market_settlement_source": market,
        "compatible": not warnings,
        "warnings": warnings,
    }


def _compatibility_warnings(
    label_source: str,
    market_source: str | None,
    settlement_source: str | None,
) -> list[str]:
    warnings: list[str] = []
    if label_source == UNKNOWN:
        warnings.append("No normalized final-temperature label source was found.")
    if not market_source:
        warnings.append(
            "Market rule settlement source is unknown; verify Kalshi rules before trading."
        )
    if market_source and label_source != UNKNOWN and market_source != label_source:
        warnings.append(
            f"Market source {market_source} does not match label source {label_source}."
        )
    if settlement_source and label_source != UNKNOWN and settlement_source != label_source:
        warnings.append(
            f"Settlement row source {settlement_source} does not match label source {label_source}."
        )
    return warnings


def _dominant(counter: Counter[str]) -> str:
    if not counter:
        return UNKNOWN
    return counter.most_common(1)[0][0]


def _dominant_without_unknown(counter: Counter[str]) -> str | None:
    cleaned = Counter({key: value for key, value in counter.items() if key != UNKNOWN})
    if not cleaned:
        return None
    return cleaned.most_common(1)[0][0]


def _clean(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip().lower().replace("-", "_")
