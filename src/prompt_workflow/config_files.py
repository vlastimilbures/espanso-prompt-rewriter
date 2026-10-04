"""The saved settings files in the user config dir: ``config.toml`` (non-secret settings) and
``secrets.toml`` (API keys, private to the user), plus the atomic, private file writes the
config services (config_store.py) build on.

This module is on the trigger path (ConfigLayers.resolve reads both files), so it stays
light: tomllib is imported only when a file exists, and the writer (tomli_w) and subprocess
only when something is written.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

SETTINGS_FILE = "config.toml"
SECRETS_FILE = "secrets.toml"
# Format version of config.toml. A file with a newer version is read (best effort) but never
# written, since this version cannot know what a rewrite would lose.
VERSION_KEY = "config_version"
CONFIG_VERSION = 1

_WINDOWS = os.name == "nt"
# Seconds a Windows ACL helper (icacls, whoami) may take.
_SUBPROCESS_TIMEOUT = 10


class ConfigFileError(ValueError):
    """A saved settings file that cannot be read or parsed. The message names the file and,
    for a syntax error, the position, never the content (it may hold a key)."""


class SecretStoreError(Exception):
    """The secret store failed. Never answered by falling back to a plaintext file."""


def _position(exc: Exception) -> str:
    # tomllib reports "Invalid value (at line 3, column 9)"; only the position is kept.
    found = re.search(r"\((at line \d+, column \d+|at end of document)\)", str(exc))
    return f" ({found.group(1)})" if found else ""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_toml(path: Path) -> tuple[dict[str, Any], str] | None:
    """The parsed table of a TOML file and the sha256 of its bytes, or None when it does not
    exist. Raises ConfigFileError when it exists but cannot be read or parsed."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise ConfigFileError(f"{path.name} cannot be read") from None
    import tomllib  # only once a saved file exists (about 2 ms on top of the CLI imports)

    try:
        table = tomllib.loads(data.decode("utf-8"))
    except UnicodeDecodeError:
        raise ConfigFileError(f"{path.name} is not UTF-8 text") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigFileError(f"{path.name} is not valid TOML{_position(exc)}") from None
    return table, digest(data)


def scalar_text(value: object) -> str | None:
    """A TOML value as the string a .env or environment variable would hold, or None when it
    is not a scalar setting value (a table, an array, a date)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float | str):
        return str(value)
    return None


def dump_toml(table: Mapping[str, Any]) -> bytes:
    import tomli_w  # never on the trigger path

    return tomli_w.dumps(dict(table)).encode("utf-8")


def _windows_tool(name: str) -> str:
    # By absolute path, so a planted icacls.exe earlier on PATH is never run.
    return str(Path(os.environ.get("SYSTEMROOT") or r"C:\Windows") / "System32" / name)


def _current_user_sid() -> str:
    import csv
    import subprocess

    try:
        result = subprocess.run(  # noqa: S603 - fixed System32 executable and arguments
            [_windows_tool("whoami.exe"), "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=True,
        )
        rows = list(csv.reader(result.stdout.splitlines()))
        sid = rows[0][1].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        raise SecretStoreError("could not look up the current Windows user") from None
    if not sid.startswith("S-1-"):
        raise SecretStoreError("could not look up the current Windows user")
    return sid


def restrict_to_user(path: Path) -> None:
    """Windows: replace the file's ACL with one entry for the current user (read, write,
    delete), dropping inherited entries, so no other account or group can read it. Called
    while the file is still empty. Raises SecretStoreError; there is no fallback."""
    import subprocess

    sid = _current_user_sid()
    command = [
        _windows_tool("icacls.exe"),
        str(path),
        "/inheritance:r",
        "/grant:r",
        f"*{sid}:(R,W,D)",
    ]
    try:
        subprocess.run(  # noqa: S603 - fixed System32 executable; the path is one argument
            command, capture_output=True, timeout=_SUBPROCESS_TIMEOUT, check=True
        )
    except (OSError, subprocess.SubprocessError):
        raise SecretStoreError(f"could not make {path.name} private to your user") from None


def make_private_dir(directory: Path) -> None:
    """Create ``directory`` (and parents) if missing; a new one is private to the user on
    POSIX (an existing one keeps its mode)."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)


def write_atomic(path: Path, data: bytes, *, private: bool) -> None:
    """Replace ``path`` with ``data`` all at once: a reader sees the old file or the new one,
    never a torn write. The temporary file is created in the same directory (os.replace is
    atomic only within one file system; on Windows it is MoveFileEx with
    MOVEFILE_REPLACE_EXISTING, which fails rather than half-replaces when another process
    holds the file open).

    The temporary file is created with mode 600 on POSIX (mkstemp), so a ``private`` file is
    never readable by others, not even for a moment. On Windows a ``private`` file gets a
    user-only ACL while still empty, before any secret is written into it.
    """
    make_private_dir(path.parent)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temp = Path(name)
    try:
        os.close(fd)
        if private and _WINDOWS:
            restrict_to_user(temp)
        with temp.open("r+b") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    if not _WINDOWS:
        # Persist the rename itself; best effort, since some file systems refuse it.
        with contextlib.suppress(OSError):
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)


class SecretStore(Protocol):
    """Where API keys are saved. Reads and writes raise SecretStoreError, never fall back to
    another store."""

    @property
    def source(self) -> str:
        """Provenance label for ConfigLayers, e.g. ``file:<path>``."""
        ...

    def read(self) -> dict[str, str]:
        """Every saved secret by env var name; empty when nothing is saved."""
        ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class FileSecretStore:
    """Secrets in a TOML file (``NAME = "value"``) private to the user: mode 600 on POSIX, a
    user-only ACL on Windows. Never part of config.toml."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def source(self) -> str:
        return f"file:{self.path}"

    def read(self) -> dict[str, str]:
        try:
            loaded = load_toml(self.path)
        except ConfigFileError as exc:
            raise SecretStoreError(str(exc)) from None
        if loaded is None:
            return {}
        return {k: v for k, v in loaded[0].items() if isinstance(v, str)}

    def exposed(self) -> bool:
        """POSIX: the file is readable or writable by the group or others. (Windows ACLs are
        checked when the file is written, not on every read.)"""
        if _WINDOWS:
            return False
        try:
            return bool(self.path.stat().st_mode & 0o077)
        except OSError:
            return False

    def _write(self, values: Mapping[str, str]) -> None:
        try:
            write_atomic(self.path, dump_toml(dict(sorted(values.items()))), private=True)
        except OSError as exc:
            raise SecretStoreError(f"could not write {self.path.name}: {exc.strerror}") from None

    def set(self, name: str, value: str) -> None:
        self._write({**self.read(), name: value})

    def delete(self, name: str) -> None:
        values = self.read()
        if name in values:
            del values[name]
            self._write(values)


def secret_store(directory: Path) -> SecretStore:
    """The secret store for the config dir ``directory``.

    TODO(D-CFG-3): an opt-in KeyringSecretStore, once it has been tested from an
    Espanso-launched (non-TTY) process on macOS, including after the interpreter path changes,
    and on Windows. It must raise SecretStoreError on any keyring failure; it must never fall
    back to this file. Import keyring lazily (it is forbidden on the trigger path).
    """
    return FileSecretStore(directory / SECRETS_FILE)
