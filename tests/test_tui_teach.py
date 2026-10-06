"""Every command the interface names (#111, stage 1) parses against the CLI: each button's
tooltip, the recipes on Home and the command a result shows. A renamed command, option or
argument fails here instead of teaching a command that no longer works."""

from __future__ import annotations

import pytest
import typer

from prompt_workflow import cli
from prompt_workflow.tui import teach

# What a placeholder stands for, so the command can be parsed.
EXAMPLES = {
    "<NAME>": "PROMPT_PROFILE",
    "<VALUE>": "general",
    "<FILE>": "usage.json",
    "<DAYS>": "30",
    teach.CHECKOUT: "Projects/epr",
}


def parse(argv: tuple[str, ...]) -> None:
    """Resolve ``argv`` down the Click tree and parse the leaf's arguments and options, as
    the CLI would before running it; raises on an unknown command, option or argument."""
    root = typer.main.get_command(cli.app)
    ctx = root.make_context(teach.PROGRAM, ["--help"], resilient_parsing=True)
    command, words = root, [EXAMPLES.get(word, word) for word in argv]
    while isinstance(command, typer.core.TyperGroup):
        assert words, f"{argv}: names a group, not a command"
        sub = command.get_command(ctx, words[0])
        assert sub is not None, f"{argv}: no command {words[0]!r}"
        command, words = sub, words[1:]
    leaf = command.make_context(command.name or "", list(words))
    assert not leaf.args, f"{argv}: left over {leaf.args}"


ALL = sorted(
    {argv for commands in teach.BUTTONS.values() for argv in commands}
    | {recipe.argv for recipe in teach.RECIPES}
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
        "prompt-workflow config set <NAME> <VALUE>"
    )
    assert teach.equivalent("history", "export", "-o", "my file.csv") == (
        "prompt-workflow history export -o 'my file.csv'"
    )
    assert teach.equivalent("config", "set", "X", teach.WITHHELD).endswith("X <value withheld>")


def test_tooltips() -> None:
    assert teach.tooltip("deploy") == "In a terminal:\n$ prompt-workflow espanso deploy"
    smoke = teach.tooltip("smoke") or ""
    assert smoke.startswith("In a terminal:\n$ prompt-workflow setup\n")
    assert "smoke test" in smoke
    assert (teach.tooltip("home-previous") or "").count("$ prompt-workflow") == 4
    assert teach.tooltip("import-check") == teach.NO_COMMAND["import-check"]
    assert teach.tooltip("previous-close") is None
    assert teach.tooltip("no-such-button") is None


def test_entry_summary_is_the_first_line() -> None:
    assert teach.Entry("c", "one\ntwo").summary == "one"
    assert teach.Entry("c", "").summary == ""
