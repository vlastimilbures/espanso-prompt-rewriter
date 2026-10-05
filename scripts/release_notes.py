"""The release version and its notes, checked against CHANGELOG.md, for the release workflow.

    python scripts/release_notes.py version   # the pyproject version, once CHANGELOG agrees
    python scripts/release_notes.py notes     # that version's CHANGELOG section
    python scripts/release_notes.py notes --release 0.10.0   # an older section (a backfill)

CHANGELOG.md may open with `## Unreleased`; every other `## ` heading must read
`## X.Y.Z - YYYY-MM-DD`, newest first, and the first of them must be the pyproject version.
Standard library only, so the workflow can run it with any Python 3.11+. Exits nonzero with a
message on the first problem.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UNRELEASED = "Unreleased"
_HEADING = re.compile(r"^## (.*)$", re.MULTILINE)
_VERSION = re.compile(r"(?P<version>\d+\.\d+\.\d+) - (?P<date>\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Release:
    version: str
    date: date
    notes: str


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def releases(changelog: str) -> list[Release]:
    """Every released section, newest first; raises ValueError on a malformed heading."""
    matches = list(_HEADING.finditer(changelog))
    found: list[Release] = []
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(changelog)
        if title == UNRELEASED and i == 0:
            continue
        heading = _VERSION.fullmatch(title)
        if heading is None:
            raise ValueError(f"heading {title!r} is not 'X.Y.Z - YYYY-MM-DD'")
        try:
            day = date.fromisoformat(heading["date"])
        except ValueError:
            raise ValueError(f"heading {title!r} has an invalid date") from None
        release = Release(heading["version"], day, changelog[match.end() : end].strip())
        if found and _key(release.version) >= _key(found[-1].version):
            raise ValueError(f"{release.version} is listed after {found[-1].version}")
        if found and release.date > found[-1].date:
            raise ValueError(f"{release.version} is dated after {found[-1].version}")
        if not release.notes:
            raise ValueError(f"{release.version} has no notes")
        found.append(release)
    if not found:
        raise ValueError("no released version")
    return found


def pyproject_version(pyproject: Path) -> str:
    version = tomllib.loads(pyproject.read_text("utf-8"))["project"]["version"]
    if not isinstance(version, str):
        raise ValueError("project.version is not a string")
    return version


def current(changelog: str, version: str) -> Release:
    """The newest released section, which must be `version`."""
    newest = releases(changelog)[0]
    if newest.version != version:
        raise ValueError(
            f"the newest CHANGELOG version is {newest.version}, pyproject has {version}"
        )
    return newest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("what", choices=["version", "notes"])
    parser.add_argument("--changelog", type=Path, default=REPO / "CHANGELOG.md")
    parser.add_argument("--pyproject", type=Path, default=REPO / "pyproject.toml")
    parser.add_argument("--release", help="this CHANGELOG version instead of the pyproject one")
    args = parser.parse_args(argv)
    try:
        changelog = args.changelog.read_text("utf-8")
        if args.release:
            found = {r.version: r for r in releases(changelog)}
            if args.release not in found:
                raise ValueError(f"CHANGELOG has no {args.release} section")
            release = found[args.release]
        else:
            release = current(changelog, pyproject_version(args.pyproject))
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(f"release_notes: {exc}")
    sys.stdout.write(release.version if args.what == "version" else release.notes + "\n")


if __name__ == "__main__":
    main()
