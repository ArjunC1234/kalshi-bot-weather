"""Discovery shim for TheTemp template tests."""

from __future__ import annotations

import sys
import unittest
from importlib import util
from pathlib import Path


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str):
    next_gen = Path(__file__).resolve().parents[1]
    thetemp_v1 = next_gen / "maxtemp-engine" / "thetemp" / "v1"
    test_dir = thetemp_v1 / "tests"
    for path in (next_gen, thetemp_v1):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    suite = unittest.TestSuite()
    for path in sorted(test_dir.glob(pattern or "test*.py")):
        _clear_model_modules()
        module_name = f"thetemp_v1_{path.stem}"
        spec = util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


def _clear_model_modules() -> None:
    for name in (
        "artifacts",
        "config",
        "dataset",
        "distribution",
        "evaluate",
        "features",
        "predict",
        "train",
    ):
        sys.modules.pop(name, None)
