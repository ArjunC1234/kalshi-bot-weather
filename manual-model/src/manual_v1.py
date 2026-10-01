from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ManualModel:
    global_bias: float
    global_sigma: float
    group_bias: dict[tuple[str, str], float]
    group_sigma: dict[tuple[str, str], float]
    min_sigma: float = 2.0

    @classmethod
    def fit(cls, rows: pd.DataFrame) -> "ManualModel":
        train = rows.dropna(subset=["residual_f"]).copy()
        if train.empty:
            return cls(0.0, 3.0, {}, {})
        global_bias = float(train["residual_f"].mean())
        global_sigma = _rmse(train["residual_f"] - global_bias)
        group_bias: dict[tuple[str, str], float] = {}
        group_sigma: dict[tuple[str, str], float] = {}
        for (city, checkpoint), group in train.groupby(["city", "checkpoint"], dropna=False):
            key = (str(city), str(checkpoint))
            residuals = group["residual_f"].astype(float)
            local_bias = float(residuals.mean())
            shrink = min(1.0, len(group) / 20.0)
            bias = shrink * local_bias + (1.0 - shrink) * global_bias
            sigma = _rmse(residuals - bias)
            group_bias[key] = bias
            group_sigma[key] = max(global_sigma, sigma)
        return cls(global_bias, max(2.0, global_sigma), group_bias, group_sigma)

    def predict_row(self, row: pd.Series) -> tuple[float, float]:
        key = (str(row["city"]), str(row["checkpoint"]))
        mean = float(row["baseline_high_f"]) + self.group_bias.get(key, self.global_bias)
        observed = _finite(row.get("observed_high_so_far_f"))
        if observed is not None:
            mean = max(mean, observed)
        source_std = _finite(row.get("source_std_f")) or 0.0
        source_range = _finite(row.get("source_range_f")) or 0.0
        sigma = max(
            self.min_sigma,
            self.group_sigma.get(key, self.global_sigma),
            source_std * 1.35,
            source_range * 0.45,
        )
        return mean, min(8.0, sigma)


def add_predictions(rows: pd.DataFrame, model: ManualModel) -> pd.DataFrame:
    output = rows.copy()
    means: list[float] = []
    sigmas: list[float] = []
    for _, row in output.iterrows():
        mean, sigma = model.predict_row(row)
        means.append(mean)
        sigmas.append(sigma)
    output["expected_high_f"] = means
    output["sigma_f"] = sigmas
    for level, z in {0.05: -1.645, 0.25: -0.674, 0.50: 0.0, 0.75: 0.674, 0.95: 1.645}.items():
        output[f"q{int(level * 100):02d}"] = output["expected_high_f"] + z * output["sigma_f"]
    return output


def bracket_probability(mean: float, sigma: float, lower_f: float | None, upper_f: float | None) -> float:
    lower = -math.inf if lower_f is None or pd.isna(lower_f) else float(lower_f) - 0.5
    upper = math.inf if upper_f is None or pd.isna(upper_f) else float(upper_f) + 0.5
    return max(0.0, min(1.0, _normal_cdf(upper, mean, sigma) - _normal_cdf(lower, mean, sigma)))


def _normal_cdf(value: float, mean: float, sigma: float) -> float:
    if value == -math.inf:
        return 0.0
    if value == math.inf:
        return 1.0
    z = (value - mean) / max(1e-6, sigma)
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _rmse(values: pd.Series) -> float:
    clean = values.astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return 3.0
    return float(np.sqrt(np.mean(np.square(clean))))


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed):
        return None
    return parsed
