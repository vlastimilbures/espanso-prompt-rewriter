"""Find an earlier checkout (editable) install whose settings this install does not see (#110).

A wheel or channel install reads no checkout `.env`, so after a switch the settings look gone.
Detection only looks where the old install left a trace: the launcher in the deployed match
files, the deploy manifest, the uv tool receipt, and a path the user typed. It never guesses and
never scans the disk, and it runs no command `doctor` does not run already. A `.env` is only
checked for existence here, never read.

Used by the management commands (setup, doctor, the interface); never on the trigger path.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

from . import config, config_files, deploy
from .profiles import PROMPTS_PATH

PROJECT_NAME = "espanso-prompt-rewriter"
SKIP_FILE = "previous-install.json"
SKIP_VERSION = 1

# Candidate.signals values, strongest first.
LAUNCHER, MANIFEST, RECEIPT, ENTERED = "launcher", "manifest", "receipt", "entered"
SIGNALS = (LAUNCHER, MANIFEST, RECEIPT, ENTERED)

# Detection.gated values: why nothing is looked for (D-MIG-4).
LEGACY, SAVED, SECRETS = "legacy", "saved", "secrets"


@dataclass(frozen=True)
class Candidate:
    """A checkout of this project that an earlier install ran from."""

    root: Path
    signals: frozenset[str]
    env_file: Path | None  # the checkout's .env, when one exists (never read here)
    launchers_in_root: tuple[str, ...]  # deployed launchers that still point inside it
    profiles_dir: Path | None


@dataclass(frozen=True)
class Shadow:
    """The `prompt-workflow` the shell finds first is not this install's launcher."""

    path: str
    launcher: str
    hint: str


@dataclass(frozen=True)
class Detection:
    candidates: tuple[Candidate, ...] = ()
    gated: str | None = None
    shadow: Shadow | None = None


def gate(environ: Mapping[str, str]) -> str | None:
    """Why detection does not run, or None: legacy mode keeps its own .env, and once
    config.toml or the secret store exists the user's settings are no longer missing. A deploy
    manifest does not count: an editable install had one before the switch."""
    if environ.get("PROMPT_WORKFLOW_ENV"):
        return LEGACY
    if config_files.is_file(config.settings_file(environ)):
        return SAVED
    # The file store's path; a keyring store (config_files.secret_store's TODO) needs its own test.
    if config_files.is_file(config._user_config_dir(environ) / config_files.SECRETS_FILE):
        return SECRETS
    return None


def checkout_root_of(launcher: str) -> PurePath | None:
    """The checkout a launcher belongs to: only `<root>/.venv/bin/prompt-workflow` or
    `<root>\\.venv\\Scripts\\prompt-workflow.exe` (deployed with forward slashes), never a walk
    upward, so a uv, Homebrew or Scoop launcher gives none."""
    windows = PureWindowsPath(launcher)
    if windows.drive:
        tail = tuple(p.lower() for p in windows.parts[-3:])
        if tail == (".venv", "scripts", "prompt-workflow.exe"):
            return windows.parents[2]
        return None
    posix = PurePosixPath(launcher)
    if posix.is_absolute() and posix.parts[-3:] == (".venv", "bin", "prompt-workflow"):
        return posix.parents[2]
    return None


def _project_name(root: Path) -> str | None:
    import tomllib

    try:
        table = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    project = table.get("project")
    name = project.get("name") if isinstance(project, dict) else None
    return name if isinstance(name, str) else None


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path.absolute()


def is_checkout(root: Path) -> bool:
    """``root`` holds a pyproject.toml naming this project."""
    return config_files.is_file(root / "pyproject.toml") and _project_name(root) == PROJECT_NAME


def _is_running_checkout(root: Path) -> bool:
    # An editable install that reads its own .env is `config migrate`'s case (#84).
    return _resolved(root) == _resolved(config._PROJECT_ROOT)


def receipt_root(runner: deploy.Runner) -> Path | None:
    """The source path uv recorded for an editable (or directory) tool install, from
    `<uv tool dir>/espanso-prompt-rewriter/uv-receipt.toml`. A wheel URL gives none, and so
    does a receipt that `uv tool install --force` of a wheel has overwritten."""
    import tomllib

    tool_dir = deploy.output(runner(["uv", "tool", "dir"]))
    if not tool_dir or not tool_dir.strip():
        return None
    receipt = Path(tool_dir.strip()) / PROJECT_NAME / "uv-receipt.toml"
    try:
        table = tomllib.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    tool = table.get("tool")
    requirements = tool.get("requirements") if isinstance(tool, dict) else None
    for item in requirements if isinstance(requirements, list) else []:
        if not isinstance(item, dict) or item.get("name") != PROJECT_NAME:
            continue
        for key in ("editable", "directory"):
            source = item.get(key)
            if isinstance(source, str) and Path(source).is_absolute():
                return Path(source)
    return None


