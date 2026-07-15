"""Command line interface for the MarketEdge v2 research pipeline."""

from __future__ import annotations

import argparse

from marketedge_v2.pipeline import (
    WEATHER_FEATURE_PROFILES,
    MarketEdgeConfig,
    run_marketedge_backtest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Market-aware weather research backtest")
    parser.add_argument("--data", required=True, help="Frozen next-gen export directory")
    parser.add_argument("--output", required=True, help="New report directory")
    parser.add_argument("--min-training-dates", type=int, default=5)
    parser.add_argument("--minimum-ev", type=float, default=0.03)
    parser.add_argument("--max-spread", type=float, default=0.10)
    parser.add_argument("--daily-budget", type=float, default=40.0)
    parser.add_argument("--max-order-cost", type=float, default=3.0)
    parser.add_argument("--blend-penalty", type=float, default=0.05)
    parser.add_argument(
        "--weather-feature-profile", choices=WEATHER_FEATURE_PROFILES, default="legacy"
    )
    args = parser.parse_args(argv)
    summary = run_marketedge_backtest(
        args.data,
        args.output,
        MarketEdgeConfig(
            min_training_dates=args.min_training_dates,
            minimum_ev=args.minimum_ev,
            max_spread=args.max_spread,
            daily_budget=args.daily_budget,
            max_order_cost=args.max_order_cost,
            blend_penalty=args.blend_penalty,
            weather_feature_profile=args.weather_feature_profile,
        ),
    )
    strategy = summary["strategy"]
    print(
        "marketedge-v2: "
        f"trades={strategy['trades']} pnl={strategy['total_pnl']:.4f} "
        f"roi={strategy['roi']:.4f} output={summary['output_dir']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
