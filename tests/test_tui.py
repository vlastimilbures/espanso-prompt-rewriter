"""The full-screen interface (#93), driven headless through Textual's Pilot: one test per
screen's main action, each through the same services the headless commands use. Espanso,
uv and brew run through a fake runner, the editor and the smoke test are replaced, and no
test calls a provider."""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
from collections.abc import Awaitable, Callable, Iterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from textual.pilot import Pilot
from textual.widgets import Button, Input, Select, Static, TabbedContent

from promptmend import config, config_store, deploy, doctor, history, smoke
from promptmend import profiles as profile_service
from promptmend.history import HistoryStore
from promptmend.tui import panes
from promptmend.tui.app import HIGH_CONTRAST, ManageApp
from promptmend.tui.home import pill
from promptmend.tui.modals import ConfirmModal, FormModal, TextModal
from promptmend.tui.previous import PreviousInstallScreen
from promptmend.tui.state import State, gather

if TYPE_CHECKING:
    from conftest import SeedHistory
    from textual.widget import Widget
    from typer.testing import Result

# Built at runtime, so no key-shaped literal lands in the repo (gitleaks).
KEY = "sk-or-v1-" + "cd34" * 16
SIZE = (120, 50)


class FakeRunner:
    def __init__(self, answers: dict[str, str | deploy.CommandFailure]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []
        # The Espanso folder the espanso fixture made (it answers `espanso path config`).
        self.root = Path()

    def __call__(self, argv: list[str]) -> str | deploy.CommandFailure | None:
        self.calls.append(list(argv))
        return self.answers.get(" ".join(argv))


@pytest.fixture
def espanso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
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
    launcher = tmp_path / "bin" / "promptmend"
    launcher.parent.mkdir()
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(deploy, "resolve_launcher", lambda **_: deploy.Launcher(launcher, "uv"))
    fake.root = root
    return fake


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Saved mode: config.toml and the secret store are read and written (per-test dirs)."""
    monkeypatch.delenv("PROMPTMEND_ENV")
    return config._user_config_dir()


async def settle(pilot: Pilot[int]) -> None:
    """Let every worker (the state load, an action) and what it set off finish."""
    for _ in range(4):
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def drive(scenario: Callable[[ManageApp, Pilot[int]], Awaitable[None]], **kwargs: Any) -> ManageApp:
    # No intro unless a test asks for one (#112): it would sit over every screen until a key.
    kwargs.setdefault("intro", False)
    app = ManageApp(**kwargs)

    async def main() -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await scenario(app, pilot)

    asyncio.run(main())
    return app


def _state(app: ManageApp) -> State:
    assert app.state is not None
    return app.state


def _dialog(app: ManageApp) -> ConfirmModal | FormModal | TextModal:
    screen = app.screen
    assert isinstance(screen, ConfirmModal | FormModal | TextModal)
    return screen


def _previous_screen(app: ManageApp) -> PreviousInstallScreen:
    screen = app.screen
    assert isinstance(screen, PreviousInstallScreen)
    return screen


def _plan() -> deploy.Plan:
    plan = gather().plan
    assert plan is not None
    return plan


def pane(app: ManageApp, tab: str) -> panes.Pane:
    return app.main.query_one(f"#{tab}-pane", panes.Pane)


def table_rows(app: ManageApp, tab: str, selector: str) -> dict[str, list[str]]:
    """A DataTable's rows by their first cell, from its data: a rendered line can lag (#118)."""
    from textual.widgets import DataTable

    table = pane(app, tab).query_one(selector, DataTable)
    rows = (table.get_row_at(i) for i in range(table.row_count))
    found = {row[0].plain: [cell.plain for cell in row] for row in rows}
    assert len(found) == table.row_count, "two rows share a first cell"
    return found


async def press(app: ManageApp, pilot: Pilot[int], selector: str) -> None:
    app.screen.query_one(selector, Button).press()
    await settle(pilot)


async def fill(app: ManageApp, pilot: Pilot[int], **values: str) -> None:
    """Set a dialog's fields by id and submit it."""
    for field_id, value in values.items():
        widget = app.screen.query_one(f"#{field_id.replace('_', '-')}")
        assert isinstance(widget, Input | Select)
        widget.value = value
    await pilot.pause()
    await press(app, pilot, "#submit")


@pytest.fixture
def record_ops(seed_history: SeedHistory) -> Callable[..., None]:
    """record_ops(n) seeds n recorded -i- calls to OpenRouter."""

    def record(n: int = 2) -> None:
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
            seed_history(op, [attempt])

    return record


# --- app, keys, theme ---------------------------------------------------------------------


def test_digits_switch_tabs_and_t_toggles_contrast(espanso: FakeRunner) -> None:
    seen = []

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        from textual.widgets import TabbedContent

        for key, tab in zip(
            "7654321",
            ("try", "diagnostics", "history", "triggers", "profiles", "providers", "home"),
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
    assert seen == [True] * 9


def test_header_shows_the_name_and_installed_version(
    espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#112: the header names the tool and its version on every screen."""
    from textual.widgets._header import HeaderTitle

    from promptmend.tui import app as app_module

    monkeypatch.setattr(app_module, "__version__", "9.8.7")
    seen = []

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        seen.append(str(app.main.query_one(HeaderTitle).render()))
        await pilot.press("6")
        seen.append(str(app.main.query_one(HeaderTitle).render()))
        assert app.state is not None
        seen.append(pill(app.state.report))

    drive(scenario)
    # Then the status pill (#112), first so a narrow terminal cuts the tagline, not the status
    # (#174): the espanso fixture leaves the key unset, a problem.
    assert "problem" in seen[-1]
    assert seen[:2] == [f"PromptMend 9.8.7 — {seen[-1]} · set up and manage"] * 2


def test_a_failing_load_is_shown_not_raised(espanso: FakeRunner) -> None:
    def broken(group_by: str) -> State:
        raise RuntimeError("boom")

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
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
def test_cancelling_a_dialog_changes_nothing(
    saved: Path, espanso: FakeRunner, tab: str, button: str
) -> None:
    config_store.save_secret("OPENROUTER_API_KEY", KEY)
    before = sorted((p, p.read_bytes()) for p in saved.rglob("*") if p.is_file())

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press(tab)
        await press(app, pilot, button)
        assert isinstance(app.screen, FormModal)
        await pilot.press("escape")
        await settle(pilot)
        assert not isinstance(app.screen, FormModal)

    drive(scenario)
    assert sorted((p, p.read_bytes()) for p in saved.rglob("*") if p.is_file()) == before


def test_no_color_is_left_to_textual(monkeypatch: pytest.MonkeyPatch, espanso: FakeRunner) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        assert app.no_color

    drive(scenario)


# --- Home ---------------------------------------------------------------------------------


def _rendered(widget: Widget, selector: str, width: int = 116) -> str:
    """What a Static shows, as plain text at ``width`` columns (Home's grids are Rich
    tables, not Text)."""
    from rich.console import Console

    console = Console(width=width, record=True, color_system=None, file=io.StringIO())
    console.print(widget.query_one(selector, Static).content)
    return console.export_text()


def test_home_shows_doctor_and_checks_again(espanso: FakeRunner) -> None:
    calls: list[str] = []

    def loader(group_by: str) -> State:
        calls.append(group_by)
        return gather(group_by)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        home = app.main.query_one("#home-pane", panes.HomePane)
        rows = {row.label: row for row in home.rows}
        assert rows["Triggers"].text.endswith("Espanso running")
        assert (rows["Rewrites"].status, rows["Rewrites"].detail) == ("fail", "key not set")
        assert rows["Checks"].problem == (
            "keys: PROMPT_PROVIDER is openrouter but OPENROUTER_API_KEY is not set"
        )
        shown = _rendered(home, "#home-headline") + _rendered(home, "#home-rows")
        assert "Not ready: OPENROUTER_API_KEY is not set, so -i- cannot rewrite." in shown
        assert "fail  Rewrites" not in shown  # the label is FAIL, as in Diagnostics
        assert "FAIL  Rewrites" in shown
        assert "-> 2 Providers" in shown
        await press(app, pilot, "#home-reload")
        await pilot.press("r")
        await settle(pilot)

    drive(scenario, loader=loader)
    assert calls == ["trigger"] * 3
    # Doctor never read the clipboard, and only read-only Espanso commands ran.
    assert {" ".join(c[:2]) for c in espanso.calls} <= {"espanso path", "espanso status"}


# A persona matching the gate's patterns is a warning on Home and in Diagnostics, by finding
# label only (#29 B).
def test_home_warns_of_a_persona_matching_the_patterns(
    espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    address = "jane.doe" + "@" + "example.com"
    monkeypatch.setenv("PROMPT_PERSONA", f"I am an analyst, mail {address}.")
    warning = (
        "persona: PROMPT_PERSONA matches the data-protection patterns: email; it is sent "
        "unscanned with every cloud call"
    )

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        home = _rendered(pane(app, "home"), "#home-rows")
        home_pane = app.main.query_one("#home-pane", panes.HomePane)
        checks = {row.label: row for row in home_pane.rows}["Checks"]
        assert checks.status == "fail"  # the missing key; the persona is the warning
        assert " 0 warn" not in checks.text
        everything = str(pane(app, "diagnostics").query_one("#all-checks").render())
        assert warning in everything
        assert address not in home + everything

    drive(scenario)


# --- Providers ----------------------------------------------------------------------------


def test_providers_set_a_key_never_shows_it(saved: Path, espanso: FakeRunner) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("2")
        await press(app, pilot, "#set-key")
        assert isinstance(app.screen, FormModal)
        assert app.screen.query_one("#key-value", Input).password
        await fill(app, pilot, key_name="OPENROUTER_API_KEY", key_value=f"  {KEY} ")
        assert KEY not in app.export_screenshot()
        assert table_rows(app, "providers", "#keys")["OPENROUTER_API_KEY"][1] == "set"
        assert KEY not in pane(app, "providers").last_message

    drive(scenario)
    assert config_store.saved_secret_names() == ("OPENROUTER_API_KEY",)
    assert KEY in (saved / "secrets.toml").read_text("utf-8")


def test_providers_refuse_an_empty_key_and_legacy_mode(espanso: FakeRunner) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
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
        assert message.startswith("error: PROMPTMEND_ENV is set")
        assert KEY not in message
        await press(app, pilot, "#remove-key")
        assert "PROMPTMEND_ENV is set" in pane(app, "providers").last_message

    drive(scenario)


def test_providers_remove_a_key_after_confirming(saved: Path, espanso: FakeRunner) -> None:
    config_store.save_secret("ANTHROPIC_API_KEY", KEY)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
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


def test_providers_remove_a_key_still_set_elsewhere(
    saved: Path, espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_store.save_secret("OPENROUTER_API_KEY", KEY)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("2")
        await press(app, pilot, "#remove-key")
        await fill(app, pilot)
        await press(app, pilot, "#confirm")
        assert pane(app, "providers").last_message.endswith("it is still set, from environment.")

    drive(scenario)


def test_an_unexpected_error_is_shown_not_raised(
    saved: Path, espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bug(name: str, value: str) -> None:
        raise RuntimeError("kaput")

    monkeypatch.setattr(config_store, "save_secret", bug)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("2")
        await press(app, pilot, "#set-key")
        await fill(app, pilot, key_value=KEY)
        message = pane(app, "providers").last_message
        assert message == "error: unexpected RuntimeError: kaput"

    drive(scenario)


def test_providers_migrate_reports_a_refusal(
    saved: Path, espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(environ: Mapping[str, str] | None = None) -> None:
        raise config_store.MigrationError("the marker is damaged")

    monkeypatch.setattr(config_store, "plan_migration", refused)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("2")
        await press(app, pilot, "#migrate-env")
        assert pane(app, "providers").last_message == "error: the marker is damaged"

    drive(scenario)


def test_providers_change_a_setting_and_reload(
    saved: Path, espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMPT_PROFILE", "general")

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("2")
        await press(app, pilot, "#set-setting")
        options = app.screen.query_one("#setting-name", Select)._options
        assert "PROMPT_PERSONA" not in [value for _, value in options]
        await fill(app, pilot, setting_name="PROMPT_LOCAL_ONLY", setting_value="true")
        assert _state(app).settings.local_only
        routes = table_rows(app, "providers", "#routes")
        assert routes["openrouter"][3] == routes["anthropic"][3] == "refused (local only)"
        assert routes["ollama"][3] == "stays local"
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


# A change that makes a flagged persona go out says so, as `config set` does: the finding
# names only, and the setting stays saved.
def test_providers_change_warns_about_a_flagged_persona(
    saved: Path, espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    address = "jane.doe" + "@" + "example.com"
    monkeypatch.setenv("PROMPT_PERSONA", f"I am an analyst, mail {address}.")

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("2")
        await press(app, pilot, "#set-setting")
        await fill(app, pilot, setting_name="PROMPT_PROFILE", setting_value="general")
        assert "PROMPT_PERSONA" not in pane(app, "providers").last_message
        await press(app, pilot, "#set-setting")
        await fill(app, pilot, setting_name="PROMPT_PROFILE", setting_value="default")
        message = pane(app, "providers").last_message
        assert message.startswith("PROMPT_PROFILE saved in ")
        assert "PROMPT_PERSONA matches the data-protection patterns: email" in message
        assert address not in message

    drive(scenario)
    assert 'PROMPT_PROFILE = "default"' in (saved / "config.toml").read_text("utf-8")


def test_providers_migrate_env_with_preview(saved: Path, espanso: FakeRunner) -> None:
    saved.mkdir(parents=True)
    (saved / ".env").write_text(
        f"PROMPT_PROFILE=general\nOPENROUTER_API_KEY={KEY}\n", encoding="utf-8"
    )

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("2")
        await press(app, pilot, "#migrate-env")
        assert isinstance(app.screen, ConfirmModal)
        assert KEY not in _dialog(app).preview
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


def test_providers_test_call_uses_the_stub_service(
    espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = []

    def fake_run(provider: str) -> smoke.SmokeResult:
        called.append(provider)
        return smoke.SmokeResult(True, smoke.REPLY, 1, "improve reached the stub")

    monkeypatch.setattr(smoke, "run", fake_run)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("2")
        await press(app, pilot, "#smoke")
        assert "No provider is called" in _dialog(app).preview
        await press(app, pilot, "#cancel")
        assert called == []
        await press(app, pilot, "#smoke")
        await fill(app, pilot, provider="ollama")
        assert pane(app, "providers").last_message == "ok: improve reached the stub"

    drive(scenario)
    assert called == ["ollama"]


def test_providers_test_call_adds_no_history_row(
    espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#116: the real Test call (a child improve against the stub) is not usage."""
    monkeypatch.setenv("PROMPT_HISTORY", "true")
    store = HistoryStore(history.history_path())

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("2")
        await press(app, pilot, "#smoke")
        await fill(app, pilot, provider="ollama")
        for _ in range(100):  # the child process runs in a worker
            if pane(app, "providers").last_message.startswith("ok: "):
                break
            await asyncio.sleep(0.1)
            await settle(pilot)
        assert pane(app, "providers").last_message.startswith("ok: improve reached the stub")

    drive(scenario)
    assert store.health().operations in (None, 0)


# --- Profiles -----------------------------------------------------------------------------


def _user_profile(name: str = "mine") -> Path:
    from promptmend.prompt_builder import user_profiles_dir

    folder = user_profiles_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.md"
    path.write_text("Rewrite the draft.", encoding="utf-8")
    return path


def test_profiles_set_default_and_edit_in_editor(
    saved: Path, espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _user_profile()
    edited = []

    def editor(target: Path) -> int:
        edited.append(target)
        return 0

    monkeypatch.setattr(panes, "run_editor", editor)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        from contextlib import nullcontext

        from textual.widgets import DataTable

        # the headless driver cannot hand the terminal over
        monkeypatch.setattr(app, "suspend", nullcontext)
        await pilot.press("3")
        await press(app, pilot, "#edit-profile")
        assert "Select one of your profiles" in pane(app, "profiles").last_message
        table = pane(app, "profiles").query_one("#profiles", DataTable)
        table.move_cursor(row=table.get_row_index("user:mine"))
        await press(app, pilot, "#edit-profile")
        assert pane(app, "profiles").last_message == "mine.md: the editor exited with 0"
        await press(app, pilot, "#set-profile")
        await fill(app, pilot, profile="mine")
        assert _state(app).settings.profile == "mine"

    drive(scenario)
    assert edited == [path]
    assert 'PROMPT_PROFILE = "mine"' in (saved / "config.toml").read_text("utf-8")


def test_profiles_editor_unsupported_terminal(
    espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _user_profile()
    monkeypatch.setattr(panes, "run_editor", lambda target: pytest.fail("editor ran"))

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        from textual.widgets import DataTable

        table = pane(app, "profiles").query_one("#profiles", DataTable)
        table.move_cursor(row=table.get_row_index("user:mine"))
        await pilot.press("3")
        await press(app, pilot, "#edit-profile")
        assert "cannot hand over" in pane(app, "profiles").last_message

    drive(scenario)


def test_editor_command() -> None:
    def nowhere(name: str) -> None:
        return None

    assert panes.editor_command({}, windows=False, which=nowhere) == ["vi"]
    assert panes.editor_command({}, windows=True, which=nowhere) == ["notepad"]
    both = {"VISUAL": "vim", "EDITOR": "code --wait"}
    assert panes.editor_command(both, windows=False, which=nowhere) == ["vim"]


def test_profiles_migrate_copies_after_confirming(
    espanso: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "project" / profile_service.PROMPTS_PATH
    source.mkdir(parents=True)
    (source / "general.md").write_text("Edited.", encoding="utf-8")
    (source / "extra.md").write_text("Mine.", encoding="utf-8")
    monkeypatch.setattr(
        profile_service, "git_pristine_profiles", lambda root, rev=None: {"general": "Old."}
    )

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("3")
        await press(app, pilot, "#migrate-profiles")
        assert "extra.md (added)" in _dialog(app).preview
        await press(app, pilot, "#confirm")
        assert pane(app, "profiles").last_message == (
            "extra: copied\ngeneral: copied\nYour general.md replaces the built-in only once "
            "PROMPT_PROFILE_OVERRIDES lists it: "
            "`promptmend config set PROMPT_PROFILE_OVERRIDES general`."
        )

    drive(scenario)
    from promptmend.prompt_builder import user_profiles_dir

    assert sorted(p.name for p in user_profiles_dir().iterdir()) == ["extra.md", "general.md"]


def test_profiles_migrate_default_and_nothing_to_copy(
    espanso: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "project" / profile_service.PROMPTS_PATH
    source.mkdir(parents=True)
    (source / "default.md").write_text("Edited.", encoding="utf-8")
    pristine = {"default": "Old."}
    monkeypatch.setattr(profile_service, "git_pristine_profiles", lambda root, rev=None: pristine)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("3")
        await press(app, pilot, "#migrate-profiles")
        await press(app, pilot, "#cancel")
        assert pane(app, "profiles").last_message == ""
        await press(app, pilot, "#migrate-profiles")
        await press(app, pilot, "#confirm")
        assert "PROMPT_PROFILE_OVERRIDES default`" in pane(app, "profiles").last_message
        pristine["default"] = "Edited."
        await press(app, pilot, "#migrate-profiles")
        assert pane(app, "profiles").last_message == profile_service.NOTHING_CHANGED

    drive(scenario)


def test_profiles_editor_that_cannot_start(
    espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _user_profile()

    def missing(target: Path) -> int:
        raise FileNotFoundError("no-such-editor")

    monkeypatch.setattr(panes, "run_editor", missing)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        from contextlib import nullcontext

        from textual.widgets import DataTable

        monkeypatch.setattr(app, "suspend", nullcontext)
        await pilot.press("3")
        table = pane(app, "profiles").query_one("#profiles", DataTable)
        table.move_cursor(row=table.get_row_index("user:mine"))
        await press(app, pilot, "#edit-profile")
        assert "could not start the editor" in pane(app, "profiles").last_message

    drive(scenario)


def test_run_editor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import subprocess

    seen: list[list[str]] = []

    class Done:
        returncode = 0

    def run(argv: list[str], check: bool) -> Done:
        seen.append(argv)
        return Done()

    monkeypatch.setenv("VISUAL", "myeditor -w")
    monkeypatch.setattr(subprocess, "run", run)
    assert panes.run_editor(tmp_path / "mine.md") == 0
    assert seen == [["myeditor", "-w", str(tmp_path / "mine.md")]]


def test_profiles_migrate_without_a_checkout(espanso: FakeRunner) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("3")
        await press(app, pilot, "#migrate-profiles")
        assert "no src/promptmend/prompts" in pane(app, "profiles").last_message

    drive(scenario)


# --- Triggers -----------------------------------------------------------------------------


def test_triggers_say_when_espanso_cannot_name_its_folder(espanso: FakeRunner) -> None:
    """#115: deploy and detach then use the default folder; the Triggers tab says so."""
    espanso.answers["espanso path config"] = deploy.CommandFailure(
        found=True, returncode=101, error="unable to load config"
    )

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("4")
        target = str(pane(app, "triggers").query_one("#deploy-target").render())
        assert "`espanso path config` failed (exit 101): unable to load config" in target
        assert "using the default folder" in target

    drive(scenario)


def test_triggers_show_fixed_providers_and_deploy(espanso: FakeRunner) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("4")
        rows = table_rows(app, "triggers", "#triggers")
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
        states = {row[4] for row in table_rows(app, "triggers", "#triggers").values()}
        assert states == {"in sync", "commented out"}
        await press(app, pilot, "#deploy")
        assert pane(app, "triggers").last_message == "Nothing to do: every match file is in sync."
        await press(app, pilot, "#show-diff")
        assert isinstance(app.screen, TextModal)
        assert _dialog(app).preview == "Every match file is in sync."
        await press(app, pilot, "#close")

    drive(scenario)
    assert sorted(p.name for p in (espanso.root / "match").iterdir()) == sorted(
        s.name for s in _plan().steps
    )
    assert ["espanso", "restart"] in espanso.calls


def test_triggers_deploy_keeps_an_edited_file_by_default(espanso: FakeRunner) -> None:
    target = espanso.root / "match" / "prompts-core.yml"
    target.write_text("matches: []  # mine\n", encoding="utf-8")

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("4")
        await press(app, pilot, "#show-diff")
        assert "# mine" in _dialog(app).preview
        await pilot.press("escape")
        await press(app, pilot, "#deploy")
        assert app.screen.query_one("#choice-0", Select).value == deploy.KEEP
        await press(app, pilot, "#submit")
        assert "WARNING: kept as you have them and NOT updated: prompts-core.yml" in (
            pane(app, "triggers").last_message
        )

    drive(scenario)
    assert target.read_text("utf-8") == "matches: []  # mine\n"


def test_triggers_deploy_side_by_side(espanso: FakeRunner) -> None:
    target = espanso.root / "match" / "prompts-core.yml"
    target.write_text("matches: []  # mine\n", encoding="utf-8")

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("4")
        await press(app, pilot, "#deploy")
        await fill(app, pilot, choice_0=deploy.SIDE)

    drive(scenario)
    assert target.read_text("utf-8") == "matches: []  # mine\n"
    assert (target.parent / f"prompts-core.yml{deploy.SIDE_SUFFIX}").is_file()


def test_triggers_detach_after_confirming(espanso: FakeRunner) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("4")
        await press(app, pilot, "#detach")
        assert "no deployed match files" in pane(app, "triggers").last_message
        await press(app, pilot, "#deploy")
        await press(app, pilot, "#submit")
        await press(app, pilot, "#detach")
        assert "prompts-llm.yml" in _dialog(app).preview
        await press(app, pilot, "#cancel")
        assert (espanso.root / "match" / "prompts-llm.yml").is_file()
        await press(app, pilot, "#detach")
        await press(app, pilot, "#submit")
        assert "removed" in pane(app, "triggers").last_message

    drive(scenario)
    left = sorted(p.name for p in (espanso.root / "match").iterdir())
    assert left == ["prompts-core.yml"]  # --keep-static: the static snippets stay


def test_triggers_report_failures(espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    del espanso.answers["espanso restart"]  # neither restart nor start succeeds

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("4")
        await press(app, pilot, "#deploy")
        await press(app, pilot, "#submit")
        message = pane(app, "triggers").last_message
        assert "Could not restart Espanso; run `espanso restart` yourself." in message
        await press(app, pilot, "#detach")
        await press(app, pilot, "#submit")
        assert "Could not restart Espanso" in pane(app, "triggers").last_message

        def fail(*args: Any, **kwargs: Any) -> None:
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


def test_triggers_deploy_retires_the_legacy_file(espanso: FakeRunner) -> None:
    legacy = espanso.root / "match" / "base.yml"
    legacy.write_text('matches:\n  - trigger: "-p-"  # promptmend\n', encoding="utf-8")

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("4")
        await press(app, pilot, "#deploy")
        assert "legacy    base.yml will be retired" in _dialog(app).preview
        await press(app, pilot, "#submit")
        assert "retired legacy" in pane(app, "triggers").last_message

    drive(scenario)
    assert not legacy.exists()


def test_triggers_without_a_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy, "run_command", lambda argv: None)

    def no_launcher(**_: Any) -> None:
        raise deploy.DeployError("Could not find a stable promptmend launcher")

    monkeypatch.setattr(deploy, "resolve_launcher", no_launcher)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("4")
        text = str(pane(app, "triggers").query_one("#deploy-target").render())
        assert text.startswith("Cannot compare with Espanso: Could not find"), (text, app.state)
        await press(app, pilot, "#show-diff")
        assert "No plan" in pane(app, "triggers").last_message
        await press(app, pilot, "#deploy")
        assert pane(app, "triggers").last_message.startswith("error: Could not find")

    drive(scenario)


# --- History ------------------------------------------------------------------------------


def test_history_stats_export_prune_reset(
    espanso: FakeRunner, tmp_path: Path, record_ops: Callable[..., None]
) -> None:
    record_ops()
    out = tmp_path / "out.json"

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
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
        assert _state(app).group_by == "provider"
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
        assert _state(app).stats
        await press(app, pilot, "#reset")
        await press(app, pilot, "#confirm")
        assert history_pane.last_message == "The usage history is empty."
        assert not _state(app).stats
        assert "No usage recorded yet." in str(history_pane.query_one("#stats-note").render())

    drive(scenario)
    assert len(json.loads(out.read_text("utf-8"))["operations"]) == 2


def test_cost_badges() -> None:
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


def test_history_export_needs_a_path(espanso: FakeRunner) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("5")
        await press(app, pilot, "#export")
        await fill(app, pilot, path=" ")
        assert pane(app, "history").last_message == "error: enter the file to write"

    drive(scenario)


def test_history_unreadable_store(espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: HistoryStore, group_by: str = "trigger") -> None:
        raise history.HistoryError("the database is damaged")

    monkeypatch.setattr(HistoryStore, "stats", broken)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        note = str(pane(app, "history").query_one("#stats-note").render())
        assert note == "error: the database is damaged"

    drive(scenario)


# --- Diagnostics --------------------------------------------------------------------------


def test_diagnostics_provenance_and_import_check(
    espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setattr(
        doctor, "import_check", lambda: doctor.ImportCheck(True, "40 ms, 200 module(s)")
    )

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("6")
        diagnostics = pane(app, "diagnostics")
        rows = table_rows(app, "diagnostics", "#settings")
        assert rows["OPENROUTER_API_KEY"][1:3] == [f"<set, {len(KEY)} chars>", "environment"]
        assert KEY not in app.export_screenshot()
        store = str(diagnostics.query_one("#store").render())
        assert "Lost history writes: 0" in store
        assert "SQLite" in store
        await press(app, pilot, "#import-check")
        assert diagnostics.last_message == "40 ms, 200 module(s)"

    drive(scenario)


def test_diagnostics_on_a_broken_config(espanso: FakeRunner, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PROMPT_LOCAL_ONLY=maybe\n", encoding="utf-8")

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
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


def test_files_under_home_show_as_tilde(
    monkeypatch: pytest.MonkeyPatch, espanso: FakeRunner
) -> None:
    # #174: display only, so the From column keeps the file name on a narrow terminal.
    monkeypatch.delenv("PROMPTMEND_ENV")
    # The config dir is $XDG_CONFIG_HOME on POSIX and %APPDATA% on Windows.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    monkeypatch.setenv("APPDATA", str(Path.home() / ".config"))
    config_store.save_secret("OPENROUTER_API_KEY", KEY)
    folder = config_store.config_dir()
    (folder / "config.toml").write_text('PROMPT_PROFILE = "general"\n', encoding="utf-8")
    monkeypatch.setenv("PROMPT_PROFILE", "default")
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        keys = table_rows(app, "providers", "#keys")
        assert keys["OPENROUTER_API_KEY"][2:4] == [
            "environment",
            "~/.config/promptmend/secrets.toml",
        ]
        await pilot.press("6")
        rows = table_rows(app, "diagnostics", "#settings")
        assert rows["PROMPT_PROFILE"][2:4] == [
            "environment",
            "overrides ~/.config/promptmend/config.toml",
        ]
        assert rows["PROMPT_HISTORY"][2] == "default"

    drive(scenario)


def test_short_label_only_shortens_files_under_home(monkeypatch: pytest.MonkeyPatch) -> None:
    from promptmend.commands import common

    home = Path.home()
    assert common.short_label(f"file:{home / 'a' / 'b.toml'}") == "~/a/b.toml"
    assert common.short_label(f"file:{home}") == "~"
    elsewhere = str(Path("/elsewhere/config.toml").resolve())
    assert common.short_label(f"file:{elsewhere}") == elsewhere
    assert common.short_label(config.ENV_SOURCE) == "environment"
    assert common.short_label(config.DEFAULT_SOURCE) == "default"


def test_import_check_runs_a_fresh_interpreter() -> None:
    found = doctor.import_check()
    assert found.ok, found.message
    assert found.modules
    assert found.seconds is not None
    assert "no heavy module" in found.message


def test_import_check_reports_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def timeout(*args: Any, **kwargs: Any) -> None:
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


def test_routes_classify_each_provider() -> None:
    from promptmend.config import Settings
    from promptmend.factory import routes

    cfg = Settings(ollama_model="gpt-oss:120b-cloud", local_only=True)
    found = {r.name: r for r in routes(cfg)}
    assert (found["ollama"].remote, found["ollama"].refused) == (True, True)
    assert (found["lmstudio"].remote, found["lmstudio"].refused) == (False, False)
    assert found["openrouter"].key == "OPENROUTER_API_KEY"
    assert found["lmstudio"].key is None


def test_triggers_match_the_match_files() -> None:
    import yaml

    from promptmend import assets

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
def terminal(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Make stdin and stdout read as terminals, each switchable, and record each launch."""
    from promptmend.commands import common

    state: dict[str, Any] = {"stdin": True, "stdout": True, "launched": 0, "code": None}
    monkeypatch.setattr(common, "stdin_is_tty", lambda: state["stdin"])
    monkeypatch.setattr(common, "stdout_is_tty", lambda: state["stdout"])

    def run(self: ManageApp, *args: Any, **kwargs: Any) -> None:
        state["launched"] += 1
        state["intro"] = self.intro
        self._return_code = state["code"]

    monkeypatch.setattr(ManageApp, "run", run)
    return state


def _invoke(*args: str) -> Result:
    from typer.testing import CliRunner

    from promptmend.cli import app

    return CliRunner().invoke(app, list(args))


EOL = os.linesep.encode()
GOLDEN = Path(__file__).parent / "golden" / "no-args-help.txt"


def _words(text: str) -> str:
    """Help text without colour, box drawing, the program name or line wrapping, which
    differ by terminal width, CI and OS."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = re.sub(r"Usage: .*? \[OPTIONS\]", "Usage: promptmend [OPTIONS]", text)
    # Every box-drawing character: Windows draws square corners where macOS draws round ones.
    return " ".join(re.sub(r"[\u2500-\u257f|+]", " ", text).split())


def test_words_ignore_the_border_style() -> None:
    rounded = "╭─ Options ─╮\n│ --help │\n╰───────────╯"
    square = "┌─ Options ─┐\n│ --help │\n└───────────┘"
    assert _words(rounded) == _words(square) == "Options --help"


def test_bare_command_without_a_terminal_prints_the_help_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
def test_bare_command_needs_both_streams_on_a_terminal(
    terminal: dict[str, Any], stdin: bool, stdout: bool
) -> None:
    terminal.update(stdin=stdin, stdout=stdout)
    result = _invoke()
    assert result.exit_code == 2
    assert "Usage:" in result.stdout
    assert terminal["launched"] == 0


@pytest.mark.parametrize("args", [(), ("ui",)])
@pytest.mark.parametrize("code", [None, 0, 1])
def test_terminal_opens_the_interface_and_exits_with_its_code(
    terminal: dict[str, Any], args: tuple[str, ...], code: int | None
) -> None:
    terminal["code"] = code
    result = _invoke(*args)
    assert terminal["launched"] == 1
    assert result.exit_code == (code or 0)
    assert result.stdout == ""


def test_ui_without_a_terminal_exits_3(terminal: dict[str, Any]) -> None:
    from promptmend.commands.common import NEEDS_TERMINAL

    terminal["stdout"] = False
    result = _invoke("ui")
    assert result.exit_code == NEEDS_TERMINAL == 3
    assert result.stderr.startswith("error: the interface needs a terminal")
    assert terminal["launched"] == 0


# The real process with pipes, not a terminal: the bare command prints the same bytes as
# --help and exits 2, as no_args_is_help always did.
def test_bare_command_in_a_real_process(tmp_path: Path) -> None:
    import subprocess
    import sys

    env = {**os.environ, "COLUMNS": "100", "PROMPTMEND_ENV": str(tmp_path / ".env")}
    env.pop("GITHUB_ACTIONS", None)

    def run(*args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-m", "promptmend.cli", *args],
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


def test_providers_never_show_credentials_in_a_base_url(
    espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "hunter" + "2pass"
    url = f"https://bob:{secret}@llm.example.com/v1"
    monkeypatch.setenv("OPENROUTER_BASE_URL", url)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("2")
        assert secret not in app.export_screenshot()
        await pilot.press("6")
        assert secret not in app.export_screenshot()

    drive(scenario)


def test_an_older_slower_load_never_overwrites_a_newer_one(espanso: FakeRunner) -> None:
    import threading

    release, started = threading.Event(), threading.Event()
    loaded: list[int] = []

    def loader(group_by: str) -> State:
        n = len(loaded)
        loaded.append(n)
        if n == 1:  # the second load is slow and finishes last
            started.set()
            release.wait(10)
        state = gather(group_by)
        object.__setattr__(state, "plan_error", f"load {n}")
        return state

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        app.reload()
        while not started.is_set():
            await pilot.pause(0.01)
        app.reload()
        await settle(pilot)
        assert _state(app).plan_error == "load 2"
        release.set()
        for _ in range(20):
            await pilot.pause(0.02)
        assert _state(app).plan_error == "load 2"

    drive(scenario, loader=loader)
    assert loaded == [0, 1, 2]


def test_a_failing_worker_is_reported_not_raised(
    espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bug(provider: str) -> smoke.SmokeResult:
        raise RuntimeError("kaput")

    monkeypatch.setattr(smoke, "run", bug)

    def bad_apply(*args: Any, **kwargs: Any) -> None:
        raise ValueError("bad choice")

    monkeypatch.setattr(deploy, "apply", bad_apply)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
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


def test_deploy_twice_opens_one_dialog(espanso: FakeRunner) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
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


def test_deploy_refuses_a_plan_that_changed_since_the_preview(espanso: FakeRunner) -> None:
    target = espanso.root / "match" / "prompts-core.yml"

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("4")
        await press(app, pilot, "#deploy")
        target.write_text("matches: []  # written meanwhile\n", encoding="utf-8")
        await press(app, pilot, "#submit")
        assert "changed since the preview" in pane(app, "triggers").last_message

    drive(scenario)
    assert target.read_text("utf-8") == "matches: []  # written meanwhile\n"
    assert not (espanso.root / "match" / "prompts-llm.yml").exists()


def test_detach_refuses_a_manifest_that_changed_since_the_preview(espanso: FakeRunner) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
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


def test_prune_enter_does_not_prune(espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    pruned: list[int | None] = []

    def prune(self: HistoryStore, age: int | None = None) -> int:
        pruned.append(age)
        return 0

    monkeypatch.setattr(HistoryStore, "prune", prune)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
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
def test_editor_command_forms(windows: bool, value: str, expected: list[str]) -> None:
    found = {"code": "/bin/code"} if not windows else {"code": "C:/bin/code.cmd"}
    command = panes.editor_command(
        {"EDITOR": value}, windows=windows, which=lambda name: found.get(name)
    )
    assert command == expected


def test_children_ignore_modules_planted_in_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_builtin_profiles_load_in_a_fixed_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # The Linux snapshot listed `general` before `default`: a directory's order is the file
    # system's, so the loader sorts.
    from promptmend import prompt_builder

    class Entry:
        def __init__(self, name: str) -> None:
            self.name = name

        def read_text(self, encoding: str) -> str:
            return self.name

    class Folder:
        def iterdir(self) -> Iterator[Entry]:
            return iter([Entry("general.md"), Entry("zeta.md"), Entry("default.md")])

    monkeypatch.setattr(prompt_builder, "_PROMPT_DIR", Folder())
    assert list(prompt_builder._load_profiles()) == ["default", "general", "zeta"]


# --- Previous install (#110) --------------------------------------------------------------


def _old_checkout(tmp_path: Path, espanso: FakeRunner) -> Path:
    """A checkout an editable install ran from: a .env with one setting and the key, an
    edited profile, and the match files rendered with its .venv launcher."""
    from promptmend import assets, previous_install

    root = tmp_path / "Projects" / "epr"
    (root / profile_service.PROMPTS_PATH).mkdir(parents=True)
    name = previous_install.PROJECT_NAME
    (root / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n', "utf-8")
    (root / ".env").write_text(f"PROMPT_TIMEOUT_SECONDS=45\nOPENROUTER_API_KEY={KEY}\n", "utf-8")
    (root / profile_service.PROMPTS_PATH / "mine.md").write_text("Mine.\n", "utf-8")
    if os.name == "nt":
        launcher = (root / ".venv" / "Scripts" / "promptmend.exe").as_posix()
    else:
        launcher = str(root / ".venv" / "bin" / "promptmend")
    for match in assets.match_names():
        text = assets.read_match(match).replace(deploy.PLACEHOLDER, launcher)
        (espanso.root / "match" / match).write_bytes(text.encode("utf-8"))
    return root


@pytest.fixture
def previous(
    saved: Path, espanso: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:

    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    return _old_checkout(tmp_path, espanso)


def _found(app: ManageApp) -> str:
    return str(app.screen.query_one("#previous-found").render())


def _disabled(app: ManageApp, step: str) -> bool:
    return app.screen.query_one(f"#previous-{step}", Button).disabled


def test_previous_install_walks_copy_deploy_retire(previous: Path) -> None:
    from promptmend.tui.previous import PreviousInstallScreen

    root = previous.resolve()

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        assert isinstance(app.screen, PreviousInstallScreen)
        assert f"Previous install: {root} (launcher); .env present" in _found(app)
        assert _disabled(app, "retire")
        await press(app, pilot, "#previous-copy")
        assert isinstance(app.screen, ConfirmModal)
        assert "OPENROUTER_API_KEY" in _dialog(app).preview
        assert KEY not in _dialog(app).preview
        await press(app, pilot, "#confirm")
        assert _previous_screen(app).last_message.startswith("Copied; backup in")
        assert f"Settings copied from {root}" in _found(app)
        assert _disabled(app, "copy")
        assert _disabled(app, "retire")  # the match files still run the checkout's CLI
        await press(app, pilot, "#previous-deploy")
        assert isinstance(app.screen, FormModal)
        await press(app, pilot, "#submit")
        assert isinstance(app.screen, PreviousInstallScreen)
        assert not _disabled(app, "retire")
        await press(app, pilot, "#previous-retire")
        assert isinstance(app.screen, ConfirmModal)
        await press(app, pilot, "#confirm")
        assert _previous_screen(app).last_message.startswith(f"Retired: {root / '.env'}")

    drive(scenario)
    assert not (previous / ".env").exists()
    from promptmend.config import Settings

    settings = Settings.load()
    assert settings.timeout == 45
    assert settings.openrouter_api_key == KEY


def test_previous_install_cancel_writes_nothing_and_deploy_warns(previous: Path) -> None:
    before = {p.name: p.read_bytes() for p in previous.parent.parent.rglob("*.yml")}

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("1")
        await settle(pilot)
        assert isinstance(app.screen, ConfirmModal)
        await press(app, pilot, "#cancel")
        await pilot.press("4")  # retire is disabled: the key does nothing
        await settle(pilot)
        assert not isinstance(app.screen, ConfirmModal)
        await press(app, pilot, "#previous-deploy")
        assert isinstance(app.screen, ConfirmModal)
        assert "not copied yet" in app.screen.dialog_title
        assert app.screen.focused.id == "cancel"
        await press(app, pilot, "#cancel")

    drive(scenario)
    assert not config.settings_file().exists()
    assert not (config._user_config_dir() / "secrets.toml").exists()
    after = {p.name: p.read_bytes() for p in previous.parent.parent.rglob("*.yml")}
    assert after == before


def test_previous_install_shown_once_skip_and_home_reopens(previous: Path) -> None:
    from promptmend import previous_install
    from promptmend.tui.previous import PreviousInstallScreen

    async def first(app: ManageApp, pilot: Pilot[int]) -> None:
        assert isinstance(app.screen, PreviousInstallScreen)
        await pilot.press("escape")
        await settle(pilot)
        app.reload()
        await settle(pilot)
        assert not isinstance(app.screen, PreviousInstallScreen)  # once per session
        await press(app, pilot, "#home-previous")
        assert isinstance(app.screen, PreviousInstallScreen)
        await press(app, pilot, "#previous-skip")

    drive(first)
    assert previous_install.skipped_roots() == {str(previous.resolve())}

    async def second(app: ManageApp, pilot: Pilot[int]) -> None:
        assert not isinstance(app.screen, PreviousInstallScreen)
        await press(app, pilot, "#home-previous")
        assert "No previous install found" in _found(app)
        assert _disabled(app, "skip")

    drive(second)


def test_previous_install_entered_path(
    saved: Path, espanso: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from promptmend.tui.previous import PreviousInstallScreen

    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    root = _old_checkout(tmp_path, espanso)
    for path in (espanso.root / "match").iterdir():
        path.unlink()  # no launcher signal: only the path the user types finds it

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        assert not isinstance(app.screen, PreviousInstallScreen)
        await press(app, pilot, "#home-previous")
        assert "No previous install found" in _found(app)
        await press(app, pilot, "#previous-enter")
        await fill(app, pilot, previous_path=str(root))
        assert f"Previous install: {root.resolve()} (entered)" in _found(app)
        await press(app, pilot, "#previous-enter")
        await fill(app, pilot, previous_path="sk-or-v1-" + "ab12" * 16)
        assert "looks like a key" in _previous_screen(app).last_message

    drive(scenario)


def test_previous_install_profiles_tab_uses_the_detected_root(
    previous: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profile_service, "git_pristine_profiles", lambda root, rev=None: {})

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("escape")
        await settle(pilot)
        await pilot.press("3")
        await press(app, pilot, "#migrate-profiles")
        assert isinstance(app.screen, ConfirmModal)
        assert f"From {previous.resolve()}:" in _dialog(app).preview
        assert "mine.md (added)" in _dialog(app).preview

    drive(scenario)


def test_previous_install_detect_failure_is_shown(
    espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from promptmend import previous_install

    def broken(*args: Any, **kwargs: Any) -> None:
        raise OSError("no access")

    monkeypatch.setattr(previous_install, "detect", broken)

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await press(app, pilot, "#home-previous")
        assert "Could not look for a previous install: no access" in _found(app)

    drive(scenario)


def test_previous_install_steps_wait_for_a_deploy(previous: Path) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        app.main.query_one("#triggers-pane", panes.TriggersPane).busy = True
        for step in ("copy", "profiles", "deploy", "enter"):
            await press(app, pilot, f"#previous-{step}")
            assert not isinstance(app.screen, ConfirmModal | FormModal), step
            assert _previous_screen(app).last_message.startswith("A deploy is in progress")

    drive(scenario)


def test_previous_install_retire_needs_espanso_to_name_its_folder(
    previous: Path, espanso: FakeRunner
) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await press(app, pilot, "#previous-copy")
        await press(app, pilot, "#confirm")
        await press(app, pilot, "#previous-deploy")
        await press(app, pilot, "#submit")
        del espanso.answers["espanso path config"]
        await press(app, pilot, "#previous-retire")
        assert not isinstance(app.screen, ConfirmModal)
        message = _previous_screen(app).last_message
        assert "cannot check which CLI the match files run" in message
        assert f"config retire --from {previous.resolve()} --espanso-dir PATH" in message

    drive(scenario)
    assert (previous / ".env").is_file()


def test_previous_install_entered_path_that_is_no_checkout(
    saved: Path, espanso: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    root = _old_checkout(tmp_path, espanso)
    for path in (espanso.root / "match").iterdir():
        path.unlink()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await press(app, pilot, "#home-previous")
        await press(app, pilot, "#previous-enter")
        await fill(app, pilot, previous_path=str(elsewhere))
        assert _previous_screen(app).last_message.startswith(f"{elsewhere} is not a checkout")
        assert _previous_screen(app).entered is None
        await press(app, pilot, "#previous-enter")
        await fill(app, pilot, previous_path=f'"{root}"')  # pasted with its quotes
        assert f"Previous install: {root.resolve()} (entered)" in _found(app)

    drive(scenario)


# --- Intro and About (#112) ---------------------------------------------------------------


def test_intro_stays_until_a_key(espanso: FakeRunner) -> None:
    from promptmend.tui.intro import INTRO_HINT, IntroScreen

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        assert isinstance(app.screen, IntroScreen)
        await pilot.pause(1.2)  # longer than the old 0.8 s timer (#173)
        await settle(pilot)
        assert isinstance(app.screen, IntroScreen)
        shown = str(app.screen.query_one("#intro").render())
        assert INTRO_HINT in shown
        assert "promptmend config set PROMPT_UI_INTRO false" in shown

    drive(scenario, intro=True)


@pytest.mark.parametrize("key", ["enter", "escape", "q"])
def test_intro_closes_on_enter_escape_or_any_key_and_nothing_else(
    espanso: FakeRunner, key: str
) -> None:
    from textual.widgets import TabbedContent

    from promptmend.tui.intro import IntroScreen

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        assert isinstance(app.screen, IntroScreen)
        await pilot.press(key)
        await settle(pilot)
        assert app.screen_stack[-1] is app.main
        assert app.is_running
        assert app.main.query_one(TabbedContent).active == "home"

    drive(scenario, intro=True)


def test_intro_any_key_closes_it_and_is_consumed(espanso: FakeRunner) -> None:
    from textual.widgets import TabbedContent

    from promptmend.tui.intro import IntroScreen

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        intro = app.screen
        assert isinstance(intro, IntroScreen)
        assert "promptmend" in str(intro.query_one("#intro").render())
        await pilot.press("q")  # closes the intro, does not quit
        await settle(pilot)
        assert not isinstance(app.screen, IntroScreen)
        intro.close()  # a second close changes nothing
        await settle(pilot)
        assert app.screen is app.main
        assert app.is_running
        assert app.main.query_one(TabbedContent).active == "home"

    drive(scenario, intro=True)


def test_intro_click_closes_it(espanso: FakeRunner) -> None:
    from promptmend.tui.intro import IntroScreen

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        assert isinstance(app.screen, IntroScreen)
        await pilot.click("#intro")
        await settle(pilot)
        assert not isinstance(app.screen, IntroScreen)

    drive(scenario, intro=True)


def test_intro_digit_does_not_switch_tabs(espanso: FakeRunner) -> None:
    from textual.widgets import TabbedContent

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("6")
        await settle(pilot)
        assert app.main.query_one(TabbedContent).active == "home"
        await pilot.press("6")
        assert app.main.query_one(TabbedContent).active == "diagnostics"

    drive(scenario, intro=True)


def test_previous_install_offer_waits_for_the_intro(previous: Path) -> None:
    from promptmend.tui.intro import IntroScreen
    from promptmend.tui.previous import PreviousInstallScreen

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        assert app.state is not None
        assert app.state.previous.candidates
        assert isinstance(app.screen, IntroScreen)
        assert not any(isinstance(s, PreviousInstallScreen) for s in app.screen_stack)
        await pilot.press("space")
        await settle(pilot)
        assert isinstance(app.screen, PreviousInstallScreen)

    drive(scenario, intro=True)


def test_about_lists_version_runtime_and_folders(
    espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from promptmend import config_store
    from promptmend.tui import app as app_module
    from promptmend.tui import brand
    from promptmend.tui.intro import AboutScreen

    monkeypatch.setattr(app_module, "__version__", "9.8.7")

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("a")
        await settle(pilot)
        assert isinstance(app.screen, AboutScreen)
        facts = "\n".join(app.screen.facts)
        assert f"{brand.NAME} 9.8.7" in facts
        assert "Installed: uv" in facts
        assert str(config_store.config_dir()) in facts
        assert "Licence:   MIT" in facts
        assert KEY not in facts
        await pilot.press("escape")
        await settle(pilot)
        assert not isinstance(app.screen, AboutScreen)
        await pilot.press("a")
        await settle(pilot)
        await pilot.press("a")
        await settle(pilot)
        assert not isinstance(app.screen, AboutScreen)
        await pilot.press("a")
        await settle(pilot)
        await press(app, pilot, "#about-close")
        assert not isinstance(app.screen, AboutScreen)

    drive(scenario)


def test_about_before_the_state_is_read(espanso: FakeRunner) -> None:
    from promptmend.tui.intro import AboutScreen

    def broken(group_by: str) -> State:
        raise RuntimeError("boom")

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("a")
        await settle(pilot)
        assert isinstance(app.screen, AboutScreen)
        assert "Installed: unknown" in app.screen.facts

    drive(scenario, loader=broken)


@pytest.mark.parametrize(
    ("args", "environ", "intro"),
    [
        (("ui",), {}, True),
        ((), {}, True),
        (("ui", "--no-intro"), {}, False),
        (("ui",), {"PROMPT_UI_INTRO": "false"}, False),
        # A value the strict parser rejects falls back to the default in repair mode.
        (("ui",), {"PROMPT_UI_INTRO": "maybe"}, True),
    ],
)
def test_intro_opt_out(
    terminal: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    environ: dict[str, str],
    intro: bool,
) -> None:
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    result = _invoke(*args)
    assert result.exit_code == 0
    assert terminal["intro"] == intro


def test_ui_intro_setting_is_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPT_UI_INTRO", "maybe")
    with pytest.raises(ValueError, match="PROMPT_UI_INTRO"):
        config.Settings.load()
    monkeypatch.setenv("PROMPT_UI_INTRO", "false")
    assert config.Settings.load().ui_intro is False


def test_brand_is_ascii_and_narrow_terminals_get_text_only() -> None:
    from promptmend.tui import brand

    wide, narrow = brand.splash(110, "1.2.3"), brand.splash(70, "1.2.3")
    assert wide.isascii()
    assert brand.TAGLINE.isascii()
    assert brand.WORDMARK[0] in wide
    assert brand.WORDMARK[0] not in narrow
    assert narrow == f"{brand.NAME} 1.2.3\n\n{brand.TAGLINE}"
    assert max(map(len, brand.WORDMARK)) < brand.NARROW


# --- Every action shows its command (#111, stage 1) ---------------------------------------


def test_every_button_has_its_command_as_tooltip(previous: Path) -> None:
    from promptmend.tui import teach
    from promptmend.tui.previous import PreviousInstallScreen

    seen: dict[str, object] = {}

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        assert isinstance(app.screen, PreviousInstallScreen)
        for screen in (app.main, app.screen):
            for button in screen.query(Button):
                seen[button.id or ""] = button.tooltip

    drive(scenario)
    known = set(teach.BUTTONS) | set(teach.NO_COMMAND)
    assert set(seen) - known == set(), "give the new button a command in tui/teach.py"
    assert {i: teach.tooltip(i) for i in seen} == seen
    assert seen["deploy"] == "In a terminal:\n$ promptmend espanso deploy"


# Copy last (#111) takes what another tab logged, withheld as the log shows it: never the key.
def test_copy_last_takes_another_tabs_entry_withheld(
    saved: Path, espanso: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pyperclip

    def touched(*_: Any) -> Any:
        raise AssertionError("pyperclip was used")

    monkeypatch.setattr(pyperclip, "paste", touched)
    monkeypatch.setattr(pyperclip, "copy", touched)
    copied: list[str] = []

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        copy = app.main.query_one("#home-copy", Button)
        assert copy.disabled
        await pilot.press("2")
        await press(app, pilot, "#set-setting")
        await fill(app, pilot, setting_name="PROMPT_PROFILE", setting_value=KEY)
        assert app.session[-1].error
        await pilot.press("1")
        assert not copy.disabled
        await press(app, pilot, "#home-copy")
        assert copied == ["promptmend config set PROMPT_PROFILE <value withheld>"]
        assert KEY not in pane(app, "home").last_message

    drive(scenario)
    assert all(KEY not in text for text in copied)


def test_results_show_their_command_and_home_keeps_the_session(
    saved: Path, espanso: FakeRunner
) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        home = pane(app, "home")
        before = _rendered(home, "#home-session")
        assert "In a terminal, try:" in before
        assert "$ promptmend config show" in before
        await pilot.press("2")
        await press(app, pilot, "#set-setting")
        await fill(app, pilot, setting_name="PROMPT_TIMEOUT_SECONDS", setting_value="45")
        result = _rendered(pane(app, "providers"), ".result")
        assert result.startswith("$ promptmend config set PROMPT_TIMEOUT_SECONDS 45\n")
        # A value that looks like a key is never shown, even refused.
        await press(app, pilot, "#set-setting")
        await fill(app, pilot, setting_name="PROMPT_PROFILE", setting_value=KEY)
        await pilot.press("5")
        await press(app, pilot, "#reset")
        await press(app, pilot, "#confirm")
        await pilot.press("1")
        session = _rendered(home, "#home-session")
        assert "This session, as commands:" in session
        assert "$ promptmend config set PROMPT_TIMEOUT_SECONDS 45" in session
        assert "$ promptmend config set PROMPT_PROFILE '<value withheld>'" not in session
        assert "$ promptmend config set PROMPT_PROFILE <value withheld>" in session
        assert "    error: " in session
        assert "$ promptmend history reset" in session
        assert KEY not in session
        assert [e.command.split()[1:3] for e in app.session] == [
            ["config", "set"],
            ["config", "set"],
            ["history", "reset"],
        ]

    drive(scenario)


def test_session_log_keeps_the_latest(espanso: FakeRunner) -> None:
    from promptmend.tui import panes as panes_module
    from promptmend.tui import teach

    entries = [teach.Entry(f"promptmend doctor {n}", f"done {n}") for n in range(9)]
    text = panes_module.session_text(entries).plain
    assert "doctor 2\n" not in text
    assert text.count("$ promptmend doctor") == panes_module.SESSION_LINES
    failed = panes_module.session_text([teach.Entry("c", "boom", error=True)]).plain
    assert "    error: boom" in failed


# --- The command line's dialogs (#111) --------------------------------------------------------


# A line that would start a real child fails the test (conftest refuses console.run).
def test_conftest_refuses_the_real_runner_here() -> None:
    from promptmend.tui import console

    with pytest.raises(AssertionError, match="a test started a real promptmend"):
        console.run(["--version"])


async def enter(app: ManageApp, pilot: Pilot[int], line: str) -> None:
    """Type ``line`` on Home's command line and press Enter."""
    await pilot.press("c")
    app.main.query_one("#home-command", Input).value = line
    await pilot.pause()
    await pilot.press("enter")
    await settle(pilot)


def _help(app: ManageApp) -> str:
    return str(app.main.query_one("#home-command-help", Static).render())


def _files(folder: Path) -> list[tuple[Path, bytes]]:
    return sorted((p, p.read_bytes()) for p in folder.rglob("*") if p.is_file())


@pytest.mark.parametrize(
    ("line", "tab", "modal", "field", "value"),
    [
        ("espanso deploy --yes", "triggers", FormModal, None, None),
        ("history reset -y", "history", ConfirmModal, None, None),
        ("history prune --older-than 9", "history", FormModal, "#days", "9"),
        ("secrets set ANTHROPIC_API_KEY", "providers", FormModal, "#key-name", "ANTHROPIC_API_KEY"),
        ("secrets remove ANTHROPIC_API_KEY", "providers", FormModal, "#key-name", None),
        ("secrets remove", "providers", FormModal, "#key-name", None),
    ],
)
def test_command_line_opens_the_dialog_and_cancel_writes_nothing(
    saved: Path,
    espanso: FakeRunner,
    line: str,
    tab: str,
    modal: type[Widget],
    field: str | None,
    value: str | None,
) -> None:
    config_store.save_secret("OPENROUTER_API_KEY", KEY)
    config_store.save_secret("ANTHROPIC_API_KEY", KEY)
    before = (_files(saved), _files(espanso.root))

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await enter(app, pilot, line)
        assert isinstance(app.screen, modal)
        assert app.main.query_one(TabbedContent).active == tab
        focused = app.focused
        assert focused is not None
        assert focused.id == "cancel"
        if field is not None:
            widget = app.screen.query_one(field)
            assert isinstance(widget, Input | Select)
            if value is not None:
                assert widget.value == value
            if line == "secrets remove ANTHROPIC_API_KEY":
                assert widget.value == "ANTHROPIC_API_KEY"
            if "secrets set" in line:
                assert app.screen.query_one("#key-value", Input).password
        if "--yes" in line or "-y" in line:
            assert "ignored: the dialog asks" in _help(app)
        await pilot.press("escape")
        await settle(pilot)
        assert not isinstance(app.screen, modal)
        assert app.session == []

    drive(scenario)
    assert (_files(saved), _files(espanso.root)) == before


def test_command_line_migrate_and_detach_dialogs(saved: Path, espanso: FakeRunner) -> None:
    saved.mkdir(parents=True)
    (saved / ".env").write_text("PROMPT_PROFILE=general\n", encoding="utf-8")

    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await enter(app, pilot, "config migrate --yes --preview-token abc")
        assert isinstance(app.screen, ConfirmModal)
        assert "--yes, --preview-token, abc" not in _help(app)
        await pilot.press("n")
        await settle(pilot)
        assert (saved / ".env").is_file()
        # Detach: deploy first (through its dialog), then detach with --remove-all picked.
        await enter(app, pilot, "espanso deploy")
        await press(app, pilot, "#submit")
        await enter(app, pilot, "espanso detach --remove-all")
        assert isinstance(app.screen, FormModal)
        assert app.screen.query_one("#mode", Select).value == "remove-all"
        await press(app, pilot, "#cancel")
        assert (espanso.root / "match" / "prompts-llm.yml").is_file()
        # A busy deploy opens no second dialog, and the line says so.
        triggers = pane(app, "triggers")
        triggers.busy = True
        await pilot.press("1")
        await enter(app, pilot, "espanso deploy")
        assert not isinstance(app.screen, FormModal)
        assert "already in progress" in _help(app)
        assert app.main.query_one(TabbedContent).active == "home"

    drive(scenario)
