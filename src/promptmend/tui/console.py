"""Home's command line (#111, stage 2): it completes and explains `promptmend` commands as
they are typed, against the CLI's own Click tree, but never runs one (that comes in the next
version). A word that looks like a key is never echoed: not in the help line, not in the
history. Only tui/ imports this module, so the triggers never load it."""

from __future__ import annotations

import functools
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

import typer
from rich.text import Text
from textual.binding import Binding, BindingType
from textual.suggester import Suggester
from textual.widgets import Input, Static
from typer._click import Context
from typer._click.exceptions import UsageError
from typer._types import TyperChoice
from typer.core import TyperArgument, TyperGroup, TyperOption

from .. import config, prompt_builder
from ..commands import common
from ..redaction import redact_words
from . import teach

if TYPE_CHECKING:
    from typer._click import Command

# The settings whose value is a profile name: `config set PROMPT_PROFILE <profile>`.
PROFILE_SETTINGS = ("PROMPT_PROFILE", "PROMPT_PRO_PROFILE")
PROFILE_OPTION = "--profile"
# (command path, argument) -> what it takes; tests/test_tui_console.py checks each exists.
SETTING_ARGUMENTS = {("config", "get"), ("config", "set"), ("config", "unset")}
SECRET_ARGUMENTS = {("secrets", "set"), ("secrets", "remove")}
ARGUMENT = "name"
VALUE_ARGUMENT = "value"
# The words a line may start with that name the program itself.
PROGRAMS = (teach.PROGRAM, "prompt-workflow")
HISTORY_SIZE = 50

NOT_YET = "Running commands comes in the next version; in a terminal:"
WITHHELD_NOTE = (
    "That line looked like it holds a key: it was cleared, not kept. "
    "Keys go in Providers & keys (Set key), typed hidden."
)


@functools.cache
def root() -> TyperGroup:
    """The CLI's Click tree, built once (the lazy commands are imported as they are named)."""
    from .. import cli

    return cast("TyperGroup", typer.main.get_command(cli.app))


@dataclass(frozen=True)
class Resolved:
    """Where a command line leads: the commands walked, the deepest one and its context."""

    path: tuple[str, ...]
    command: Command
    context: Context


def resolve(argv: Sequence[str], *, resilient: bool = False, partial: bool = False) -> Resolved:
    """Walk ``argv`` down the Click tree and parse the deepest command's arguments and
    options, as the CLI would before running it, but with no option's callback run (no
    --help or --version printed). Raises a click UsageError on an unknown command or option,
    a group with no command (unless ``partial``) or a word left over; ``resilient`` skips the
    check a half-typed line fails (a missing argument)."""
    command: Command = root()
    ctx = command.make_context(teach.PROGRAM, [], resilient_parsing=True)
    path: list[str] = []
    words = list(argv)
    while isinstance(command, TyperGroup):
        if not words or words[0].startswith("-"):
            if not words and not partial:
                raise UsageError(f"{' '.join(path) or teach.PROGRAM} needs a command.", ctx)
            # The group's own options (--version, all flags) are checked, never parsed: a
            # callback would print.
            known = {*_options(command), *command.get_help_option_names(ctx)}
            for word in words:
                if word not in known:
                    raise UsageError(f"No such option: {word}", ctx)
            return Resolved(tuple(path), command, ctx)
        sub = command.get_command(ctx, words[0])
        if sub is None or sub.hidden:
            raise UsageError(f"No such command {words[0]!r}.", ctx)
        path.append(words.pop(0))
        command = sub
        if isinstance(command, TyperGroup):
            ctx = command.make_context(path[-1], [], parent=ctx, resilient_parsing=True)
    # The context shows the usage; the words go through the parser alone, which raises the
    # CLI's usage errors (a resilient context would swallow them) but runs no callback.
    leaf = command.make_context(path[-1], [], parent=ctx, resilient_parsing=True)
    options = _options(command)
    if resilient and words and (option := options.get(words[-1])) and not option.is_flag:
        words = words[:-1]  # its value is still to come
    strict = Context(command, parent=ctx, info_name=path[-1])
    values, left, _ = command.make_parser(strict).parse_args(words)
    if left:
        raise UsageError(f"Got unexpected extra argument ({left[0]})", leaf)
    if not resilient:
        for param in command.params:
            if param.required and values.get(param.name or "") is None:
                if isinstance(param, TyperOption):
                    missing = f"option {param.opts[0]}"
                else:
                    missing = f"argument {param.human_readable_name.upper()}"
                raise UsageError(f"Missing {missing}.", leaf)
    return Resolved(tuple(path), command, leaf)


