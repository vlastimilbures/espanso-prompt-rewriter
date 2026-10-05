"""The full-screen interface (#93), driven headless through Textual's Pilot: one test per
screen's main action, each through the same services the headless commands use. Espanso,
uv and brew run through a fake runner, the editor and the smoke test are replaced, and no
test calls a provider."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path

import pytest
from textual.widgets import Button, Input, Select

from prompt_workflow import config, config_store, deploy, doctor, history, smoke
from prompt_workflow import profiles as profile_service
from prompt_workflow.history import HistoryStore
from prompt_workflow.tui import panes
from prompt_workflow.tui.app import HIGH_CONTRAST, ManageApp
from prompt_workflow.tui.modals import ConfirmModal, FormModal, TextModal
from prompt_workflow.tui.state import gather

# Built at runtime, so no key-shaped literal lands in the repo (gitleaks).
KEY = "sk-or-v1-" + "cd34" * 16
SIZE = (120, 50)


class FakeRunner:
    def __init__(self, answers):
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.answers.get(" ".join(argv))


@pytest.fixture
def espanso(tmp_path, monkeypatch):
    """Espanso's folder, found and running through a fake runner, and this install's
    launcher resolved to a fixed path."""
    root = tmp_path / "espanso"
    (root / "match").mkdir(parents=True)
    fake = FakeRunner(
        {
            "espanso path config": f"{root}\n",
            "espanso status": "espanso is running",
            "espanso restart": "",
        }
    )
    monkeypatch.setattr(deploy, "run_command", fake)
    launcher = tmp_path / "bin" / "prompt-workflow"
    launcher.parent.mkdir()
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(deploy, "resolve_launcher", lambda **_: deploy.Launcher(launcher, "uv"))
    fake.root = root
    return fake


@pytest.fixture
def saved(monkeypatch):
    """Saved mode: config.toml and the secret store are read and written (per-test dirs)."""
    monkeypatch.delenv("PROMPT_WORKFLOW_ENV")
    return config._user_config_dir()


async def settle(pilot) -> None:
    """Let every worker (the state load, an action) and what it set off finish."""
    for _ in range(4):
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def drive(scenario: Callable[[ManageApp, object], Awaitable[None]], **kwargs) -> ManageApp:
    app = ManageApp(**kwargs)

    async def main() -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await scenario(app, pilot)

    asyncio.run(main())
    return app


def pane(app: ManageApp, tab: str) -> panes.Pane:
    return app.main.query_one(f"#{tab}-pane", panes.Pane)


async def press(app: ManageApp, pilot, selector: str) -> None:
    app.screen.query_one(selector, Button).press()
    await settle(pilot)


async def fill(app: ManageApp, pilot, **values: str) -> None:
    """Set a dialog's fields by id and submit it."""
    for field_id, value in values.items():
        widget = app.screen.query_one(f"#{field_id.replace('_', '-')}")
        assert isinstance(widget, Input | Select)
        widget.value = value
    await pilot.pause()
    await press(app, pilot, "#submit")


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


# --- app, keys, theme ---------------------------------------------------------------------


def test_digits_switch_tabs_and_t_toggles_contrast(espanso):
    seen = []

    async def scenario(app, pilot):
        from textual.widgets import TabbedContent

        for key, tab in zip(
            "654321",
            ("diagnostics", "history", "triggers", "profiles", "providers", "home"),
            strict=True,
        ):
            await pilot.press(key)
            seen.append(app.main.query_one(TabbedContent).active == tab)
        await pilot.press("t")
        seen.append(app.theme == HIGH_CONTRAST.name)
        await pilot.press("t")
        seen.append(app.theme == "textual-dark")
        await pilot.press("q")

    drive(scenario)
    assert seen == [True] * 8


def test_a_failing_load_is_shown_not_raised(espanso):
    def broken(group_by):
        raise RuntimeError("boom")

    async def scenario(app, pilot):
        assert app.state is None
        # Before any state, an action needing it does nothing or says what is missing.
        await pilot.press("3")
        await press(app, pilot, "#set-profile")
        assert not isinstance(app.screen, FormModal)
        await press(app, pilot, "#edit-profile")
        assert "Select one of your profiles" in pane(app, "profiles").last_message
        await pilot.press("4")
        await press(app, pilot, "#show-diff")
        assert "No plan" in pane(app, "triggers").last_message

    drive(scenario, loader=broken)


@pytest.mark.parametrize(
    ("tab", "button"),
    [
        ("2", "#set-key"),
        ("2", "#remove-key"),
        ("2", "#set-setting"),
        ("2", "#smoke"),
        ("3", "#set-profile"),
        ("5", "#export"),
        ("5", "#prune"),
    ],
)
def test_cancelling_a_dialog_changes_nothing(saved, espanso, tab, button):
    config_store.save_secret("OPENROUTER_API_KEY", KEY)
    before = sorted((p, p.read_bytes()) for p in saved.rglob("*") if p.is_file())

    async def scenario(app, pilot):
        await pilot.press(tab)
        await press(app, pilot, button)
        assert isinstance(app.screen, FormModal)
        await pilot.press("escape")
        await settle(pilot)
        assert not isinstance(app.screen, FormModal)

    drive(scenario)
    assert sorted((p, p.read_bytes()) for p in saved.rglob("*") if p.is_file()) == before


def test_no_color_is_left_to_textual(monkeypatch, espanso):
    monkeypatch.setenv("NO_COLOR", "1")

    async def scenario(app, pilot):
        assert app.no_color

    drive(scenario)


