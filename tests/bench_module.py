"""Imports scripts/bench_models.py, which is a script rather than a package module."""

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_models.py"
_spec = importlib.util.spec_from_file_location("bench_models", _SCRIPT)
assert _spec and _spec.loader
bench = importlib.util.module_from_spec(_spec)
# @dataclass resolves annotations through sys.modules, so register before executing.
sys.modules["bench_models"] = bench
_spec.loader.exec_module(bench)
