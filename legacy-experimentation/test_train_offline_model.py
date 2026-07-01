from __future__ import annotations

import math
import unittest

from train_offline_model import (
    ForecastExample,
    WEATHER_CANDIDATES,
    apply_probability_floor,
    blend_probabilities,
    bracket_index_for_temperature,
    calibrated_temperature_error_probabilities,
    anchored_regression_probabilities,
    expanding_window_scores,
    expected_bracket_index,
    finite_normal_index_probabilities,
    fit_anchored_regression_blend,
    fit_calibrated_temperature_error_model,
    fit_checkpoint_models,
    fit_regression_weather_hrrr,
    regression_weather_hrrr_probabilities,
    fit_blend_weights,
    score_checkpoint_models,
    soft_observation_floor_probabilities,
)


def example(
    target_date: str,
    city: str,
    winner: str,
    good_probability: float,
    bad_probability: float,
    checkpoint: str = "t_plus_18h",
) -> ForecastExample:
    return ForecastExample(
        city=city,
        target_date=target_date,
        event_ticker=f"EVENT-{target_date}-{city}",
        checkpoint=checkpoint,
        scheduled_at=f"{target_date}T18:00:00+00:00",
        as_of=f"{target_date}T18:00:10+00:00",
        winner_ticker=winner,
        tickers=("A", "B"),
        distributions={
            "good": (good_probability, 1.0 - good_probability),
            "bad": (bad_probability, 1.0 - bad_probability),
        },
    )


def weather_example(
    target_date: str,
    city: str,
    winner: str,
    weather_probability: float,
    hrrr_probability: float | None,
) -> ForecastExample:
    distributions = {
        name: (weather_probability, 1.0 - weather_probability)
        for name in WEATHER_CANDIDATES
    }
    distributions["market_midpoint"] = (0.5, 0.5)
    distributions["uniform"] = (0.5, 0.5)
    if hrrr_probability is not None:
        distributions["hrrr_top3_rerank"] = (
            hrrr_probability,
            1.0 - hrrr_probability,
        )
    return ForecastExample(
        city=city,
        target_date=target_date,
        event_ticker=f"EVENT-{target_date}-{city}",
        checkpoint="t_plus_18h",
        scheduled_at=f"{target_date}T18:00:00+00:00",
        as_of=f"{target_date}T18:00:10+00:00",
        winner_ticker=winner,
        tickers=("A", "B"),
        distributions=distributions,
    )


def three_bracket_regression_example(
    target_date: str,
    city: str,
    winner_index: int,
    hrrr_top_index: int,
) -> ForecastExample:
    tickers = ("LOW", "MID", "HIGH")
    baseline = (1 / 3, 1 / 3, 1 / 3)
    hrrr = [0.05, 0.05, 0.05]
    hrrr[hrrr_top_index] = 0.9
    distributions = {
        name: baseline
        for name in WEATHER_CANDIDATES
    }
    distributions.update(
        {
            "market_midpoint": baseline,
            "uniform": baseline,
            "hrrr_top3_rerank": tuple(hrrr),
        }
    )
    return ForecastExample(
        city=city,
        target_date=target_date,
        event_ticker=f"EVENT-{target_date}-{city}",
        checkpoint="t_plus_18h",
        scheduled_at=f"{target_date}T18:00:00+00:00",
        as_of=f"{target_date}T18:00:10+00:00",
        winner_ticker=tickers[winner_index],
        tickers=tickers,
        distributions=distributions,
        feature_metadata={"hrrr_index": float(hrrr_top_index)},
    )


def anchor_map(
    rows: list[ForecastExample],
    probabilities: tuple[float, ...],
) -> dict[tuple[str, str, str, str], tuple[float, ...]]:
    return {
        (row.target_date, row.city, row.checkpoint, row.as_of): probabilities
        for row in rows
    }