def _visible_commands(group: TyperGroup, ctx: Context) -> list[str]:
    return [
        name
        for name in group.list_commands(ctx)
        if (sub := group.get_command(ctx, name)) is not None and not sub.hidden
    ]


def _options(command: Command) -> dict[str, TyperOption]:
    """Every option string of ``command`` (--yes, -y, --no-copy) -> its option, hidden ones
    included: a hidden option is never offered, but its value is still skipped."""
    return {
        opt: param
        for param in command.params
        if isinstance(param, TyperOption)
        for opt in (*param.opts, *param.secondary_opts)
    }


def settings() -> list[str]:
    """The settings `config get|set|unset` take: every one but the keys."""
    secrets = set(config.secret_names())
    return sorted(name for name in config.env_names() if name not in secrets)


def secrets() -> list[str]:
    return sorted(config.secret_names())


def profiles() -> list[str]:
    """The built-in profiles and the user's own with a valid name."""
    names = set(prompt_builder.PROFILES)
    names.update(
        p.name
        for p in prompt_builder.user_profiles()
        if p.status not in (prompt_builder.INVALID_NAME, prompt_builder.MISSING)
    )
    return sorted(names)


def _option_values(option: TyperOption, opt: str) -> list[str]:
    if isinstance(option.type, TyperChoice):
        return sorted(str(c) for c in option.type.choices)
    if opt == PROFILE_OPTION:
        return profiles()
    return []


def _argument_values(path: tuple[str, ...], argument: TyperArgument, given: list[str]) -> list[str]:
    if isinstance(argument.type, TyperChoice):
        return sorted(str(c) for c in argument.type.choices)
    if argument.name == ARGUMENT and path in SETTING_ARGUMENTS:
        return settings()
    if argument.name == ARGUMENT and path in SECRET_ARGUMENTS:
        return secrets()
    if path == ("config", "set") and argument.name == VALUE_ARGUMENT:
        return profiles() if given[:1] and given[0] in PROFILE_SETTINGS else []
    return []


def words_for(prefix_words: Sequence[str]) -> list[str]:
    """Every word that may come after ``prefix_words``, sorted: a command of the group they
    name, or the command's options (hidden ones never) and the values of its next argument
    or of the option just typed. Empty when they do not resolve."""
    try:
        found = resolve(prefix_words, resilient=True, partial=True)
    except UsageError:
        return []
    command, ctx = found.command, found.context
    if isinstance(command, TyperGroup):
        if prefix_words[len(found.path) :]:
            return []  # past the group's own options: nothing else goes there
        names = _visible_commands(command, ctx)
        return sorted({*names, *_long_options(command)})
    options = _options(command)
    rest = list(prefix_words[len(found.path) :])
    given: list[str] = []
    used: set[str] = set()
    expecting: tuple[TyperOption, str] | None = None
    for word in rest:
        if expecting is not None:
            expecting = None
        elif word.startswith("-") and word.split("=", 1)[0] in options:
            option = options[word.split("=", 1)[0]]
            used.update((*option.opts, *option.secondary_opts))
            if not option.is_flag and "=" not in word:
                expecting = (option, word)
        else:
            given.append(word)
    if expecting is not None:
        return _option_values(*expecting)
    arguments = [p for p in command.params if isinstance(p, TyperArgument)]
    values: list[str] = []
    if len(given) < len(arguments):
        values = _argument_values(found.path, arguments[len(given)], given)
    return sorted({*values, *(o for o in _long_options(command) if o not in used)})


def _long_options(command: Command) -> list[str]:
    return [
        opt
        for param in command.params
        if isinstance(param, TyperOption) and not param.hidden
        for opt in (*param.opts, *param.secondary_opts)
        if opt.startswith("--")
    ]


