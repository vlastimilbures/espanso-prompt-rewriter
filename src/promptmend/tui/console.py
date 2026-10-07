"""Home's command line (#111): it completes and explains `promptmend` commands as they are
typed, against the CLI's own Click tree, and Enter acts on them by POLICY: a read-only
command (or a --dry-run preview) runs in a child process, its output on Home; a change with a
dialog of its own opens that dialog; the rest is refused (a terminal's prompt, or the
triggers). A word that looks like a key is never echoed: not in the help line, the output or
the history. The logic without Textual lives in promptmend/console.py (`promptmend shell`
uses it too); this module adds the widgets. Only tui/ imports it, so the triggers never load
it."""

from __future__ import annotations

from typing import Any, ClassVar, Protocol

from rich.text import Text
from textual.binding import Binding, BindingType
from textual.suggester import Suggester
from textual.widgets import Input, Static
from typer._click.exceptions import UsageError

from ..console import (
    DIALOG,
    RUN,
    Decision,
    Resolved,
    Runner,
    decide,
    describe,
    error_text,
    holds_a_key,
    loose,
    refused_secret,
    resolve,
    shown,
    split,
    suggest,
)
from ..console import run as run  # looked up here at Enter: conftest patches it here too
from . import teach

HISTORY_SIZE = 50

WITHHELD_NOTE = (
    "That line looked like it holds a key: it was cleared, not kept. "
    "Keys go in Providers (Set key), typed hidden."
)


RUNNING = "A command is still running; wait for its exit line."
SECRET_NOTE = (
    f"$ promptmend secrets set {teach.WITHHELD}: refused, and the line was cleared, not kept. "
    "Type only the key's name (secrets set OPENROUTER_API_KEY); its value goes in the dialog, "
    "hidden."
)


class Host(Protocol):
    """Home: runs a line in the background and opens a dialog on another tab."""

    running: bool

    def run_line(self, argv: tuple[str, ...], runner: Runner) -> None: ...

    def open_dialog(self, decision: Decision) -> str: ...


class CommandSuggester(Suggester):
    """Completes the word being typed to the first candidate, in order; nothing after a
    space, or inside an open quote."""

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value: str) -> str | None:
        return suggest(value)


class CommandLine(Input):
    """Home's command line: completion, live help below it, Up/Down through what was entered
    this session. Enter does what decide() says: ``host`` (Home) runs the line through
    ``runner`` (tests pass a fake) or opens its dialog; a refusal shows in the help line."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "history(-1)", "Previous", show=False),
        Binding("down", "history(1)", "Next", show=False),
        Binding("tab", "complete", "Complete"),
        Binding("escape", "leave", "Leave"),
    ]

    def __init__(
        self, help_line: Static, host: Host, runner: Runner | None = None, **kwargs: Any
    ) -> None:
        super().__init__(
            placeholder="Type a promptmend command (c)",
            suggester=CommandSuggester(),
            id="home-command",
            **kwargs,
        )
        self.help_line = help_line
        self.host = host
        self.runner: Runner = runner or run
        self.history: list[str] = []
        self.position = 0
        # The help shows Enter's answer until the next edit, not the recipes the cleared
        # line would show.
        self._hold = False

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._hold:
            self._hold = False
            return
        self.help_line.update(describe(event.value, typing=True))

    def _clear(self, message: Text) -> None:
        self._hold = bool(self.value)
        self.value = ""
        self.help_line.update(message)

    async def action_submit(self) -> None:
        line = self.value
        self.position = len(self.history)
        if not line.strip():
            return
        if holds_a_key(line):
            self._clear(Text(WITHHELD_NOTE))
            return
        try:
            words = split(line)
        except ValueError as exc:
            self.help_line.update(error_text(str(exc)))
            return
        if refused_secret(words):
            self._clear(Text(SECRET_NOTE))
            return
        if not self.history or self.history[-1] != line:
            self.history.append(line)
            del self.history[:-HISTORY_SIZE]
        self.position = len(self.history)
        found: Resolved | None
        try:
            found = resolve(words)
        except UsageError as exc:
            found = loose(words)
            if found is None:
                # Kept, so Up brings it back to fix.
                self._clear(error_text(exc.format_message()))
                return
        decision = decide(found, words)
        if decision.kind == RUN and self.host.running:
            # The line stays, to send once the run ends.
            self.help_line.update(Text(RUNNING))
        elif decision.kind == RUN:
            self._clear(Text(f"Running $ {shown(decision.argv)} (output below)"))
            self.host.run_line(decision.argv, self.runner)
        elif decision.kind == DIALOG:
            self._clear(Text(self.host.open_dialog(decision)))
        else:
            self._clear(Text(decision.message))

    def prefill(self, line: str) -> None:
        """Put ``line`` on the command line (a recipe), cursor at its end, focused, with its
        help as if typed. Runs nothing: Enter still decides."""
        self._hold = False
        self.value = line
        self.cursor_position = len(line)
        self.help_line.update(describe(line, typing=True))
        self.focus()

    def action_history(self, step: int) -> None:
        if not self.history:
            return
        self.position = max(0, min(len(self.history), self.position + step))
        self.value = self.history[self.position] if self.position < len(self.history) else ""
        self.cursor_position = len(self.value)

    def action_complete(self) -> None:
        """Take the suggestion; with none, Tab moves the focus on as anywhere else."""
        if self._suggestion and self.cursor_at_end:
            self.value = self._suggestion
            self.cursor_position = len(self.value)
        else:
            self.screen.focus_next()

    def action_leave(self) -> None:
        self.screen.set_focus(None)