# --- Home ---------------------------------------------------------------------------------


def test_home_shows_doctor_and_checks_again(espanso):
    calls = []

    def loader(group_by):
        calls.append(group_by)
        return gather(group_by)

    async def scenario(app, pilot):
        text = str(pane(app, "home").query_one("#home-checks").render())
        assert "espanso: running" in text
        assert "keys: PROMPT_PROVIDER is openrouter but OPENROUTER_API_KEY is not set" in text
        await press(app, pilot, "#home-reload")
        await pilot.press("r")
        await settle(pilot)

    drive(scenario, loader=loader)
    assert calls == ["trigger"] * 3
    # Doctor never read the clipboard, and only read-only Espanso commands ran.
    assert {" ".join(c[:2]) for c in espanso.calls} <= {"espanso path", "espanso status"}


# --- Providers & keys ---------------------------------------------------------------------


def test_providers_set_a_key_never_shows_it(saved, espanso):
    async def scenario(app, pilot):
        await pilot.press("2")
        await press(app, pilot, "#set-key")
        assert isinstance(app.screen, FormModal)
        assert app.screen.query_one("#key-value", Input).password
        await fill(app, pilot, key_name="OPENROUTER_API_KEY", key_value=f"  {KEY} ")
        assert KEY not in app.export_screenshot()
        keys = str(pane(app, "providers").query_one("#keys").render_line(1))
        assert "set" in keys
        assert KEY not in pane(app, "providers").last_message

    drive(scenario)
    assert config_store.saved_secret_names() == ("OPENROUTER_API_KEY",)
    assert KEY in (saved / "secrets.toml").read_text("utf-8")


def test_providers_refuse_an_empty_key_and_legacy_mode(espanso):
    async def scenario(app, pilot):
        await pilot.press("2")
        await press(app, pilot, "#set-key")
        # Enter in a field submits the dialog, like its Save button.
        app.screen.query_one("#key-value", Input).focus()
        await pilot.press("space", "enter")
        await settle(pilot)
        assert "no value entered" in pane(app, "providers").last_message
        await press(app, pilot, "#set-key")
        await fill(app, pilot, key_value=KEY)
        message = pane(app, "providers").last_message
        assert message.startswith("error: PROMPT_WORKFLOW_ENV is set")
        assert KEY not in message
        await press(app, pilot, "#remove-key")
        assert "PROMPT_WORKFLOW_ENV is set" in pane(app, "providers").last_message

    drive(scenario)


def test_providers_remove_a_key_after_confirming(saved, espanso):
    config_store.save_secret("ANTHROPIC_API_KEY", KEY)

    async def scenario(app, pilot):
        await pilot.press("2")
        await press(app, pilot, "#remove-key")
        await fill(app, pilot, key_name="ANTHROPIC_API_KEY")
        assert isinstance(app.screen, ConfirmModal)
        await press(app, pilot, "#cancel")
        assert config_store.saved_secret_names() == ("ANTHROPIC_API_KEY",)
        await press(app, pilot, "#remove-key")
        await fill(app, pilot)
        await pilot.press("y")
        await settle(pilot)
        assert pane(app, "providers").last_message == (
            "ANTHROPIC_API_KEY removed from the secret store."
        )
        await press(app, pilot, "#remove-key")
        assert "holds no key" in pane(app, "providers").last_message

    drive(scenario)
    assert config_store.saved_secret_names() == ()


def test_providers_remove_a_key_still_set_elsewhere(saved, espanso, monkeypatch):
    config_store.save_secret("OPENROUTER_API_KEY", KEY)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)

    async def scenario(app, pilot):
        await pilot.press("2")
        await press(app, pilot, "#remove-key")
        await fill(app, pilot)
        await press(app, pilot, "#confirm")
        assert pane(app, "providers").last_message.endswith("it is still set, from environment.")

    drive(scenario)


def test_an_unexpected_error_is_shown_not_raised(saved, espanso, monkeypatch):
    def bug(name, value):
        raise RuntimeError("kaput")

    monkeypatch.setattr(config_store, "save_secret", bug)

    async def scenario(app, pilot):
        await pilot.press("2")
        await press(app, pilot, "#set-key")
        await fill(app, pilot, key_value=KEY)
        message = pane(app, "providers").last_message
        assert message == "error: unexpected RuntimeError: kaput"

    drive(scenario)


def test_providers_migrate_reports_a_refusal(saved, espanso, monkeypatch):
    def refused(environ=None):
        raise config_store.MigrationError("the marker is damaged")

    monkeypatch.setattr(config_store, "plan_migration", refused)

    async def scenario(app, pilot):
        await pilot.press("2")
        await press(app, pilot, "#migrate-env")
        assert pane(app, "providers").last_message == "error: the marker is damaged"

    drive(scenario)


