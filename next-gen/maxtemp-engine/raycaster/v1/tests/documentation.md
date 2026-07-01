# Raycaster v1 Tests

These tests validate Raycaster v1 feature extraction, training fallback behavior,
temperature-to-bracket distribution conversion, and leakage-safe evaluation.

Because the parent folder `maxtemp-engine` contains a hyphen, top-level unittest
discovery uses `next-gen/tests/test_raycaster_v1.py` as a shim to load this
folder's tests.

