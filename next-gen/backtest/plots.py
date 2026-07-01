"""Plotting hooks for backtest reports."""

from __future__ import annotations

from pathlib import Path


def ensure_charts_dir(output: Path) -> Path:
    charts = output / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    return charts
