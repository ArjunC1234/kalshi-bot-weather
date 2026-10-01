# Causal Bracket Logit v2 Result

**Status: FAIL**

At least one frozen 14-day success requirement failed.

## Selection

- Model: `weather_logit_c0.1`
- Policy: `no_p0.80_e0.05_px0.50-0.80_s0.05_h10`
- Model ready: 2026-08-27T15:01:27.237804+00:00
- Selection trades / hit / stress PnL: 12 / 75.0% / $0.63

## Frozen 14-day test

- Trades: 20
- Wins / losses: 12 / 8
- Hit rate: 60.0%
- Exact one-sided 95% hit lower bound: 39.4%
- Net PnL: $-1.05
- Doubled-fee stress PnL: $-1.39
- Day-bootstrap 95% lower PnL: $-5.39
- Active days / cities: 12 / 6

## Frozen checks

- FAIL: `full_14_day_window_complete`
- FAIL: `minimum_trades`
- PASS: `minimum_active_days`
- PASS: `minimum_cities`
- PASS: `maximum_city_trade_fraction`
- FAIL: `minimum_hit_rate`
- FAIL: `minimum_iid_exact_95_hit_lower_bound`
- FAIL: `positive_net_pnl`
- FAIL: `positive_fee_stress_pnl`
- FAIL: `positive_day_bootstrap_95_lower_bound`
- FAIL: `positive_pnl_in_each_seven_day_half`
- FAIL: `nonnegative_leave_one_city_out_pnl`
- PASS: `minimum_label_coverage`
- PASS: `zero_availability_violations`
- PASS: `zero_input_integrity_errors`

## Limits

September 4-10 outcomes were examined in prior research, so this is a strict chronological replay but not a pristine independent confirmation.
Passing this replay would support prospective paper trading only. It would not prove future profitability, fill quality, or authorize live orders.
