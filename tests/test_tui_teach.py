"""Every command the interface names (#111, stage 1) parses against the CLI: each button's
tooltip, the recipes on Home and the command a result shows. A renamed command, option or
argument fails here instead of teaching a command that no longer works."""

from __future__ import annotations

import pytest
from typer.core import TyperGroup

from promptmend import console
from promptmend.tui import teach

# What a placeholder stands for, so the command can be parsed.
EXAMPLES = {
    "<NAME>": "PROMPT_PROFILE",
    "<VALUE>": "general",
    "<FILE>": "usage.json",
    "<DAYS>": "30",
    "<PROVIDER>": "openrouter",
    "<TIER>": "standard",
    "<DRAFT>": "a rough draft",
    teach.CHECKOUT: "Projects/epr",
}


def parse(argv: tuple[str, ...]) -> None:
    """Resolve ``argv`` down the Click tree and parse the leaf's arguments and options, as
    the CLI would before running it (console.resolve(), which the command line on Home uses
    too); raises on an unknown command, option or argument."""
    found = console.resolve([EXAMPLES.get(word, word) for word in argv])
    assert not isinstance(found.command, TyperGroup), f"{argv}: names a group, not a command"


ALL = sorted(
    {argv for commands in teach.BUTTONS.values() for argv in commands}
    | {recipe.argv for recipe in teach.RECIPES}
    | {teach.INTRO_OFF}
)


@pytest.mark.parametrize("argv", ALL, ids=" ".join)
def test_every_command_parses(argv: tuple[str, ...]) -> None:
    parse(argv)
    assert all(w in EXAMPLES for w in argv if teach.PLACEHOLDER.fullmatch(w))


def test_the_drift_check_catches_a_wrong_command() -> None:
    for wrong in [("espanso", "deplo"), ("history", "prune", "--days", "3"), ("config",)]:
        with pytest.raises(Exception):  # noqa: B017, PT011  an assert or a Click UsageError
            parse(wrong)


def test_equivalent_quotes_values_and_keeps_placeholders() -> None:
    assert teach.equivalent("config", "set", "<NAME>", "<VALUE>") == (
        "promptmend config set <NAME> <VALUE>"
    )
    assert teach.equivalent("history", "export", "-o", "my file.csv") == (
        "promptmend history export -o 'my file.csv'"
    )
    assert teach.equivalent("config", "set", "X", teach.WITHHELD).endswith("X <value withheld>")


def test_tooltips() -> None:
    assert teach.tooltip("deploy") == "In a terminal:\n$ promptmend espanso deploy"
    smoke = teach.tooltip("smoke") or ""
    assert smoke.startswith("In a terminal:\n$ promptmend setup\n")
    assert "smoke test" in smoke
    assert (teach.tooltip("home-previous") or "").count("$ promptmend") == 4
    assert teach.tooltip("import-check") == teach.NO_COMMAND["import-check"]
    assert teach.tooltip("previous-close") is None
    assert teach.tooltip("no-such-button") is None


def test_entry_summary_is_the_first_line() -> None:
    assert teach.Entry("c", "one\ntwo").summary == "one"
    assert teach.Entry("c", "").summary == ""
