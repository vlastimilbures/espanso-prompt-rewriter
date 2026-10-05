"""Each doctor check's verdicts (doctor.py), offline: the runner, the clipboard and the
history store are faked; the CLI-level tests are in tests/test_commands.py."""

from __future__ import annotations

import pytest

from prompt_workflow import config, deploy, doctor
from prompt_workflow.config import ConfigLayers, Settings
from prompt_workflow.history import Health

LAUNCHER = "/Users/me/.local/bin/prompt-workflow"


def _runner(answers=None):
    answers = answers or {}
    return lambda argv: answers.get(" ".join(argv))


def _status(check_id, report):
    return next(c for c in report.checks if c.id == check_id)


@pytest.fixture
def no_clipboard(monkeypatch):
    import pyperclip

    from prompt_workflow import clipboard_guard

    monkeypatch.setattr(clipboard_guard, "is_concealed", lambda: None)
    monkeypatch.setattr(pyperclip, "paste", lambda: "")


# --- install ------------------------------------------------------------------------------


def test_install_reports_the_channel(tmp_path):
    exe = tmp_path / "bin" / "prompt-workflow"
    exe.parent.mkdir()
    exe.write_text("", "utf-8")
    check = doctor._install_check(deploy.Launcher(exe, "uv"), None)
    assert (check.status, check.data["channel"], check.data["launcher"]) == ("ok", "uv", str(exe))


def test_install_without_a_launcher_guesses(tmp_path, monkeypatch):
    check = doctor._install_check(None, "nothing found")
    assert (check.status, check.data["channel"]) == ("warn", "unknown")
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("", "utf-8")
    assert doctor._install_check(None, "x").data["channel"] == "editable"


# --- config and keys ----------------------------------------------------------------------


def _config(**changes):
    layers = ConfigLayers.resolve(strict=False)
    return doctor._config_check(layers, changes.get("strict"))


def test_config_modes(tmp_path, monkeypatch):
    assert _config().data["mode"] == "legacy"
    monkeypatch.delenv("PROMPT_WORKFLOW_ENV")
    assert _config().data["mode"] == "defaults"
    assert _config().status == "ok"
    env = config._user_config_dir() / ".env"
    env.parent.mkdir(parents=True)
    env.write_text("PROMPT_PROFILE=general\n", "utf-8")
    assert _config().data["mode"] == "env"
    config.settings_file().write_text("config_version = 1\n", "utf-8")
    check = _config()
    assert check.data["mode"] == "saved"
    assert check.status == "warn"  # the .env is now ignored: a finding


def test_config_strict_error_fails():
    check = _config(strict="PROMPT_LOCAL_ONLY must be true or false")
    assert (check.status, check.data["valid"]) == ("fail", False)


