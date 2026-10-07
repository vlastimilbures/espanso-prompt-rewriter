"""`shell` (#183): a line-based REPL for a terminal, with the completion and live help of
Home's command line in the interface (promptmend/console.py), for people who would rather not
use the full-screen interface. Enter runs the line as `promptmend …` in this terminal: a
command that asks (deploy, migrate, `secrets set NAME`) asks here, as on any terminal. Only
the triggers (`improve`, `persona`), a nested shell and a key typed on the line are refused;
a line that looks like it holds a key is never echoed or kept in the history (memory only,
never a file). prompt_toolkit is imported only once the shell opens, so --help, the triggers
and every other command never load it."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import typer

from . import common
from .ui import on_a_terminal

if TYPE_CHECKING:
    from prompt_toolkit import PromptSession

    from ..console import Resolved

app = typer.Typer(name="shell", add_completion=False)

# What a line can be, besides a command to run.
RUN = "run"
REFUSE = "refuse"  # dropped with a note, nothing runs
ERROR = "error"  # a usage error, nothing runs
HELP = "help"  # an empty line or `help`
EXIT = "exit"

EXITS = ("exit", "quit")
HELPS = ("help", "?")
PROMPT = "promptmend> "
# Triggers and the shell itself: what a line here never runs, and why.
REFUSED: dict[tuple[str, ...], str] = {
    ("improve",): "the Espanso triggers run it, and it reads the clipboard; to try a "
    "rewrite, use the Try tab of `promptmend ui`",
    ("persona",): "the -p- trigger runs it",
    ("shell",): "this is the shell",
}
WITHHELD_NOTE = (
    "That line looked like it holds a key: nothing ran, and it was not kept. "
    "Keys are typed hidden: secrets set OPENROUTER_API_KEY"
)
LEAVE = "Type exit or quit (or press Ctrl-D) to leave."


@dataclass(frozen=True)
class Plan:
    """What Enter does with a line: ``argv`` runs (RUN), else ``message`` is printed."""

    kind: str
    argv: tuple[str, ...] = ()
    message: str = ""


def plan(line: str) -> Plan:
    """What Enter does with ``line``: run it, refuse it, show a usage error or the help, or
    leave. A leaf's --help counts only as the parser read it, never as another option's value
    (`improve --profile --help` is `improve`, refused)."""
    from typer._click.exceptions import UsageError
    from typer.core import TyperGroup

    from .. import console

    if line.strip() in EXITS:
        return Plan(EXIT)
    if line.strip() in HELPS:
        return Plan(HELP)
    if console.holds_a_key(line):
        return Plan(REFUSE, message=WITHHELD_NOTE)
    try:
        words = console.split(line)
    except ValueError as exc:
        return Plan(ERROR, message=console.error_text(str(exc)).plain)
    if not words:
        return Plan(HELP)
    if console.refused_secret(words):
        return Plan(REFUSE, message=secret_note())
    argv = tuple(words)
    found: Resolved | None
    try:
        found = console.resolve(words)
    except UsageError as exc:
        # `config set --help`: the CLI prints the help before it misses the arguments, but
        # only for a --help its parser reads as one.
        found = _help_only(words)
        if found is None:
            return Plan(ERROR, message=console.error_text(exc.format_message()).plain)
    if isinstance(found.command, TyperGroup):
        decision = console.decide(found, words)
        if decision.kind == console.RUN:
            return Plan(RUN, argv)
        return Plan(ERROR, message=console.error_text(decision.message).plain)
    if found.values.get("help") is True:
        return Plan(RUN, argv)
    why = REFUSED.get(found.path)
    if why is not None:
        return Plan(REFUSE, argv, f"Not from the shell: {why}.")
    return Plan(RUN, argv)


def _help_only(words: Sequence[str]) -> Resolved | None:
    from typer._click.exceptions import UsageError

    from .. import console

    try:
        found = console.resolve(words, resilient=True)
    except UsageError:
        return None
    return found if found.values.get("help") is True else None


def secret_note() -> str:
    from ..tui import teach

    return (
        f"$ promptmend secrets set {teach.WITHHELD}: refused, and the line was not kept. "
        "Type only the key's name (secrets set OPENROUTER_API_KEY); it then asks for the "
        "value, hidden."
    )


def runs_here(argv: Sequence[str]) -> bool:
    """The last check before a child starts: a fresh parse of exactly ``argv`` (as one line)
    plans to run exactly ``argv``."""
    import shlex

    decided = plan(shlex.join(argv))
    return decided.kind == RUN and decided.argv == tuple(argv)


def kept(line: str) -> bool:
    """Whether the history may keep ``line``: never one that holds a key, nor a `secrets
    set` with more than a key's name."""
    from .. import console

    if console.holds_a_key(line):
        return False
    try:
        return not console.refused_secret(console.split(line))
    except ValueError:
        return True


