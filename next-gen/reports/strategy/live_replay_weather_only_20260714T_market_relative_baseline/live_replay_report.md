# Live-Like Strategy Replay

- Trades: 4
- Fills: 5
- Blocked orders: 13
- Total PnL: -0.0613
- ROI: -0.0208
- Hit rate: 0.5000
- Max drawdown: -2.0190
- Open positions settled at end: 4
- Open positions left unsettled: 1
- Slice gate enabled: False

This replay is intentionally stricter than the settlement-only paper backtest:
it supports YES/NO sides, execution quote refresh, partial fills when quote size is present, model exits, observed-high forced exits, no-bid exit cooldowns, stale-market entry guards, and side-specific calibration outputs.
