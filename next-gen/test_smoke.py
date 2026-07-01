from __future__ import annotations

import unittest
from pathlib import Path


class NextGenSmokeTests(unittest.TestCase):
    def test_documented_subsystems_exist(self) -> None:
        root = Path(__file__).resolve().parent
        for relative in (
            "documentation.md",
            "libs/documentation.md",
            "maxtemp-engine/documentation.md",
            "backtest/documentation.md",
            "strategy-engine/documentation.md",
            "production/documentation.md",
            "production/deployable/documentation.md",
        ):
            self.assertTrue((root / relative).exists(), relative)


if __name__ == "__main__":
    unittest.main()
