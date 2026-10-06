"""Check that a tool venv holds exactly the versions uv.lock pins (#34).

`uv tool install` resolves afresh and ignores uv.lock, so the contributor install scripts pass
the lock as constraints and then run this against the tool's interpreter:

    python scripts/check_tool_lock.py --python <tool venv python> [--lock uv.lock]

It compares `uv pip list --format json` for that interpreter with the lock and exits 1, naming
each package, when one is installed at a version the lock does not pin or is not in the lock
at all. Standard library only, so the tool's own interpreter can run it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

PROJECT = "promptmend"


def normalize(name: str) -> str:
    """PEP 503: `Foo_Bar.baz` and `foo-bar-baz` are the same package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def locked_versions(lock: Mapping[str, Any]) -> dict[str, set[str]]:
    """Every version uv.lock pins, per package (a forked resolution can pin several)."""
    pinned: dict[str, set[str]] = {}
    for package in lock.get("package", []):
        if "version" in package:
            pinned.setdefault(normalize(package["name"]), set()).add(str(package["version"]))
    return pinned


def mismatches(installed: Iterable[Mapping[str, str]], lock: Mapping[str, Any]) -> list[str]:
    """One line per installed package the lock does not pin at that version. The project
    itself is skipped: it is installed editable from the checkout, not from the lock."""
    pinned = locked_versions(lock)
    problems = []
    for item in installed:
        name, version = normalize(item["name"]), item["version"]
        if name == PROJECT:
            continue
        if name not in pinned:
            problems.append(f"{name} {version} is installed but not in uv.lock")
        elif version not in pinned[name]:
            locked = ", ".join(sorted(pinned[name]))
            problems.append(f"{name} {version} is installed, uv.lock pins {locked}")
    return problems


def pip_list(python: str) -> list[dict[str, str]]:
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["uv", "pip", "list", "--format", "json", "--python", python],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    result: list[dict[str, str]] = json.loads(proc.stdout)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", required=True, help="the tool venv's interpreter")
    parser.add_argument("--lock", default=str(Path(__file__).resolve().parents[1] / "uv.lock"))
    args = parser.parse_args(argv)
    lock = tomllib.loads(Path(args.lock).read_text(encoding="utf-8"))
    problems = mismatches(pip_list(args.python), lock)
    for line in problems:
        print(f"lock mismatch: {line}", file=sys.stderr)
    if problems:
        return 1
    print("The tool venv matches uv.lock.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
