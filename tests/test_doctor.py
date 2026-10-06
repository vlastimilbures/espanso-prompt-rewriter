"""Each doctor check's verdicts (doctor.py), offline: the runner, the clipboard and the
history store are faked; the CLI-level tests are in tests/test_commands.py."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from promptmend import assets, config, deploy, doctor
from promptmend.config import ConfigLayers, Settings
from promptmend.history import Health

LAUNCHER = "/Users/me/.local/bin/promptmend"


def _runner(answers: dict[str, str] | None = None) -> deploy.Runner:
    found = answers or {}
    return lambda argv: found.get(" ".join(argv))


def _status(check_id: str, report: doctor.Report) -> doctor.Check:
    return next(c for c in report.checks if c.id == check_id)


@pytest.fixture
def no_clipboard(monkeypatch: pytest.MonkeyPatch) -> None:
    import pyperclip

    from promptmend import clipboard_guard

    monkeypatch.setattr(clipboard_guard, "is_concealed", lambda: None)
    monkeypatch.setattr(pyperclip, "paste", lambda: "")


# --- install ------------------------------------------------------------------------------


def test_install_reports_the_channel(tmp_path: Path) -> None:
    exe = tmp_path / "bin" / "promptmend"
    exe.parent.mkdir()
    exe.write_text("", "utf-8")
    check = doctor._install_check(deploy.Launcher(exe, "uv"), None)
    assert (check.status, check.data["channel"], check.data["launcher"]) == ("ok", "uv", str(exe))


def test_install_without_a_launcher_guesses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = doctor._install_check(None, "nothing found")
    assert (check.status, check.data["channel"]) == ("warn", "unknown")
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("", "utf-8")
    assert doctor._install_check(None, "x").data["channel"] == "editable"


# --- config and keys ----------------------------------------------------------------------


def _config(**changes: Any) -> doctor.Check:
    layers = ConfigLayers.resolve(strict=False)
    return doctor._config_check(layers, changes.get("strict"))


def test_config_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _config().data["mode"] == "legacy"
    monkeypatch.delenv("PROMPTMEND_ENV")
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


def test_config_strict_error_fails() -> None:
    check = _config(strict="PROMPT_LOCAL_ONLY must be true or false")
    assert (check.status, check.data["valid"]) == ("fail", False)


def _keys(
    monkeypatch: pytest.MonkeyPatch, provider: str, local_only: bool = False, **env: str
) -> doctor.Check:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return doctor._keys_check(ConfigLayers.resolve(strict=False), provider, local_only)


def test_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _keys(monkeypatch, "anthropic").status == "fail"
    assert _keys(monkeypatch, "ollama").status == "warn"  # -i- needs the OpenRouter key
    assert _keys(monkeypatch, "ollama", local_only=True).status == "ok"
    check = _keys(monkeypatch, "openrouter", OPENROUTER_API_KEY="-".join(("test", "key")))
    assert check.status == "ok"
    assert check.data["keys"]["OPENROUTER_API_KEY"] == {"set": True, "source": "env"}
    assert "test-key" not in check.message


# --- Espanso, match files, launcher -------------------------------------------------------


def test_espanso_not_found_and_not_running(tmp_path: Path) -> None:
    check, target = doctor._espanso_check(_runner(), None)
    assert (check.status, check.data["found"], check.data["running"]) == ("warn", False, None)
    assert target == deploy.default_espanso_dir()
    check, _ = doctor._espanso_check(_runner({"espanso path config": str(tmp_path)}), None)
    assert (check.status, check.data["running"]) == ("warn", False)
    answers = {"espanso path config": str(tmp_path), "espanso status": "espanso is not running"}
    assert doctor._espanso_check(_runner(answers), None)[0].data["running"] is False


@pytest.fixture
def espanso(tmp_path: Path) -> Path:
    root = tmp_path / "espanso"
    (root / "match").mkdir(parents=True)
    return root


def test_match_files_in_sync_edited_and_unknown(espanso: Path) -> None:
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


def test_launcher_checks(tmp_path: Path, espanso: Path) -> None:
    assert doctor._launcher_check(LAUNCHER, None).status == "warn"
    assert doctor._launcher_check(LAUNCHER, deploy.Manifest.load()).status == "info"
    exe = tmp_path / "promptmend"
    exe.write_text("", "utf-8")
    deploy.apply(deploy.plan(espanso, str(exe), deploy.Manifest.load()))
    check = doctor._launcher_check(str(exe), deploy.Manifest.load())
    assert (check.status, check.data["drift"]) == ("ok", False)


# #169: match files still calling the deprecated `prompt-workflow` alias get a redeploy hint.
def test_launcher_check_warns_about_the_old_command(tmp_path: Path, espanso: Path) -> None:
    old = tmp_path / "prompt-workflow"
    old.write_text("", "utf-8")
    deploy.apply(deploy.plan(espanso, str(old), deploy.Manifest.load()))
    check = doctor._launcher_check(str(tmp_path / "promptmend"), deploy.Manifest.load())
    assert check.status == "warn"
    assert "redeploy with `promptmend espanso deploy`" in check.message
    assert set(check.data) == set(doctor.DATA_KEYS["launcher"])
    assert check.data["drift"] is True


def test_launcher_check_warns_about_another_launcher(tmp_path: Path, espanso: Path) -> None:
    other = tmp_path / "other" / "promptmend"
    other.parent.mkdir()
    other.write_text("", "utf-8")
    deploy.apply(deploy.plan(espanso, str(other), deploy.Manifest.load()))
    check = doctor._launcher_check(str(tmp_path / "promptmend"), deploy.Manifest.load())
    assert (check.status, check.data["drift"]) == ("warn", True)
    assert "redeploy" not in check.message


def test_launcher_check_skips_entries_whose_file_is_gone(tmp_path: Path, espanso: Path) -> None:
    """A deleted folder (or another Espanso config folder) leaves manifest entries behind:
    their launcher calls nothing, so a gone one is no FAIL; a live file's gone launcher is."""
    exe = tmp_path / "promptmend"
    exe.write_text("", "utf-8")
    old = tmp_path / "old-espanso"
    (old / "match").mkdir(parents=True)
    deploy.apply(deploy.plan(old, str(tmp_path / "gone" / "promptmend"), deploy.Manifest.load()))
    deploy.apply(deploy.plan(espanso, str(exe), deploy.Manifest.load()))
    for path in (old / "match").iterdir():
        path.unlink()
    (old / "match").rmdir()
    check = doctor._launcher_check(str(exe), deploy.Manifest.load())
    assert check.status == "ok"
    assert check.data["missing"] == []
    assert check.data["deployed"] == [str(exe)]
    assert check.data["orphans"] == sorted(str(old / "match" / n) for n in assets.match_names())
    assert "no longer exist" in check.message
    exe.unlink()  # a live file's launcher gone still fails
    check = doctor._launcher_check(str(exe), deploy.Manifest.load())
    assert (check.status, check.data["missing"]) == ("fail", [str(exe)])
    for path in (espanso / "match").iterdir():
        path.unlink()
    assert doctor._launcher_check(None, deploy.Manifest.load()).status == "info"


