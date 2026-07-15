# Live-Like Strategy Replay

- Trades: 6
- Fills: 9
- Blocked orders: 0
- Total PnL: -2.2232
- ROI: -0.5650
- Hit rate: 0.5000
- Max drawdown: -2.5602
- Open positions settled at end: 3
- Open positions left unsettled: 0

This replay is intentionally stricter than the settlement-only paper backtest:
it supports YES/NO sides, execution quote refresh, partial fills when quote size is present, model exits, observed-high forced exits, no-bid exit cooldowns, stale-market entry guards, and side-specific calibration outputs.