# --- Skip marker (D-MIG-3) ----------------------------------------------------------------


def skip_file(environ: Mapping[str, str] | None = None) -> Path:
    return config.user_data_dir(os.environ if environ is None else environ) / SKIP_FILE


def skipped_roots(environ: Mapping[str, str] | None = None) -> set[str]:
    """The roots the user chose to skip. A missing or damaged marker counts as empty, so the
    offer comes back rather than any command failing."""
    try:
        raw = json.loads(skip_file(environ).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    items = raw.get("skipped") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return set()
    return {i["root"] for i in items if isinstance(i, dict) and isinstance(i.get("root"), str)}


def skip(root: Path, environ: Mapping[str, str] | None = None) -> None:
    """Remember that the user skipped ``root``; a different checkout found later is offered."""
    roots = skipped_roots(environ) | {str(root)}
    at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {"version": SKIP_VERSION, "skipped": [{"root": r, "at": at} for r in sorted(roots)]}
    path = skip_file(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, indent=2) + "\n").encode("utf-8")
    config_files.write_atomic(path, data, private=False)


# --- Shadowed CLI -------------------------------------------------------------------------


def _inside(path: Path, parent: Path) -> bool:
    return _resolved(path).is_relative_to(_resolved(parent))


def shadow(environ: Mapping[str, str], runner: deploy.Runner) -> Shadow | None:
    """The `prompt-workflow` first on PATH, when it is not this install's launcher: the shell
    (and anything started from it) then runs another CLI with other settings."""
    found = shutil.which("prompt-workflow", path=environ.get("PATH"))
    if not found:
        return None
    try:
        launcher = deploy.resolve_launcher(runner=runner).path
    except deploy.DeployError:
        return None
    try:
        if os.path.samefile(found, launcher):
            return None
    except OSError:
        pass
    venv = environ.get("VIRTUAL_ENV")
    if venv and _inside(Path(found), Path(venv)):
        hint = "a virtual environment is active: run `deactivate` (or `env -u VIRTUAL_ENV`)"
    elif ".venv" in Path(found).parts:
        hint = f"remove {Path(found).parent} from PATH"
    else:
        hint = f"uninstall the old CLI, or put {launcher.parent} earlier on PATH"
    return Shadow(found, str(launcher), hint)


# --- Detection ----------------------------------------------------------------------------


def _manifest_launchers() -> set[str]:
    try:
        manifest = deploy.Manifest.load()
    except deploy.DeployError:
        return set()
    return {e.launcher for e in manifest.entries.values() if not deploy.is_gone(e)}


def detect(
    environ: Mapping[str, str] | None = None,
    *,
    runner: deploy.Runner | None = None,
    espanso_dir: Path | None = None,
    entered: Path | None = None,
) -> Detection:
    """Every earlier checkout install the signals name, unless the gate is closed. A root the
    user skipped is left out unless they entered it. Reads files; runs only `espanso path
    config` (when ``espanso_dir`` is not given) and `uv tool dir`, through ``runner``."""
    env = os.environ if environ is None else environ
    run = runner or deploy.run_command
    found_shadow = shadow(env, run)
    why = gate(env)
    if why:
        return Detection(gated=why, shadow=found_shadow)

    espanso = espanso_dir or deploy.espanso_dir(run)
    in_files = deploy.deployed_launchers(espanso)
    in_manifest = _manifest_launchers()
    launchers = in_files | in_manifest
    signals: dict[Path, set[str]] = {}

    def add(root: PurePath | Path | None, signal: str) -> None:
        if root is not None:
            signals.setdefault(_resolved(Path(root)), set()).add(signal)

    for launcher in sorted(in_files):
        add(checkout_root_of(launcher), LAUNCHER)
    for launcher in sorted(in_manifest):
        add(checkout_root_of(launcher), MANIFEST)
    add(receipt_root(run), RECEIPT)
    if entered is not None:
        add(entered.expanduser(), ENTERED)

    skipped = skipped_roots(env)
    candidates = []
    for root, kinds in sorted(signals.items()):
        if not is_checkout(root) or _is_running_checkout(root):
            continue
        if str(root) in skipped and ENTERED not in kinds:
            continue
        inside = tuple(
            sorted(
                launcher
                for launcher in launchers
                if (owner := checkout_root_of(launcher)) is not None
                and _resolved(Path(owner)) == root
            )
        )
        env_file = root / ".env"
        profiles = root / PROMPTS_PATH
        candidates.append(
            Candidate(
                root=root,
                signals=frozenset(kinds),
                env_file=env_file if config_files.is_file(env_file) else None,
                launchers_in_root=inside,
                profiles_dir=profiles if profiles.is_dir() else None,
            )
        )
    return Detection(candidates=tuple(candidates), shadow=found_shadow)
