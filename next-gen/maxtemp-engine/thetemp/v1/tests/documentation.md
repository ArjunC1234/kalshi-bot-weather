# TheTemp v1 Tests

These tests verify the clonable template remains runnable after repo changes.

## Should Cover

- CLI smoke behavior.
- Template artifact shape.
- Prediction/distribution output shape.
- No market-price feature usage.
- Compatibility with the shared backtest loader.

Keep these tests small. Their job is to preserve the template contract, not prove forecast quality.
