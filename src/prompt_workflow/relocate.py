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


def _move(what: str, new: Path, old: Path) -> list[str]:
    if not old.is_dir():
        return []
    if not os.path.lexists(new):
        os.rename(old, new)
        return [f"moved the {what} folder {old} to {new}"]
    if not new.is_dir():
        return []  # something else has the new name: the legacy folder stays in use
    # Both exist (a command of this version created the new one first, or a move was cut
    # short): move what the new folder lacks. lexists() is checked first because a POSIX
    # rename would silently replace an existing file.
    moved = []
    for entry in sorted(old.iterdir()):
        target = new / entry.name
        if not os.path.lexists(target):
            os.rename(entry, target)
            moved.append(entry.name)
    left = sorted(entry.name for entry in old.iterdir())
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
