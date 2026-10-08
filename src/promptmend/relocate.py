"""Moves the user folders from their legacy name (`prompt-workflow`) to `promptmend` (#169).

Run by cli._LazyGroup before every management command and the interface, never on the trigger
path: until it has run, config.folder_in_use() keeps the triggers on the legacy folders, so
moving them later loses nothing. Every step is a rename within the same parent folder, never a
copy: it is atomic, and secrets.toml keeps its mode 600 (POSIX) or its user-only ACL (Windows).
Nothing is ever overwritten, and a step that fails is tried again by the next command.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import config, config_files
from .config_store import MARKER_FILE


def migrate_folders(environ: Mapping[str, str] = os.environ) -> list[str]:
    """Move the legacy config and data folders, then point migration.json at the new config
    folder. Returns one line per thing done or failed; never raises."""
    messages: list[str] = []
    for what, folders in (
        ("settings", config.config_folders(environ)),
        ("data", config.data_folders(environ)),
    ):
        new, old = folders
        variable = config.env_file_inside(old, environ) if old.is_dir() else None
        if variable:
            # Legacy mode reads that .env alone: after a move the variable would name a file
            # that is gone, and every trigger would run on the defaults.
            messages.append(
                f"{variable} names a file inside {old}, so that folder was not moved: point "
                f"{variable} at the same file under {new}, then run the command again"
            )
            continue
        try:
            messages.extend(_move(what, new, old))
        except OSError as exc:
            # A file another process holds open (Windows: a trigger writing the history), or
            # permissions: the legacy folder stays in use, and the next command tries again.
            reason = exc.strerror or type(exc).__name__
            messages.append(f"could not move {old} to {new} ({reason}); the next command retries")
    try:
        _rebase_marker(*config.config_folders(environ))
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        messages.append(f"could not update {MARKER_FILE} ({reason}); the next command retries")
    return messages


# Files that belong to another one and move with it, all or none: SQLite's journals next to
# history.sqlite3, and history.py's lock, temporary and stale-lock files next to history.lost
# (history.spool's temporary file too).
# A -wal moved next to someone else's database would corrupt it.
_COMPANION = re.compile(r"(-wal|-shm|-journal|\.lock(\.[0-9a-f]+\.stale)?|\.\d+\.tmp)$")


def _unit(name: str) -> str:
    """The name of the file ``name`` belongs to (itself, unless it is a companion)."""
    return _COMPANION.sub("", name)


def _move(what: str, new: Path, old: Path) -> list[str]:
    # A symlinked legacy folder is left alone: merging through it, or removing it, would act
    # on a folder elsewhere. It stays in use until moved by hand (doctor says so).
    if old.is_symlink() or not old.is_dir():
        return []
    if not os.path.lexists(new):
        try:
            os.rename(old, new)
        except FileNotFoundError:
            return []  # a concurrent command moved it first
        return [f"moved the {what} folder {old} to {new}"]
    if not new.is_dir():
        return []  # something else has the new name: the legacy folder stays in use
    # Both exist (a command of this version created the new one first, or a move was cut
    # short): move each unit the new folder lacks entirely. lexists() is checked first
    # because a POSIX rename would silently replace an existing file.
    try:
        names = sorted(entry.name for entry in old.iterdir())
    except FileNotFoundError:
        return []
    taken = {_unit(entry.name) for entry in new.iterdir()}
    moved = []
    for name in names:  # sorted: a unit's own file comes before its companions
        target = new / name
        if _unit(name) in taken or os.path.lexists(target):
            continue
        try:
            os.rename(old / name, target)
        except FileNotFoundError:
            continue  # a concurrent command moved it first
        moved.append(name)
    try:
        left = sorted(entry.name for entry in old.iterdir())
    except FileNotFoundError:
        left = []
    else:
        if not left:
            old.rmdir()
    if not moved:
        return []  # only conflicts, reported by doctor's `folders` check, not on every command
    lines = [f"moved {', '.join(moved)} from the {what} folder {old} to {new}"]
    if left:
        lines.append(
            f"kept in {old}, since {new} has its own (never overwritten): {', '.join(left)}"
        )
    return lines


def _rebase_marker(new: Path, old: Path) -> None:
    """migration.json records absolute paths (the moved .env, the backup folder), which
    `config rollback` follows: rewrite those under the legacy config folder."""
    path = new / MARKER_FILE
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return
    try:
        record = json.loads(data)
    except ValueError:
        return  # damaged: left alone, and rollback says so
    rebased = _rebase(record, str(old), str(new))
    if rebased != record:
        # As config_store._write_marker() writes it.
        config_files.write_atomic(
            path, json.dumps(rebased, indent=2).encode("utf-8"), private=False
        )


def _rebase(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: _rebase(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_rebase(item, old, new) for item in value]
    if isinstance(value, str) and (
        value == old or (value.startswith(old) and value[len(old)] in ("/", "\\"))
    ):
        return new + value[len(old) :]
    return value
