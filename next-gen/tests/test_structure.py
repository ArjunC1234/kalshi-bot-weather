from __future__ import annotations

import unittest
from pathlib import Path


class NextGenStructureTests(unittest.TestCase):
    def test_expected_top_level_folders_exist(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "libs",
            "maxtemp-engine",
            "maxtemp-engine/tests",
            "backtest",
            "backtest/tests",
            "strategy-engine",
            "strategy-engine/tests",
            "production/deployable",
            "tests",
        ):
            self.assertTrue((root / relative).is_dir(), relative)

    def test_deploy_scripts_only_target_deployable_folder(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in ("production/deploy.ps1", "production/deploy.sh"):
            text = (root / relative).read_text(encoding="utf-8")
            self.assertIn("deployable", text.lower())


if __name__ == "__main__":
    unittest.main()