def test_providers_change_a_setting_and_reload(saved, espanso, monkeypatch):
    monkeypatch.setenv("PROMPT_PROFILE", "general")

    async def scenario(app, pilot):
        await pilot.press("2")
        await press(app, pilot, "#set-setting")
        options = app.screen.query_one("#setting-name", Select)._options
        assert "PROMPT_PERSONA" not in [value for _, value in options]
        await fill(app, pilot, setting_name="PROMPT_LOCAL_ONLY", setting_value="true")
        assert app.state.settings.local_only
        routes = str(pane(app, "providers").query_one("#routes").render_line(3))
        assert "refused (local only)" in routes
        # A bad value is refused as the CLI would refuse it; a key never reaches config.toml.
        await press(app, pilot, "#set-setting")
        await fill(app, pilot, setting_name="PROMPT_LOCAL_ONLY", setting_value="maybe")
        assert pane(app, "providers").last_message.startswith("error: PROMPT_LOCAL_ONLY")
        await press(app, pilot, "#set-setting")
        await fill(app, pilot, setting_name="PROMPT_PROFILE", setting_value=KEY)
        assert "looks like a key" in pane(app, "providers").last_message
        await press(app, pilot, "#set-setting")
        await fill(app, pilot, setting_name="PROMPT_PROFILE", setting_value="default")
        assert "overrides the saved value" in pane(app, "providers").last_message

    drive(scenario)
    text = (saved / "config.toml").read_text("utf-8")
    assert "PROMPT_LOCAL_ONLY = true" in text
    assert KEY not in text


def test_providers_migrate_env_with_preview(saved, espanso):
    saved.mkdir(parents=True)
    (saved / ".env").write_text(
        f"PROMPT_PROFILE=general\nOPENROUTER_API_KEY={KEY}\n", encoding="utf-8"
    )

    async def scenario(app, pilot):
        await pilot.press("2")
        await press(app, pilot, "#migrate-env")
        assert isinstance(app.screen, ConfirmModal)
        assert KEY not in app.screen.preview
        await pilot.press("n")
        await settle(pilot)
        assert (saved / ".env").is_file()
        await press(app, pilot, "#migrate-env")
        await press(app, pilot, "#confirm")
        assert pane(app, "providers").last_message.startswith("Migrated. Backup:")
        await press(app, pilot, "#migrate-env")
        assert "already" in pane(app, "providers").last_message

    drive(scenario)
    assert config_store.saved_secret_names() == ("OPENROUTER_API_KEY",)


def test_providers_test_call_uses_the_stub_service(espanso, monkeypatch):
    called = []

    def fake_run(provider):
        called.append(provider)
        return smoke.SmokeResult(True, smoke.REPLY, 1, "improve reached the stub")

    monkeypatch.setattr(smoke, "run", fake_run)

    async def scenario(app, pilot):
        await pilot.press("2")
        await press(app, pilot, "#smoke")
        assert "No provider is called" in app.screen.preview
        await press(app, pilot, "#cancel")
        assert called == []
        await press(app, pilot, "#smoke")
        await fill(app, pilot, provider="ollama")
        assert pane(app, "providers").last_message == "ok: improve reached the stub"

    drive(scenario)
    assert called == ["ollama"]


# --- Profiles -----------------------------------------------------------------------------


def _user_profile(name="mine"):
    from prompt_workflow.prompt_builder import user_profiles_dir

    folder = user_profiles_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.md"
    path.write_text("Rewrite the draft.", encoding="utf-8")
    return path


def test_profiles_set_default_and_edit_in_editor(saved, espanso, monkeypatch):
    path = _user_profile()
    edited = []

    def editor(target):
        edited.append(target)
        return 0

    monkeypatch.setattr(panes, "run_editor", editor)

    async def scenario(app, pilot):
        from contextlib import nullcontext

        from textual.widgets import DataTable

        app.suspend = nullcontext  # the headless driver cannot hand the terminal over
        await pilot.press("3")
        await press(app, pilot, "#edit-profile")
        assert "Select one of your profiles" in pane(app, "profiles").last_message
        table = pane(app, "profiles").query_one("#profiles", DataTable)
        table.move_cursor(row=table.get_row_index("user:mine"))
        await press(app, pilot, "#edit-profile")
        assert pane(app, "profiles").last_message == "mine.md: the editor exited with 0"
        await press(app, pilot, "#set-profile")
        await fill(app, pilot, profile="mine")
        assert app.state.settings.profile == "mine"

    drive(scenario)
    assert edited == [path]
    assert 'PROMPT_PROFILE = "mine"' in (saved / "config.toml").read_text("utf-8")


def test_profiles_editor_unsupported_terminal(espanso, monkeypatch):
    _user_profile()
    monkeypatch.setattr(panes, "run_editor", lambda target: pytest.fail("editor ran"))

    async def scenario(app, pilot):
        from textual.widgets import DataTable

        table = pane(app, "profiles").query_one("#profiles", DataTable)
        table.move_cursor(row=table.get_row_index("user:mine"))
        await pilot.press("3")
        await press(app, pilot, "#edit-profile")
        assert "cannot hand over" in pane(app, "profiles").last_message

    drive(scenario)


def test_editor_command():
    def nowhere(name):
        return None

    assert panes.editor_command({}, windows=False, which=nowhere) == ["vi"]
    assert panes.editor_command({}, windows=True, which=nowhere) == ["notepad"]
    both = {"VISUAL": "vim", "EDITOR": "code --wait"}
    assert panes.editor_command(both, windows=False, which=nowhere) == ["vim"]


def test_profiles_migrate_copies_after_confirming(espanso, tmp_path, monkeypatch):
    source = tmp_path / "project" / profile_service.PROMPTS_PATH
    source.mkdir(parents=True)
    (source / "general.md").write_text("Edited.", encoding="utf-8")
    (source / "extra.md").write_text("Mine.", encoding="utf-8")
    monkeypatch.setattr(
        profile_service, "git_pristine_profiles", lambda root, rev=None: {"general": "Old."}
    )

    async def scenario(app, pilot):
        await pilot.press("3")
        await press(app, pilot, "#migrate-profiles")
        assert "extra.md (added)" in app.screen.preview
        await press(app, pilot, "#confirm")
        assert pane(app, "profiles").last_message == "extra: copied\ngeneral: copied"

    drive(scenario)
    from prompt_workflow.prompt_builder import user_profiles_dir

    assert sorted(p.name for p in user_profiles_dir().iterdir()) == ["extra.md", "general.md"]


