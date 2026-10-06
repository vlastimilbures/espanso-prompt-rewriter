"""Home's command line (#111, stage 2): what it completes, what its help line says, and that
it runs nothing. Every word it offers parses against the CLI (the drift test), a key is never
offered outside `secrets`, and a line that holds a key is never echoed or kept."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pytest
from textual.pilot import Pilot
from textual.widgets import Static, TabbedContent
from typer._click.exceptions import UsageError
from typer._types import TyperChoice
from typer.core import TyperArgument, TyperGroup, TyperOption

from promptmend import config, prompt_builder, smoke
from promptmend.tui import console, teach
from promptmend.tui.app import ManageApp

# Built at runtime, so no key-shaped literal lands in the repo (gitleaks).
KEY = "sk-or-v1-" + "ab12" * 16
SETTINGS = set(config.env_names()) - set(config.secret_names())
SECRETS = set(config.secret_names())


# --- Candidates -----------------------------------------------------------------------------


def test_top_level_offers_every_visible_command() -> None:
    words = console.words_for([])
    assert {"config", "doctor", "espanso", "secrets", "ui", "--version"} <= set(words)
    assert words == sorted(words)


def test_config_set_offers_settings_never_a_key() -> None:
    words = set(console.words_for(["config", "set"]))
    assert words >= SETTINGS
    assert not words & SECRETS
    assert console.suggest("config set PROMPT_") == "config set PROMPT_EXTRA_PATTERNS"
    assert console.suggest("config set OPENROUTER_API") is None
    for command in ("get", "unset"):
        assert not set(console.words_for(["config", command])) & SECRETS


def test_secrets_set_offers_only_key_names() -> None:
    for command in ("set", "remove"):
        names = {w for w in console.words_for(["secrets", command]) if not w.startswith("-")}
        assert names == SECRETS
    assert console.suggest("secrets set O") == "secrets set OPENROUTER_API_KEY"
    assert console.words_for(["secrets", "status"]) == []


def test_profiles_after_a_profile_setting_and_the_profile_option() -> None:
    folder = prompt_builder.user_profiles_dir()
    folder.mkdir(parents=True)
    (folder / "mine.md").write_text("x", encoding="utf-8")
    (folder / "Bad Name.md").write_text("x", encoding="utf-8")
    expected = sorted({*prompt_builder.PROFILES, "mine"})
    for prefix in (
        ["config", "set", "PROMPT_PROFILE"],
        ["config", "set", "PROMPT_PRO_PROFILE"],
        ["improve", "--profile"],
        ["setup", "--profile"],
    ):
        assert console.words_for(prefix) == expected, prefix
    assert console.words_for(["config", "set", "PROMPT_HISTORY"]) == []
    assert console.suggest("improve --profile mi") == "improve --profile mine"


def test_hidden_options_and_used_ones_are_not_offered() -> None:
    assert "--trigger-id" not in console.words_for(["improve"])
    assert console.words_for(["persona"]) == []
    assert "--yes" not in console.words_for(["espanso", "deploy", "-y"])
    assert console.words_for(["no-such"]) == []
    assert console.words_for(["--version"]) == []


def test_option_values_and_choices() -> None:
    assert "--profile" not in console.words_for(["improve", "--profile", "general"])
    assert "--profile" not in console.words_for(["improve", "--profile=general"])
    assert console.words_for(["improve", "--model"]) == []
    choice = TyperOption(param_decls=["--colour"], type=TyperChoice(["red", "blue"]))
    assert console._option_values(choice, "--colour") == ["blue", "red"]
    argument = TyperArgument(param_decls=["colour"], type=TyperChoice(["red", "blue"]))
    assert console._argument_values(("x",), argument, []) == ["blue", "red"]


def test_suggest_completes_only_the_word_being_typed() -> None:
    assert console.suggest("esp") == "espanso"
    assert console.suggest("promptmend esp") == "promptmend esp" + "anso"
    assert console.suggest("espanso st") == "espanso status"
    assert console.suggest("espanso status --d") == "espanso status --diff"
    assert console.suggest("doctor ") is None
    assert console.suggest("") is None
    assert console.suggest('config set "PROMPT_') is None  # open quote
    assert console.suggest("doctor") is None  # complete already
    assert console.suggest("zzz") is None
    assert console.suggest("promptmend") is None


def test_the_suggester_is_uncached_and_case_sensitive() -> None:
    suggester = console.CommandSuggester()
    assert suggester.cache is None
    assert suggester.case_sensitive
    assert asyncio.run(suggester.get_suggestion("espanso de")) == "espanso deploy"


# --- Drift: every word offered parses ---------------------------------------------------------

# A value for an option or argument that has no candidates of its own.
PLACEHOLDER = "x"


def _walk() -> Iterator[tuple[str, ...]]:
    """Every command path of the CLI: groups and leaves."""

    def walk(path: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
        yield path
        found = console.resolve(path, resilient=True, partial=True)
        if isinstance(found.command, TyperGroup):
            for name in console.words_for(path):
                if not name.startswith("-"):
                    yield from walk((*path, name))

    return walk(())


PATHS = list(_walk())


def test_the_walk_reaches_every_leaf() -> None:
    assert ("config", "set") in PATHS
    assert ("espanso", "deploy") in PATHS
    assert ("history", "prune") in PATHS
    assert len(PATHS) > 25


@pytest.mark.parametrize("path", PATHS, ids=lambda p: " ".join(p) or "promptmend")
def test_every_candidate_parses(path: tuple[str, ...]) -> None:
    found = console.resolve(path, resilient=True, partial=True)
    options = {
        opt: param
        for param in found.command.params
        if isinstance(param, TyperOption)
        for opt in (*param.opts, *param.secondary_opts)
    }
    for word in console.words_for(path):
        argv = (*path, word)
        option = options.get(word)
        if option is not None and not option.is_flag:
            values = console.words_for(argv) or [PLACEHOLDER]
            for value in values:
                console.resolve((*argv, value), resilient=True, partial=True)
        else:
            console.resolve(argv, resilient=True, partial=True)
            # A value the next argument takes (config set PROMPT_PROFILE <profile>).
            for value in console.words_for(argv):
                if not value.startswith("-"):
                    console.resolve((*argv, value), resilient=True)
        # A key name is offered only where a key is named.
        if word in SECRETS:
            assert path in console.SECRET_ARGUMENTS, argv


def test_the_value_tables_name_real_arguments() -> None:
    def arguments(path: tuple[str, ...]) -> set[str]:
        command = console.resolve(path, resilient=True).command
        return {p.name or "" for p in command.params if isinstance(p, TyperArgument)}

    for path in console.SETTING_ARGUMENTS | console.SECRET_ARGUMENTS:
        assert console.ARGUMENT in arguments(path), path
    assert console.VALUE_ARGUMENT in arguments(("config", "set"))
    assert set(console.PROFILE_SETTINGS) <= SETTINGS
    improve = console.resolve(("improve",)).command
    assert any(console.PROFILE_OPTION in getattr(p, "opts", ()) for p in improve.params)


def test_resolve_raises_the_cli_usage_errors() -> None:
    for wrong in [
        ("espanso", "deplo"),
        ("history", "prune", "--days", "3"),
        ("config",),
        ("config", "set", "PROMPT_HISTORY"),
        ("doctor", "extra"),
        ("--nope",),
    ]:
        with pytest.raises(UsageError):
            console.resolve(wrong)
    # A half-typed line is fine where it may still become one.
    console.resolve(("config", "set", "PROMPT_HISTORY"), resilient=True)
    console.resolve(("history", "prune", "--older-than"), resilient=True)
    with pytest.raises(UsageError, match="Missing option --from"):
        console.resolve(("config", "retire"))
    with pytest.raises(UsageError, match="Missing argument VALUE"):
        console.resolve(("config", "set", "PROMPT_HISTORY"))
    assert console.resolve(("--version",), partial=True).path == ()


def test_resolve_runs_no_callback(capsys: pytest.CaptureFixture[str]) -> None:
    console.resolve(("--version",), partial=True)
    console.resolve(("doctor", "--help"))
    assert capsys.readouterr().out == ""


# --- The help line ----------------------------------------------------------------------------


def test_help_for_a_valid_command() -> None:
    text = console.describe("espanso status --diff").plain
    assert text.startswith("Usage: promptmend espanso status [OPTIONS]")
    assert "\n" in text
    group = console.describe("config").plain
    assert "Commands: show, get, set" in group
    assert console.describe("promptmend doctor").plain.startswith("Usage: promptmend doctor")


def test_help_for_a_usage_error_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    assert console.describe("espanso deplo").plain == "error: No such command 'deplo'."
    # Behind the withheld notice, a usage error quoting a key is redacted too.
    monkeypatch.setattr(console, "holds_a_key", lambda _: False)
    text = console.describe(f"espanso {KEY}").plain
    assert text.startswith("error: No such command")
    assert KEY not in text
    assert "<redacted" in text
    assert console.describe('config set "x').plain == "error: No closing quotation"


def test_help_while_typing_explains_the_word_being_typed() -> None:
    text = console.describe("espanso st", typing=True).plain
    assert text.startswith("Usage: promptmend espanso [OPTIONS] COMMAND")
    assert text.endswith("Matches: status")
    assert console.describe("espanso zz", typing=True).plain.startswith("error:")
    assert console.describe("espanso st ", typing=True).plain.startswith("error:")


def test_empty_line_shows_recipes() -> None:
    text = console.describe("").plain
    for recipe in teach.RECIPES[: console.HELP_RECIPES]:
        assert f"$ {teach.equivalent(*recipe.argv)}" in text


def test_a_key_is_never_echoed() -> None:
    for line in (f"config set X {KEY}", KEY, f"secrets set {KEY}", f'config set X "{KEY}'):
        for typing in (True, False):
            text = console.describe(line, typing=typing).plain
            assert KEY not in text
            assert KEY[:12] not in text
            assert text.startswith(teach.WITHHELD)
    assert console.suggest(f"config set X {KEY}") is None


# --- In the interface -------------------------------------------------------------------------

SIZE = (120, 50)


async def settle(pilot: Pilot[int]) -> None:
    for _ in range(4):
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def drive(scenario: Callable[[ManageApp, Pilot[int]], Awaitable[None]]) -> ManageApp:
    app = ManageApp(intro=False)

    async def main() -> None:
        async with app.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await scenario(app, pilot)

    asyncio.run(main())
    return app


def _line(app: ManageApp) -> console.CommandLine:
    return app.main.query_one("#home-command", console.CommandLine)


def _help(app: ManageApp) -> str:
    return str(app.main.query_one("#home-command-help", Static).render())


def _tab(app: ManageApp) -> str:
    return app.main.query_one(TabbedContent).active


@pytest.fixture
def nothing_runs(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """Once the state has loaded, any subprocess or smoke test fails the test."""

    def refuse(*_: Any, **__: Any) -> Any:
        raise AssertionError("the command line ran something")

    def arm() -> None:
        monkeypatch.setattr(subprocess, "run", refuse)
        monkeypatch.setattr(subprocess, "Popen", refuse)
        monkeypatch.setattr(smoke, "run", refuse)

    return arm


def test_not_focused_at_launch_and_c_focuses_it() -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        assert not _line(app).has_focus
        await pilot.press("3")
        assert _tab(app) == "profiles"
        await pilot.press("c")
        await pilot.pause()
        assert _tab(app) == "home"
        assert _line(app).has_focus
        # Every binding's key is text while it has the focus.
        await pilot.press(*"qrta12c")
        await pilot.pause()
        assert _line(app).value == "qrta12c"
        assert _tab(app) == "home"
        assert app.is_running
        assert app.theme != "high-contrast"
        await pilot.press("escape")
        assert not _line(app).has_focus
        await pilot.press("2")
        assert _tab(app) == "providers"

    drive(scenario)


def test_typing_shows_suggestion_and_help() -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        await pilot.press("c", *"espanso st")
        await settle(pilot)
        assert _line(app)._suggestion == "espanso status"
        assert "Matches: status" in _help(app)
        await pilot.press("tab")
        assert _line(app).value == "espanso status"
        await settle(pilot)
        assert _help(app).startswith("Usage: promptmend espanso status")
        # With no suggestion, Tab moves on as anywhere else.
        await pilot.press("tab")
        assert not _line(app).has_focus

    drive(scenario)


def test_enter_runs_nothing_and_up_recalls(nothing_runs: Callable[[], None]) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        nothing_runs()
        line = _line(app)
        await pilot.press("c", *"doctor --json", "enter")
        await settle(pilot)
        assert line.value == ""
        shown = _help(app)
        assert console.NOT_YET in shown
        assert "$ promptmend doctor --json" in shown
        assert line.history == ["doctor --json"]
        await pilot.press(*"stats", "enter", "up")
        assert line.value == "stats"
        await pilot.press("up")
        assert line.value == "doctor --json"
        await pilot.press("up")
        assert line.value == "doctor --json"
        await pilot.press("down", "down")
        assert line.value == ""
        # An empty Enter or an open quote keeps nothing.
        await pilot.press("enter", *'config set "x', "enter")
        await settle(pilot)
        assert line.history == ["doctor --json", "stats"]
        assert "No closing quotation" in _help(app)
        await pilot.press("ctrl+u", *"espanso deplo", "enter")
        await settle(pilot)
        assert _help(app) == "error: No such command 'deplo'."
        await pilot.press("up")
        assert line.value == "espanso deplo"
        assert app.session == []

    drive(scenario)


def test_a_key_line_is_cleared_and_never_recalled(nothing_runs: Callable[[], None]) -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        nothing_runs()
        line = _line(app)
        await pilot.press("c")
        line.value = f"config set X {KEY}"
        await settle(pilot)
        assert KEY not in _help(app)
        await pilot.press("enter")
        await settle(pilot)
        assert line.value == ""
        assert line.history == []
        assert "Providers & keys" in _help(app)
        assert KEY not in _help(app)
        await pilot.press("up")
        assert line.value == ""
        assert all(KEY not in entry.command for entry in app.session)

    drive(scenario)


def test_the_history_is_capped() -> None:
    async def scenario(app: ManageApp, pilot: Pilot[int]) -> None:
        line = _line(app)
        await pilot.press("c")
        for n in range(console.HISTORY_SIZE + 5):
            line.value = f"stats --by {n}"
            await line.action_submit()
        assert len(line.history) == console.HISTORY_SIZE
        assert line.history[-1] == f"stats --by {console.HISTORY_SIZE + 4}"

    drive(scenario)
