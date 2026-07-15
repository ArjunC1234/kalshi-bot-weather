# Live-Like Strategy Replay

- Trades: 0
- Fills: 0
- Blocked orders: 0
- Total PnL: 0.0000
- ROI: 0.0000
- Hit rate: 0.0000
- Max drawdown: 0.0000
- Open positions settled at end: 0
- Open positions left unsettled: 0
- Slice gate enabled: True

This replay is intentionally stricter than the settlement-only paper backtest:
it supports YES/NO sides, execution quote refresh, partial fills when quote size is present, model exits, observed-high forced exits, no-bid exit cooldowns, stale-market entry guards, and side-specific calibration outputs.