def _keys(monkeypatch, provider, local_only=False, **env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return doctor._keys_check(ConfigLayers.resolve(strict=False), provider, local_only)


def test_keys(monkeypatch):
    assert _keys(monkeypatch, "anthropic").status == "fail"
    assert _keys(monkeypatch, "ollama").status == "warn"  # -i- needs the OpenRouter key
    assert _keys(monkeypatch, "ollama", local_only=True).status == "ok"
    check = _keys(monkeypatch, "openrouter", OPENROUTER_API_KEY="-".join(("test", "key")))
    assert check.status == "ok"
    assert check.data["keys"]["OPENROUTER_API_KEY"] == {"set": True, "source": "env"}
    assert "test-key" not in check.message


# --- Espanso, match files, launcher -------------------------------------------------------


def test_espanso_not_found_and_not_running(tmp_path):
    check, target = doctor._espanso_check(_runner(), None)
    assert (check.status, check.data["found"], check.data["running"]) == ("warn", False, None)
    assert target == deploy.default_espanso_dir()
    check, _ = doctor._espanso_check(_runner({"espanso path config": str(tmp_path)}), None)
    assert (check.status, check.data["running"]) == ("warn", False)
    answers = {"espanso path config": str(tmp_path), "espanso status": "espanso is not running"}
    assert doctor._espanso_check(_runner(answers), None)[0].data["running"] is False


@pytest.fixture
def espanso(tmp_path):
    root = tmp_path / "espanso"
    (root / "match").mkdir(parents=True)
    return root


def test_match_files_in_sync_edited_and_unknown(espanso):
    manifest = deploy.Manifest.load()
    check = doctor._match_check(espanso, LAUNCHER, manifest)
    assert check.status == "fail"  # all missing
    deploy.apply(deploy.plan(espanso, LAUNCHER, manifest))
    assert doctor._match_check(espanso, LAUNCHER, deploy.Manifest.load()).status == "ok"
    target = espanso / "match" / "prompts-core.yml"
    target.write_text(target.read_text("utf-8") + "# mine\n", "utf-8")
    check = doctor._match_check(espanso, LAUNCHER, deploy.Manifest.load())
    assert check.status == "warn"
    assert {"name": "prompts-core.yml", "state": "modified"} in check.data["files"]
    assert "no launcher" in doctor._match_check(espanso, None, deploy.Manifest.load()).message
    assert "manifest" in doctor._match_check(espanso, LAUNCHER, None).message


def test_launcher_checks(tmp_path, espanso):
    assert doctor._launcher_check(LAUNCHER, None).status == "warn"
    assert doctor._launcher_check(LAUNCHER, deploy.Manifest.load()).status == "info"
    exe = tmp_path / "prompt-workflow"
    exe.write_text("", "utf-8")
    deploy.apply(deploy.plan(espanso, str(exe), deploy.Manifest.load()))
    check = doctor._launcher_check(str(exe), deploy.Manifest.load())
    assert (check.status, check.data["drift"]) == ("ok", False)


def test_launcher_check_skips_entries_whose_file_is_gone(tmp_path, espanso):
    """A deleted folder (or another Espanso config folder) leaves manifest entries behind:
    their launcher calls nothing, so a gone one is no FAIL; a live file's gone launcher is."""
    exe = tmp_path / "prompt-workflow"
    exe.write_text("", "utf-8")
    old = tmp_path / "old-espanso"
    (old / "match").mkdir(parents=True)
    deploy.apply(
        deploy.plan(old, str(tmp_path / "gone" / "prompt-workflow"), deploy.Manifest.load())
    )
    deploy.apply(deploy.plan(espanso, str(exe), deploy.Manifest.load()))
    for path in (old / "match").iterdir():
        path.unlink()
    (old / "match").rmdir()
    check = doctor._launcher_check(str(exe), deploy.Manifest.load())
    assert check.status == "ok"
    assert check.data["missing"] == []
    assert check.data["deployed"] == [str(exe)]
    assert check.data["orphans"] == sorted(
        str(old / "match" / n) for n in deploy.assets.match_names()
    )
    assert "no longer exist" in check.message
    exe.unlink()  # a live file's launcher gone still fails
    check = doctor._launcher_check(str(exe), deploy.Manifest.load())
    assert (check.status, check.data["missing"]) == ("fail", [str(exe)])
    for path in (espanso / "match").iterdir():
        path.unlink()
    assert doctor._launcher_check(None, deploy.Manifest.load()).status == "info"


def test_run_compares_with_the_deployed_launcher(espanso, no_clipboard):
    """With no launcher found or given, the files are compared with what was deployed."""
    deploy.apply(deploy.plan(espanso, LAUNCHER, deploy.Manifest.load()))
    report = doctor.run(espanso_dir=espanso, runner=_runner())
    files = _status("match_files", report).data["files"]
    assert {f["state"] for f in files} == {deploy.IN_SYNC}
    assert _status("launcher", report).data["missing"] == [LAUNCHER]


def test_run_compares_with_a_live_entry_not_an_orphan(tmp_path, espanso, no_clipboard):
    """With no launcher found, a gone file's launcher (here sorting first) is not the one the
    live files are compared with."""
    old = tmp_path / "old-espanso"
    (old / "match").mkdir(parents=True)
    deploy.apply(deploy.plan(espanso, LAUNCHER, deploy.Manifest.load()))
    deploy.apply(deploy.plan(old, "/A/gone/prompt-workflow", deploy.Manifest.load()))
    for path in (old / "match").iterdir():
        path.unlink()
    report = doctor.run(espanso_dir=espanso, runner=_runner())
    files = _status("match_files", report).data["files"]
    assert {f["state"] for f in files} == {deploy.IN_SYNC}


def test_run_with_a_damaged_manifest(espanso, no_clipboard):
    path = config.user_data_dir() / deploy.MANIFEST_NAME
    path.parent.mkdir(parents=True)
    path.write_text("{not json", "utf-8")
    report = doctor.run(espanso_dir=espanso, launcher=LAUNCHER, runner=_runner())
    assert _status("launcher", report).status == "warn"
    assert "manifest" in _status("match_files", report).message


def test_run_survives_a_failing_espanso_probe(espanso, no_clipboard):
    def runner(argv):
        if argv[0] == "espanso":
            raise RuntimeError("probe broke")

    report = doctor.run(espanso_dir=espanso, launcher=LAUNCHER, runner=runner)
    assert _status("espanso", report).status == "fail"
    assert [c.id for c in report.checks] == list(doctor.CHECK_IDS)


def test_every_data_key_is_in_the_json_schema(espanso, no_clipboard):
    """to_json() keeps only DATA_KEYS, so a key a check sets but the schema lacks is
    silently dropped (as launcher's orphans was)."""
    deploy.apply(deploy.plan(espanso, LAUNCHER, deploy.Manifest.load()))
    report = doctor.run(espanso_dir=espanso, launcher=LAUNCHER, runner=_runner())
    for check in report.checks:
        assert set(check.data) <= set(doctor.DATA_KEYS[check.id]), check.id


# --- history and SQLite -------------------------------------------------------------------


def _health(**changes):
    values = {
        "path": "/x/history.sqlite3",
        "exists": True,
        "schema_version": 1,
        "operations": 3,
        "attempts": 3,
        "lost_writes": 0,
        "last_lost_utc": None,
        "tracking_incomplete": False,
        "writable": True,
        "sqlite_version": "3.51.3",
        **changes,
    }
    return Health(**values)


@pytest.mark.parametrize(
    ("history_on", "health", "status", "words"),
    [
        (True, {}, "ok", "3 call(s)"),
        (False, {}, "info", "off"),
        (True, {"lost_writes": 2, "tracking_incomplete": True}, "warn", "2 write(s) lost"),
        (True, {"writable": False, "tracking_incomplete": True}, "warn", "not writable"),
        (True, {"error": "corrupt"}, "warn", "corrupt"),
    ],
)
def test_history_check(monkeypatch, history_on, health, status, words):
    from prompt_workflow import history

    monkeypatch.setattr(history.HistoryStore, "health", lambda self: _health(**health))
    check, sqlite = doctor._history_checks(Settings(history=history_on))
    assert check.status == status
    assert words in check.message
    assert sqlite.status == "ok"


@pytest.mark.parametrize(
    ("version", "status", "bug"), [("3.45.1", "warn", True), ("unknown", "warn", False)]
)
def test_sqlite_check(monkeypatch, version, status, bug):
    from prompt_workflow import history

    monkeypatch.setattr(
        history.HistoryStore, "health", lambda self: _health(sqlite_version=version)
    )
    sqlite = doctor._history_checks(Settings())[1]
    assert (sqlite.status, sqlite.data["wal_reset_bug"]) == (status, bug)


# --- clipboard and profiles ---------------------------------------------------------------


def test_clipboard_unavailable(monkeypatch):
    import pyperclip

    from prompt_workflow import clipboard_guard

    def broken():
        raise pyperclip.PyperclipException("no copy/paste mechanism")

    monkeypatch.setattr(clipboard_guard, "is_concealed", lambda: None)
    monkeypatch.setattr(pyperclip, "paste", broken)
    check = doctor._clipboard_check(True)
    assert (check.status, check.data["error"]) == ("warn", "PyperclipException")


def test_profiles_check(monkeypatch):
    assert doctor._profiles_check(Settings(profile="nosuch")).status == "fail"
    folder = config._user_config_dir() / "profiles"
    folder.mkdir(parents=True)
    (folder / "general.md").write_text("x", "utf-8")
    check = doctor._profiles_check(Settings())
    hint = (
        "Your general.md replaces the built-in only once PROMPT_PROFILE_OVERRIDES lists it: "
        "`prompt-workflow config set PROMPT_PROFILE_OVERRIDES general`."
    )
    assert (check.status, check.message) == ("warn", f"general: shadowed; {hint}")
    assert {"name": "general", "status": "shadowed"} in check.data["user"]


def test_a_check_that_raises_is_reported(no_clipboard, espanso, monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor, "_cli_check", boom)
    report = doctor.run(espanso_dir=espanso, launcher=LAUNCHER, runner=_runner())
    check = _status("cli", report)
    assert (check.status, check.message) == ("fail", "check failed: RuntimeError: boom")
    assert report.status == "fail"


def test_overrides_hint_keeps_the_names_already_listed():
    from prompt_workflow.profiles import overrides_hint

    assert overrides_hint(["mine", "default-pro"]) is None  # not a built-in
    assert overrides_hint(["general"], ("general",)) is None
    hint = overrides_hint(["mine", "general"], ("default",))
    assert hint is not None
    assert hint.startswith("Your general.md replaces")
    assert hint.endswith("`prompt-workflow config set PROMPT_PROFILE_OVERRIDES default,general`.")
