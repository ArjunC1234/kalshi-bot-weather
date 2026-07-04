# MaxTemp Engine Tests

Use this folder for max-temperature engine tests that are not specific to one model family.

## Should Cover

- Shared temperature-to-bracket conversion behavior.
- Shared max-temperature feature helpers.
- Weather-only metric helpers.
- Cross-model compatibility contracts.

## Should Not Cover

- Raycaster v1 internals. Put those in `raycaster/v1/tests/`.
- TheTemp template internals. Put those in `thetemp/v1/tests/`.
- Trading strategy or PnL code.

Run from repository root:

```powershell
python -m unittest discover -s next-gen
```