def run_here(argv: Sequence[str]) -> int:
    """``promptmend argv`` in a child process on this terminal: its stdin, stdout and stderr
    are the shell's, no capture, no timeout, never a shell; ``-P``, so a module in the
    working directory never runs. Ctrl-C reaches the child (same terminal) and the shell
    waits for it to end instead of ending itself."""
    import subprocess
    import sys

    if not runs_here(argv):
        raise ValueError("refused to run a line the shell does not run")
    child = subprocess.Popen(  # noqa: S603 - our own interpreter and module, an argv list
        [sys.executable, "-P", "-m", "promptmend.cli", *argv]
    )
    while True:
        try:
            return child.wait()
        except KeyboardInterrupt:
            continue


Runner = Callable[[Sequence[str]], int]


def recipes() -> str:
    from ..tui import teach

    width = max(len(teach.equivalent(*r.argv)) for r in teach.RECIPES)
    lines = ["Tab completes; the bar below explains the line as you type; Enter runs it."]
    lines += [f"  $ {teach.equivalent(*r.argv):<{width}}  {r.what}" for r in teach.RECIPES]
    lines.append(LEAVE)
    return "\n".join(lines)


def repl(
    read: Callable[[], str],
    runner: Runner | None = None,
    echo: Callable[[str], None] = typer.echo,
) -> int:
    """Read lines until exit, quit or Ctrl-D (EOFError), and do what plan() says with each.
    Ctrl-C at the prompt (KeyboardInterrupt) drops the line. Returns the shell's exit code."""
    echo(recipes())
    while True:
        try:
            line = read()
        except KeyboardInterrupt:
            continue
        except EOFError:
            return common.OK
        decided = plan(line)
        if decided.kind == EXIT:
            return common.OK
        if decided.kind == HELP:
            echo(recipes())
        elif decided.kind == RUN:
            code = (runner or run_here)(decided.argv)
            if code != 0:
                echo(f"exit {code}")
        else:
            echo(decided.message)


# --- The line editor ---------------------------------------------------------------------------


def completions(text: str) -> list[tuple[str, int]]:
    """The candidates for the word before the cursor in ``text``, each with how far back
    (as a negative start position) it replaces: none inside an open quote or on a line that
    looks like it holds a key."""
    from .. import console

    if console.holds_a_key(text):
        return []
    try:
        words = console.split(text)
    except ValueError:
        return []
    if not text.strip() or text[-1].isspace():
        partial, prefix = "", words
    elif not words:
        return []  # `promptmend` itself, still without its space
    else:
        partial, prefix = words[-1], words[:-1]
    return [(w, -len(partial)) for w in console.words_for(prefix) if w.startswith(partial)]


def toolbar(text: str) -> str:
    """The live help under the prompt, as plain text."""
    from .. import console

    return console.describe(text, typing=True).plain


def session(**kwargs: Any) -> PromptSession[str]:
    """The prompt: completion from the Click tree, the console's suggestion (Right arrow takes
    it), live help in the bottom bar and a history in memory that keeps no key-like line.
    ``kwargs`` go to PromptSession (tests pass a pipe input and a dummy output)."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
    from prompt_toolkit.completion import CompleteEvent, Completer, Completion
    from prompt_toolkit.document import Document
    from prompt_toolkit.history import InMemoryHistory

    from .. import console

    class Words(Completer):
        def get_completions(
            self, document: Document, complete_event: CompleteEvent
        ) -> Iterable[Completion]:
            for word, start in completions(document.text_before_cursor):
                yield Completion(word, start_position=start)

    class Suggest(AutoSuggest):
        def get_suggestion(self, buffer: Any, document: Document) -> Suggestion | None:
            text = document.text
            if console.holds_a_key(text):
                return None
            completed = console.suggest(text)
            return Suggestion(completed[len(text) :]) if completed else None

    class History(InMemoryHistory):
        def append_string(self, string: str) -> None:
            strings = list(self.get_strings())
            if kept(string) and string.strip() and (not strings or strings[-1] != string):
                super().append_string(string)

    prompt: PromptSession[str] = PromptSession(
        PROMPT,
        completer=Words(),
        auto_suggest=Suggest(),
        history=History(),
        complete_while_typing=False,
        **kwargs,
    )
    prompt.bottom_toolbar = lambda: toolbar(prompt.default_buffer.text)
    return prompt


@app.command("shell", short_help="A command line with completion and live help (a terminal).")
@common.guard
def shell() -> None:
    """Type promptmend commands with completion (Tab) and live help in the bar below; Enter
    runs each in this terminal, so a command that asks (deploy, migrate, secrets set NAME)
    asks here. The triggers (improve, persona) are refused, and a line that holds a key is
    never shown or kept. exit, quit or Ctrl-D leaves. It needs a terminal."""
    if not on_a_terminal():
        common.fail(
            "the shell needs a terminal (stdin and stdout); run the commands directly "
            "instead (see --help)",
            common.NEEDS_TERMINAL,
        )
    raise typer.Exit(repl(session().prompt))