def test_profiles_migrate_default_and_nothing_to_copy(espanso, tmp_path, monkeypatch):
    source = tmp_path / "project" / profile_service.PROMPTS_PATH
    source.mkdir(parents=True)
    (source / "default.md").write_text("Edited.", encoding="utf-8")
    pristine = {"default": "Old."}
    monkeypatch.setattr(profile_service, "git_pristine_profiles", lambda root, rev=None: pristine)

    async def scenario(app, pilot):
        await pilot.press("3")
        await press(app, pilot, "#migrate-profiles")
        await press(app, pilot, "#cancel")
        assert pane(app, "profiles").last_message == ""
        await press(app, pilot, "#migrate-profiles")
        await press(app, pilot, "#confirm")
        assert "PROMPT_PROFILE_OVERRIDES includes default" in pane(app, "profiles").last_message
        pristine["default"] = "Edited."
        await press(app, pilot, "#migrate-profiles")
        assert pane(app, "profiles").last_message == (
            "No added or edited profiles; nothing to copy."
        )

    drive(scenario)


def test_profiles_editor_that_cannot_start(espanso, monkeypatch):
    _user_profile()

    def missing(target):
        raise FileNotFoundError("no-such-editor")

    monkeypatch.setattr(panes, "run_editor", missing)

    async def scenario(app, pilot):
        from contextlib import nullcontext

        from textual.widgets import DataTable

        app.suspend = nullcontext
        await pilot.press("3")
        table = pane(app, "profiles").query_one("#profiles", DataTable)
        table.move_cursor(row=table.get_row_index("user:mine"))
        await press(app, pilot, "#edit-profile")
        assert "could not start the editor" in pane(app, "profiles").last_message

    drive(scenario)


def test_run_editor(monkeypatch, tmp_path):
    import subprocess

    seen = []

    class Done:
        returncode = 0

    monkeypatch.setenv("VISUAL", "myeditor -w")
    monkeypatch.setattr(subprocess, "run", lambda argv, check: seen.append(argv) or Done())
    assert panes.run_editor(tmp_path / "mine.md") == 0
    assert seen == [["myeditor", "-w", str(tmp_path / "mine.md")]]


def test_profiles_migrate_without_a_checkout(espanso):
    async def scenario(app, pilot):
        await pilot.press("3")
        await press(app, pilot, "#migrate-profiles")
        assert "no src/prompt_workflow/prompts" in pane(app, "profiles").last_message

    drive(scenario)


# --- Triggers -----------------------------------------------------------------------------


def test_triggers_show_fixed_providers_and_deploy(espanso):
    async def scenario(app, pilot):
        from textual.widgets import DataTable

        await pilot.press("4")
        table = pane(app, "triggers").query_one("#triggers", DataTable)
        rows = {
            table.get_row_at(i)[0].plain: [c.plain for c in table.get_row_at(i)]
            for i in range(table.row_count)
        }
        assert rows["-i-"][1:5] == ["openrouter", "standard", "PROMPT_PROFILE", "missing"]
        assert rows["-il-"][1:4] == ["ollama", "standard", "general"]
        assert rows["-ic-"][4] == "commented out"
        assert "PROMPT_PROVIDER does not change these" in str(
            pane(app, "triggers").query_one(".note").render()
        )
        await press(app, pilot, "#deploy")
        assert isinstance(app.screen, FormModal)
        await press(app, pilot, "#cancel")
        assert not list((espanso.root / "match").iterdir())
        await press(app, pilot, "#deploy")
        await press(app, pilot, "#submit")
        assert pane(app, "triggers").last_message.endswith("The match files are up to date.")
        rows = [table.get_row_at(i)[4].plain for i in range(table.row_count)]
        assert set(rows) == {"in sync", "commented out"}
        await press(app, pilot, "#deploy")
        assert pane(app, "triggers").last_message == "Nothing to do: every match file is in sync."
        await press(app, pilot, "#show-diff")
        assert isinstance(app.screen, TextModal)
        assert app.screen.preview == "Every match file is in sync."
        await press(app, pilot, "#close")

    drive(scenario)
    assert sorted(p.name for p in (espanso.root / "match").iterdir()) == sorted(
        s.name for s in gather().plan.steps
    )
    assert ["espanso", "restart"] in espanso.calls


def test_triggers_deploy_keeps_an_edited_file_by_default(espanso):
    target = espanso.root / "match" / "prompts-core.yml"
    target.write_text("matches: []  # mine\n", encoding="utf-8")

    async def scenario(app, pilot):
        await pilot.press("4")
        await press(app, pilot, "#show-diff")
        assert "# mine" in app.screen.preview
        await pilot.press("escape")
        await press(app, pilot, "#deploy")
        assert app.screen.query_one("#choice-0", Select).value == deploy.KEEP
        await press(app, pilot, "#submit")
        assert "WARNING: kept as you have them and NOT updated: prompts-core.yml" in (
            pane(app, "triggers").last_message
        )

    drive(scenario)
    assert target.read_text("utf-8") == "matches: []  # mine\n"


