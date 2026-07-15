# Live-Like Strategy Replay

- Trades: 7
- Fills: 7
- Blocked orders: 0
- Total PnL: 4.4077
- ROI: 1.7745
- Hit rate: 0.8571
- Max drawdown: -0.2700
- Open positions settled at end: 7
- Open positions left unsettled: 0

This replay is intentionally stricter than the settlement-only paper backtest:
it supports YES/NO sides, execution quote refresh, partial fills when quote size is present, model exits, observed-high forced exits, no-bid exit cooldowns, stale-market entry guards, and side-specific calibration outputs.