def test_run_compares_with_the_deployed_launcher(espanso: Path, no_clipboard: None) -> None:
    """With no launcher found or given, the files are compared with what was deployed."""
    deploy.apply(deploy.plan(espanso, LAUNCHER, deploy.Manifest.load()))
    report = doctor.run(espanso_dir=espanso, runner=_runner())
    files = _status("match_files", report).data["files"]
    assert {f["state"] for f in files} == {deploy.IN_SYNC}
    assert _status("launcher", report).data["missing"] == [LAUNCHER]


def test_run_compares_with_a_live_entry_not_an_orphan(
    tmp_path: Path, espanso: Path, no_clipboard: None
) -> None:
    """With no launcher found, a gone file's launcher (here sorting first) is not the one the
    live files are compared with."""
    old = tmp_path / "old-espanso"
    (old / "match").mkdir(parents=True)
    deploy.apply(deploy.plan(espanso, LAUNCHER, deploy.Manifest.load()))
    deploy.apply(deploy.plan(old, "/A/gone/promptmend", deploy.Manifest.load()))
    for path in (old / "match").iterdir():
        path.unlink()
    report = doctor.run(espanso_dir=espanso, runner=_runner())
    files = _status("match_files", report).data["files"]
    assert {f["state"] for f in files} == {deploy.IN_SYNC}


def test_run_with_a_damaged_manifest(espanso: Path, no_clipboard: None) -> None:
    path = config.user_data_dir() / deploy.MANIFEST_NAME
    path.parent.mkdir(parents=True)
    path.write_text("{not json", "utf-8")
    report = doctor.run(espanso_dir=espanso, launcher=LAUNCHER, runner=_runner())
    assert _status("launcher", report).status == "warn"
    assert "manifest" in _status("match_files", report).message


