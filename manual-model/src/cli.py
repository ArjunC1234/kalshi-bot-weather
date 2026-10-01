from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import pandas as pd

from .data_loader import load_export, normalize_tables
from .features import build_weather_rows
from .manual_v1 import ManualModel, add_predictions
from .strategy import StrategyConfig, daily_pnl, generate_trades, summarize_trades


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual Kalshi weather model experiments")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="train and evaluate manual_v1")
    run.add_argument("--data", required=True)
    run.add_argument("--train-start", required=True)
    run.add_argument("--train-end", required=True)
    run.add_argument("--test-start", required=True)
    run.add_argument("--test-end", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--min-edge", type=float, default=0.03)
    run.add_argument("--max-spread", type=float, default=0.10)
    run.add_argument("--min-entry-price", type=float, default=0.35)
    run.add_argument("--max-entry-price", type=float, default=0.65)
    run.add_argument("--market-weight", type=float, default=0.35)
    args = parser.parse_args()
    if args.command == "run":
        return run_model(args)
    return 1


def run_model(args: argparse.Namespace) -> int:
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)

    tables = normalize_tables(load_export(args.data))
    rows = build_weather_rows(tables)
    train_start = pd.to_datetime(args.train_start).date()
    train_end = pd.to_datetime(args.train_end).date()
    test_start = pd.to_datetime(args.test_start).date()
    test_end = pd.to_datetime(args.test_end).date()
    train_rows = rows[(rows["target_date"] >= train_start) & (rows["target_date"] <= train_end)]
    test_rows = rows[(rows["target_date"] >= test_start) & (rows["target_date"] <= test_end)]

    model = ManualModel.fit(train_rows)
    predictions = add_predictions(test_rows, model)
    config = StrategyConfig(
        min_edge=args.min_edge,
        max_spread=args.max_spread,
        min_entry_price=args.min_entry_price,
        max_entry_price=args.max_entry_price,
        market_weight=args.market_weight,
    )
    trades = generate_trades(predictions, tables["markets"], tables["settlements"], config)
    daily = daily_pnl(trades)
    summary = {
        "model": "manual_v1",
        "data": str(args.data),
        "train_start": args.train_start,
        "train_end": args.train_end,
        "test_start": args.test_start,
        "test_end": args.test_end,
        "training_rows": int(len(train_rows.dropna(subset=["final_high_f"]))),
        "test_rows": int(len(test_rows)),
        "config": config.__dict__,
        **summarize_trades(trades),
    }

    predictions.to_csv(output / "predictions.csv", index=False)
    trades.to_csv(output / "trades.csv", index=False)
    daily.to_csv(output / "daily_pnl.csv", index=False)
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _plot_daily(daily, output / "daily_pnl.png")
    print(
        f"manual_v1: trades={summary['trades']} "
        f"pnl={summary['total_pnl']:.2f} roi={summary['roi']:.2%} "
        f"hit={summary['hit_rate']:.2%} output={output}"
    )
    return 0


def _plot_daily(daily: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 5), dpi=150)
    fig.patch.set_facecolor("#fbf7ee")
    ax.set_facecolor("#fbf7ee")
    if not daily.empty:
        ax.bar(daily["target_date"].astype(str), daily["pnl"], color="#2f6f63", label="Daily PnL")
        ax.plot(daily["target_date"].astype(str), daily["cumulative_pnl"], color="#243b6b", marker="o", label="Cumulative PnL")
    ax.axhline(0, color="#b9ab91", linewidth=1)
    ax.set_ylabel("PnL ($)")
    ax.set_xlabel("Target date")
    ax.tick_params(axis="x", rotation=30)
    ax.grid(axis="y", color="#ddd4c2", alpha=0.8)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