def test_triggers_deploy_side_by_side(espanso):
    target = espanso.root / "match" / "prompts-core.yml"
    target.write_text("matches: []  # mine\n", encoding="utf-8")

    async def scenario(app, pilot):
        await pilot.press("4")
        await press(app, pilot, "#deploy")
        await fill(app, pilot, choice_0=deploy.SIDE)

    drive(scenario)
    assert target.read_text("utf-8") == "matches: []  # mine\n"
    assert (target.parent / f"prompts-core.yml{deploy.SIDE_SUFFIX}").is_file()


def test_triggers_detach_after_confirming(espanso):
    async def scenario(app, pilot):
        await pilot.press("4")
        await press(app, pilot, "#detach")
        assert "no deployed match files" in pane(app, "triggers").last_message
        await press(app, pilot, "#deploy")
        await press(app, pilot, "#submit")
        await press(app, pilot, "#detach")
        assert "prompts-llm.yml" in app.screen.preview
        await press(app, pilot, "#cancel")
        assert (espanso.root / "match" / "prompts-llm.yml").is_file()
        await press(app, pilot, "#detach")
        await press(app, pilot, "#submit")
        assert "removed" in pane(app, "triggers").last_message

    drive(scenario)
    left = sorted(p.name for p in (espanso.root / "match").iterdir())
    assert left == ["prompts-core.yml"]  # --keep-static: the static snippets stay


def test_triggers_report_failures(espanso, monkeypatch):
    del espanso.answers["espanso restart"]  # neither restart nor start succeeds

    async def scenario(app, pilot):
        await pilot.press("4")
        await press(app, pilot, "#deploy")
        await press(app, pilot, "#submit")
        message = pane(app, "triggers").last_message
        assert "Could not restart Espanso; run `espanso restart` yourself." in message
        await press(app, pilot, "#detach")
        await press(app, pilot, "#submit")
        assert "Could not restart Espanso" in pane(app, "triggers").last_message

        def fail(*args, **kwargs):
            raise deploy.DeployError("the disk is full")

        monkeypatch.setattr(deploy, "apply", fail)
        monkeypatch.setattr(deploy, "detach", fail)
        # Detach removed prompts-llm.yml, so there is something to deploy again.
        await press(app, pilot, "#deploy")
        await press(app, pilot, "#submit")
        assert pane(app, "triggers").last_message == "error: the disk is full"
        await press(app, pilot, "#detach")
        await press(app, pilot, "#submit")
        assert pane(app, "triggers").last_message == "error: the disk is full"
        monkeypatch.setattr(deploy.Manifest, "load", classmethod(lambda cls, path=None: fail()))
        await press(app, pilot, "#detach")
        assert pane(app, "triggers").last_message == "error: the disk is full"

    drive(scenario)


def test_triggers_deploy_retires_the_legacy_file(espanso):
    legacy = espanso.root / "match" / "base.yml"
    legacy.write_text('matches:\n  - trigger: "-p-"  # prompt-workflow\n', encoding="utf-8")

    async def scenario(app, pilot):
        await pilot.press("4")
        await press(app, pilot, "#deploy")
        assert "legacy    base.yml will be retired" in app.screen.preview
        await press(app, pilot, "#submit")
        assert "retired legacy" in pane(app, "triggers").last_message

    drive(scenario)
    assert not legacy.exists()


def test_triggers_without_a_launcher(monkeypatch):
    monkeypatch.setattr(deploy, "run_command", lambda argv: None)

    def no_launcher(**_):
        raise deploy.DeployError("Could not find a stable prompt-workflow launcher")

    monkeypatch.setattr(deploy, "resolve_launcher", no_launcher)

    async def scenario(app, pilot):
        await pilot.press("4")
        text = str(pane(app, "triggers").query_one("#deploy-target").render())
        assert text.startswith("Cannot compare with Espanso: Could not find"), (text, app.state)
        await press(app, pilot, "#show-diff")
        assert "No plan" in pane(app, "triggers").last_message
        await press(app, pilot, "#deploy")
        assert pane(app, "triggers").last_message.startswith("error: Could not find")

    drive(scenario)


# --- History ------------------------------------------------------------------------------


def test_history_stats_export_prune_reset(espanso, tmp_path):
    _record()
    out = tmp_path / "out.json"

    async def scenario(app, pilot):
        from textual.widgets import DataTable

        await pilot.press("5")
        history_pane = pane(app, "history")
        assert "not provider billing" in str(history_pane.query_one(".note").render())
        table = history_pane.query_one("#stats", DataTable)
        row = [c.plain for c in table.get_row_at(0)]
        assert row[:3] == ["-i-", "2", "2"]
        assert row[-1] == "[reported] 0.0002 credits"
        history_pane.query_one("#group-by", Select).value = "provider"
        await settle(pilot)
        assert app.state.group_by == "provider"
        assert table.get_row_at(0)[0].plain == "openrouter"

        await press(app, pilot, "#export")
        await fill(app, pilot, path=str(out))
        assert history_pane.last_message == f"Exported 2 call(s) to {out}"
        await press(app, pilot, "#export")
        await fill(app, pilot, path=str(out))
        assert history_pane.last_message.startswith("error:")  # never replaced

        await press(app, pilot, "#prune")
        await fill(app, pilot, days="soon")
        assert "number of days" in history_pane.last_message
        await press(app, pilot, "#prune")
        await fill(app, pilot, days="30")
        await press(app, pilot, "#confirm")
        assert history_pane.last_message == "Deleted 0 call(s) older than 30 day(s)."

        await press(app, pilot, "#reset")
        await press(app, pilot, "#cancel")
        assert app.state.stats
        await press(app, pilot, "#reset")
        await press(app, pilot, "#confirm")
        assert history_pane.last_message == "The usage history is empty."
        assert not app.state.stats
        assert "No usage recorded yet." in str(history_pane.query_one("#stats-note").render())

    drive(scenario)
    assert len(json.loads(out.read_text("utf-8"))["operations"]) == 2


