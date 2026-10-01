# ruff: noqa: E402

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

NEXT_GEN_DIR = Path(__file__).resolve().parents[1]
V3_DIR = NEXT_GEN_DIR / "maxtemp-engine" / "neuralcaster" / "v3"
RAYCASTER_DIR = NEXT_GEN_DIR / "maxtemp-engine" / "raycaster" / "v1"
for path in (V3_DIR, RAYCASTER_DIR, NEXT_GEN_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from residual_evaluate import _normalize_possible_probabilities
from residual_features import build_residual_rows
from residual_models import ResidualModel, train_residual_model

from libs.models import (
    BacktestDataset,
    Bracket,
    EventSnapshot,
    FinalTemperatureLabel,
    MarketSnapshot,
    Settlement,
    WeatherSnapshot,
)


def test_residual_rows_are_nws_relative() -> None:
    snapshot_time = datetime(2026, 7, 20, 12, tzinfo=UTC)
    target_date = date(2026, 7, 20)
    dataset = BacktestDataset(
        events=[
            EventSnapshot(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                snapshot_hour_utc=snapshot_time,
                climate_window_start_utc=datetime(2026, 7, 20, 6, tzinfo=UTC),
                climate_window_end_utc=datetime(2026, 7, 21, 6, tzinfo=UTC),
                station_id="KAUS",
            )
        ],
        weather=[
            WeatherSnapshot(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                snapshot_hour_utc=snapshot_time,
                nws_anchor_high_f=100.0,
                observed_high_so_far_f=93.0,
                hrrr_projected_high_f=102.0,
                nbm_projected_high_f=99.0,
                ensemble_raw_median_high_f=101.0,
                features={
                    "hrrr_next_3h_max_f": 103.0,
                    "nbm_next_3h_max_f": 98.0,
                    "source_range_f": 4.0,
                    "source_std_f": 1.5,
                },
            )
        ],
        final_temperature_labels=[
            FinalTemperatureLabel(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                station_id="KAUS",
                final_high_f=104.0,
                source_provider="test",
            )
        ],
    )

    row = build_residual_rows(dataset)[0]

    assert row.target_offset_f == 4.0
    assert row.nws_anchor_high_f == 100.0
    assert row.features["observed_minus_nws"] == -7.0
    assert row.features["hrrr_projected_minus_nws"] == 2.0
    assert row.features["nbm_projected_minus_nws"] == -1.0
    assert row.features["ensemble_median_minus_nws"] == 1.0
    assert row.features["hrrr_next_3h_max_minus_nws"] == 3.0
    assert row.features["nbm_next_3h_max_minus_nws"] == -2.0


def test_residual_rows_prefer_settlement_observed_high() -> None:
    snapshot_time = datetime(2026, 8, 28, 22, tzinfo=UTC)
    target_date = date(2026, 8, 28)
    dataset = BacktestDataset(
        events=[
            EventSnapshot(
                city="nyc",
                event_ticker="KXHIGHNY-26AUG28",
                target_date=target_date,
                snapshot_hour_utc=snapshot_time,
                climate_window_start_utc=datetime(2026, 8, 28, 5, tzinfo=UTC),
                climate_window_end_utc=datetime(2026, 8, 29, 5, tzinfo=UTC),
                station_id="KNYC",
            )
        ],
        weather=[
            WeatherSnapshot(
                city="nyc",
                event_ticker="KXHIGHNY-26AUG28",
                target_date=target_date,
                snapshot_hour_utc=snapshot_time,
                nws_anchor_high_f=100.0,
                observed_high_so_far_f=93.0,
                features={
                    "settlement_observed_high_so_far_f": 97.0,
                    "settlement_observed_source_count": 2,
                    "settlement_observed_age_hours": 0.5,
                    "settlement_observed_source_range_f": 1.0,
                    "settlement_observed_source_stddev_f": 0.5,
                    "settlement_observed_nws_delta_f": 2.0,
                },
            )
        ],
        final_temperature_labels=[
            FinalTemperatureLabel(
                city="nyc",
                event_ticker="KXHIGHNY-26AUG28",
                target_date=target_date,
                station_id="KNYC",
                final_high_f=101.0,
                source_provider="weather_company_daily",
            )
        ],
    )

    row = build_residual_rows(dataset)[0]

    assert row.features["observed_minus_nws"] == -3.0
    assert row.features["settlement_observed_source_count"] == 2.0
    assert row.features["settlement_observed_nws_delta_f"] == 2.0


def test_residual_rows_include_market_disagreement_and_deltas() -> None:
    first_time = datetime(2026, 7, 20, 12, tzinfo=UTC)
    second_time = datetime(2026, 7, 20, 13, tzinfo=UTC)
    target_date = date(2026, 7, 20)
    dataset = BacktestDataset(
        events=[
            EventSnapshot(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                snapshot_hour_utc=first_time,
                climate_window_start_utc=datetime(2026, 7, 20, 6, tzinfo=UTC),
                climate_window_end_utc=datetime(2026, 7, 21, 6, tzinfo=UTC),
                station_id="KAUS",
            ),
            EventSnapshot(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                snapshot_hour_utc=second_time,
                climate_window_start_utc=datetime(2026, 7, 20, 6, tzinfo=UTC),
                climate_window_end_utc=datetime(2026, 7, 21, 6, tzinfo=UTC),
                station_id="KAUS",
            ),
        ],
        weather=[
            WeatherSnapshot(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                snapshot_hour_utc=first_time,
                nws_anchor_high_f=100.0,
                observed_high_so_far_f=93.0,
                hrrr_projected_high_f=102.0,
                nbm_projected_high_f=99.0,
                ensemble_raw_median_high_f=101.0,
                features={"source_range_f": 4.0, "source_std_f": 1.5},
            ),
            WeatherSnapshot(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                snapshot_hour_utc=second_time,
                nws_anchor_high_f=101.0,
                observed_high_so_far_f=96.0,
                hrrr_projected_high_f=104.0,
                nbm_projected_high_f=100.0,
                ensemble_raw_median_high_f=102.0,
                features={"source_range_f": 5.0, "source_std_f": 2.0},
            ),
        ],
        markets=[
            MarketSnapshot(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                market_ticker="KXHIGHAUS-26JUL20-B99.5",
                target_date=target_date,
                snapshot_hour_utc=second_time,
                bracket=Bracket("KXHIGHAUS-26JUL20-B99.5", "99 or below", None, 99, 0),
                yes_bid=0.2,
                yes_ask=0.3,
                normalized_market_midpoint_probability=0.25,
            ),
            MarketSnapshot(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                market_ticker="KXHIGHAUS-26JUL20-B101.5",
                target_date=target_date,
                snapshot_hour_utc=second_time,
                bracket=Bracket("KXHIGHAUS-26JUL20-B101.5", "100 to 101", 100, 101, 1),
                yes_bid=0.6,
                yes_ask=0.7,
                normalized_market_midpoint_probability=0.75,
            ),
        ],
        final_temperature_labels=[
            FinalTemperatureLabel(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                station_id="KAUS",
                final_high_f=104.0,
                source_provider="test",
            )
        ],
    )

    first, second = build_residual_rows(dataset)

    assert first.features["nws_anchor_delta_1h"] is None
    assert second.features["nws_anchor_delta_1h"] == 1.0
    assert second.features["hrrr_projected_minus_nws_delta_1h"] == 1.0
    assert second.features["observed_minus_nws_delta_1h"] == 2.0
    assert second.features["source_std_delta_1h"] == pytest.approx(0.36098595702500913)
    assert second.features["market_expected_minus_nws"] == -1.125
    assert second.features["market_top_probability"] == 0.75
    assert second.features["market_avg_spread"] == pytest.approx(0.1)


def test_nws_baseline_predicts_zero_offset() -> None:
    snapshot_time = datetime(2026, 7, 20, 12, tzinfo=UTC)
    target_date = date(2026, 7, 20)
    dataset = BacktestDataset(
        events=[
            EventSnapshot(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                snapshot_hour_utc=snapshot_time,
                climate_window_start_utc=datetime(2026, 7, 20, 6, tzinfo=UTC),
                climate_window_end_utc=datetime(2026, 7, 21, 6, tzinfo=UTC),
                station_id="KAUS",
            )
        ],
        weather=[
            WeatherSnapshot(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                snapshot_hour_utc=snapshot_time,
                nws_anchor_high_f=100.0,
                observed_high_so_far_f=93.0,
                hrrr_projected_high_f=102.0,
                nbm_projected_high_f=99.0,
                ensemble_raw_median_high_f=101.0,
            )
        ],
        final_temperature_labels=[
            FinalTemperatureLabel(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                station_id="KAUS",
                final_high_f=104.0,
                source_provider="test",
            )
        ],
    )
    row = build_residual_rows(dataset)[0]

    model = train_residual_model([row], kind="nws_baseline", min_training_rows=1, seed=1)

    assert model.predict_offset(row) == 0.0
    assert model.sigma_for(row) >= 0.75


def test_bracket_classifier_predicts_native_bracket_probabilities() -> None:
    snapshot_time = datetime(2026, 8, 28, 22, tzinfo=UTC)
    target_date = date(2026, 8, 28)
    dataset = BacktestDataset(
        events=[
            EventSnapshot(
                city="nyc",
                event_ticker="KXHIGHNY-26AUG28",
                target_date=target_date,
                snapshot_hour_utc=snapshot_time,
                climate_window_start_utc=datetime(2026, 8, 28, 5, tzinfo=UTC),
                climate_window_end_utc=datetime(2026, 8, 29, 5, tzinfo=UTC),
                station_id="KNYC",
            )
        ],
        weather=[
            WeatherSnapshot(
                city="nyc",
                event_ticker="KXHIGHNY-26AUG28",
                target_date=target_date,
                snapshot_hour_utc=snapshot_time,
                nws_anchor_high_f=84.0,
                observed_high_so_far_f=83.0,
            )
        ],
        settlements=[
            Settlement(
                city="nyc",
                event_ticker="KXHIGHNY-26AUG28",
                target_date=target_date,
                settled_at_utc=datetime(2026, 8, 29, 6, tzinfo=UTC),
                winner_ticker="HIGH",
                settlement_temperature_f=85.0,
                settlement_bracket_index=1,
            )
        ],
    )
    row = build_residual_rows(dataset)[0]

    model = train_residual_model([row], kind="bracket_classifier", min_training_rows=1, seed=1)
    probabilities = model.predict_bracket_probabilities(row)

    assert probabilities == {1: 1.0}


def test_bracket_classifier_probabilities_respect_observed_constraints() -> None:
    brackets = [
        Bracket("LOW", "91 or below", None, 91, 0),
        Bracket("MID", "92 to 94", 92, 94, 1),
        Bracket("HIGH", "95 or above", 95, None, 2),
    ]

    probabilities = _normalize_possible_probabilities(
        {"LOW": 0.9, "MID": 0.1, "HIGH": 0.0},
        brackets,
        observed=91.94,
        probability_floor=0.0,
    )

    assert probabilities["LOW"] == 0.0
    assert probabilities["MID"] == 1.0
    assert probabilities["HIGH"] == 0.0


def test_robust_ensemble_predicts_from_small_residual_dataset() -> None:
    events = []
    weather = []
    labels = []
    for day in range(1, 9):
        snapshot_time = datetime(2026, 7, day, 12, tzinfo=UTC)
        target_date = date(2026, 7, day)
        event_ticker = f"KXHIGHAUS-26JUL{day:02d}"
        anchor = 95.0 + day
        events.append(
            EventSnapshot(
                city="aus",
                event_ticker=event_ticker,
                target_date=target_date,
                snapshot_hour_utc=snapshot_time,
                climate_window_start_utc=datetime(2026, 7, day, 6, tzinfo=UTC),
                climate_window_end_utc=datetime(2026, 7, day + 1, 6, tzinfo=UTC),
                station_id="KAUS",
            )
        )
        weather.append(
            WeatherSnapshot(
                city="aus",
                event_ticker=event_ticker,
                target_date=target_date,
                snapshot_hour_utc=snapshot_time,
                nws_anchor_high_f=anchor,
                observed_high_so_far_f=anchor - 3.0,
                hrrr_projected_high_f=anchor + 1.0,
                nbm_projected_high_f=anchor + 0.5,
                ensemble_raw_median_high_f=anchor + 1.5,
                features={"source_range_f": 2.0, "source_std_f": 0.8},
            )
        )
        labels.append(
            FinalTemperatureLabel(
                city="aus",
                event_ticker=event_ticker,
                target_date=target_date,
                station_id="KAUS",
                final_high_f=anchor + 1.0,
                source_provider="test",
            )
        )
    rows = build_residual_rows(
        BacktestDataset(events=events, weather=weather, final_temperature_labels=labels)
    )

    model = train_residual_model(rows, kind="robust_ensemble", min_training_rows=1, seed=7)

    assert model.kind == "robust_ensemble"
    assert model.training_rows == len(rows)
    assert model.predict_offset(rows[-1]) == pytest.approx(1.0, abs=2.0)


def test_bucket_sigma_overrides_city_checkpoint_sigma() -> None:
    snapshot_time = datetime(2026, 7, 20, 12, tzinfo=UTC)
    target_date = date(2026, 7, 20)
    dataset = BacktestDataset(
        events=[
            EventSnapshot(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                snapshot_hour_utc=snapshot_time,
                climate_window_start_utc=datetime(2026, 7, 20, 6, tzinfo=UTC),
                climate_window_end_utc=datetime(2026, 7, 21, 6, tzinfo=UTC),
                station_id="KAUS",
            )
        ],
        weather=[
            WeatherSnapshot(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                snapshot_hour_utc=snapshot_time,
                nws_anchor_high_f=100.0,
                observed_high_so_far_f=93.0,
                hrrr_projected_high_f=102.0,
                nbm_projected_high_f=99.0,
                ensemble_raw_median_high_f=101.0,
            )
        ],
        final_temperature_labels=[
            FinalTemperatureLabel(
                city="aus",
                event_ticker="KXHIGHAUS-26JUL20",
                target_date=target_date,
                station_id="KAUS",
                final_high_f=104.0,
                source_provider="test",
            )
        ],
    )
    row = build_residual_rows(dataset)[0]
    row.features["hours_remaining"] = 18.0
    row.features["source_std_f"] = 1.2
    row.features["market_expected_minus_nws"] = 0.5
    model = ResidualModel(
        kind="nws_baseline",
        estimator=None,
        training_rows=1,
        fit_rows=1,
        validation_rows=1,
        global_sigma=3.0,
        group_sigmas={("aus", str(row.features["checkpoint"])): 2.0},
        bucket_sigmas={("mid", "remaining_high", "source_mid", "market_near_nws"): 1.25},
        fallback_offset=0.0,
    )

    assert model.sigma_for(row) == 1.25
