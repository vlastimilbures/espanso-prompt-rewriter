"""The management commands (#92): setup, config, secrets, profiles, stats, history, doctor and
--version. They run offline: Espanso, uv and brew through a fake runner, the clipboard mocked,
and setup's smoke test against its own stub on 127.0.0.1."""

from __future__ import annotations

import io
import json
import re
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prompt_workflow import __version__, assets, config, deploy, doctor, history, smoke
from prompt_workflow.cli import app
from prompt_workflow.commands import common
from prompt_workflow.config import secret_names
from prompt_workflow.history import HistoryStore

runner = CliRunner()
LAUNCHER = "/Users/me/.local/bin/prompt-workflow"
# Built at runtime, so no key-shaped literal lands in the repo (gitleaks).
KEY = "sk-or-v1-" + "ab12" * 16
PERSONA = "I am the sentinel persona of Example Corp."
ALLOWED_EXITS = {0, 1, 2, 3, 4}


class FakeRunner:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.answers.get(" ".join(argv))


@pytest.fixture
def fake_run(monkeypatch):
    fake = FakeRunner({"espanso restart": ""})
    monkeypatch.setattr(deploy, "run_command", fake)
    return fake


@pytest.fixture
def saved(monkeypatch):
    """Saved mode: no PROMPT_WORKFLOW_ENV, so config.toml and the secret store are read (in
    conftest's per-test config dir)."""
    monkeypatch.delenv("PROMPT_WORKFLOW_ENV")
    return config._user_config_dir()


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(common, "stdin_is_tty", lambda: True)


@pytest.fixture
def clipboard(monkeypatch):
    import pyperclip

    from prompt_workflow import clipboard_guard

    text = "clipboard sentinel text 42"
    monkeypatch.setattr(pyperclip, "paste", lambda: text)
    monkeypatch.setattr(clipboard_guard, "is_concealed", lambda: None)
    return text


@pytest.fixture
def espanso(tmp_path):
    root = tmp_path / "espanso"
    (root / "match").mkdir(parents=True)
    return root


def _run(*args, input=None):
    return runner.invoke(app, list(args), input=input)