def test_cost_badges():
    row = history.StatsRow(
        "m",
        1,
        2,
        "2026-10-04T09:12:31.000000Z",
        None,
        None,
        {},
        estimated={"USD": Decimal("0.5")},
        unknown_cost_attempts=1,
    )
    assert panes._cost(row) == "[estimated] 0.5 USD; [unknown] 1"
    row = history.StatsRow("m", 1, 1, "2026-10-04T09:12:31.000000Z", None, None, {})
    assert panes._cost(row) == "none recorded"


def test_history_export_needs_a_path(espanso):
    async def scenario(app, pilot):
        await pilot.press("5")
        await press(app, pilot, "#export")
        await fill(app, pilot, path=" ")
        assert pane(app, "history").last_message == "error: enter the file to write"

    drive(scenario)


def test_history_unreadable_store(espanso, monkeypatch):
    def broken(self, group_by="trigger"):
        raise history.HistoryError("the database is damaged")

    monkeypatch.setattr(HistoryStore, "stats", broken)

    async def scenario(app, pilot):
        note = str(pane(app, "history").query_one("#stats-note").render())
        assert note == "error: the database is damaged"

    drive(scenario)


# --- Diagnostics --------------------------------------------------------------------------


def test_diagnostics_provenance_and_import_check(espanso, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setattr(
        doctor, "import_check", lambda: doctor.ImportCheck(True, "40 ms, 200 module(s)")
    )

    async def scenario(app, pilot):
        from textual.widgets import DataTable

        await pilot.press("6")
        diagnostics = pane(app, "diagnostics")
        table = diagnostics.query_one("#settings", DataTable)
        rows = {
            table.get_row_at(i)[0].plain: [c.plain for c in table.get_row_at(i)]
            for i in range(table.row_count)
        }
        assert rows["OPENROUTER_API_KEY"][1:3] == [f"<set, {len(KEY)} chars>", "environment"]
        assert KEY not in app.export_screenshot()
        store = str(diagnostics.query_one("#store").render())
        assert "Lost history writes: 0" in store
        assert "SQLite" in store
        await press(app, pilot, "#import-check")
        assert diagnostics.last_message == "40 ms, 200 module(s)"

    drive(scenario)


def test_diagnostics_on_a_broken_config(espanso, tmp_path):
    (tmp_path / ".env").write_text("PROMPT_LOCAL_ONLY=maybe\n", encoding="utf-8")

    async def scenario(app, pilot):
        from textual.widgets import DataTable

        await pilot.press("6")
        diagnostics = pane(app, "diagnostics")
        table = diagnostics.query_one("#settings", DataTable)
        row = [c.plain for c in table.get_row_at(table.get_row_index("PROMPT_LOCAL_ONLY"))]
        assert row[1:3] == ["false", "default"]
        assert row[3].startswith("invalid value in ")
        assert "PROMPT_LOCAL_ONLY must be true or false" in str(
            diagnostics.query_one("#findings").render()
        )

    drive(scenario)


def test_import_check_runs_a_fresh_interpreter():
    found = doctor.import_check()
    assert found.ok, found.message
    assert found.modules
    assert found.seconds is not None
    assert "no heavy module" in found.message


def test_import_check_reports_failures(monkeypatch):
    import subprocess

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("python", 1)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert doctor.import_check().message == "the import check could not run (TimeoutExpired)"

    class Heavy:
        stdout = json.dumps({"seconds": 0.5, "modules": 900, "heavy": ["textual"]}).encode()

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Heavy())
    found = doctor.import_check()
    assert not found.ok
    assert found.message == "500 ms, 900 module(s); loads textual"


# --- services the interface added ---------------------------------------------------------


def test_routes_classify_each_provider():
    from prompt_workflow.config import Settings
    from prompt_workflow.factory import routes

    cfg = Settings(ollama_model="gpt-oss:120b-cloud", local_only=True)
    found = {r.name: r for r in routes(cfg)}
    assert (found["ollama"].remote, found["ollama"].refused) == (True, True)
    assert (found["lmstudio"].remote, found["lmstudio"].refused) == (False, False)
    assert found["openrouter"].key == "OPENROUTER_API_KEY"
    assert found["lmstudio"].key is None


def test_triggers_match_the_match_files():
    import yaml

    from prompt_workflow import assets

    found = {t.trigger: t for t in assets.triggers()}
    match_dir = Path(__file__).parents[1] / "espanso" / "match"
    shipped = {
        m["trigger"]
        for path in match_dir.glob("*.yml")
        for m in yaml.safe_load(path.read_text("utf-8"))["matches"]
    }
    assert {t for t, item in found.items() if item.active} == shipped
    assert (found["-ip-"].provider, found["-ip-"].tier) == ("openrouter", "pro")
    assert (found["-ilm-"].provider, found["-ilm-"].profile) == ("lmstudio", "general")
    assert (found["-ic-"].active, found["-ic-"].provider) == (False, "anthropic")
    assert (found["-p-"].command, found["-risk-"].command) == ("persona", None)


# --- launching: the bare command and `ui` (D-UI-1) -----------------------------------------