class OfflineTrainingTests(unittest.TestCase):
    def test_blend_probabilities_normalizes_weighted_distribution(self) -> None:
        probabilities = blend_probabilities(
            {
                "one": (0.8, 0.2),
                "two": (0.4, 0.6),
            },
            {"one": 0.75, "two": 0.25},
            probability_floor=None,
        )
        self.assertTrue(math.isclose(sum(probabilities), 1.0))
        self.assertTrue(math.isclose(probabilities[0], 0.7))

    def test_probability_floor_reserves_nonzero_mass_for_every_bracket(self) -> None:
        probabilities = apply_probability_floor((1.0, 0.0, 0.0), floor=0.01)
        self.assertTrue(math.isclose(sum(probabilities), 1.0))
        self.assertGreaterEqual(min(probabilities), 0.01)
        self.assertGreater(probabilities[0], probabilities[1])

    def test_soft_floor_allows_only_one_bracket_below_observation(self) -> None:
        brackets = [
            {"ticker": "A", "lower": None, "upper": 85},
            {"ticker": "B", "lower": 86, "upper": 87},
            {"ticker": "C", "lower": 88, "upper": 89},
            {"ticker": "D", "lower": 90, "upper": 91},
        ]
        self.assertEqual(bracket_index_for_temperature(brackets, 89.6), 3)
        probabilities = soft_observation_floor_probabilities(
            brackets,
            (0.0, 0.0, 0.0, 1.0),
            observed_high_f=89.6,
            mass=0.02,
        )
        self.assertTrue(math.isclose(sum(probabilities), 1.0))
        self.assertEqual(probabilities[0], 0.0)
        self.assertEqual(probabilities[1], 0.0)
        self.assertTrue(math.isclose(probabilities[2], 0.02))
        self.assertTrue(math.isclose(probabilities[3], 0.98))

    def test_fit_blend_weights_prefers_lower_log_loss_source(self) -> None:
        rows = [
            example("2026-06-20", "nyc", "A", 0.9, 0.1),
            example("2026-06-20", "mia", "A", 0.8, 0.2),
            example("2026-06-21", "nyc", "B", 0.1, 0.9),
            example("2026-06-21", "mia", "B", 0.2, 0.8),
        ]
        fit = fit_blend_weights(rows, ("good", "bad"), 0.1, 0.0)
        self.assertEqual(fit["weights"], {"good": 1.0, "bad": 0.0})

    def test_expanding_window_uses_prior_dates_only(self) -> None:
        rows = [
            example("2026-06-20", "nyc", "A", 0.9, 0.1),
            example("2026-06-20", "mia", "A", 0.9, 0.1),
            example("2026-06-21", "nyc", "B", 0.1, 0.9),
        ]
        scores, weights = expanding_window_scores(
            rows,
            ("good", "bad"),
            grid_step=0.1,
            regularization=0.0,
            min_train_events=2,
        )
        global_weights = [row for row in weights if row["checkpoint"] == "all"]
        self.assertEqual(global_weights[0]["mode"], "fallback_min_train_events")
        self.assertEqual(global_weights[1]["mode"], "trained")
        trained_rows = [row for row in scores if row["model"] == "trained_blend"]
        june_21 = [row for row in trained_rows if row["target_date"] == "2026-06-21"]
        self.assertEqual(june_21[0]["training_events"], 2)

    def test_checkpoint_trained_blend_falls_back_deterministically(self) -> None:
        rows = [
            example("2026-06-20", "nyc", "A", 0.9, 0.1),
            example("2026-06-20", "mia", "A", 0.9, 0.1),
            example("2026-06-21", "nyc", "B", 0.1, 0.9),
        ]
        scores, weights = expanding_window_scores(
            rows,
            ("good", "bad"),
            grid_step=0.1,
            regularization=0.0,
            min_train_events=2,
        )
        checkpoint_rows = [
            row for row in scores if row["model"] == "checkpoint_trained_blend"
        ]
        self.assertEqual(len(checkpoint_rows), 3)
        self.assertTrue(any(row["checkpoint"] == "all" for row in weights))
        self.assertTrue(any(row["checkpoint"] == "t_plus_18h" for row in weights))

    def test_fit_checkpoint_models_trains_independent_time_of_day_weights(self) -> None:
        rows = [
            example("2026-06-20", "nyc", "A", 0.9, 0.1, "t_plus_10h"),
            example("2026-06-20", "mia", "A", 0.9, 0.1, "t_plus_10h"),
            example("2026-06-21", "nyc", "B", 0.1, 0.9, "t_plus_10h"),
            example("2026-06-21", "mia", "B", 0.1, 0.9, "t_plus_10h"),
            example("2026-06-20", "den", "A", 0.1, 0.9, "t_plus_18h"),
            example("2026-06-20", "aus", "A", 0.1, 0.9, "t_plus_18h"),
            example("2026-06-21", "den", "B", 0.9, 0.1, "t_plus_18h"),
            example("2026-06-21", "aus", "B", 0.9, 0.1, "t_plus_18h"),
        ]
        models = fit_checkpoint_models(
            rows,
            ("good", "bad"),
            grid_step=0.1,
            regularization=0.0,
            min_train_events=1,
            probability_floor=0.0,
        )
        self.assertEqual(models["t_plus_10h"]["weights"], {"good": 1.0, "bad": 0.0})
        self.assertEqual(models["t_plus_18h"]["weights"], {"good": 0.0, "bad": 1.0})
        scores = score_checkpoint_models(rows, models, probability_floor=0.0)
        self.assertEqual(len(scores), len(rows))

    def test_staged_hrrr_training_uses_weather_blend_then_hrrr_weight(self) -> None:
        rows = [
            weather_example("2026-06-20", "nyc", "A", 0.5, 0.9),
            weather_example("2026-06-21", "nyc", "B", 0.5, 0.1),
        ]
        scores, weights = expanding_window_scores(
            rows,
            ("full",),
            grid_step=0.1,
            regularization=0.0,
            min_train_events=1,
            probability_floor=0.0,
        )
        first_hrrr_weights = [
            row
            for row in weights
            if row["target_date"] == "2026-06-20"
            and row["model"] == "trained_weather_hrrr_blend"
        ][0]
        self.assertEqual(first_hrrr_weights["mode"], "fallback_no_prior_hrrr_training_data")
        second_hrrr_weights = [
            row
            for row in weights
            if row["target_date"] == "2026-06-21"
            and row["model"] == "trained_weather_hrrr_blend"
        ][0]
        self.assertEqual(second_hrrr_weights["mode"], "trained")
        self.assertEqual(
            second_hrrr_weights["weights_json"],
            '{"hrrr_top3_rerank": 1.0, "trained_weather_blend": 0.0}',
        )
        scored = [
            row
            for row in scores
            if row["target_date"] == "2026-06-21"
            and row["model"] == "trained_weather_hrrr_blend"
        ][0]
        self.assertTrue(scored["top_one_correct"])
        self.assertTrue(math.isclose(float(scored["outcome_probability"]), 0.9))

    def test_regression_weather_hrrr_learns_hrrr_distance_feature(self) -> None:
        train = [
            three_bracket_regression_example("2026-06-20", "nyc", 0, 0),
            three_bracket_regression_example("2026-06-20", "la", 1, 1),
            three_bracket_regression_example("2026-06-21", "nyc", 2, 2),
            three_bracket_regression_example("2026-06-21", "la", 0, 0),
            three_bracket_regression_example("2026-06-22", "nyc", 1, 1),
            three_bracket_regression_example("2026-06-22", "la", 2, 2),
        ]
        fit = fit_regression_weather_hrrr(train, min_train_events=2)
        self.assertEqual(fit["mode"], "trained")
        test = three_bracket_regression_example("2026-06-23", "den", 2, 2)
        probabilities = regression_weather_hrrr_probabilities(
            test,
            fit,
            fallback_probabilities=(1 / 3, 1 / 3, 1 / 3),
            probability_floor=0.0,
        )
        self.assertEqual(max(range(3), key=lambda index: probabilities[index]), 2)
        self.assertGreater(probabilities[2], probabilities[1])

    def test_anchored_regression_blend_can_trust_regression_over_anchor(self) -> None:
        train = [
            three_bracket_regression_example("2026-06-20", "nyc", 0, 0),
            three_bracket_regression_example("2026-06-20", "la", 1, 1),
            three_bracket_regression_example("2026-06-21", "nyc", 2, 2),
            three_bracket_regression_example("2026-06-21", "la", 0, 0),
            three_bracket_regression_example("2026-06-22", "nyc", 1, 1),
            three_bracket_regression_example("2026-06-22", "la", 2, 2),
        ]
        regression_fit = fit_regression_weather_hrrr(train, min_train_events=2)
        fit = fit_anchored_regression_blend(
            train,
            regression_fit,
            anchor_map(train, (1 / 3, 1 / 3, 1 / 3)),
            grid_step=0.1,
            regularization=0.0,
            min_train_events=2,
            probability_floor=0.0,
        )
        self.assertEqual(fit["mode"], "trained")
        self.assertGreater(fit["weights"]["regression_hrrr"], 0.0)
        self.assertLessEqual(fit["weights"]["regression_hrrr"], 0.5)
        test = three_bracket_regression_example("2026-06-23", "den", 2, 2)
        probabilities = anchored_regression_probabilities(
            test,
            regression_fit,
            anchor_probabilities=(1 / 3, 1 / 3, 1 / 3),
            anchor_weights={
                str(name): float(value) for name, value in fit["weights"].items()
            },
            probability_floor=0.0,
        )
        self.assertEqual(max(range(3), key=lambda index: probabilities[index]), 2)

    def test_expected_index_and_finite_normal_distribution(self) -> None:
        self.assertTrue(math.isclose(expected_bracket_index((0.0, 1.0, 0.0)), 1.0))
        probabilities = finite_normal_index_probabilities(
            center=2.0,
            spread=0.75,
            count=5,
            probability_floor=0.001,
        )
        self.assertTrue(math.isclose(sum(probabilities), 1.0))
        self.assertEqual(max(range(5), key=lambda index: probabilities[index]), 2)
        self.assertGreaterEqual(min(probabilities), 0.001)

    def test_calibrated_temperature_error_falls_back_before_training(self) -> None:
        fit = fit_calibrated_temperature_error_model([], min_train_events=12)
        example_row = three_bracket_regression_example("2026-06-23", "den", 2, 2)
        fallback = (0.2, 0.6, 0.2)
        self.assertEqual(
            calibrated_temperature_error_probabilities(
                example_row,
                fit,
                fallback,
                probability_floor=0.0,
            ),
            fallback,
        )

    def test_calibrated_temperature_error_learns_hrrr_shift(self) -> None:
        train = [
            three_bracket_regression_example("2026-06-20", "nyc", 0, 0),
            three_bracket_regression_example("2026-06-20", "la", 1, 1),
            three_bracket_regression_example("2026-06-21", "nyc", 2, 2),
            three_bracket_regression_example("2026-06-21", "la", 0, 0),
            three_bracket_regression_example("2026-06-22", "nyc", 1, 1),
            three_bracket_regression_example("2026-06-22", "la", 2, 2),
        ]
        fit = fit_calibrated_temperature_error_model(train, min_train_events=2)
        self.assertEqual(fit["mode"], "trained")
        test = three_bracket_regression_example("2026-06-23", "den", 2, 2)
        probabilities = calibrated_temperature_error_probabilities(
            test,
            fit,
            fallback_probabilities=(1 / 3, 1 / 3, 1 / 3),
            probability_floor=0.0,
        )
        self.assertTrue(math.isclose(sum(probabilities), 1.0))
        self.assertEqual(max(range(3), key=lambda index: probabilities[index]), 2)

    def test_expanding_window_outputs_calibrated_variants_without_price_leakage(self) -> None:
        rows = [
            three_bracket_regression_example("2026-06-20", "nyc", 0, 0),
            three_bracket_regression_example("2026-06-20", "la", 1, 1),
            three_bracket_regression_example("2026-06-21", "nyc", 2, 2),
        ]
        scores, weights = expanding_window_scores(
            rows,
            ("full",),
            grid_step=0.1,
            regularization=0.0,
            min_train_events=1,
            probability_floor=0.0,
        )
        calibrated = [
            row
            for row in scores
            if row["model"] == "calibrated_temperature_error_model"
        ]
        market_aware = [
            row
            for row in scores
            if row["model"] == "calibrated_temperature_error_market_aware"
        ]
        anchored = [
            row
            for row in scores
            if row["model"] == "anchored_regression_weather_hrrr"
        ]
        self.assertEqual(len(calibrated), len(rows))
        self.assertEqual(len(market_aware), len(rows))
        self.assertEqual(len(anchored), len(rows))
        calibrated_weights = [
            row
            for row in weights
            if row["model"] == "calibrated_temperature_error_model"
        ]
        anchored_weights = [
            row
            for row in weights
            if row["model"] == "anchored_regression_weather_hrrr"
        ]
        self.assertTrue(calibrated_weights)
        self.assertTrue(anchored_weights)
        self.assertTrue(
            all(
                row["training_events"] < 12
                and row["mode"] == "fallback_min_train_events"
                for row in calibrated_weights
            )
        )


if __name__ == "__main__":
    unittest.main()
