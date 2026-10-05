"""The `doctor` service: one report of everything a broken trigger can come from (#25, #92).

Read-only: it runs only `espanso path config`, `espanso status` and the launcher lookup
(`uv tool dir`, `brew --prefix`) through deploy.run_command, reads the settings in repair
mode, and reads the clipboard only to report its length. The report never holds a key, the
persona or clipboard text, since people paste it into bug reports. Its JSON shape is stable
(SCHEMA_VERSION): ids and keys are only ever added.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__, config, config_files, deploy
from .config import ConfigLayers, secret_names

SCHEMA_VERSION = 1
OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
_RANK = {INFO: 0, OK: 0, WARN: 1, FAIL: 2}
# Every check, in report order; each appears in every report.
CHECK_IDS = (
    "version",
    "cli",
    "install",
    "config",
    "keys",
    "espanso",
    "match_files",
    "launcher",
    "history",
    "sqlite",
    "clipboard",
    "profiles",
    "previous_install",
)
# Every key of each check's data, in every report: a check that could not run has them all
# as None, so a consumer never meets a missing key.
DATA_KEYS = {
    "version": ("version", "python", "platform"),
    "cli": ("path", "executable"),
    "install": ("channel", "launcher", "editable"),
    "config": ("mode", "files", "valid", "findings"),
    "keys": ("keys", "provider"),
    "espanso": (
        "found",
        "config_dir",
        "running",
        "query_failed",
        "exit_code",
        "timed_out",
        "error",
    ),
    "match_files": ("espanso_dir", "files", "legacy"),
    "launcher": ("current", "deployed", "drift", "missing", "orphans"),
    "history": (
        "enabled",
        "path",
        "exists",
        "schema_version",
        "operations",
        "attempts",
        "lost_writes",
        "last_lost_utc",
        "tracking_incomplete",
        "writable",
        "error",
    ),
    "sqlite": ("version", "wal_reset_bug"),
    "clipboard": ("read", "length", "concealed", "error"),
    "profiles": ("profile", "pro_profile", "user"),
    "previous_install": ("gated", "roots", "signals", "env_file", "retire_pending", "shadow"),
}
# The keys each check needs, by provider; -i-, -ip-, -if- and -iok- always use OpenRouter.
_PROVIDER_KEYS = {"openrouter": "OPENROUTER_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
# WAL could rarely reset a database before SQLite 3.51.3 (history.Health).
_SQLITE_FIXED = (3, 51, 3)


@dataclass(frozen=True)
class Check:
    id: str
    status: str
    message: str
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    checks: tuple[Check, ...]

    @property
    def status(self) -> str:
        return max((c.status for c in self.checks), key=_RANK.__getitem__, default=OK)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "version": __version__,
            "status": self.status,
            "checks": {
                c.id: {
                    "status": c.status,
                    "message": c.message,
                    "data": {k: c.data.get(k) for k in DATA_KEYS[c.id]},
                }
                for c in self.checks
            },
        }


def _version_check() -> Check:
    data = {
        "version": __version__,
        "python": platform.python_version(),
        "platform": sys.platform,
    }
    return Check("version", INFO, f"prompt-workflow {__version__}", data)


def _cli_check() -> Check:
    script = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    data = {"path": str(script) if script else None, "executable": sys.executable}
    return Check("cli", INFO, f"running {script or sys.executable}", data)


def _install_check(launcher: deploy.Launcher | None, error: str | None) -> Check:
    editable = (config._PROJECT_ROOT / "pyproject.toml").is_file()
    channel = launcher.channel if launcher else ("editable" if editable else "unknown")
    data = {
        "channel": channel,
        "launcher": str(launcher.path) if launcher else None,
        "editable": editable,
    }
    if launcher is None:
        return Check(
            "install", WARN, f"no stable launcher found ({error}); best guess: {channel}", data
        )
    note = ", editable checkout" if editable else ""
    return Check("install", OK, f"{channel}{note}: {launcher.path}", data)


def _config_check(layers: ConfigLayers, strict_error: str | None) -> Check:
    legacy = os.environ.get("PROMPT_WORKFLOW_ENV")
    files = [
        layer.source.removeprefix("file:")
        for layer in layers.layers
        if layer.source.startswith("file:")
    ]
    saved = config.settings_file()
    if legacy:
        mode = "legacy"
    elif config_files.is_file(saved):
        mode = "saved"
    elif any(Path(f).name == ".env" for f in files):
        mode = "env"
    else:
        mode = "defaults"
    findings = [{"source": f.source, "message": f.message} for f in layers.findings]
    data = {"mode": mode, "files": files, "valid": strict_error is None, "findings": findings}
    if strict_error is not None:
        return Check("config", FAIL, f"improve would stop with: {strict_error}", data)
    if findings:
        return Check("config", WARN, f"{len(findings)} finding(s); run `config validate`", data)
    where = ", ".join(files) or "built-in defaults"
    return Check("config", OK, f"valid ({mode}: {where})", data)


def _keys_check(layers: ConfigLayers, provider: str, local_only: bool) -> Check:
    keys = {}
    for name in secret_names():
        entry = layers.entries[name]
        keys[name] = {"set": bool(entry.value), "source": entry.source if entry.value else None}
    data = {"keys": keys, "provider": provider}
    needed = _PROVIDER_KEYS.get(provider)
    if needed and not keys[needed]["set"]:
        return Check("keys", FAIL, f"PROMPT_PROVIDER is {provider} but {needed} is not set", data)
    if not local_only and not keys["OPENROUTER_API_KEY"]["set"]:
        return Check(
            "keys",
            WARN,
            "OPENROUTER_API_KEY is not set: -i-, -ip-, -if- and -iok- will fail",
            data,
        )
    shown = ", ".join(f"{n}: {'set' if k['set'] else 'not set'}" for n, k in keys.items())
    return Check("keys", OK, shown, data)


def _espanso_check(runner: deploy.Runner, espanso_dir: Path | None) -> tuple[Check, Path]:
    answer = runner(deploy.PATH_CONFIG)
    out, why = deploy.output(answer), deploy.failure(answer)
    located = Path(out.strip()) if out and out.strip() else None
    # Found means on PATH: an installed Espanso that cannot answer is not a PATH problem (#115).
    found = why is None or why.found
    running = None
    if found:
        # `espanso status` exits non-zero when Espanso is not running (no output then).
        status = deploy.output(runner(["espanso", "status"]))
        running = status is not None and "not running" not in status.lower()
    target = espanso_dir or located or deploy.default_espanso_dir()
    data = {
        "found": found,
        "config_dir": str(target),
        "running": running,
        "query_failed": why is not None and why.found,
        "exit_code": why.returncode if why else None,
        "timed_out": why is not None and why.timed_out,
        "error": why.error if why else None,
    }
    if why is not None and not why.found:
        return Check("espanso", WARN, "espanso was not found on PATH", data), target
    if why is not None:
        where = f" at {why.path}" if why.path else ""
        hint = "" if why.timed_out else "; start Espanso once (`espanso start`) or check its config"
        message = f"espanso found{where}, but {why.describe(deploy.PATH_CONFIG)}{hint}"
        if espanso_dir is None:
            message += f" (using the default folder {target})"
        return Check("espanso", WARN, message, data), target
    if not running:
        return Check("espanso", WARN, f"Espanso is not running (config: {target})", data), target
    return Check("espanso", OK, f"running (config: {target})", data), target


def _match_check(target: Path, launcher: str | None, manifest: deploy.Manifest | None) -> Check:
    data: dict[str, Any] = {"espanso_dir": str(target), "files": [], "legacy": False}
    if launcher is None or manifest is None:
        why = "the deploy manifest cannot be read" if manifest is None else "no launcher found"
        return Check("match_files", WARN, f"cannot compare: {why} (pass --launcher)", data)
    if not (target / "match").is_dir():
        return Check("match_files", WARN, f"no Espanso match folder in {target}", data)
    the_plan = deploy.plan(target, deploy.launcher_text(launcher), manifest)
    data["files"] = [{"name": s.name, "state": s.state} for s in the_plan.steps]
    data["legacy"] = the_plan.legacy is not None
    broken = [s for s in the_plan.steps if s.state in (deploy.STALE, deploy.MISSING)]
    edited = [s for s in the_plan.steps if s.state in (deploy.MODIFIED, deploy.FOREIGN)]
    if broken:
        names = ", ".join(f"{s.name}: {s.state}" for s in broken)
        return Check("match_files", FAIL, f"{names}; run `prompt-workflow espanso deploy`", data)
    if edited or the_plan.legacy is not None:
        return Check("match_files", WARN, "some files are not ours as deployed", data)
    return Check("match_files", OK, "every match file is in sync", data)


def _launcher_check(current: str | None, manifest: deploy.Manifest | None) -> Check:
    # An entry whose file is gone (a deleted folder, another Espanso config folder) calls
    # nothing, so only live entries are judged; the next deploy forgets the others.
    entries = list(manifest.entries.values()) if manifest else []
    orphans = sorted(e.target for e in entries if deploy.is_gone(e))
    deployed = sorted({e.launcher for e in entries if not deploy.is_gone(e)})
    missing = [p for p in deployed if not Path(p).is_file()]
    drift = bool(current) and any(p != current for p in deployed)
    data = {
        "current": current,
        "deployed": deployed,
        "drift": drift,
        "missing": missing,
        "orphans": orphans,
    }
    note = (
        f"; {len(orphans)} manifest entries for files that no longer exist "
        "(`prompt-workflow espanso deploy` forgets them)"
        if orphans
        else ""
    )
    if manifest is None:
        return Check("launcher", WARN, "the deploy manifest cannot be read", data)
    if not deployed:
        return Check("launcher", INFO, f"nothing deployed by prompt-workflow on record{note}", data)
    if missing:
        return Check(
            "launcher",
            FAIL,
            f"the deployed matches call {', '.join(missing)}, which is gone{note}",
            data,
        )
    if drift:
        return Check(
            "launcher",
            WARN,
            f"the deployed matches call {', '.join(deployed)}, this install is {current}{note}",
            data,
        )
    return Check("launcher", OK, f"the deployed matches call {', '.join(deployed)}{note}", data)


def _history_checks(settings: config.Settings) -> tuple[Check, Check]:
    from .history import HistoryStore

    health = HistoryStore.from_settings(settings).health()
    data = {"enabled": settings.history, **vars(health)}
    data.pop("sqlite_version")
    if not settings.history:
        history = Check("history", INFO, "off (PROMPT_HISTORY=false)", data)
    elif health.error or health.tracking_incomplete:
        reason = health.error or (
            f"{health.lost_writes} write(s) lost" if health.lost_writes else "not writable"
        )
        history = Check("history", WARN, f"tracking incomplete: {reason} ({health.path})", data)
    else:
        count = health.operations if health.operations is not None else 0
        history = Check("history", OK, f"{count} call(s) recorded in {health.path}", data)
    version = health.sqlite_version
    try:
        parts = tuple(int(p) for p in version.split("."))
    except ValueError:
        parts = ()
    old = bool(parts) and parts < _SQLITE_FIXED
    sqlite_data = {"version": version, "wal_reset_bug": old}
    if not parts:
        sqlite = Check("sqlite", WARN, f"SQLite version {version}", sqlite_data)
    elif old:
        sqlite = Check(
            "sqlite",
            WARN,
            f"SQLite {version}: before 3.51.3, WAL can rarely reset a database",
            sqlite_data,
        )
    else:
        sqlite = Check("sqlite", OK, f"SQLite {version}", sqlite_data)
    return history, sqlite


def _clipboard_check(read: bool) -> Check:
    data: dict[str, Any] = {"read": False, "length": None, "concealed": None, "error": None}
    if not read:
        return Check("clipboard", INFO, "skipped (--no-clipboard)", data)
    import pyperclip

    from .clipboard_guard import is_concealed

    concealed = is_concealed()
    data["concealed"] = concealed
    if concealed:
        return Check("clipboard", OK, "holds a password-manager item; not read", data)
    try:
        length = len(str(pyperclip.paste()))
    except pyperclip.PyperclipException as exc:
        data["error"] = type(exc).__name__
        return Check("clipboard", WARN, f"cannot be read ({type(exc).__name__})", data)
    data.update(read=True, length=length)
    return Check("clipboard", OK, f"readable: {length} character(s) (content not shown)", data)


def _profiles_check(settings: config.Settings) -> Check:
    from .prompt_builder import ADDED, OVERRIDES, system_prompt, user_profiles

    found = user_profiles(settings.profile_overrides)
    data: dict[str, Any] = {
        "profile": settings.profile,
        "pro_profile": settings.pro_profile,
        "user": [{"name": p.name, "status": p.status} for p in found],
    }
    for name in dict.fromkeys(filter(None, (settings.profile, settings.pro_profile))):
        try:
            system_prompt(name, "", settings.profile_overrides)
        except ValueError as exc:
            return Check("profiles", FAIL, str(exc), data)
    odd = [f"{p.name}: {p.status}" for p in found if p.status not in (ADDED, OVERRIDES)]
    if odd:
        return Check("profiles", WARN, "; ".join(odd), data)
    return Check("profiles", OK, f"PROMPT_PROFILE {settings.profile} resolves", data)


def _previous_install_check(
    runner: deploy.Runner, espanso_dir: Path, launcher: str | None
) -> Check:
    from . import previous_install

    try:
        found = previous_install.detect(
            runner=runner,
            espanso_dir=espanso_dir,
            launcher=Path(launcher) if launcher else None,
            look_up_launcher=False,  # run() already looked: ``launcher`` is its answer
        )
    except Exception as exc:
        # Information only: a folder it cannot look at never fails the report.
        message = f"could not look for a previous install: {type(exc).__name__}"
        return Check("previous_install", WARN, message)
    first = found.candidates[0] if found.candidates else None
    pending = found.pending
    retire = pending is not None and not pending.launchers_in_root
    data = {
        "gated": found.gated,
        "roots": [str(c.root) for c in found.candidates],
        "signals": sorted(first.signals) if first else [],
        "env_file": str(first.env_file) if first and first.env_file else None,
        "retire_pending": str(pending.env_file) if pending and retire else None,
        "shadow": found.shadow.path if found.shadow else None,
    }
    found_at = (
        f"a previous install was found at {', '.join(str(c.root) for c in found.candidates)} "
        "and is not migrated"
    )
    notes = [found_at] if first else []
    if pending and retire:
        notes.append(
            f"the old .env at {pending.env_file} is still in place "
            f"(`prompt-workflow config retire --from {pending.root}`)"
        )
    elif pending:
        notes.append(
            f"the settings of {pending.root} were copied, but the match files still run "
            f"{', '.join(pending.launchers_in_root)} (`prompt-workflow espanso deploy`)"
        )
    if found.shadow:
        notes.append(
            f"`prompt-workflow` on PATH is {found.shadow.path}, not the installed launcher "
            f"{found.shadow.launcher}: {found.shadow.hint}"
        )
    if found.shadow or (pending and retire):
        return Check("previous_install", WARN, "; ".join(notes), data)
    if notes:
        return Check("previous_install", INFO, "; ".join(notes), data)
    if found.gated:
        return Check("previous_install", OK, "settings are in place; nothing to look for", data)
    return Check("previous_install", OK, "no previous install found", data)


def _safely(check_id: str, build: Callable[[], Check]) -> Check:
    """One check that fails on its own never stops the report."""
    try:
        return build()
    except Exception as exc:
        return Check(check_id, FAIL, f"check failed: {type(exc).__name__}: {exc}")


def run(
    *,
    espanso_dir: Path | None = None,
    launcher: str | None = None,
    clipboard: bool = True,
    runner: deploy.Runner | None = None,
) -> Report:
    """Every check in CHECK_IDS. ``launcher`` and ``espanso_dir`` override the lookups."""
    run_command = runner or deploy.run_command
    layers = ConfigLayers.resolve(strict=False)
    settings = layers.settings()
    try:
        ConfigLayers.resolve().settings()
        strict_error = None
    except ValueError as exc:
        strict_error = str(exc)

    found: deploy.Launcher | None = None
    launcher_error = None
    try:
        found = deploy.resolve_launcher(runner=run_command)
    except deploy.DeployError as exc:
        launcher_error = str(exc)
    current = launcher or (str(found.path) if found else None)
    try:
        manifest: deploy.Manifest | None = deploy.Manifest.load()
    except deploy.DeployError:
        manifest = None
    compare = current
    if compare is None and manifest and manifest.entries:
        # Compare with what was deployed, so a missing launcher still shows each file's state;
        # an entry whose file is gone says nothing about the files there now.
        live = [e for e in manifest.entries.values() if not deploy.is_gone(e)]
        compare = min(e.launcher for e in live or manifest.entries.values())

    checks: list[Check] = [
        _version_check(),
        _safely("cli", _cli_check),
        _safely("install", lambda: _install_check(found, launcher_error)),
        _safely("config", lambda: _config_check(layers, strict_error)),
        _safely("keys", lambda: _keys_check(layers, settings.provider, settings.local_only)),
    ]
    try:
        espanso, target = _espanso_check(run_command, espanso_dir)
    except Exception as exc:
        espanso = Check("espanso", FAIL, f"check failed: {type(exc).__name__}")
        target = espanso_dir or deploy.default_espanso_dir()
    checks.append(espanso)
    checks.append(_safely("match_files", lambda: _match_check(target, compare, manifest)))
    checks.append(_safely("launcher", lambda: _launcher_check(current, manifest)))
    try:
        checks.extend(_history_checks(settings))
    except Exception as exc:
        checks.append(Check("history", FAIL, f"check failed: {type(exc).__name__}"))
        checks.append(Check("sqlite", WARN, "unknown"))
    checks.append(_safely("clipboard", lambda: _clipboard_check(clipboard)))
    checks.append(_safely("profiles", lambda: _profiles_check(settings)))
    checks.append(
        _safely("previous_install", lambda: _previous_install_check(run_command, target, current))
    )
    return Report(tuple(checks))


# Modules the trigger path must never load (tests/test_trigger_contract.py freezes the same).
HEAVY_MODULES = (
    "textual",
    "tomli_w",
    "tomlkit",
    "keyring",
    "prompt_workflow.deploy",
    "prompt_workflow.commands",
    "prompt_workflow.doctor",
    "prompt_workflow.config_store",
    "prompt_workflow.previous_install",
)
IMPORT_TIMEOUT = 30
_IMPORT_PROBE = """
import json, sys, time
started = set(sys.modules)
clock = time.perf_counter()
import prompt_workflow.cli
seconds = time.perf_counter() - clock
heavy = sorted(m for m in sys.modules if m.split(".")[0] in HEAVY or m.startswith(PREFIXES))
print(json.dumps({"seconds": seconds, "modules": len(set(sys.modules) - started), "heavy": heavy}))
"""


@dataclass(frozen=True)
class ImportCheck:
    ok: bool
    message: str
    seconds: float | None = None
    modules: int | None = None
    heavy: tuple[str, ...] = ()


def import_check(timeout: float = IMPORT_TIMEOUT) -> ImportCheck:
    """Import the CLI module the way a trigger starts, in a fresh interpreter, and report how
    long it took, how many modules it loaded and any of HEAVY_MODULES among them. Runs no
    command and reads no setting."""
    top = [m for m in HEAVY_MODULES if "." not in m]
    prefixes = tuple(m for m in HEAVY_MODULES if "." in m)
    probe = f"HEAVY = {top!r}\nPREFIXES = {prefixes!r}\n{_IMPORT_PROBE}"
    try:
        proc = subprocess.run(  # noqa: S603 - this interpreter, a fixed script, no shell
            # -P: the working directory is not put on sys.path, so a module planted there
            # (a json.py) never runs.
            [sys.executable, "-P", "-c", probe],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        data = json.loads(proc.stdout.decode("utf-8"))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return ImportCheck(False, f"the import check could not run ({type(exc).__name__})")
    heavy = tuple(data["heavy"])
    seconds, modules = float(data["seconds"]), int(data["modules"])
    summary = f"{seconds * 1000:.0f} ms, {modules} module(s)"
    if heavy:
        return ImportCheck(False, f"{summary}; loads {', '.join(heavy)}", seconds, modules, heavy)
    return ImportCheck(True, f"{summary}; no heavy module", seconds, modules)