@pytest.fixture
def terminal(monkeypatch):
    """Make stdin and stdout read as terminals, each switchable, and record each launch."""
    from prompt_workflow.commands import common

    state = {"stdin": True, "stdout": True, "launched": 0, "code": None}
    monkeypatch.setattr(common, "stdin_is_tty", lambda: state["stdin"])
    monkeypatch.setattr(common, "stdout_is_tty", lambda: state["stdout"])

    def run(self, *args, **kwargs):
        state["launched"] += 1
        self._return_code = state["code"]

    monkeypatch.setattr(ManageApp, "run", run)
    return state


def _invoke(*args):
    from typer.testing import CliRunner

    from prompt_workflow.cli import app

    return CliRunner().invoke(app, list(args))


EOL = os.linesep.encode()
GOLDEN = Path(__file__).parent / "golden" / "no-args-help.txt"


def _words(text: str) -> str:
    """Help text without colour, box drawing, the program name or line wrapping, which
    differ by terminal width, CI and OS."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = re.sub(r"Usage: .*? \[OPTIONS\]", "Usage: prompt-workflow [OPTIONS]", text)
    # Every box-drawing character: Windows draws square corners where macOS draws round ones.
    return " ".join(re.sub(r"[\u2500-\u257f|+]", " ", text).split())


def test_words_ignore_the_border_style():
    rounded = "╭─ Options ─╮\n│ --help │\n╰───────────╯"
    square = "┌─ Options ─┐\n│ --help │\n└───────────┘"
    assert _words(rounded) == _words(square) == "Options --help"


def test_bare_command_without_a_terminal_prints_the_help_as_before(monkeypatch):
    # Help panels wrap at the terminal width, which differs on CI runners (Windows too).
    monkeypatch.setenv("COLUMNS", "100")
    bare, helped = _invoke(), _invoke("--help")
    # As no_args_is_help always did: --help's bytes less its last newline, on stdout, exit 2.
    assert (bare.exit_code, helped.exit_code) == (2, 0)
    assert bare.stdout_bytes + EOL == helped.stdout_bytes
    assert bare.stderr_bytes == helped.stderr_bytes == b""
    # The same help as before #93 (tests/golden, captured from it), plus the `ui` row.
    ui_row = "ui Open the full-screen interface to set up and manage (a terminal)."
    before = _words(GOLDEN.read_text("utf-8"))
    assert _words(bare.stdout) == f"{before} {ui_row}"


@pytest.mark.parametrize(("stdin", "stdout"), [(True, False), (False, True)])
def test_bare_command_needs_both_streams_on_a_terminal(terminal, stdin, stdout):
    terminal.update(stdin=stdin, stdout=stdout)
    result = _invoke()
    assert result.exit_code == 2
    assert "Usage:" in result.stdout
    assert terminal["launched"] == 0


@pytest.mark.parametrize("args", [(), ("ui",)])
@pytest.mark.parametrize("code", [None, 0, 1])
def test_terminal_opens_the_interface_and_exits_with_its_code(terminal, args, code):
    terminal["code"] = code
    result = _invoke(*args)
    assert terminal["launched"] == 1
    assert result.exit_code == (code or 0)
    assert result.stdout == ""


def test_ui_without_a_terminal_exits_3(terminal):
    from prompt_workflow.commands.common import NEEDS_TERMINAL

    terminal["stdout"] = False
    result = _invoke("ui")
    assert result.exit_code == NEEDS_TERMINAL == 3
    assert result.stderr.startswith("error: the interface needs a terminal")
    assert terminal["launched"] == 0


# The real process with pipes, not a terminal: the bare command prints the same bytes as
# --help and exits 2, as no_args_is_help always did.
def test_bare_command_in_a_real_process(tmp_path):
    import subprocess
    import sys

    env = {**os.environ, "COLUMNS": "100", "PROMPT_WORKFLOW_ENV": str(tmp_path / ".env")}
    env.pop("GITHUB_ACTIONS", None)

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "prompt_workflow.cli", *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env=env,
            timeout=60,
            check=False,
        )

    bare, helped = run(), run("--help")
    assert (bare.returncode, helped.returncode) == (2, 0)
    assert bare.stdout + EOL == helped.stdout
    assert bare.stderr == helped.stderr == b""


# --- review of #109: regressions ----------------------------------------------------------


def test_providers_never_show_credentials_in_a_base_url(espanso, monkeypatch):
    secret = "hunter" + "2pass"
    url = f"https://bob:{secret}@llm.example.com/v1"
    monkeypatch.setenv("OPENROUTER_BASE_URL", url)

    async def scenario(app, pilot):
        await pilot.press("2")
        assert secret not in app.export_screenshot()
        await pilot.press("6")
        assert secret not in app.export_screenshot()

    drive(scenario)


def test_an_older_slower_load_never_overwrites_a_newer_one(espanso):
    import threading

    release, started = threading.Event(), threading.Event()
    loaded = []

    def loader(group_by):
        n = len(loaded)
        loaded.append(n)
        if n == 1:  # the second load is slow and finishes last
            started.set()
            release.wait(10)
        state = gather(group_by)
        object.__setattr__(state, "plan_error", f"load {n}")
        return state

    async def scenario(app, pilot):
        app.reload()
        while not started.is_set():
            await pilot.pause(0.01)
        app.reload()
        await settle(pilot)
        assert app.state.plan_error == "load 2"
        release.set()
        for _ in range(20):
            await pilot.pause(0.02)
        assert app.state.plan_error == "load 2"

    drive(scenario, loader=loader)
    assert loaded == [0, 1, 2]


def test_a_failing_worker_is_reported_not_raised(espanso, monkeypatch):
    def bug(provider):
        raise RuntimeError("kaput")

    monkeypatch.setattr(smoke, "run", bug)

    def bad_apply(*args, **kwargs):
        raise ValueError("bad choice")

    monkeypatch.setattr(deploy, "apply", bad_apply)

    async def scenario(app, pilot):
        await pilot.press("2")
        await press(app, pilot, "#smoke")
        await fill(app, pilot)
        assert pane(app, "providers").last_message == "error: unexpected RuntimeError: kaput"
        await pilot.press("4")
        await press(app, pilot, "#deploy")
        await press(app, pilot, "#submit")
        assert pane(app, "triggers").last_message == "error: bad choice"
        assert app.is_running

    drive(scenario)


def test_deploy_twice_opens_one_dialog(espanso):
    async def scenario(app, pilot):
        await pilot.press("4")
        buttons = app.screen.query_one("#deploy", Button), app.screen.query_one("#detach", Button)
        buttons[0].press()
        buttons[0].press()
        buttons[1].press()
        await settle(pilot)
        assert isinstance(app.screen, FormModal)
        assert "in progress" in pane(app, "triggers").last_message
        await press(app, pilot, "#cancel")
        assert not isinstance(app.screen, FormModal)
        # Once the dialog is closed, Deploy works again.
        await press(app, pilot, "#deploy")
        assert isinstance(app.screen, FormModal)
        await press(app, pilot, "#submit")
        assert pane(app, "triggers").last_message.endswith("The match files are up to date.")

    drive(scenario)


def test_deploy_refuses_a_plan_that_changed_since_the_preview(espanso):
    target = espanso.root / "match" / "prompts-core.yml"

    async def scenario(app, pilot):
        await pilot.press("4")
        await press(app, pilot, "#deploy")
        target.write_text("matches: []  # written meanwhile\n", encoding="utf-8")
        await press(app, pilot, "#submit")
        assert "changed since the preview" in pane(app, "triggers").last_message

    drive(scenario)
    assert target.read_text("utf-8") == "matches: []  # written meanwhile\n"
    assert not (espanso.root / "match" / "prompts-llm.yml").exists()


def test_detach_refuses_a_manifest_that_changed_since_the_preview(espanso):
    async def scenario(app, pilot):
        await pilot.press("4")
        await press(app, pilot, "#deploy")
        await press(app, pilot, "#submit")
        await press(app, pilot, "#detach")
        manifest = deploy.Manifest.load()
        manifest.entries.popitem()
        manifest.save()
        await press(app, pilot, "#submit")
        assert "changed since the preview" in pane(app, "triggers").last_message

    drive(scenario)
    assert (espanso.root / "match" / "prompts-llm.yml").is_file()


def test_prune_enter_does_not_prune(espanso, monkeypatch):
    pruned = []
    monkeypatch.setattr(HistoryStore, "prune", lambda self, age=None: pruned.append(age) or 0)

    async def scenario(app, pilot):
        await pilot.press("5")
        await press(app, pilot, "#prune")
        await pilot.press("enter")
        await settle(pilot)
        assert not isinstance(app.screen, FormModal | ConfirmModal)
        await press(app, pilot, "#prune")
        await fill(app, pilot, days="30")
        assert isinstance(app.screen, ConfirmModal)
        assert "older than 30 day(s)" in app.screen.dialog_title
        await pilot.press("enter")
        await settle(pilot)
        assert pruned == []
        await press(app, pilot, "#prune")
        await fill(app, pilot, days="30")
        await press(app, pilot, "#confirm")

    drive(scenario)
    from datetime import timedelta

    assert pruned == [timedelta(days=30)]


@pytest.mark.parametrize(
    ("windows", "value", "expected"),
    [
        (False, "code --wait", ["/bin/code", "--wait"]),
        (False, "'/opt/my editor/ed' -w", ["/opt/my editor/ed", "-w"]),
        (True, "code --wait", ["C:/bin/code.cmd", "--wait"]),
        (True, '"C:\\Program Files\\Ed\\ed.exe" -w', ["C:\\Program Files\\Ed\\ed.exe", "-w"]),
    ],
)
def test_editor_command_forms(windows, value, expected):
    found = {"code": "/bin/code"} if not windows else {"code": "C:/bin/code.cmd"}
    command = panes.editor_command(
        {"EDITOR": value}, windows=windows, which=lambda name: found.get(name)
    )
    assert command == expected


def test_children_ignore_modules_planted_in_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "ran"
    for name in ("json", "typer"):
        (tmp_path / f"{name}.py").write_text(
            f"open({str(marker)!r}, 'w').write('planted')\nraise SystemExit(9)\n",
            encoding="utf-8",
        )
    assert doctor.import_check().ok
    assert smoke.run("ollama").ok
    assert not marker.exists()


def test_builtin_profiles_load_in_a_fixed_order(monkeypatch):
    # The Linux snapshot listed `general` before `default`: a directory's order is the file
    # system's, so the loader sorts.
    from prompt_workflow import prompt_builder

    class Entry:
        def __init__(self, name):
            self.name = name

        def read_text(self, encoding):
            return self.name

    class Folder:
        def iterdir(self):
            return iter([Entry("general.md"), Entry("zeta.md"), Entry("default.md")])

    monkeypatch.setattr(prompt_builder, "_PROMPT_DIR", Folder())
    assert list(prompt_builder._load_profiles()) == ["default", "general", "zeta"]
