"""Fill the release version into the Windows installer for the release workflow (#186).

    python3 scripts/render_installer.py 0.20.0 dist/install.ps1

Reads scripts/install.ps1, replaces its single PLACEHOLDER with the version and writes the
result, which the workflow attaches to the GitHub Release. Standard library only. Exits
nonzero with a message if the version is not X.Y.Z or the placeholder is not there exactly once.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "scripts" / "install.ps1"
PLACEHOLDER = "__PROMPTMEND_VERSION__"
_VERSION = re.compile(r"\d+\.\d+\.\d+")


def render(source: str, version: str) -> str:
    if not _VERSION.fullmatch(version):
        raise ValueError(f"{version!r} is not X.Y.Z")
    count = source.count(PLACEHOLDER)
    if count != 1:
        raise ValueError(f"{PLACEHOLDER} appears {count} times, not once")
    return source.replace(PLACEHOLDER, version)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version")
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args(argv)
    try:
        text = render(args.source.read_text("utf-8"), args.version)
        args.output.write_bytes(text.encode("ascii"))
    except (OSError, ValueError) as exc:
        sys.exit(f"render_installer: {exc}")


if __name__ == "__main__":
    main()