def test_run_survives_a_failing_espanso_probe(espanso: Path, no_clipboard: None) -> None:
    def runner(argv: Sequence[str]) -> None:
        if argv[0] == "espanso":
            raise RuntimeError("probe broke")

    report = doctor.run(espanso_dir=espanso, launcher=LAUNCHER, runner=runner)
    assert _status("espanso", report).status == "fail"
    assert [c.id for c in report.checks] == list(doctor.CHECK_IDS)


def test_every_data_key_is_in_the_json_schema(espanso: Path, no_clipboard: None) -> None:
    """to_json() keeps only DATA_KEYS, so a key a check sets but the schema lacks is
    silently dropped (as launcher's orphans was)."""
    deploy.apply(deploy.plan(espanso, LAUNCHER, deploy.Manifest.load()))
    report = doctor.run(espanso_dir=espanso, launcher=LAUNCHER, runner=_runner())
    for check in report.checks:
        assert set(check.data) <= set(doctor.DATA_KEYS[check.id]), check.id


# --- history and SQLite -------------------------------------------------------------------


def _health(**changes: Any) -> Health:
    values: dict[str, Any] = {
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
def test_history_check(
    monkeypatch: pytest.MonkeyPatch,
    history_on: bool,
    health: dict[str, Any],
    status: str,
    words: str,
) -> None:
    from promptmend import history

    monkeypatch.setattr(history.HistoryStore, "health", lambda self: _health(**health))
    check, sqlite = doctor._history_checks(Settings(history=history_on))
    assert check.status == status
    assert words in check.message
    assert sqlite.status == "ok"


@pytest.mark.parametrize(
    ("version", "status", "bug"), [("3.45.1", "warn", True), ("unknown", "warn", False)]
)
def test_sqlite_check(
    monkeypatch: pytest.MonkeyPatch, version: str, status: str, bug: bool
) -> None:
    from promptmend import history

    monkeypatch.setattr(
        history.HistoryStore, "health", lambda self: _health(sqlite_version=version)
    )
    sqlite = doctor._history_checks(Settings())[1]
    assert (sqlite.status, sqlite.data["wal_reset_bug"]) == (status, bug)


# --- clipboard and profiles ---------------------------------------------------------------


def test_clipboard_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import pyperclip

    from promptmend import clipboard_guard

    def broken() -> None:
        raise pyperclip.PyperclipException("no copy/paste mechanism")

    monkeypatch.setattr(clipboard_guard, "is_concealed", lambda: None)
    monkeypatch.setattr(pyperclip, "paste", broken)
    check = doctor._clipboard_check(True)
    assert (check.status, check.data["error"]) == ("warn", "PyperclipException")


# --- persona (#29 B) -----------------------------------------------------------------------


def test_persona_check_without_a_persona() -> None:
    check = doctor._persona_check(Settings())
    assert (check.status, check.message) == ("ok", "PROMPT_PERSONA is not set")
    assert check.data == {"set": False, "local_only": False, "findings": []}


def test_persona_check_clean_persona() -> None:
    check = doctor._persona_check(Settings(persona="I am a data engineer at Example Corp."))
    assert (check.status, check.data["findings"]) == ("ok", [])
    # bare_token is the gate's rule for a one-word draft, not for a persona.
    assert doctor._persona_check(Settings(persona="DataOps2Lead")).status == "ok"


def test_persona_check_warns_with_labels_only() -> None:
    address = "jane.doe" + "@" + "example.com"
    persona = f"I am a falcon analyst, mail {address}."
    check = doctor._persona_check(Settings(persona=persona, extra_patterns="falcon"))
    assert check.status == "warn"
    assert check.message == (
        "PROMPT_PERSONA matches the data-protection patterns: email, custom_1; it is sent "
        "unscanned with every cloud call"
    )
    assert check.data == {"set": True, "local_only": False, "findings": ["email", "custom_1"]}
    shown = str(check.message) + str(check.data)
    assert "falcon" not in shown
    assert address not in shown
    local = doctor._persona_check(
        Settings(persona=persona, extra_patterns="falcon", local_only=True)
    )
    assert (local.status, local.message) == (
        "info",
        "matches email, custom_1; not sent (PROMPT_LOCAL_ONLY=true)",
    )
    # A loopback server that may relay to a cloud API (PROMPT_GATE_LOCAL) can send it on.
    relay = Settings(persona=persona, local_only=True, gate_local=True)
    assert (doctor._persona_check(relay).status, doctor._persona_check(relay).message) == (
        "warn",
        "PROMPT_PERSONA matches the data-protection patterns: email; it is sent unscanned "
        "with every call through a local relay (PROMPT_GATE_LOCAL=true)",
    )


# Flagged only when a configured profile sends it: `general` has no {{PERSONA_RULE}}.
def test_persona_check_only_when_a_profile_sends_it() -> None:
    address = "jane.doe" + "@" + "example.com"
    unused = doctor._persona_check(Settings(persona=f"Mail {address}", profile="general"))
    assert (unused.status, unused.message) == (
        "info",
        "matches email; not used by the configured profiles",
    )
    pro = Settings(persona=f"Mail {address}", profile="general", pro_profile="default")
    assert doctor._persona_check(pro).status == "warn"
    folder = config._user_config_dir() / "profiles"
    folder.mkdir(parents=True)
    (folder / "mine.md").write_text("Rewrite it. {{PERSONA_RULE}}", "utf-8")
    (folder / "plain.md").write_text("Rewrite it.", "utf-8")
    assert doctor._persona_check(Settings(persona=address, profile="mine")).status == "warn"
    assert doctor._persona_check(Settings(persona=address, profile="plain")).status == "info"
    # A profile that cannot load sends nothing; the profiles check reports it.
    assert doctor._persona_check(Settings(persona=address, profile="nosuch")).status == "info"


def test_persona_check_when_the_persona_cannot_be_read(
    tmp_path: Path, espanso: Path, no_clipboard: None
) -> None:
    # A merged line: the value holds another setting's NAME=, so it is rejected.
    (tmp_path / ".env").write_text("PROMPT_PERSONA=I am Jane OLLAMA_MODEL=qwen3:8b\n", "utf-8")
    report = doctor.run(espanso_dir=espanso, launcher=LAUNCHER, runner=_runner())
    check = _status("persona", report)
    assert (check.status, check.message) == ("info", "PROMPT_PERSONA could not be read; see config")
    assert "Jane" not in str(report.to_json())


def test_persona_check_in_the_report(
    monkeypatch: pytest.MonkeyPatch, espanso: Path, no_clipboard: None
) -> None:
    monkeypatch.setenv("PROMPT_PERSONA", "Reach me at " + "jane" + "@" + "example.com")
    report = doctor.run(espanso_dir=espanso, launcher=LAUNCHER, runner=_runner())
    check = _status("persona", report)
    assert check.status == "warn"
    assert report.to_json()["checks"]["persona"]["data"]["findings"] == ["email"]


def test_profiles_check(monkeypatch: pytest.MonkeyPatch) -> None:
    assert doctor._profiles_check(Settings(profile="nosuch")).status == "fail"
    folder = config._user_config_dir() / "profiles"
    folder.mkdir(parents=True)
    (folder / "general.md").write_text("x", "utf-8")
    check = doctor._profiles_check(Settings())
    hint = (
        "Your general.md replaces the built-in only once PROMPT_PROFILE_OVERRIDES lists it: "
        "`promptmend config set PROMPT_PROFILE_OVERRIDES general`."
    )
    assert (check.status, check.message) == ("warn", f"general: shadowed; {hint}")
    assert {"name": "general", "status": "shadowed"} in check.data["user"]


def test_a_check_that_raises_is_reported(
    no_clipboard: None, espanso: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor, "_cli_check", boom)
    report = doctor.run(espanso_dir=espanso, launcher=LAUNCHER, runner=_runner())
    check = _status("cli", report)
    assert (check.status, check.message) == ("fail", "check failed: RuntimeError: boom")
    assert report.status == "fail"


def test_overrides_hint_keeps_the_names_already_listed() -> None:
    from promptmend.profiles import overrides_hint

    assert overrides_hint(["mine", "default-pro"]) is None  # not a built-in
    assert overrides_hint(["general"], ("general",)) is None
    hint = overrides_hint(["mine", "general"], ("default",))
    assert hint is not None
    assert hint.startswith("Your general.md replaces")
    assert hint.endswith("`promptmend config set PROMPT_PROFILE_OVERRIDES default,general`.")