def split(line: str) -> list[str]:
    """The line's words as a POSIX shell splits them, without a leading `promptmend`;
    raises ValueError on an open quote."""
    words = shlex.split(line)
    if words and words[0] in PROGRAMS:
        words = words[1:]
    return words


def holds_a_key(line: str) -> bool:
    """A line with a word (or as a whole) the gate would block as a credential."""
    try:
        words = shlex.split(line)
    except ValueError:
        words = line.split()
    return any(common.looks_like_a_key(w) for w in [*words, line] if w.strip())


class CommandSuggester(Suggester):
    """Completes the word being typed to the first candidate, in order; nothing after a
    space, or inside an open quote."""

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value: str) -> str | None:
        return suggest(value)


def suggest(value: str) -> str | None:
    if not value.strip() or value[-1].isspace():
        return None
    try:
        words = split(value)
    except ValueError:
        return None
    if not words:
        return None
    partial = words[-1]
    for word in words_for(words[:-1]):
        if word.startswith(partial) and word != partial:
            return value + word[len(partial) :]
    return None


# How many recipes the empty line's help names (Home's session log lists them all).
HELP_RECIPES = 2


def _recipes() -> Text:
    text = Text("Tab or Right arrow completes; Enter shows the command, nothing runs yet. Try ")
    shown = (f"$ {teach.equivalent(*r.argv)}" for r in teach.RECIPES[:HELP_RECIPES])
    text.append(" · ".join(shown), style="dim")
    return text


def _withheld() -> Text:
    return Text(f"{teach.WITHHELD}: this line looks like it holds a key; nothing of it is shown.")


def _command_help(found: Resolved) -> Text:
    text = Text(no_wrap=False)
    text.append(found.command.get_usage(found.context), style="bold")
    short = found.command.get_short_help_str(limit=120)
    if short:
        text.append(f"\n{short}")
    if isinstance(found.command, TyperGroup):
        names = _visible_commands(found.command, found.context)
        text.append("\nCommands: " + ", ".join(names), style="dim")
    return text


def _error(message: str) -> Text:
    return Text(f"error: {redact_words(message)}")


def describe(line: str, *, typing: bool = False) -> Text:
    """The help line for ``line``: recipes when empty, else the usage and short help of the
    command it names, or the usage error the CLI would print (redacted). ``typing`` (the line
    is still being typed) explains a half-typed last word by the command before it and the
    words it may become, instead of an error."""
    if holds_a_key(line):
        return _withheld()
    if not line.strip():
        return _recipes()
    try:
        words = split(line)
    except ValueError as exc:
        return _error(str(exc))
    try:
        return _command_help(resolve(words, resilient=True, partial=True))
    except UsageError as exc:
        error = exc
    if typing and words and not line[-1].isspace():
        matches = [w for w in words_for(words[:-1]) if w.startswith(words[-1])]
        if matches:
            try:
                text = _command_help(resolve(words[:-1], resilient=True, partial=True))
            except UsageError:  # pragma: no cover - words_for() resolved it already
                return _error(error.format_message())
            text.append("\nMatches: " + ", ".join(matches), style="dim")
            return text
    return _error(error.format_message())


class CommandLine(Input):
    """Home's command line: completion, live help below it, Up/Down through what was entered
    this session. Enter shows the command as it runs in a terminal; it runs nothing."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "history(-1)", "Previous", show=False),
        Binding("down", "history(1)", "Next", show=False),
        Binding("tab", "complete", "Complete"),
        Binding("escape", "leave", "Leave"),
    ]

    def __init__(self, help_line: Static, **kwargs: Any) -> None:
        super().__init__(
            placeholder="Type a promptmend command (c)",
            suggester=CommandSuggester(),
            id="home-command",
            **kwargs,
        )
        self.help_line = help_line
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
            self.help_line.update(_error(str(exc)))
            return
        if not self.history or self.history[-1] != line:
            self.history.append(line)
            del self.history[:-HISTORY_SIZE]
        self.position = len(self.history)
        try:
            found = resolve(words)
        except UsageError as exc:
            # Kept, so Up brings it back to fix.
            self._clear(_error(exc.format_message()))
            return
        answer = _command_help(found)
        answer.append(f"\n{NOT_YET}\n")
        answer.append(f"$ {teach.equivalent(*words)}", style="bold")
        self._clear(answer)

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
