"""Discovery shim for Raycaster v1 tests under the hyphenated maxtemp-engine path."""

from __future__ import annotations

import sys
import unittest
from importlib import util
from pathlib import Path


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str):
    next_gen = Path(__file__).resolve().parents[1]
    raycaster_v1 = next_gen / "maxtemp-engine" / "raycaster" / "v1"
    test_dir = raycaster_v1 / "tests"
    for path in (next_gen, raycaster_v1):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    suite = unittest.TestSuite()
    for path in sorted(test_dir.glob(pattern or "test*.py")):
        module_name = f"raycaster_v1_{path.stem}"
        spec = util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))
    return suite