def _tree(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


# --- --version, help, lazy loading --------------------------------------------------------


def test_version():
    result = _run("--version")
    assert result.exit_code == 0
    assert result.stdout == f"{__version__}\n"


def test_help_lists_every_command():
    result = _run("--help")
    assert result.exit_code == 0
    for name in (
        "improve",
        "persona",
        "espanso",
        "setup",
        "config",
        "secrets",
        "profiles",
        "stats",
        "history",
        "doctor",
    ):
        assert name in result.stdout


# --- exit codes, NO_COLOR -----------------------------------------------------------------


def test_unknown_setting_is_a_usage_error():
    result = _run("config", "get", "NOPE")
    assert result.exit_code == 2


def test_no_terminal_never_prompts(saved):
    config_store_secret(saved)
    result = _run("secrets", "remove", "OPENROUTER_API_KEY")
    assert result.exit_code == common.NEEDS_TERMINAL
    assert "not a terminal" in result.stderr
    assert "OPENROUTER_API_KEY" in (saved / "secrets.toml").read_text("utf-8")


@pytest.mark.parametrize(
    ("no_color", "tty_out", "colored"),
    [
        ("", True, True),
        ("1", True, False),
        ("", False, False),
    ],
)
def test_no_color(monkeypatch, no_color, tty_out, colored):
    monkeypatch.setenv("NO_COLOR", no_color)
    monkeypatch.setattr(common, "stdout_is_tty", lambda: tty_out)
    assert ("\x1b[" in common.paint("ok", "green")) is colored


# --- config -------------------------------------------------------------------------------


def config_store_secret(directory: Path, value: str = KEY) -> None:
    from prompt_workflow import config_store

    config_store.save_secret("OPENROUTER_API_KEY", value)
    assert (directory / "secrets.toml").is_file()


def test_config_show_provenance_and_masking(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        f"PROMPT_PROFILE=general\nPROMPT_PERSONA={PERSONA}\nOPENROUTER_API_KEY={KEY}\n", "utf-8"
    )
    monkeypatch.setenv("PROMPT_PROFILE", "default")
    result = _run("config", "show", "--raw")
    assert result.exit_code == 0, result.output
    line = next(x for x in result.stdout.splitlines() if x.startswith("PROMPT_PROFILE "))
    assert "[environment; overrides" in line
    assert str(tmp_path / ".env") in line
    assert KEY not in result.output
    assert PERSONA not in result.output
    assert f"<set, {len(KEY)} chars>" in result.stdout


def test_config_get_never_prints_a_secret(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    result = _run("config", "get", "OPENROUTER_API_KEY")
    assert result.exit_code == 1
    assert KEY not in result.output
    monkeypatch.setenv("PROMPT_PROFILE", "general")
    assert _run("config", "get", "PROMPT_PROFILE").stdout == "general\n"


def test_config_set_and_unset(saved):
    result = _run("config", "set", "PROMPT_PROFILE", "general")
    assert result.exit_code == 0, result.output
    assert 'PROMPT_PROFILE = "general"' in (saved / "config.toml").read_text("utf-8")
    assert _run("config", "get", "PROMPT_PROFILE").stdout == "general\n"
    assert _run("config", "unset", "PROMPT_PROFILE").exit_code == 0
    assert "PROMPT_PROFILE" not in (saved / "config.toml").read_text("utf-8")


def test_config_set_validates(saved):
    result = _run("config", "set", "PROMPT_LOCAL_ONLY", "maybe")
    assert result.exit_code == 1
    assert "true or false" in result.stderr
    assert not (saved / "config.toml").exists()


def test_config_set_refuses_a_secret_and_a_key_shaped_value(saved):
    for name in secret_names():
        result = _run("config", "set", name, KEY)
        assert result.exit_code == 2
        assert KEY not in result.output
    result = _run("config", "set", "PROMPT_PERSONA", KEY)
    assert result.exit_code == 2
    assert KEY not in result.output
    assert not (saved / "config.toml").exists()


def test_config_set_refuses_in_legacy_mode(tmp_path):
    result = _run("config", "set", "PROMPT_PROFILE", "general")
    assert result.exit_code == 1
    assert "PROMPT_WORKFLOW_ENV is set" in result.stderr
    assert not config.settings_file().exists()


def test_config_set_refuses_to_orphan_a_dotenv(saved):
    saved.mkdir(parents=True)
    (saved / ".env").write_text("PROMPT_PROFILE=general\n", "utf-8")
    result = _run("config", "set", "PROMPT_TIMEOUT_SECONDS", "10")
    assert result.exit_code == 1
    assert "config migrate" in result.stderr
    assert not (saved / "config.toml").exists()


def test_config_validate(monkeypatch):
    assert _run("config", "validate").exit_code == 0
    monkeypatch.setenv("PROMPT_LOCAL_ONLY", "maybe")
    result = _run("config", "validate")
    assert result.exit_code == common.PROBLEMS
    assert "PROMPT_LOCAL_ONLY must be true or false" in result.stdout


def test_config_validate_unknown_profile(monkeypatch):
    monkeypatch.setenv("PROMPT_PROFILE", "nosuch")
    result = _run("config", "validate")
    assert result.exit_code == common.PROBLEMS
    assert "Unknown profile" in result.stdout


def _dotenv(saved: Path) -> Path:
    saved.mkdir(parents=True, exist_ok=True)
    env = saved / ".env"
    env.write_text(f"PROMPT_PROFILE=general\nOPENROUTER_API_KEY={KEY}\n", "utf-8")
    return env


def test_config_migrate_needs_consent(saved):
    env = _dotenv(saved)
    result = _run("config", "migrate")
    assert result.exit_code == common.NEEDS_TERMINAL
    assert "Preview token:" in result.stdout
    assert KEY not in result.output
    assert _run("config", "migrate", "--dry-run").exit_code == 0
    assert _run("config", "migrate", "--yes").exit_code == 2
    assert _run("config", "migrate", "--yes", "--preview-token", "wrong").exit_code == 2
    assert env.is_file()
    assert not (saved / "config.toml").exists()


def test_config_migrate_and_rollback_with_the_token(saved):
    env = _dotenv(saved)
    preview = _run("config", "migrate", "--dry-run").stdout
    token = preview.split("Preview token: ")[1].split()[0]
    result = _run("config", "migrate", "--yes", "--preview-token", token)
    assert result.exit_code == 0, result.output
    assert not env.exists()
    assert 'PROMPT_PROFILE = "general"' in (saved / "config.toml").read_text("utf-8")
    preview = _run("config", "rollback", "--dry-run").stdout
    token = preview.split("Preview token: ")[1].split()[0]
    result = _run("config", "rollback", "--yes", "--preview-token", token)
    assert result.exit_code == 0, result.output
    assert env.is_file()
    assert not (saved / "config.toml").exists()


def test_config_migrate_interactive(saved, tty):
    env = _dotenv(saved)
    assert _run("config", "migrate", input="n\n").exit_code == 1
    assert env.is_file()
    assert _run("config", "migrate", input="y\n").exit_code == 0
    assert not env.exists()


# --- secrets ------------------------------------------------------------------------------


def test_secrets_set_from_stdin_status_and_remove(saved):
    result = _run("secrets", "set", "OPENROUTER_API_KEY", "--stdin", input=f"{KEY}\n")
    assert result.exit_code == 0, result.output
    assert KEY not in result.output
    status = _run("secrets", "status")
    assert "OPENROUTER_API_KEY  set  from" in status.stdout
    assert "ANTHROPIC_API_KEY   not set" in status.stdout
    assert KEY not in status.output
    assert _run("secrets", "remove", "OPENROUTER_API_KEY", "--yes").exit_code == 0
    assert "not set" in _run("secrets", "status").stdout.splitlines()[-1]


def test_secrets_set_hidden_prompt(saved, tty, monkeypatch):
    monkeypatch.setattr(common, "_getpass", lambda prompt: KEY)
    assert _run("secrets", "set", "ANTHROPIC_API_KEY").exit_code == 0
    assert KEY in (saved / "secrets.toml").read_text("utf-8")


def test_secrets_set_without_terminal_or_stdin(saved):
    result = _run("secrets", "set", "OPENROUTER_API_KEY")
    assert result.exit_code == common.NEEDS_TERMINAL
    assert "--stdin" in result.stderr


def test_secrets_refused_in_legacy_mode():
    result = _run("secrets", "set", "OPENROUTER_API_KEY", "--stdin", input=f"{KEY}\n")
    assert result.exit_code == 1
    assert "PROMPT_WORKFLOW_ENV is set" in result.stderr


def test_secrets_set_rejects_a_non_secret_name(saved):
    assert _run("secrets", "set", "PROMPT_PROFILE", "--stdin", input="x\n").exit_code == 2


# --- No command accepts a key as an argument ----------------------------------------------


def _leaves(command, path=()):
    from typer.core import TyperGroup

    if isinstance(command, TyperGroup):
        ctx = None
        for name in command.list_commands(ctx):
            yield from _leaves(command.get_command(ctx, name), (*path, name))
    else:
        yield path, command


def _all_leaves():
    import typer

    return dict(_leaves(typer.main.get_command(app)))


# improve takes the draft (--text), which the data-protection gate scans; it is data, never a
# setting. Every other value a command takes is listed here with a harmless stand-in.
TRIGGER_COMMANDS = {("improve",), ("persona",)}
REQUIRED = {
    ("config", "get"): ["PROMPT_PROFILE"],
    ("config", "set"): ["PROMPT_PERSONA", "x"],
    ("config", "unset"): ["PROMPT_PROFILE"],
    ("secrets", "set"): ["OPENROUTER_API_KEY"],
    ("secrets", "remove"): ["OPENROUTER_API_KEY"],
}
BASE = {
    ("setup",): ["--non-interactive", "--no-smoke-test"],
}
# Option names that suggest a secret. Allowed: the migration preview's consent token and the
# improve output cap.
SUSPICIOUS = ("key", "secret", "password", "token", "credential")
ALLOWED_NAMES = {"preview_token", "max_tokens"}  # a consent digest; an output cap


def test_no_option_is_named_like_a_secret():
    for path, command in _all_leaves().items():
        for param in command.params:
            name = param.name or ""
            if name in ALLOWED_NAMES:
                continue
            flag_only = getattr(param, "is_flag", False)
            assert flag_only or not any(w in name for w in SUSPICIOUS), (path, name)


def test_secrets_set_takes_no_value_argument():
    command = _all_leaves()[("secrets", "set")]
    values = [p.name for p in command.params if not getattr(p, "is_flag", False)]
    assert values == ["name"]


def _value_params(command):
    return [p for p in command.params if not getattr(p, "is_flag", False) and p.name != "help"]


@pytest.mark.parametrize("path", sorted(set(_all_leaves()) - TRIGGER_COMMANDS), ids=" ".join)
def test_no_command_accepts_a_key_as_an_argument(path, saved, tmp_path, monkeypatch, clipboard):
    """Each value a command takes, given a key: the key is never saved anywhere and never
    becomes a setting or a secret."""
    monkeypatch.setattr(deploy, "run_command", FakeRunner())
    monkeypatch.setattr(smoke, "run", lambda provider: pytest.fail("no smoke test here"))
    command = _all_leaves()[path]
    for param in _value_params(command):
        args = list(REQUIRED.get(path, []))
        if param.param_type_name == "argument":
            position = [p for p in command.params if p.param_type_name == "argument"].index(param)
            args[position] = KEY
        else:
            args += [param.opts[0], KEY]
        result = _run(*path, *BASE.get(path, []), *args, input="")
        assert result.exit_code in ALLOWED_EXITS, (param.name, result.output)
        assert KEY not in result.output, param.name
        layers = config.ConfigLayers.resolve(strict=False)
        assert KEY not in [e.value for e in layers.entries.values()], param.name
        for file in _tree(tmp_path):
            assert KEY.encode() not in file.read_bytes(), (param.name, file)


# --- Repair mode: every command runs on a broken config ------------------------------------


def _commands(espanso: Path) -> dict[tuple[str, ...], list[list[str]]]:
    where = ["--espanso-dir", str(espanso), "--launcher", LAUNCHER]
    return {
        ("setup",): [["setup", "--non-interactive", "--no-smoke-test", *where]],
        ("config", "show"): [["config", "show"], ["config", "show", "--raw"]],
        ("config", "get"): [["config", "get", "PROMPT_PROFILE"]],
        ("config", "set"): [["config", "set", "PROMPT_PROFILE", "general"]],
        ("config", "unset"): [["config", "unset", "PROMPT_PROFILE"]],
        ("config", "validate"): [["config", "validate"]],
        ("config", "migrate"): [["config", "migrate", "--dry-run"]],
        ("config", "rollback"): [["config", "rollback", "--dry-run"]],
        ("secrets", "set"): [["secrets", "set", "OPENROUTER_API_KEY", "--stdin"]],
        ("secrets", "status"): [["secrets", "status"]],
        ("secrets", "remove"): [["secrets", "remove", "OPENROUTER_API_KEY", "--yes"]],
        ("profiles", "list"): [["profiles", "list"]],
        ("profiles", "migrate"): [["profiles", "migrate", "--dry-run"]],
        ("stats",): [["stats"], ["stats", "--json", "--by", "day"]],
        ("history", "export"): [["history", "export"]],
        ("history", "prune"): [["history", "prune"]],
        ("history", "reset"): [["history", "reset", "--yes"]],
        ("doctor",): [["doctor"], ["doctor", "--json", *where]],
        ("espanso", "status"): [["espanso", "status", *where]],
        ("espanso", "deploy"): [["espanso", "deploy", "--dry-run", *where]],
        ("espanso", "detach"): [["espanso", "detach", "--yes"]],
        ("improve",): [["improve", "--source", "argument", "--text", "a draft"]],
        ("persona",): [["persona"]],
        # Without a terminal (as here) the interface exits 3 before reading anything.
        ("ui",): [["ui"]],
    }


def test_the_repair_list_covers_every_command(tmp_path):
    assert set(_commands(tmp_path)) == set(_all_leaves())


def _break(kind: str, tmp_path: Path, monkeypatch) -> None:
    if kind == "dotenv":
        (tmp_path / ".env").write_text(
            "PROMPT_LOCAL_ONLY=maybe\nOPENROUTER_MODEL=xPROMPT_PROFILE=general\nno equals\n\xff\n",
            "utf-8",
        )
        return
    monkeypatch.delenv("PROMPT_WORKFLOW_ENV")
    directory = config._user_config_dir()
    directory.mkdir(parents=True)
    name = "config.toml" if kind == "toml" else "secrets.toml"
    (directory / name).write_text("this = = not toml\n[[\n", "utf-8")


@pytest.mark.parametrize("kind", ["dotenv", "toml", "secrets"])
def test_every_command_runs_on_a_broken_config(kind, tmp_path, monkeypatch, espanso, clipboard):
    _break(kind, tmp_path, monkeypatch)
    monkeypatch.setattr(deploy, "run_command", FakeRunner())
    for argv_list in _commands(espanso).values():
        for argv in argv_list:
            result = _run(*argv, input="")
            assert result.exception is None or isinstance(result.exception, SystemExit), (
                argv,
                result.exception,
            )
            assert result.exit_code in ALLOWED_EXITS, (argv, result.output)
            assert "Traceback" not in result.output, argv


# --- doctor -------------------------------------------------------------------------------


def _doctor(*args):
    return _run("doctor", "--launcher", LAUNCHER, *args)


def test_doctor_json_schema_is_stable(espanso, clipboard, monkeypatch):
    fake = FakeRunner({"espanso path config": str(espanso), "espanso status": "espanso is running"})
    monkeypatch.setattr(deploy, "run_command", fake)
    result = _doctor("--json")
    data = json.loads(result.stdout)
    assert set(data) == {"schema_version", "version", "status", "checks"}
    assert data["schema_version"] == doctor.SCHEMA_VERSION == 1
    assert tuple(data["checks"]) == doctor.CHECK_IDS
    for check in data["checks"].values():
        assert set(check) == {"status", "message", "data"}
        assert check["status"] in {"ok", "warn", "fail", "info"}
    checks = data["checks"]
    for check_id, check in checks.items():
        assert tuple(check["data"]) == doctor.DATA_KEYS[check_id]
    assert set(checks["keys"]["data"]) == {"keys", "provider"}
    assert set(checks["espanso"]["data"]) == {
        "found",
        "config_dir",
        "running",
        "query_failed",
        "exit_code",
        "timed_out",
        "error",
    }
    assert set(checks["match_files"]["data"]) == {"espanso_dir", "files", "legacy"}
    assert set(checks["launcher"]["data"]) == {"current", "deployed", "drift", "missing"}
    assert set(checks["clipboard"]["data"]) == {"read", "length", "concealed", "error"}
    assert set(checks["sqlite"]["data"]) == {"version", "wal_reset_bug"}
    assert checks["espanso"]["data"]["running"] is True
    # The key is missing, so the report fails, with the documented exit code.
    assert data["status"] == "fail"
    assert result.exit_code == common.PROBLEMS
    # Only read-only Espanso commands are run.
    assert {tuple(c) for c in fake.calls if c[0] == "espanso"} <= {
        ("espanso", "path", "config"),
        ("espanso", "status"),
    }


# #115: each way `espanso path config` can fail, as text and as JSON.
_PANIC = deploy.CommandFailure(
    found=True, path="/opt/bin/espanso", returncode=101, error="unable to load config"
)
_ESPANSO_CASES = [
    (
        None,
        "espanso was not found on PATH",
        {"found": False, "query_failed": False, "exit_code": None, "timed_out": False},
    ),
    (
        _PANIC,
        "espanso found at /opt/bin/espanso, but `espanso path config` failed (exit 101): "
        "unable to load config; start Espanso once (`espanso start`) or check its config "
        "(using the default folder ",
        {"found": True, "query_failed": True, "exit_code": 101, "timed_out": False},
    ),
    (
        deploy.CommandFailure(found=True, timed_out=True),
        "espanso found, but `espanso path config` timed out after 30 s (using the default ",
        {"found": True, "query_failed": True, "exit_code": None, "timed_out": True},
    ),
]


@pytest.mark.parametrize(("answer", "message", "data"), _ESPANSO_CASES)
def test_doctor_tells_espanso_failures_apart(answer, message, data, clipboard, monkeypatch):
    fake = FakeRunner({"espanso status": "espanso is running"})
    fake.answers["espanso path config"] = answer
    monkeypatch.setattr(deploy, "run_command", fake)
    text = _doctor().stdout
    assert f"espanso: {message}" in " ".join(text.split())
    espanso = json.loads(_doctor("--json").stdout)["checks"]["espanso"]
    assert espanso["status"] == "warn"
    assert espanso["message"].startswith(message)
    assert {k: espanso["data"][k] for k in data} == data
    assert espanso["data"]["error"] == (answer.error if answer else None)
    assert espanso["data"]["config_dir"] == str(deploy.default_espanso_dir())
    # Status is asked only when espanso is on PATH; both commands stay read-only.
    assert (["espanso", "status"] in fake.calls) is data["found"]
    assert {tuple(c) for c in fake.calls if c[0] == "espanso"} <= {
        ("espanso", "path", "config"),
        ("espanso", "status"),
    }


@pytest.mark.parametrize(("answer", "message", "data"), _ESPANSO_CASES)
def test_deploy_and_setup_say_when_they_use_the_default_folder(
    answer, message, data, saved, monkeypatch, tmp_path
):
    default = tmp_path / "default-espanso"
    (default / "match").mkdir(parents=True)
    monkeypatch.setattr(deploy, "default_espanso_dir", lambda: default)
    monkeypatch.setattr(deploy, "run_command", FakeRunner({"espanso path config": answer}))
    reason = deploy.locate_espanso_dir().fallback
    notice = f"{reason}; using the default Espanso folder {default.resolve()}"
    result = _run("espanso", "deploy", "--dry-run", "--launcher", LAUNCHER)
    assert result.exit_code == 0, result.output
    assert notice in " ".join(result.stderr.split())
    assert f"Espanso match folder: {default.resolve() / 'match'}" in result.stdout
    result = _run(
        "setup",
        "--non-interactive",
        "--no-smoke-test",
        "--provider",
        "ollama",
        "--launcher",
        LAUNCHER,
    )
    assert result.exit_code == 0, result.output
    assert notice in " ".join(result.stderr.split())
    # Found by Espanso: no notice.
    monkeypatch.setattr(deploy, "run_command", FakeRunner({"espanso path config": f"{default}\n"}))
    result = _run("espanso", "deploy", "--dry-run", "--launcher", LAUNCHER)
    assert "default Espanso folder" not in result.stderr


def test_doctor_drift_case_reports_stale_and_no_secret(saved, espanso, clipboard, monkeypatch):
    """The #25 drift case: a prompts-template.yml an older version deployed."""
    monkeypatch.setattr(deploy, "run_command", FakeRunner({"espanso status": "running"}))
    config_store_secret(saved)
    _run("config", "set", "PROMPT_PERSONA", PERSONA)
    real = assets.read_match
    monkeypatch.setattr(deploy, "__version__", "0.15.0")
    monkeypatch.setattr(
        assets,
        "read_match",
        lambda name: "# old\nmatches: []\n" if name == "prompts-template.yml" else real(name),
    )
    deploy.apply(deploy.plan(espanso, LAUNCHER, deploy.Manifest.load()))
    monkeypatch.setattr(deploy, "__version__", "0.16.0")
    monkeypatch.setattr(assets, "read_match", real)

    text = _doctor("--espanso-dir", str(espanso))
    assert "prompts-template.yml: stale" in text.stdout
    assert text.exit_code == common.PROBLEMS
    as_json = _doctor("--espanso-dir", str(espanso), "--json")
    files = json.loads(as_json.stdout)["checks"]["match_files"]["data"]["files"]
    assert {"name": "prompts-template.yml", "state": "stale"} in files
    for result in (text, as_json):
        for sentinel in (KEY, PERSONA, clipboard):
            assert sentinel not in result.output


def test_doctor_clipboard_reports_the_length_only(clipboard, monkeypatch):
    monkeypatch.setattr(deploy, "run_command", FakeRunner())
    result = _doctor()
    assert f"readable: {len(clipboard)} character(s)" in result.stdout
    assert clipboard not in result.output
    assert "skipped" in _doctor("--no-clipboard").stdout


def test_doctor_never_reads_a_concealed_clipboard(monkeypatch):
    import pyperclip

    from prompt_workflow import clipboard_guard

    monkeypatch.setattr(clipboard_guard, "is_concealed", lambda: True)
    monkeypatch.setattr(pyperclip, "paste", lambda: pytest.fail("read a concealed item"))
    monkeypatch.setattr(deploy, "run_command", FakeRunner())
    assert "not read" in _doctor().stdout


def test_doctor_launcher_drift_and_missing(espanso, clipboard, monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "run_command", FakeRunner())
    deploy.apply(deploy.plan(espanso, LAUNCHER, deploy.Manifest.load()))
    exe = tmp_path / "bin" / "prompt-workflow"
    exe.parent.mkdir()
    exe.write_text("", "utf-8")
    data = json.loads(
        _run("doctor", "--json", "--launcher", str(exe), "--espanso-dir", str(espanso)).stdout
    )
    launcher = data["checks"]["launcher"]
    assert launcher["data"]["drift"] is True
    assert launcher["data"]["missing"] == [LAUNCHER]
    assert launcher["status"] == "fail"


# --- stats and history --------------------------------------------------------------------


def _record(n=2):
    target = HistoryStore(history.history_path())
    for _ in range(n):
        op = {
            "id": history.new_operation_id(),
            "origin": "espanso_managed",
            "trigger_id": "-i-",
            "kind": "improve",
            "profile_id": "default",
            "outcome": "ok",
            "latency_ms": 1000.0,
        }
        attempt = {
            "provider": "openrouter",
            "requested_model": "google/gemini-3.5-flash-lite",
            "endpoint": "remote",
            "status": 200,
            "latency_ms": 900.0,
            "output": 30,
            "input_uncached": 100,
            "charged_amount": Decimal("0.0001"),
            "charged_unit": "credits",
        }
        assert target.record(op, [attempt])
    return target


def test_stats_text_states_the_caveats_and_the_history(monkeypatch):
    result = _run("stats")
    assert result.exit_code == 0
    for caveat in (
        "not provider billing",
        "rendered does not mean pasted",
        "ignore PROMPT_PROVIDER",
        "config set PROMPT_HISTORY false",
        str(history.history_path()),
    ):
        assert caveat in result.stdout
    assert "No usage recorded yet." in result.stdout
    _record()
    result = _run("stats", "--by", "provider")
    assert "openrouter: 2 call(s), 2 request(s)" in result.stdout
    assert "reported 0.0002 credits" in result.stdout


def test_stats_help_states_the_caveats(monkeypatch):
    # Help panels wrap at the terminal width, which differs on CI runners (Windows too).
    monkeypatch.setenv("COLUMNS", "400")
    out = " ".join(re.sub(r"\x1b\[[0-9;]*m|[│╭╮╰╯─]", " ", _run("stats", "--help").stdout).split())
    assert "not provider billing" in out
    assert "rendered does not mean pasted" in out
    assert "ignore PROMPT_PROVIDER" in out


def test_stats_json():
    _record()
    data = json.loads(_run("stats", "--json").stdout)
    assert data["group_by"] == "trigger"
    assert len(data["caveats"]) == 3
    assert data["history"]["enabled"] is True
    assert data["rows"][0]["key"] == "-i-"
    assert data["rows"][0]["reported"] == {"credits": "0.0002"}


def test_stats_bad_group():
    assert _run("stats", "--by", "week").exit_code == 2


def test_history_export_prune_reset(tmp_path):
    _record()
    data = json.loads(_run("history", "export").stdout)
    assert len(data["operations"]) == 2
    out = tmp_path / "out.csv"
    assert _run("history", "export", "--format", "csv", "-o", str(out)).exit_code == 0
    assert out.read_text("utf-8").count("\n") == 3
    assert _run("history", "export", "-o", str(out)).exit_code == 1  # never replaced
    assert "Deleted 0" in _run("history", "prune").stdout
    assert "Deleted 2" in _run("history", "prune", "--older-than", "0d", "--yes").stdout
    _record(1)
    assert _run("history", "reset").exit_code == common.NEEDS_TERMINAL
    assert _run("history", "reset", "--yes").exit_code == 0
    assert json.loads(_run("history", "export").stdout)["operations"] == []
    assert _run("history", "prune", "--older-than", "soon").exit_code == 2


# --- profiles -----------------------------------------------------------------------------


def test_profiles_list(monkeypatch):
    folder = config._user_config_dir() / "profiles"
    folder.mkdir(parents=True)
    (folder / "mine.md").write_text("mine", "utf-8")
    (folder / "general.md").write_text("x", "utf-8")
    result = _run("profiles", "list")
    assert result.exit_code == 0
    assert "  default" in result.stdout
    assert "mine: added" in result.stdout
    assert "general: shadowed" in result.stdout


def test_profiles_migrate(monkeypatch, tmp_path, tty):
    from prompt_workflow import profiles

    checkout = tmp_path / "checkout"
    source = checkout / profiles.PROMPTS_PATH
    source.mkdir(parents=True)
    (source / "mine.md").write_text("my profile", "utf-8")
    monkeypatch.setattr(profiles, "git_pristine_profiles", lambda root, rev=None: {})
    args = ("profiles", "migrate", "--checkout", str(checkout))
    assert "Dry run" in _run(*args, "--dry-run").stdout
    target = config._user_config_dir() / "profiles" / "mine.md"
    assert not target.exists()
    assert _run(*args, input="n\n").exit_code == 1
    assert _run(*args, "--yes").exit_code == 0
    assert target.read_text("utf-8") == "my profile"


def test_profiles_migrate_needs_a_checkout():
    result = _run("profiles", "migrate")
    assert result.exit_code == 1
    assert "--checkout" in result.stderr


# --- espanso deploy --dry-run -------------------------------------------------------------


def test_espanso_deploy_dry_run_and_no_terminal(espanso, fake_run):
    where = ["--espanso-dir", str(espanso), "--launcher", LAUNCHER]
    result = _run("espanso", "deploy", "--dry-run", *where)
    assert result.exit_code == 0
    assert "Dry run" in result.stdout
    assert not list((espanso / "match").iterdir())
    result = _run("espanso", "deploy", *where)
    assert result.exit_code == common.NEEDS_TERMINAL
    assert not list((espanso / "match").iterdir())


# --- setup --------------------------------------------------------------------------------


def _setup(espanso, *args, input=None):
    return _run("setup", "--espanso-dir", str(espanso), "--launcher", LAUNCHER, *args, input=input)


def test_setup_non_interactive_end_to_end(saved, espanso, fake_run):
    """CI acceptance: config written, deploy in dry run, the real improve against the stub."""
    result = _setup(espanso, "--non-interactive", "--api-key-stdin", input=f"{KEY}\n")
    assert result.exit_code == 0, result.output
    assert (saved / "config.toml").is_file()
    assert KEY in (saved / "secrets.toml").read_text("utf-8")
    assert KEY not in result.output
    assert "Dry run: nothing was written." in result.stdout
    assert not list((espanso / "match").iterdir())
    assert "ok: improve reached the stub" in result.stdout
    assert "Usage history is on" in result.stdout
    assert "config set PROMPT_HISTORY false" in result.stdout
    assert fake_run.calls == []  # no restart, nothing deployed
    # The stub improve is a health check, not usage: nothing is recorded (#116).
    assert json.loads(_run("history", "export").stdout)["operations"] == []


def test_setup_needs_a_terminal_or_non_interactive(espanso):
    result = _setup(espanso)
    assert result.exit_code == common.NEEDS_TERMINAL
    assert "--non-interactive" in result.stderr


def test_setup_deploy_applies(saved, espanso, fake_run, monkeypatch):
    monkeypatch.setattr(smoke, "run", lambda p: smoke.SmokeResult(True, smoke.REPLY, 1, "ok"))
    result = _setup(espanso, "--non-interactive", "--deploy", "--provider", "ollama")
    assert result.exit_code == 0, result.output
    assert len(list((espanso / "match").iterdir())) == 3
    assert fake_run.calls == [["espanso", "restart"]]
    assert 'PROMPT_PROVIDER = "ollama"' in (saved / "config.toml").read_text("utf-8")


def test_setup_smoke_failure_exits_1(saved, espanso, fake_run, monkeypatch):
    monkeypatch.setattr(smoke, "run", lambda p: smoke.SmokeResult(False, "", 0, "[pw: down]"))
    result = _setup(espanso, "--non-interactive")
    assert result.exit_code == 1
    assert "smoke test failed" in result.stderr


def test_setup_offers_migration_and_writes_nothing_without_consent(
    saved, espanso, fake_run, tty, monkeypatch
):
    monkeypatch.setattr(smoke, "run", lambda p: smoke.SmokeResult(True, smoke.REPLY, 1, "ok"))
    env = _dotenv(saved)
    result = _setup(espanso, "--provider", "openrouter", "--profile", "default", input="n\nn\n")
    assert result.exit_code == 0, result.output
    assert "config migrate" in result.stdout
    assert env.is_file()
    assert not (saved / "config.toml").exists()
    # --non-interactive only offers it.
    result = _setup(espanso, "--non-interactive")
    assert env.is_file()
    assert not (saved / "config.toml").exists()


def test_setup_interactive_migrates_with_consent(saved, espanso, fake_run, tty, monkeypatch):
    monkeypatch.setattr(smoke, "run", lambda p: smoke.SmokeResult(True, smoke.REPLY, 1, "ok"))
    monkeypatch.setattr(common, "_getpass", lambda prompt: "")
    env = _dotenv(saved)
    # Migrate: yes; provider and profile: defaults; key already set: keep; deploy: no.
    result = _setup(espanso, input="y\n\n\nn\nn\n")
    assert result.exit_code == 0, result.output
    assert not env.exists()
    assert (saved / "config.toml").is_file()


def test_setup_in_legacy_mode_writes_no_settings(espanso, fake_run, monkeypatch):
    monkeypatch.setattr(smoke, "run", lambda p: smoke.SmokeResult(True, smoke.REPLY, 1, "ok"))
    result = _setup(espanso, "--non-interactive")
    assert result.exit_code == 0, result.output
    assert "PROMPT_WORKFLOW_ENV is set" in result.stdout
    assert not config.settings_file().exists()


def test_setup_rejects_a_bad_provider(espanso):
    assert _setup(espanso, "--non-interactive", "--provider", "nope").exit_code == 2


# --- the smoke test -----------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["openrouter", "anthropic", "ollama", "lmstudio"])
def test_smoke_runs_the_real_improve_against_the_stub(provider):
    result = smoke.run(provider)
    assert result.ok, result.message
    assert result.requests == 1


def test_smoke_env_points_every_provider_at_the_stub(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    seen = {}

    def fake(argv, env):
        seen.update(env)
        return 0, smoke.REPLY.encode()

    result = smoke.run("openrouter", runner=fake)
    assert not result.ok  # the fake never reached the stub
    assert KEY not in seen.values()
    for name in (
        "OPENROUTER_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "OLLAMA_BASE_URL",
        "LMSTUDIO_BASE_URL",
    ):
        assert seen[name].startswith("http://127.0.0.1:")
    assert seen["PROMPT_HISTORY"] == "false"


def _history_rows() -> str:
    out = io.StringIO()
    HistoryStore(history.history_path()).export(out)
    return out.getvalue()


def test_smoke_and_setup_add_no_history_row(saved, espanso, fake_run, monkeypatch):
    """#116: a real smoke run reaches the stub but never the real (per-test) history."""
    monkeypatch.setenv("PROMPT_HISTORY", "true")

    def recorded(argv, env):
        return smoke.run_cli(argv, {**env, "PROMPT_HISTORY": "true"})

    # The same run with the history forced on is recorded, so the checks below are not vacuous.
    assert smoke.run("ollama", runner=recorded).ok
    store = HistoryStore(history.history_path())
    before = _history_rows()
    data = json.loads(before)
    assert [(op["origin"], op["kind"], op["outcome"]) for op in data["operations"]] == [
        ("direct", "improve", "ok")
    ]
    assert [(a["provider"], a["endpoint"]) for a in data["attempts"]] == [("ollama", "loopback")]

    assert smoke.run("ollama").ok
    result = _setup(espanso, "--non-interactive", "--no-deploy", "--provider", "ollama")
    assert result.exit_code == 0, result.output
    assert "ok: improve reached the stub" in result.stdout
    assert store.health().operations == 1
    assert _history_rows() == before


@pytest.mark.parametrize(
    "argv",
    [
        [KEY],
        ["doctor", KEY],
        ["secrets", "set", "OPENROUTER_API_KEY", KEY],
        ["config", "set", "PROMPT_PROFILE", "general", KEY],
        ["stats", "--by", KEY],
        ["history", "prune", "--older-than", KEY],
        ["config", KEY],
        ["improve", KEY],
    ],
    ids=["command", "doctor", "secrets-set", "config-set", "stats", "prune", "group", "improve"],
)
def test_usage_errors_never_repeat_a_key(argv):
    result = _run(*argv)
    assert result.exit_code == 2
    assert KEY not in result.output
    assert "<redacted" in result.output


def test_a_key_under_a_setting_name_is_never_shown(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", KEY)
    show = _run("config", "show")
    assert KEY not in show.output
    assert f"<set, {len(KEY)} chars>" in show.stdout
    get = _run("config", "get", "OPENROUTER_MODEL")
    assert get.exit_code == 1
    assert KEY not in get.output


def test_prune_sooner_than_retention_asks(tty):
    _record()
    assert _run("history", "prune", "--older-than", "0", input="n\n").exit_code == 1
    assert len(json.loads(_run("history", "export").stdout)["operations"]) == 2


def test_prune_sooner_than_retention_needs_yes_without_terminal():
    _record()
    assert _run("history", "prune", "--older-than", "0").exit_code == common.NEEDS_TERMINAL
    assert "Deleted 2" in _run("history", "prune", "--older-than", "0", "--yes").stdout


def test_stdin_at_a_terminal_asks_hidden(saved, tty, monkeypatch):
    prompts = []
    monkeypatch.setattr(common, "_getpass", lambda prompt: prompts.append(prompt) or KEY)
    assert _run("secrets", "set", "OPENROUTER_API_KEY", "--stdin").exit_code == 0
    assert prompts
    assert KEY in (saved / "secrets.toml").read_text("utf-8")


def test_stdin_drops_a_byte_order_mark(saved):
    result = _run("secrets", "set", "OPENROUTER_API_KEY", "--stdin", input=f"\ufeff{KEY}\r\n")
    assert result.exit_code == 0, result.output
    from prompt_workflow import config_store

    assert config_store.saved_secret_names() == ("OPENROUTER_API_KEY",)
    assert f'"{KEY}"' in (saved / "secrets.toml").read_text("utf-8")


def test_doctor_data_keys_are_stable_when_a_check_fails(clipboard, monkeypatch):
    monkeypatch.setattr(deploy, "run_command", FakeRunner())

    def broken(settings):
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor, "_profiles_check", broken)
    monkeypatch.setattr(doctor, "_history_checks", broken)
    data = json.loads(_doctor("--json").stdout)
    for check_id, check in data["checks"].items():
        assert tuple(check["data"]) == doctor.DATA_KEYS[check_id]
    assert data["checks"]["profiles"]["status"] == "fail"
    assert data["checks"]["profiles"]["data"] == dict.fromkeys(doctor.DATA_KEYS["profiles"])


# --- setup, step by step ------------------------------------------------------------------


@pytest.fixture
def smoke_ok(monkeypatch):
    calls = []

    def fake(provider):
        calls.append(provider)
        return smoke.SmokeResult(True, smoke.REPLY, 1, "ok")

    monkeypatch.setattr(smoke, "run", fake)
    return calls


def test_setup_stops_writing_when_the_dotenv_is_broken(saved, espanso, fake_run, smoke_ok):
    saved.mkdir(parents=True)
    (saved / ".env").write_text("PROMPT_LOCAL_ONLY=maybe\n", "utf-8")
    result = _setup(espanso, "--non-interactive")
    assert result.exit_code == 1
    assert "fix the current settings first" in result.stderr
    assert not (saved / "config.toml").exists()


def test_setup_local_provider_takes_no_key(saved, espanso, fake_run, smoke_ok):
    args = ("--non-interactive", "--provider", "ollama", "--api-key-stdin")
    result = _setup(espanso, *args, input=f"{KEY}\n")
    assert result.exit_code == 1
    assert "takes no key" in result.stderr
    assert not (saved / "secrets.toml").exists()
    assert smoke_ok == ["ollama"]


def test_setup_key_from_stdin_in_legacy_mode_is_refused(espanso, fake_run, smoke_ok):
    result = _setup(espanso, "--non-interactive", "--api-key-stdin", input=f"{KEY}\n")
    assert result.exit_code == 1
    assert "the key was not saved" in result.stderr


def test_setup_refuses_a_malformed_key(saved, espanso, fake_run, smoke_ok):
    result = _setup(espanso, "--non-interactive", "--api-key-stdin", input="two words\n")
    assert result.exit_code == 1
    assert "one line of visible characters" in result.stderr
    assert not (saved / "secrets.toml").exists()


def test_setup_reports_a_set_key_without_showing_it(saved, espanso, fake_run, smoke_ok):
    config_store_secret(saved)
    result = _setup(espanso, "--non-interactive")
    assert result.exit_code == 0
    assert "OPENROUTER_API_KEY is set (from" in result.stdout
    assert KEY not in result.output


def test_setup_interactive_replaces_the_key(saved, espanso, fake_run, smoke_ok, tty, monkeypatch):
    config_store_secret(saved, "-".join(("old", "key")))
    monkeypatch.setattr(common, "_getpass", lambda prompt: KEY)
    # Provider and profile: the defaults; replace the key: yes; deploy: no.
    result = _setup(espanso, input="\n\ny\nn\n")
    assert result.exit_code == 0, result.output
    assert KEY in (saved / "secrets.toml").read_text("utf-8")


def test_setup_interactive_skips_an_empty_key(saved, espanso, fake_run, smoke_ok, tty, monkeypatch):
    monkeypatch.setattr(common, "_getpass", lambda prompt: "")
    result = _setup(espanso, input="\n\nn\n")
    assert result.exit_code == 0, result.output
    assert "to do: OPENROUTER_API_KEY not set" in result.stdout


def test_setup_interactive_asks_again_for_a_bad_provider(
    saved, espanso, fake_run, smoke_ok, tty, monkeypatch
):
    monkeypatch.setattr(common, "_getpass", lambda prompt: "")
    result = _setup(espanso, input="nope\nollama\n\nn\n")
    assert result.exit_code == 0, result.output
    assert "Choose one of" in result.stdout
    assert smoke_ok == ["ollama"]


def test_setup_interactive_deploys_on_yes(saved, espanso, fake_run, smoke_ok, tty, monkeypatch):
    monkeypatch.setattr(common, "_getpass", lambda prompt: "")
    result = _setup(espanso, "--provider", "ollama", "--profile", "general", input="y\n")
    assert result.exit_code == 0, result.output
    assert len(list((espanso / "match").iterdir())) == 3
    # A second run finds nothing to deploy.
    result = _setup(espanso, "--non-interactive")
    assert "Every match file is in sync." in result.stdout


def test_setup_forgets_gone_entries_without_asking(saved, espanso, fake_run, smoke_ok, tmp_path):
    """Every file in sync and only gone entries on record: nothing to write, so no question,
    no dry run and no "later" step; the entries are forgotten and the files left as they are."""
    deploy.apply(deploy.plan(espanso, LAUNCHER, deploy.Manifest.load()))
    old = tmp_path / "old-espanso"
    (old / "match").mkdir(parents=True)
    deploy.apply(deploy.plan(old, LAUNCHER, deploy.Manifest.load()))
    shutil.rmtree(old)
    before = {p: p.read_bytes() for p in (espanso / "match").iterdir()}
    result = _setup(espanso, "--non-interactive")
    assert result.exit_code == 0, result.output
    assert "Every match file is in sync." in result.stdout
    assert "(already gone)" in result.stdout
    assert "Dry run" not in result.stdout
    assert "deploy the match files" not in result.stdout
    assert fake_run.calls == []
    assert {p: p.read_bytes() for p in (espanso / "match").iterdir()} == before
    assert all(Path(t).parent.parent == espanso for t in deploy.Manifest.load().entries)


def test_setup_deploy_keeps_an_edited_file(saved, espanso, fake_run, smoke_ok):
    target = espanso / "match" / "prompts-llm.yml"
    target.write_text("# mine\n", "utf-8")
    result = _setup(espanso, "--non-interactive", "--deploy")
    assert result.exit_code == 0, result.output
    assert target.read_text("utf-8") == "# mine\n"
    assert "files you edited were kept" in result.stdout


def test_setup_deploy_error_fails_the_step(saved, espanso, fake_run, smoke_ok):
    result = _run(
        "setup", "--non-interactive", "--espanso-dir", str(espanso), "--launcher", '/a"b/pw'
    )
    assert result.exit_code == 1
    assert "deploy failed" in result.stderr


def test_setup_no_deploy_and_no_smoke(saved, espanso, fake_run, smoke_ok):
    result = _setup(espanso, "--non-interactive", "--no-deploy", "--no-smoke-test")
    assert result.exit_code == 0
    assert "Skipped (--no-deploy)." in result.stdout
    assert "Skipped (--no-smoke-test)." in result.stdout
    assert smoke_ok == []


def test_setup_option_errors(saved, espanso, tty, monkeypatch):
    assert _setup(espanso, "--api-key-stdin").exit_code == 2
    assert _setup(espanso, "--non-interactive", "--profile", "nosuch").exit_code == 2
    monkeypatch.setenv("PROMPT_PROVIDER", "nope")
    # The error panel wraps at the terminal width, which differs on CI runners.
    monkeypatch.setenv("COLUMNS", "400")
    result = _setup(espanso, "--non-interactive")
    assert result.exit_code == 2
    assert "pass --provider" in re.sub(r"\x1b\[[0-9;]*m", "", result.output)


def test_setup_in_legacy_mode_lists_what_to_set(espanso, fake_run, smoke_ok):
    result = _setup(espanso, "--non-interactive", "--provider", "ollama")
    assert result.exit_code == 0
    assert "to do: set PROMPT_PROVIDER=ollama" in result.stdout


def test_smoke_reports_a_cli_that_cannot_start():
    def broken(argv, env):
        raise OSError("no python")

    result = smoke.run("openrouter", runner=broken)
    assert not result.ok
    assert "could not run the CLI" in result.message


# --- odds and ends ------------------------------------------------------------------------


def test_stats_shows_estimated_unknown_and_local_costs(monkeypatch):
    target = HistoryStore(history.history_path())
    for attempt in (
        {"provider": "ollama", "requested_model": "qwen3:8b", "cost_state": "not_applicable"},
        {"provider": "anthropic", "requested_model": "claude-sonnet-5"},
    ):
        op = {
            "id": history.new_operation_id(),
            "origin": "direct",
            "kind": "improve",
            "outcome": "ok",
        }
        assert target.record(op, [{"endpoint": "remote", "status": 200, **attempt}])
    out = _run("stats", "--by", "provider").stdout
    assert "1 local (no cost)" in out
    assert "1 attempt(s) with an unknown cost" in out
    estimated = {"estimated": {"USD": Decimal("0.5")}, "reported": {}}
    row = history.StatsRow("x", 1, 1, "2026-10-05T00:00:00.000000Z", None, None, {}, **estimated)
    from prompt_workflow.commands import usage

    assert "estimated 0.5 USD" in " ".join(usage._row_text(row))


def test_stats_when_history_is_off(monkeypatch):
    monkeypatch.setenv("PROMPT_HISTORY", "false")
    assert "Usage history is off" in _run("stats").stdout


def test_an_unexpected_error_is_one_line(monkeypatch):
    from prompt_workflow.commands import usage

    def broken(settings):
        raise RuntimeError("kaput")

    monkeypatch.setattr(usage, "store", broken)
    result = _run("stats")
    assert result.exit_code == 1
    assert result.stderr == "error: unexpected RuntimeError: kaput\n"


def test_terminal_probes_survive_a_closed_stream(monkeypatch):
    class Closed:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr("sys.stdin", Closed())
    monkeypatch.setattr("sys.stdout", Closed())
    assert common.stdin_is_tty() is False
    assert common.stdout_is_tty() is False


def test_getpass_is_hidden_input(monkeypatch):
    import getpass

    monkeypatch.setattr(getpass, "getpass", lambda prompt: f"typed for {prompt}")
    assert common._getpass("KEY: ") == "typed for KEY: "


def test_secrets_set_empty_hidden_input(saved, tty, monkeypatch):
    monkeypatch.setattr(common, "_getpass", lambda prompt: " ")
    result = _run("secrets", "set", "OPENROUTER_API_KEY")
    assert result.exit_code == 1
    assert "no value entered" in result.stderr


def test_config_show_quotes_an_unprintable_value(monkeypatch):
    monkeypatch.setenv("PROMPT_EXTRA_PATTERNS", "a\tb")
    assert "PROMPT_EXTRA_PATTERNS            'a\\tb'" in _run("config", "show").stdout


def test_config_set_warns_when_the_environment_wins(saved, monkeypatch):
    monkeypatch.setenv("PROMPT_PROFILE", "default")
    result = _run("config", "set", "PROMPT_PROFILE", "general")
    assert result.exit_code == 0
    assert "environment variable PROMPT_PROFILE is set" in result.stderr


def test_config_unset_of_an_unsaved_setting(saved):
    result = _run("config", "unset", "PROMPT_PROFILE")
    assert result.exit_code == 0
    assert "nothing to do" in result.stdout
    assert not (saved / "config.toml").exists()


def test_secrets_remove_says_where_a_key_still_comes_from(saved, monkeypatch):
    config_store_secret(saved)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    result = _run("secrets", "remove", "OPENROUTER_API_KEY", "--yes")
    assert result.exit_code == 0
    assert "still set, from environment" in result.stdout
    assert KEY not in result.output
    assert "nothing to do" in _run("secrets", "remove", "OPENROUTER_API_KEY").stdout


def test_profiles_migrate_nothing_changed_and_default(monkeypatch, tmp_path):
    from prompt_workflow import profiles

    checkout = tmp_path / "checkout"
    source = checkout / profiles.PROMPTS_PATH
    source.mkdir(parents=True)
    (source / "default.md").write_text("same", "utf-8")
    args = ("profiles", "migrate", "--checkout", str(checkout), "--yes")
    monkeypatch.setattr(
        profiles, "git_pristine_profiles", lambda root, rev=None: {"default": "same"}
    )
    assert "nothing to copy" in _run(*args).stdout
    monkeypatch.setattr(
        profiles, "git_pristine_profiles", lambda root, rev=None: {"default": "old"}
    )
    result = _run(*args)
    assert result.exit_code == 0
    assert "PROMPT_PROFILE_OVERRIDES" in result.stdout
