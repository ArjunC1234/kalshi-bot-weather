# Live-Like Strategy Replay

- Trades: 2
- Fills: 2
- Blocked orders: 0
- Total PnL: 1.9577
- ROI: 2.0963
- Hit rate: 1.0000
- Max drawdown: 0.0000
- Open positions settled at end: 2
- Open positions left unsettled: 0
- Slice gate enabled: True

This replay is intentionally stricter than the settlement-only paper backtest:
it supports YES/NO sides, execution quote refresh, partial fills when quote size is present, model exits, observed-high forced exits, no-bid exit cooldowns, stale-market entry guards, and side-specific calibration outputs.
