"""Shared typed structures for collection, modeling, and backtesting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


def _require_text(value: str, name: str) -> None:
    if not value:
        raise ValueError(f"{name} is required")


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
    settlement_aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.key, "city.key")
        _require_text(self.series_ticker, "city.series_ticker")
        _require_text(self.station_id, "city.station_id")


@dataclass(frozen=True)
class Bracket:
    ticker: str
    label: str
    lower_f: int | None
    upper_f: int | None
    index: int = 0

    def __post_init__(self) -> None:
        _require_text(self.ticker, "bracket.ticker")
        if self.lower_f is not None and self.upper_f is not None and self.lower_f > self.upper_f:
            raise ValueError("bracket lower_f cannot exceed upper_f")

    def contains_rounded_temperature(self, temperature_f: float) -> bool:
        rounded = int(temperature_f + 0.5)
        if self.lower_f is not None and rounded < self.lower_f:
            return False
        return not (self.upper_f is not None and rounded > self.upper_f)


@dataclass(frozen=True)
class CollectorRun:
    collector_run_id: str
    snapshot_hour_utc: datetime
    schema_version: int
    source: str = "supabase"


@dataclass(frozen=True)
class RawPayloadRef:
    raw_payload_id: str
    provider: str
    endpoint_name: str
    storage_path: str
    content_sha256: str


@dataclass(frozen=True)
class EventSnapshot:
    city: str
    event_ticker: str
    target_date: date
    snapshot_hour_utc: datetime
    climate_window_start_utc: datetime
    climate_window_end_utc: datetime
    station_id: str
    settlement_sources: dict[str, Any] = field(default_factory=dict)
    rules_primary: str | None = None
    rules_secondary: str | None = None
    settlement_source_provider: str | None = None
    settlement_station_id: str | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    city: str
    event_ticker: str
    market_ticker: str
    target_date: date
    snapshot_hour_utc: datetime
    bracket: Bracket
    yes_bid: float | None
    yes_ask: float | None
    no_bid: float | None = None
    no_ask: float | None = None
    yes_bid_size: float | None = None
    yes_ask_size: float | None = None
    no_bid_size: float | None = None
    no_ask_size: float | None = None
    last_price: float | None = None
    normalized_market_midpoint_probability: float | None = None
    rules_primary: str | None = None
    rules_secondary: str | None = None
    settlement_sources: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WeatherSnapshot:
    city: str
    event_ticker: str
    target_date: date
    snapshot_hour_utc: datetime
    nws_anchor_high_f: float | None = None
    observed_high_so_far_f: float | None = None
    hrrr_projected_high_f: float | None = None
    nbm_projected_high_f: float | None = None
    ensemble_raw_median_high_f: float | None = None
    features: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Settlement:
    city: str
    event_ticker: str
    target_date: date
    settled_at_utc: datetime
    winner_ticker: str
    settlement_temperature_f: float | None = None
    settlement_bracket_index: int | None = None
    market_settlement_source: str | None = None
    rules_primary: str | None = None
    rules_secondary: str | None = None
    validation_status: str = "valid"

    def __post_init__(self) -> None:
        _require_text(self.winner_ticker, "settlement.winner_ticker")


@dataclass(frozen=True)
class FinalTemperatureLabel:
    city: str
    event_ticker: str
    target_date: date
    station_id: str
    final_high_f: float
    source_provider: str
    product_id: str | None = None
    issued_at_utc: datetime | None = None
    validation_status: str = "valid"
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _require_text(self.city, "final_temperature_label.city")
        _require_text(self.event_ticker, "final_temperature_label.event_ticker")
        _require_text(self.station_id, "final_temperature_label.station_id")
        _require_text(self.source_provider, "final_temperature_label.source_provider")


@dataclass(frozen=True)
class TemperaturePrediction:
    city: str
    event_ticker: str
    snapshot_hour_utc: datetime
    model_name: str
    expected_high_f: float
    quantiles: dict[float, float] = field(default_factory=dict)


@dataclass(frozen=True)
class BracketDistribution:
    city: str
    event_ticker: str
    snapshot_hour_utc: datetime
    model_name: str
    probabilities: dict[str, float]

    def __post_init__(self) -> None:
        _require_text(self.model_name, "distribution.model_name")
        if not self.probabilities:
            raise ValueError("distribution probabilities are required")


@dataclass(frozen=True)
class BacktestDataset:
    collector_runs: list[CollectorRun] = field(default_factory=list)
    events: list[EventSnapshot] = field(default_factory=list)
    markets: list[MarketSnapshot] = field(default_factory=list)
    weather: list[WeatherSnapshot] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
    final_temperature_labels: list[FinalTemperatureLabel] = field(default_factory=list)
    model_outputs: list[BracketDistribution] = field(default_factory=list)


@dataclass(frozen=True)
class MetricSummary:
    metric: str
    value: float
    count: int
    group: str = "overall"


@dataclass(frozen=True)
class BacktestResult:
    model_name: str
    predictions: list[dict[str, Any]]
    metrics: list[MetricSummary]
    metadata: dict[str, Any] = field(default_factory=dict)


def to_dict(value: object) -> dict[str, Any]:
    return asdict(value)
