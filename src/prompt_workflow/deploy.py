"""Managed Espanso deployment: plan, apply and reverse what prompt-workflow writes into
Espanso's `match/` folder (#86).

Each packaged match file (assets.py) is rendered with the launcher path and a version stamp,
compared with the manifest of what we deployed before and with what is on disk, and written
only after the caller confirms. A file the user edited is never overwritten silently, only
files we own are removed, and nothing is ever written to Espanso's `config/` (#37).

Imported lazily by the `espanso` commands, never on the trigger path.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, assets
from .config import user_data_dir
from .match_history import KNOWN_SOURCES

PLACEHOLDER = "__PROMPT_WORKFLOW__"
STAMP = "# prompt-workflow {version} (managed; edit at your own risk)\n"
_STAMP_LINE = re.compile(r"# prompt-workflow \S+ \(managed; edit at your own risk\)\n")
MANIFEST_NAME = "espanso-manifest.json"
MANIFEST_FORMAT = 1
# Our backups of one file kept after a deploy; older ones we made are deleted.
KEEP_BACKUPS = 2
# A side-by-side copy is not a .yml file, so Espanso does not load its triggers twice.
SIDE_SUFFIX = ".prompt-workflow-new"
COMMAND_TIMEOUT = 30

# Plan states (status) and conflict choices (deploy).
MISSING, IN_SYNC, STALE, MODIFIED, FOREIGN = "missing", "in sync", "stale", "modified", "foreign"
KEEP, OURS, SIDE = "keep", "ours", "side"
CHOICES = (KEEP, OURS, SIDE)

# The launcher goes inside a double-quoted YAML string that a shell runs: refuse characters
# the shell or YAML would interpret there rather than try to escape them (the same guards the
# install scripts had). On Windows the path is checked after its backslashes become slashes.
_UNSAFE_POSIX = re.compile(r'["$`\\]')
_UNSAFE_WINDOWS = re.compile(r'["%^&|<>]')
# The quoted launcher in a cmd line as every release wrote it: `cmd: "\"<path>\" improve`.
_QUOTED_LAUNCHER = re.compile(r'\\"([^"\\]+)\\"')
# A path component that names a release (0.15.0, 0.15.0_1): gone after the next upgrade.
_VERSIONED = re.compile(r"\d+(\.\d+)+([._-].*)?")


class DeployError(Exception):
    """A deployment that cannot go ahead; the message says why."""


# Runs a command and returns its stdout, or None when it is missing, fails or times out.
# Every function that runs one looks run_command up when called, so tests can replace it.
Runner = Callable[[Sequence[str]], str | None]


def run_command(argv: Sequence[str]) -> str | None:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            list(argv), capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Launcher -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Launcher:
    path: Path
    channel: str  # uv, homebrew, scoop, script or explicit


def launcher_text(path: Path | str, *, windows: bool = os.name == "nt") -> str:
    """The launcher as written into the match files, or DeployError when it is unsafe there."""
    text = str(path)
    if windows:
        # Espanso YAML uses forward slashes; normalised for safety inside quotes.
        text = text.replace("\\", "/")
        if _UNSAFE_WINDOWS.search(text):
            raise DeployError(
                f"The CLI path contains a character cmd.exe or YAML would interpret: {path}"
            )
    elif _UNSAFE_POSIX.search(text):
        raise DeployError(f"The CLI path contains a quote, $, backtick or backslash: {path}")
    return text


def _is_stable(path: Path) -> bool:
    """False for a path an upgrade or a removed checkout venv would take away."""
    return not any(part in ("Cellar", ".venv") or _VERSIONED.fullmatch(part) for part in path.parts)


def _inside(path: Path, parent: Path) -> bool:
    return path.resolve().is_relative_to(parent.resolve())


def resolve_launcher(
    *,
    runner: Runner | None = None,
    prefix: Path | None = None,
    script: Path | None = None,
    windows: bool = os.name == "nt",
) -> Launcher:
    """The stable entry point of the install that is running now: the channel's own bin
    (uv tool's bin dir, Homebrew's `<prefix>/bin`, Scoop's shim), never a versioned path
    such as a Homebrew Cellar, else the running console script when that path is stable."""
    runner = runner or run_command
    prefix = Path(sys.prefix) if prefix is None else prefix
    script = Path(sys.argv[0]) if script is None else script
    exe = "prompt-workflow.exe" if windows else "prompt-workflow"

    tool_dir = runner(["uv", "tool", "dir"])
    if tool_dir and _inside(prefix, Path(tool_dir.strip())):
        bin_dir = runner(["uv", "tool", "dir", "--bin"])
        if bin_dir and (Path(bin_dir.strip()) / exe).is_file():
            return Launcher(Path(bin_dir.strip()) / exe, "uv")

    if "Cellar" in prefix.parts:
        brew = runner(["brew", "--prefix"])
        if brew:
            formula = prefix.parts[prefix.parts.index("Cellar") + 1]
            root = Path(brew.strip())
            for candidate in (root / "bin" / exe, root / "opt" / formula / "bin" / exe):
                if candidate.is_file():
                    return Launcher(candidate, "homebrew")

    lowered = [part.lower() for part in prefix.parts]
    if windows and "apps" in lowered and "scoop" in lowered[: lowered.index("apps")]:
        shim = Path(*prefix.parts[: lowered.index("apps")]) / "shims" / exe
        if shim.is_file():
            return Launcher(shim, "scoop")

    for candidate in (script, script.with_name(exe)):
        if candidate.is_absolute() and candidate.is_file() and candidate.name == exe:
            if _is_stable(candidate):
                return Launcher(candidate, "script")
            break
    raise DeployError(
        "Could not find a stable prompt-workflow launcher (install it with `uv tool install`, "
        "Homebrew or Scoop, or pass --launcher)"
    )


# --- Espanso folder -----------------------------------------------------------------------


def default_espanso_dir(
    environ: Mapping[str, str] = os.environ, *, platform: str = sys.platform
) -> Path:
    """Espanso's own default config folder, used when `espanso path config` cannot answer."""
    if platform == "win32":
        appdata = environ.get("APPDATA")
        return Path(appdata or Path.home() / "AppData" / "Roaming") / "espanso"
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "espanso"
    return Path(environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "espanso"


def espanso_dir(runner: Runner | None = None) -> Path:
    runner = runner or run_command
    # 'espanso path config' prints just the config dir (read-only), unlike 'espanso path'.
    out = runner(["espanso", "path", "config"])
    if out and out.strip():
        return Path(out.strip())
    return default_espanso_dir()


def restart_espanso(runner: Runner | None = None) -> bool:
    """`espanso restart`, else `espanso start` (restart fails when Espanso is not running)."""
    runner = runner or run_command
    return runner(["espanso", "restart"]) is not None or runner(["espanso", "start"]) is not None


# --- Manifest -----------------------------------------------------------------------------


@dataclass
class Entry:
    """One deployed file as we last wrote it."""

    target: str
    asset_version: str
    digest: str
    launcher: str
    calls_cli: bool
    backups: list[str] = field(default_factory=list)


_ENTRY_TYPES = {
    "target": str,
    "asset_version": str,
    "digest": str,
    "launcher": str,
    "calls_cli": bool,
    "backups": list,
}


def _entry_problem(item: object) -> str | None:
    """Why a manifest entry is not one we wrote, or None. Checked field by field, since a
    path in it is later written, read or deleted."""
    if not isinstance(item, dict):
        return "an entry is not an object"
    if set(item) != set(_ENTRY_TYPES):
        return f"an entry has the fields {sorted(item)}"
    for key, kind in _ENTRY_TYPES.items():
        if not isinstance(item[key], kind):
            return f"{key} is not a {kind.__name__}"
    if not all(isinstance(b, str) for b in item["backups"]):
        return "backups is not a list of paths"
    return None


@dataclass
class Manifest:
    path: Path
    entries: dict[str, Entry] = field(default_factory=dict)  # keyed by target path

    @classmethod
    def load(cls, path: Path | None = None) -> Manifest:
        path = path or user_data_dir() / MANIFEST_NAME
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(path)
        except (OSError, ValueError) as exc:
            raise DeployError(f"The deploy manifest {path} cannot be read: {exc}") from None
        if not isinstance(raw, dict) or raw.get("format") != MANIFEST_FORMAT:
            raise DeployError(f"The deploy manifest {path} has an unknown format")
        files = raw.get("files", [])
        if not isinstance(files, list):
            raise DeployError(f"The deploy manifest {path} is damaged: files is not a list")
        entries = {}
        for item in files:
            problem = _entry_problem(item)
            if problem:
                raise DeployError(f"The deploy manifest {path} is damaged: {problem}")
            entries[item["target"]] = Entry(**item)
        return cls(path, entries)

    def save(self) -> None:
        if not self.entries:
            self.path.unlink(missing_ok=True)
            return
        files = [vars(self.entries[key]) for key in sorted(self.entries)]
        data = json.dumps({"format": MANIFEST_FORMAT, "files": files}, indent=2) + "\n"
        _write(self.path, data)


def _write(path: Path, text: str) -> None:
    """Write UTF-8 with the text's own newlines, through a new temp file in the same folder
    and an atomic rename. mkstemp creates the temp file exclusively, so a planted link there
    is never followed, and the rename replaces a link at ``path`` instead of writing through it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(text.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        return ""  # not text we wrote: never equal to ours


# --- Plan ---------------------------------------------------------------------------------


def render(source: str, launcher: str, version: str | None = None) -> str:
    """A deployed match file: the version stamp (#25), then the shipped file with the
    launcher in place of the placeholder (literally, so no character in it is special)."""
    return STAMP.format(version=version or __version__) + source.replace(PLACEHOLDER, launcher)


@dataclass
class FileStep:
    name: str
    target: Path
    state: str
    current: str | None
    rendered: str
    calls_cli: bool
    entry: Entry | None

    def diff(self) -> str:
        """A unified diff from what is on disk to what deploy would write."""
        lines = difflib.unified_diff(
            (self.current or "").splitlines(keepends=True),
            self.rendered.splitlines(keepends=True),
            fromfile=f"{self.target} (on disk)",
            tofile=f"{self.target} (prompt-workflow {__version__})",
        )
        return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


@dataclass
class Plan:
    espanso_dir: Path
    launcher: str
    steps: list[FileStep]
    legacy: Path | None  # the pre-0.9 match/base.yml we retire, if present
    manifest: Manifest

    @property
    def is_noop(self) -> bool:
        return self.legacy is None and all(
            s.state == IN_SYNC and s.entry is not None and s.entry.digest == _digest(s.rendered)
            for s in self.steps
        )

    @property
    def conflicts(self) -> list[FileStep]:
        return [s for s in self.steps if s.state in (MODIFIED, FOREIGN)]


def _released_rendering(name: str, current: str, launchers: Sequence[str]) -> bool:
    """True when ``current`` is exactly what an install script or an earlier deploy wrote from
    a source some release shipped (match_history): its stamp, if any, dropped and the
    launcher it holds put back as the placeholder. Any edit changes the digest."""
    known = KNOWN_SOURCES.get(name, frozenset())
    body = _STAMP_LINE.sub("", current, count=1)
    # Every release quoted the launcher the same way; Windows paths have forward slashes.
    candidates = {*_QUOTED_LAUNCHER.findall(body), *launchers}
    for launcher in [None, *sorted(candidates)]:
        source = body if launcher is None else body.replace(launcher, PLACEHOLDER)
        if hashlib.sha256(source.encode("utf-8")).hexdigest() in known:
            return True
    return False


def _state(
    name: str, target: Path, current: str | None, rendered: str, entry: Entry | None, launcher: str
) -> str:
    if current is None:
        return MISSING
    if target.is_symlink():
        return FOREIGN  # the user's own link (a dotfiles repo): never replaced unasked
    if current == rendered:
        return IN_SYNC
    if entry is not None and _digest(current) == entry.digest:
        return STALE
    # No entry, or one that no longer matches: still ours if it is a released rendering
    # (an older install script wrote it, or the manifest was lost).
    if _released_rendering(name, current, [launcher]):
        return STALE
    return MODIFIED if entry is not None else FOREIGN


def _legacy_base(match_dir: Path) -> Path | None:
    """Before 0.9 the -p- template shipped as match/base.yml, the file Espanso itself
    creates for the user's snippets. Only our copy is retired; any other base.yml stays."""
    legacy = match_dir / "base.yml"
    text = _read(legacy)
    if text and 'trigger: "-p-"' in text and "prompt-workflow" in text:
        return legacy
    return None


def plan(espanso: Path, launcher: str, manifest: Manifest) -> Plan:
    match_dir = espanso / "match"
    steps = []
    for name in assets.match_names():
        source = assets.read_match(name)
        target = match_dir / name
        rendered = render(source, launcher)
        current = _read(target)
        entry = manifest.entries.get(str(target))
        steps.append(
            FileStep(
                name,
                target,
                _state(name, target, current, rendered, entry, launcher),
                current,
                rendered,
                PLACEHOLDER in source,
                entry,
            )
        )
    return Plan(espanso, launcher, steps, _legacy_base(match_dir), manifest)


# --- Apply --------------------------------------------------------------------------------


@dataclass
class Outcome:
    """What apply or detach did, one line per file, for the command to print."""

    lines: list[str] = field(default_factory=list)
    changed: bool = False
    # Files left as the user has them, so not up to date: the command warns about each.
    kept: list[Path] = field(default_factory=list)

    def add(self, line: str, *, changed: bool = True) -> None:
        self.lines.append(line)
        self.changed = self.changed or changed


def _stamp() -> str:
    return time.strftime("%Y%m%d%H%M%S")


def _backup(path: Path, stamp: str) -> Path:
    """Copy ``path`` to a new `<name>.bak-<stamp>[-n]` next to it, never through a link."""
    data = path.read_bytes()
    for n in range(1000):
        backup = path.with_name(f"{path.name}.bak-{stamp}" + (f"-{n}" if n else ""))
        # Windows follows a dangling link on exclusive create, writing outside the folder.
        if backup.is_symlink():
            continue
        try:
            with backup.open("xb") as out:  # exclusive: an existing file is never replaced
                out.write(data)
        except FileExistsError:
            continue  # two deploys within one second
        return backup
    raise DeployError(f"Could not find a free backup name for {path}")


def _is_our_backup(path: Path, target: Path) -> bool:
    """A backup _backup() made of ``target``: in its folder and named exactly like one."""
    pattern = re.escape(target.name) + r"\.bak-\d{14}(-\d+)?"
    try:
        same_dir = path.parent.resolve() == target.parent.resolve()
    except OSError:
        return False
    return same_dir and bool(re.fullmatch(pattern, path.name)) and not path.is_symlink()


def _prune(backups: list[str], target: Path) -> list[str]:
    """Delete all but the newest KEEP_BACKUPS of our own backups of ``target`` (listed oldest
    first). A listed path that is not one is dropped from the list, never deleted."""
    ours = [b for b in backups if _is_our_backup(Path(b), target)]
    for old in ours[:-KEEP_BACKUPS]:
        Path(old).unlink(missing_ok=True)
    return ours[-KEEP_BACKUPS:]


def apply(the_plan: Plan, choices: Mapping[str, str] | None = None) -> Outcome:
    """Carry out a plan. ``choices`` maps a conflicting file's name to keep, ours or side;
    a conflict without a choice keeps the user's file."""
    choices = choices or {}
    outcome = Outcome()
    stamp = _stamp()
    manifest = the_plan.manifest
    match_dir = the_plan.espanso_dir / "match"

    if the_plan.legacy is not None:
        backup = _backup(the_plan.legacy, stamp)
        the_plan.legacy.unlink()
        outcome.add(f"retired legacy {the_plan.legacy} (backed up to {backup.name})")

    for step in the_plan.steps:
        # Every write lands in Espanso's match/ folder; config/ is never touched (#37).
        if step.target.parent != match_dir:
            raise DeployError(f"Refusing to write outside {match_dir}: {step.target}")
        backups = list(step.entry.backups) if step.entry else []
        if step.state in (MODIFIED, FOREIGN):
            choice = choices.get(step.name, KEEP)
            if choice not in CHOICES:
                raise DeployError(f"Unknown choice {choice!r} for {step.name}")
            if choice == KEEP:
                outcome.add(f"kept your {step.state} {step.target}", changed=False)
                outcome.kept.append(step.target)
                continue
            if choice == SIDE:
                side = step.target.with_name(step.target.name + SIDE_SUFFIX)
                _write(side, step.rendered)
                outcome.add(f"wrote ours next to your {step.target.name}: {side}", changed=False)
                outcome.kept.append(step.target)
                continue
            backups.append(str(_backup(step.target, stamp)))
            outcome.add(f"replaced {step.target} (yours backed up to {Path(backups[-1]).name})")
        elif step.state == IN_SYNC:
            if step.entry is not None and step.entry.digest == _digest(step.rendered):
                continue
            outcome.add(f"adopted {step.target} (already up to date)", changed=False)
        else:
            outcome.add(f"{'wrote' if step.state == MISSING else 'updated'} {step.target}")

        if step.state != IN_SYNC:
            _write(step.target, step.rendered)
        manifest.entries[str(step.target)] = Entry(
            target=str(step.target),
            asset_version=__version__,
            digest=_digest(step.rendered),
            launcher=the_plan.launcher,
            calls_cli=step.calls_cli,
            backups=_prune(backups, step.target),
        )
    manifest.save()
    return outcome


# --- Detach -------------------------------------------------------------------------------


def detach(manifest: Manifest, espanso: Path, *, remove_all: bool = False) -> Outcome:
    """Remove the files we own. --keep-static (the default, D-UNI-1) removes only the files
    that call the CLI, so -prompt-/-risk- stay as plain static snippets; --remove-all removes
    every owned file. A file the user edited since our last deploy is kept and reported, and
    our backups (the user's earlier versions) are never deleted here. Only entries for one of
    our match files in ``espanso``'s match/ folder are acted on; any other is reported."""
    outcome = Outcome()
    match_dir = (espanso / "match").resolve()
    names = set(assets.match_names())
    for key, entry in sorted(manifest.entries.items()):
        target = Path(entry.target)
        if target.name not in names or target.parent.resolve() != match_dir:
            outcome.add(f"left {target} alone: not one of ours in {match_dir}", changed=False)
            continue
        if target.is_symlink():
            outcome.add(f"left {target} alone: it is a link", changed=False)
            continue
        current = _read(target)
        if current is None:
            del manifest.entries[key]
            outcome.add(f"forgot {target} (already gone)", changed=False)
        elif _digest(current) != entry.digest:
            outcome.add(f"kept {target}: edited since prompt-workflow deployed it", changed=False)
        elif remove_all or entry.calls_cli:
            target.unlink()
            del manifest.entries[key]
            outcome.add(f"removed {target}")
        else:
            outcome.add(f"kept static snippets in {target}", changed=False)
    manifest.save()
    return outcome
