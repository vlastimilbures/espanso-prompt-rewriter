"""Whether a newer release exists (#197), for `doctor` and the interface's Home, never for a
trigger (tests/test_trigger_contract.py).

One GET of PyPI's JSON for promptmend, at most once a day: the answer is cached in
user_data_dir()/update-check.json. PROMPT_UPDATE_CHECK=false makes no request and reads no
file. Every failure (network, a bad reply, a damaged cache) is ``unknown``; nothing raises.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import __version__, config, config_files

URL = "https://pypi.org/pypi/promptmend/json"
TIMEOUT = 3.0
MAX_AGE = timedelta(hours=24)
CACHE_FILE = "update-check.json"
LATEST, AVAILABLE, UNKNOWN, OFF = "latest", "available", "unknown", "off"
# A final release only: X.Y.Z, no pre, dev, rc, post or local suffix.
_RELEASE = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_INSTALL_PS1 = (
    'powershell -ExecutionPolicy ByPass -c "irm https://github.com/vlastimilbures/promptmend/'
    'releases/latest/download/install.ps1 | iex"'
)
_UV = (
    "uv tool install --force promptmend -c "
    "https://github.com/vlastimilbures/promptmend/releases/latest/download/constraints.txt"
)
# Install channel (doctor's install check) -> the command that updates it (docs/install.md,
# "Update"). uv's own `uv tool upgrade` keeps the old constraints and a Release wheel's URL.
UPGRADE_COMMANDS = {
    "uv": _UV,
    "homebrew": "brew upgrade promptmend",
    "script": _INSTALL_PS1,
    "editable": "git pull",
}


@dataclass(frozen=True)
class UpdateStatus:
    """``state`` is latest, available, unknown or off; ``latest`` the newest release PyPI
    lists and ``checked_at`` when it was asked (ISO-8601 UTC), when known."""

    state: str = UNKNOWN
    latest: str | None = None
    checked_at: str | None = None


def upgrade_command(channel: str) -> str:
    """How to update an install of ``channel`` (doctor's install check)."""
    return UPGRADE_COMMANDS.get(channel, "see docs/install.md")


def _release(text: str) -> tuple[int, int, int] | None:
    match = _RELEASE.fullmatch(text)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def _installed() -> tuple[int, ...] | None:
    """The running version as numbers: its leading X.Y.Z (a dev build's suffix dropped)."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", __version__)
    return tuple(int(part) for part in match.groups()) if match else None


def newest(releases: Any) -> str | None:
    """The newest final release in PyPI's ``releases`` map whose files are not all yanked."""
    found: list[tuple[tuple[int, int, int], str]] = []
    for name, files in releases.items():
        number = _release(name)
        if number is None or not isinstance(files, list) or not files:
            continue
        if all(isinstance(f, dict) and f.get("yanked") for f in files):
            continue
        found.append((number, name))
    return max(found)[1] if found else None


def _fetch() -> str:
    """Ask PyPI for the newest release; raises on any failure. conftest replaces it, so no
    test reaches pypi.org."""
    import httpx

    headers = {"User-Agent": f"promptmend/{__version__}"}
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.get(URL, headers=headers)
    response.raise_for_status()
    latest = newest(response.json()["releases"])
    if latest is None:
        raise ValueError("no release listed")
    return latest


def cache_path() -> Path:
    return config.user_data_dir() / CACHE_FILE


def _cached(now: datetime) -> tuple[str, str] | None:
    """The cached (latest, checked_at) while younger than MAX_AGE. A damaged file or a time in
    the future (a clock set back) counts as expired."""
    try:
        data = json.loads(cache_path().read_text("utf-8"))
        latest, checked_at = data["latest"], data["checked_at"]
        when = datetime.fromisoformat(checked_at)
        if not isinstance(latest, str) or _release(latest) is None or when.tzinfo is None:
            return None
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if not timedelta(0) <= now - when < MAX_AGE:
        return None
    return latest, checked_at


def _status(latest: str, checked_at: str) -> UpdateStatus:
    installed = _installed()
    number = _release(latest)
    if installed is None or number is None:
        return UpdateStatus(UNKNOWN, latest, checked_at)
    # An install newer than PyPI (an editable dev build) is up to date.
    state = AVAILABLE if number > installed else LATEST
    return UpdateStatus(state, latest, checked_at)


def check(cfg: config.Settings, now: datetime | None = None) -> UpdateStatus:
    """Whether a newer release exists; never raises. Off: no request and no file read."""
    if not cfg.update_check:
        return UpdateStatus(OFF)
    try:
        now = now or datetime.now(UTC)
        cached = _cached(now)
        if cached is not None:
            return _status(*cached)
        latest = _fetch()
        checked_at = now.isoformat(timespec="seconds")
        try:
            body = json.dumps({"checked_at": checked_at, "latest": latest}) + "\n"
            config_files.write_atomic(cache_path(), body.encode("utf-8"), private=False)
        except Exception:  # noqa: S110 - a cache that cannot be written only asks again
            pass
        return _status(latest, checked_at)
    except Exception:
        return UpdateStatus(UNKNOWN)
