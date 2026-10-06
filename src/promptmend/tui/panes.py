"""Six screens of the interface, one tab each (#93; the seventh, Try, is in try_pane.py).
Each shows what the headless commands print and acts through the same service calls; none
holds logic of its own. Keys are shown only as set or not set, never their value."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import typer
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, DataTable, RichLog, Select, Static

from .. import config_store, deploy, doctor, history, previous_install, smoke
from .. import profiles as profile_service
from ..commands import common, usage
from ..commands import doctor as doctor_cmd
from ..commands import profiles as profiles_cmd
from ..commands import settings as settings_cmd
from ..config import env_names, secret_names
from ..config_files import SecretStoreError
from ..factory import PROVIDER_NAMES, routes
from ..prompt_builder import ADDED, ALIASES, PROFILES, user_profiles_dir
from . import teach
from .console import (
    CommandLine,
    Decision,
    Ran,
    Runner,
    describe,
    runnable,
    shown,
    shown_arg,
    transcript,
)
from .home import TAB_LABELS, HomeRow, headline, home_rows
from .modals import ConfirmModal, Field, FormModal, TextModal
from .state import State, current_plan

if TYPE_CHECKING:
    from textual.widget import Widget

    from .app import ManageApp

# What a save, a deletion or a deploy may raise for the person to read: the same errors the
# headless commands turn into `error: …` (commands/common.guard).
EXPECTED = (
    common.CommandError,
    ValueError,
    OSError,
    config_store.ConfigStoreError,
    SecretStoreError,
    deploy.DeployError,
    history.HistoryError,
    typer.BadParameter,
)


def _status(check: doctor.Check) -> Text:
    label, color = doctor_cmd._LABEL[check.status]
    line = Text("[")
    line.append(f"{label:^4}", style=color)
    line.append(f"] {check.id}: {check.message}")
    return line


def _checks(report: doctor.Report, ids: Sequence[str] | None = None) -> Text:
    return Text("\n").join(_status(c) for c in report.checks if ids is None or c.id in ids)


def _add_row(table: DataTable[Any], *cells: str, key: str | None = None) -> None:
    """A row of plain text: a path or a value is never read as markup."""
    table.add_row(*(Text(cell) for cell in cells), key=key)


def _buttons(*buttons: tuple[str, str]) -> Horizontal:
    """A row of buttons, each with its headless command as the tooltip (#111)."""
    return Horizontal(
        *(Button(label, id=id_, tooltip=teach.tooltip(id_)) for id_, label in buttons),
        classes="buttons",
    )


def result_text(message: str, command: str | None) -> Text:
    """An action's result, under the command it was (muted) when it has one."""
    if command is None:
        return Text(message)
    shown = Text()
    shown.append(f"$ {command}\n", style="dim")
    shown.append(message)
    return shown


class Pane(VerticalScroll):
    """One tab. ``show()`` fills it from a fresh State; ``report()`` shows an action's result
    on the pane and as a notification."""

    last_message = ""
    ready = False
    # A deploy or detach dialog is open or running (TriggersPane).
    busy = False

    def on_mount(self) -> None:
        # A State read before this pane was composed is shown now (the load runs in a thread).
        self.setup()
        self.ready = True
        if self.state is not None:
            self.show(self.state)

    def setup(self) -> None:
        """Add the table columns; called once the pane's widgets exist."""

    @property
    def manage(self) -> ManageApp:
        return cast("ManageApp", self.app)

    @property
    def state(self) -> State | None:
        return self.manage.state

    def show(self, state: State) -> None:
        """Fill the pane from ``state``; each tab has its own."""

    def result(self) -> Static:
        return Static("", classes="result", markup=False)

    def report(self, message: str, *, error: bool = False, command: str | None = None) -> None:
        """Show ``message`` here and as a notification; with ``command`` (the same action in
        a terminal, teach.equivalent()), show it above and add both to Home's session log."""
        self.last_message = message
        self.query_one(".result", Static).update(result_text(message, command))
        self.app.notify(message, severity="error" if error else "information", markup=False)
        if command is not None:
            self.manage.log_action(teach.Entry(command, message, error))

    def attempt(
        self, action: Callable[[], str], *, reload: bool = True, command: str | None = None
    ) -> None:
        """Run a change; show its message, or the error a headless command would print."""
        try:
            message = action()
        except Exception as exc:
            self.report(_error(exc), error=True, command=command)
            return
        self.report(message, command=command)
        if reload:
            self.manage.reload()

    @work(thread=True, group="action", exit_on_error=False)
    def background(self, job: Callable[[], None]) -> None:
        """Run ``job`` in a thread (it may call a command or wait on Espanso); a failure is
        shown like attempt()'s, never a crashed screen."""
        try:
            job()
        except Exception as exc:
            self.app.call_from_thread(self.failed, exc)

    def failed(self, exc: Exception) -> None:
        self.busy = False
        self.report(_error(exc), error=True)
        self.manage.reload()


def _error(exc: Exception) -> str:
    """As commands/common.guard: a service error's own message; a bug named as unexpected."""
    if isinstance(exc, EXPECTED):
        return f"error: {str(exc) or type(exc).__name__}"
    return f"error: unexpected {type(exc).__name__}: {exc}"


# --- Home ---------------------------------------------------------------------------------


def _jump(tab: str | None) -> str:
    return f"-> {TAB_LABELS[tab]}" if tab else ""


def home_table(rows: Sequence[HomeRow]) -> Table:
    """Home's rows as a grid: the status word (coloured, but the word says it), the label, the
    text (cut with … when the terminal is narrow), the detail and, for a row that is not ok,
    the tab to go to."""
    grid = Table.grid(padding=(0, 2), expand=True)
    grid.add_column(width=4, no_wrap=True)
    grid.add_column(no_wrap=True)
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    grid.add_column(no_wrap=True, justify="right")
    for row in rows:
        label, color = doctor_cmd._LABEL[row.status]
        side = [row.detail] if row.detail else []
        if row.status != doctor.OK:
            side.append(_jump(row.jump))
        grid.add_row(
            Text(label, style=color),
            Text(row.label, style="bold"),
            Text(row.text),
            Text("  ".join(p for p in side if p)),
        )
    return grid


def home_headline(rows: Sequence[HomeRow]) -> Table:
    text, jump = headline(list(rows))
    grid = Table.grid(padding=(0, 2), expand=True)
    grid.add_column(ratio=1)
    grid.add_column(no_wrap=True, justify="right")
    grid.add_row(Text(text, style="bold"), Text(_jump(jump)))
    return grid


# The session log shows this many of the latest actions.
SESSION_LINES = 6


def session_text(entries: Sequence[teach.Entry]) -> Text:
    """Home's read-only session log (#111): what each action of this session was in a
    terminal, newest last, with the first line of its result. Before any, a few recipes."""
    if not entries:
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append("In a terminal, try:\n", style="bold")
        width = max(len(teach.equivalent(*r.argv)) for r in teach.RECIPES)
        for recipe in teach.RECIPES:
            text.append(f"  $ {teach.equivalent(*recipe.argv):<{width}}", style="dim")
            text.append(f"  {recipe.what}\n")
        text.append("Each button's tooltip shows its command too.", style="dim")
        return text
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append("This session, as commands:\n", style="bold")
    for entry in entries[-SESSION_LINES:]:
        text.append(f"  $ {entry.command}\n", style="dim")
        mark = "error: " if entry.error and not entry.summary.startswith("error") else ""
        text.append(f"    {mark}{entry.summary}\n")
    return text


class HomePane(Pane):
    """This install at a glance: a headline naming the worst problem, then one row per part
    (Mockup B, #112). The Diagnostics tab has every check; nothing here calls a provider."""

    rows: tuple[HomeRow, ...] = ()
    # A command line's run is under way (one at a time).
    running = False

    def compose(self) -> ComposeResult:
        yield Static("Checking…", markup=False, id="home-headline")
        yield Static("", id="home-rows")
        yield _buttons(("home-reload", "Check again"), ("home-previous", "Previous install…"))
        # The command line (#111): completes, explains and runs a command (console.POLICY).
        help_line = Static(describe(""), id="home-command-help", markup=False)
        yield CommandLine(help_line, host=self)
        yield help_line
        # What a run printed; shown from the first run on.
        output = RichLog(id="home-output", markup=False, highlight=False, wrap=True, max_lines=500)
        output.display = False
        yield output
        yield self.result()
        yield Static(session_text(()), id="home-session")

    def show(self, state: State) -> None:
        self.rows = tuple(home_rows(state))
        self.query_one("#home-headline", Static).update(home_headline(self.rows))
        self.query_one("#home-rows", Static).update(home_table(self.rows))

    def show_session(self, entries: Sequence[teach.Entry]) -> None:
        self.query_one("#home-session", Static).update(session_text(entries))

    def run_line(self, argv: tuple[str, ...], runner: Runner) -> None:
        """Run ``argv`` in a thread; its output goes below the command line when it ends.
        Only what a fresh parse decides to run starts (console.runnable(), checked again by
        console.run() itself)."""
        if not runnable(argv):
            self.report(f"error: refused to run {shown(argv)}", error=True)
            return
        self.running = True
        output = self.query_one("#home-output", RichLog)
        output.display = True
        self.background(lambda: self._run_now(argv, runner))

    def _run_now(self, argv: tuple[str, ...], runner: Runner) -> None:
        try:
            ran = runner(argv)
        except Exception as exc:  # it could not start: shown as its output
            ran = Ran(1, _error(exc))
        self.app.call_from_thread(self._ran, argv, ran)

    def _ran(self, argv: tuple[str, ...], ran: Ran) -> None:
        self.running = False
        done = transcript(argv, ran)
        output = self.query_one("#home-output", RichLog)
        output.write(Text(f"$ {done.command}", style="bold"))
        for line in done.lines:
            output.write(Text(line))
        output.write(Text(done.status, style="dim"))
        output.scroll_visible()
        prompt = self.query_one(CommandLine)
        if not prompt.value:  # not while the next line is being typed
            prompt.help_line.update(Text(f"$ {done.command}: {done.status} (output below)"))
        self.manage.log_action(teach.Entry(done.command, done.summary, done.error))
        self.manage.reload()

    def open_dialog(self, decision: Decision) -> str:
        """Open the dialog of the tab that owns ``decision``'s command; what to say in the
        help line."""
        main = self.manage.main
        values = decision.values
        triggers = main.query_one("#triggers-pane", TriggersPane)
        providers = main.query_one("#providers-pane", ProvidersPane)
        usage_history = main.query_one("#history-pane", HistoryPane)
        # Each console.DIALOG command -> its tab and what opens its dialog there.
        dialogs: dict[tuple[str, ...], tuple[str, Callable[[], None]]] = {
            ("espanso", "deploy"): ("triggers", triggers.start_deploy),
            ("espanso", "detach"): (
                "triggers",
                lambda: triggers.ask_detach(remove_all=values.get("keep_static") is False),
            ),
            ("config", "migrate"): ("providers", providers.migrate_env),
            ("secrets", "set"): ("providers", lambda: providers.set_key(values.get("name"))),
            ("secrets", "remove"): ("providers", lambda: providers.remove_key(values.get("name"))),
            ("history", "prune"): (
                "history",
                lambda: usage_history.prune(values.get("older_than")),
            ),
            ("history", "reset"): ("history", usage_history.reset),
        }
        tab, open_it = dialogs[decision.path]
        if tab == "triggers" and triggers.busy:
            return "A deploy or detach is already in progress; finish or cancel it first."
        main.action_show(tab)
        open_it()
        message = f"$ {shown(decision.argv)}: on {TAB_LABELS[tab]}, in its dialog"
        if decision.ignored:
            message += f" ({', '.join(decision.ignored)} ignored: the dialog asks)"
        return message + "."

    @on(Button.Pressed, "#home-reload")
    def _reload(self) -> None:
        self.manage.reload()

    @on(Button.Pressed, "#home-previous")
    def _previous(self) -> None:
        self.manage.open_previous()


# --- Providers ----------------------------------------------------------------------------


class ProvidersPane(Pane):
    def compose(self) -> ComposeResult:
        yield Static(
            "Keys are shown as set or not set and where they come from, never their value.",
            classes="note",
            markup=False,
        )
        yield DataTable(id="keys", cursor_type="none", zebra_stripes=True)
        yield Static("", markup=False, id="policy")
        yield DataTable(id="routes", cursor_type="none", zebra_stripes=True)
        yield _buttons(
            ("set-key", "Set key"),
            ("remove-key", "Remove key"),
            ("set-setting", "Change setting"),
            ("migrate-env", "Migrate .env"),
            ("smoke", "Test call (local stub)"),
        )
        yield self.result()

    def setup(self) -> None:
        self.query_one("#keys", DataTable).add_columns("Key", "State", "From", "Also set in")
        self.query_one("#routes", DataTable).add_columns(
            "Provider", "Model", "Base URL", "The draft", "Key"
        )

    def show(self, state: State) -> None:
        entries = state.layers.entries
        keys = self.query_one("#keys", DataTable)
        keys.clear()
        for name in secret_names():
            entry = entries[name]
            _add_row(
                keys,
                name,
                "set" if entry.value else "not set",
                common.short_label(entry.source) if entry.value else "",
                ", ".join(map(common.short_label, entry.shadows)),
            )
        cfg = state.settings
        policy = (
            "on: only providers that keep the draft on this machine run"
            if cfg.local_only
            else "off: cloud providers run, behind the data-protection gate"
        )
        self.query_one("#policy", Static).update(
            f"PROMPT_LOCAL_ONLY is {policy}.\nPROMPT_PROVIDER: {cfg.provider} (the default "
            "of a bare `improve`; the triggers name their own provider)."
        )
        table = self.query_one("#routes", DataTable)
        table.clear()
        for route in routes(cfg):
            where = "leaves this machine" if route.remote else "stays local"
            if route.refused:
                where = "refused (local only)"
            key = "not needed"
            if route.key:
                key = "set" if entries[route.key].value else "not set"
            model, url = entries[route.model_setting], entries[route.url_setting]
            _add_row(
                table,
                route.name,
                common.shown_value(route.model_setting, model),
                common.shown_value(route.url_setting, url),
                where,
                key,
            )

    @on(Button.Pressed, "#set-key")
    def _set_key(self) -> None:
        self.set_key()

    def set_key(self, name: str | None = None) -> None:
        """The hidden Set key dialog, with ``name`` (a key's name) picked (`secrets set`)."""
        fields = [
            Field("key-name", "Key", secret_names(), value=name or ""),
            Field("key-value", "Value (hidden as you type; never shown again)", secret=True),
        ]
        self.app.push_screen(
            FormModal("Save an API key in the secret store", fields), self._save_key
        )

    def _save_key(self, values: dict[str, str] | None) -> None:
        if values is None:
            return
        name, value = values["key-name"], values["key-value"].strip()
        if not value:
            self.report(f"no value entered; {name} was not changed", error=True)
            return

        def save() -> str:
            common.refuse_in_legacy_mode("the secret store")
            config_store.save_secret(name, value)
            note = settings_cmd.env_override(name)
            saved = f"{name} saved in the secret store ({config_store.config_dir()})"
            return f"{saved}; {note}" if note else saved

        self.attempt(save, command=teach.equivalent("secrets", "set", name))

    @on(Button.Pressed, "#remove-key")
    def _remove_key(self) -> None:
        self.remove_key()

    def remove_key(self, name: str | None = None) -> None:
        """Pick a saved key (``name`` first, when saved), then confirm (`secrets remove`)."""
        try:
            common.refuse_in_legacy_mode("the secret store")
            saved = config_store.saved_secret_names()
        except EXPECTED as exc:
            self.report(f"error: {exc}", error=True)
            return
        if not saved:
            self.report("The secret store holds no key; nothing to do.")
            return
        self.app.push_screen(
            FormModal(
                "Remove a key",
                [Field("key-name", "Key", saved, value=name or "")],
                submit="Remove…",
            ),
            self._confirm_remove,
        )

    def _confirm_remove(self, values: dict[str, str] | None) -> None:
        if values is None:
            return
        name = values["key-name"]

        def done(yes: bool | None) -> None:
            if yes:
                self.attempt(
                    lambda: self._delete(name),
                    command=teach.equivalent("secrets", "remove", name),
                )

        self.app.push_screen(
            ConfirmModal(f"Delete {name} from the secret store?", confirm="Delete"), done
        )

    def _delete(self, name: str) -> str:
        config_store.delete_secret(name)
        layers, _ = common.load_layers()
        entry = layers.entries[name]
        if entry.value:
            still = common.source_label(entry.source)
            return f"{name} removed from the secret store; it is still set, from {still}."
        return f"{name} removed from the secret store."

    @on(Button.Pressed, "#set-setting")
    def _set_setting(self) -> None:
        # Never the keys (Set key hides them) nor the persona, which no screen shows.
        names = [n for n in env_names() if n not in secret_names() and n not in common.PRIVATE]
        fields = [
            Field("setting-name", "Setting", names, value="PROMPT_PROVIDER"),
            Field("setting-value", "New value", placeholder="checked as the CLI reads it"),
        ]
        self.app.push_screen(FormModal("Change a setting (config.toml)", fields), self._save)

    def _save(self, values: dict[str, str] | None) -> None:
        if values is None:
            return
        name, value = values["setting-name"], values["setting-value"]

        def save() -> str:
            saved = settings_cmd.save_setting(name, value)
            notes = settings_cmd.after_save(name)
            return "; ".join([f"{name} saved in {saved.path}", *notes])

        self.attempt(save, command=teach.equivalent("config", "set", name, shown_arg(value)))

    @on(Button.Pressed, "#migrate-env")
    def _migrate(self) -> None:
        self.migrate_env()

    def migrate_env(self) -> None:
        """Preview the .env migration, then apply it on Yes (`config migrate`)."""
        try:
            plan = config_store.plan_migration()
        except EXPECTED as exc:
            self.report(f"error: {exc}", error=True)
            return
        if plan.status != "ready":
            self.report("\n".join(plan.describe()))
            return
        preview = "\n".join(
            [*plan.describe(), "", "A backup is kept; `promptmend config rollback` undoes it."]
        )

        def done(yes: bool | None) -> None:
            if yes:
                self.attempt(
                    lambda: self._apply_migration(plan.token),
                    command=teach.equivalent("config", "migrate"),
                )

        self.app.push_screen(ConfirmModal("Migrate the .env?", preview, confirm="Migrate"), done)

    def _apply_migration(self, token: str) -> str:
        result = config_store.apply_migration(consent=token)
        lines = [f"Migrated. Backup: {result.backup}"]
        lines += [f"{source} was not moved; it is no longer read" for source in result.left]
        return "\n".join(lines)

    @on(Button.Pressed, "#smoke")
    def _smoke(self) -> None:
        current = self.state.settings.provider if self.state else PROVIDER_NAMES[0]
        preview = (
            "Runs improve against a stub on 127.0.0.1 with a placeholder key. No provider is "
            "called, and neither your key nor your clipboard is sent."
        )
        fields = [Field("provider", "Provider", PROVIDER_NAMES, value=current)]
        self.app.push_screen(
            FormModal("Test call", fields, submit="Run", preview=preview), self._run_smoke
        )

    def _run_smoke(self, values: dict[str, str] | None) -> None:
        if values is not None:
            self.report(f"Running improve --provider {values['provider']} against the stub…")
            provider = values["provider"]
            self.background(lambda: self._smoke_now(provider))

    def _smoke_now(self, provider: str) -> None:
        result = smoke.run(provider)
        message = f"{'ok' if result.ok else 'failed'}: {result.message}"
        self.app.call_from_thread(self.report, message, error=not result.ok)


# --- Profiles -----------------------------------------------------------------------------


def editor_command(
    environ: Mapping[str, str] = os.environ,
    *,
    windows: bool = os.name == "nt",
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """$VISUAL or $EDITOR, else Notepad on Windows and vi elsewhere, as an argv. On Windows the
    quotes around a token are dropped (`"C:\\Program Files\\…\\ed.exe" -w`), and the program
    is looked up on PATH, so `code` finds code.cmd (PATHEXT)."""
    chosen = environ.get("VISUAL") or environ.get("EDITOR")
    if not chosen:
        argv = ["notepad"] if windows else ["vi"]
    else:
        argv = shlex.split(chosen, posix=not windows)
        if windows:
            argv = [
                token[1:-1] if len(token) > 1 and token[0] == token[-1] == '"' else token
                for token in argv
            ]
    return [which(argv[0]) or argv[0], *argv[1:]]


def run_editor(path: Path) -> int:
    """Open ``path`` in the editor and wait for it; tests replace it. No timeout: a person is
    editing."""
    return subprocess.run([*editor_command(), str(path)], check=False).returncode  # noqa: S603


class ProfilesPane(Pane):
    def compose(self) -> ComposeResult:
        yield Static("", markup=False, id="profile-settings")
        yield DataTable(id="profiles", cursor_type="row", zebra_stripes=True)
        yield _buttons(
            ("set-profile", "Set default"),
            ("edit-profile", "Edit in $EDITOR"),
            ("migrate-profiles", "Migrate from checkout"),
        )
        yield self.result()

    def setup(self) -> None:
        self.query_one("#profiles", DataTable).add_columns("Profile", "Where", "State")

    def show(self, state: State) -> None:
        cfg = state.settings
        self.query_one("#profile-settings", Static).update(
            f"PROMPT_PROFILE: {cfg.profile}\n"
            f"PROMPT_PRO_PROFILE: {cfg.pro_profile or '(empty: PROMPT_PROFILE)'}\n"
            f"Your profiles: {user_profiles_dir()}"
        )
        table = self.query_one("#profiles", DataTable)
        table.clear()
        for name in PROFILES:
            _add_row(table, name, "built in", "default" if name == cfg.profile else "", key=name)
        for alias, target in ALIASES.items():
            _add_row(table, alias, "built in", f"retired; means {target}", key=alias)
        for profile in state.profiles:
            _add_row(table, profile.name, "yours", profile.status, key=f"user:{profile.name}")

    @on(Button.Pressed, "#set-profile")
    def _set_profile(self) -> None:
        if self.state is None:
            return
        own = [p.name for p in self.state.profiles if p.status == ADDED]
        fields = [
            Field("profile", "PROMPT_PROFILE", [*PROFILES, *own], value=self.state.settings.profile)
        ]
        self.app.push_screen(FormModal("Default profile", fields), self._save_profile)

    def _save_profile(self, values: dict[str, str] | None) -> None:
        if values is not None:
            name = values["profile"]

            def save() -> str:
                saved = settings_cmd.save_setting("PROMPT_PROFILE", name)
                notes = settings_cmd.after_save("PROMPT_PROFILE")
                return "; ".join([f"PROMPT_PROFILE saved in {saved.path}", *notes])

            self.attempt(save, command=teach.equivalent("config", "set", "PROMPT_PROFILE", name))

    def selected(self) -> Path | None:
        table = self.query_one("#profiles", DataTable)
        if self.state is None or not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value or ""
        name = key.removeprefix("user:")
        found = [p.path for p in self.state.profiles if key.startswith("user:") and p.name == name]
        return found[0] if found else None

    @on(Button.Pressed, "#edit-profile")
    def _edit(self) -> None:
        from textual.app import SuspendNotSupported

        path = self.selected()
        if path is None:
            self.report(
                "Select one of your profiles. Built-in ones ship with the package, and an "
                "upgrade replaces them.",
                error=True,
            )
            return
        try:
            with self.app.suspend():
                code = run_editor(path)
        except SuspendNotSupported:
            self.report("This terminal cannot hand over to an editor.", error=True)
            return
        except OSError as exc:
            self.report(f"error: could not start the editor ({exc})", error=True)
            return
        self.report(f"{path.name}: the editor exited with {code}", error=code != 0)
        self.manage.reload()

    @on(Button.Pressed, "#migrate-profiles")
    def _migrate(self) -> None:
        # The earlier checkout detection found (#110), else this editable install's own.
        root = previous_root(self.state.previous) if self.state else None
        copy_profiles(self, root)


def previous_root(found: previous_install.Detection) -> Path | None:
    """The earlier checkout to offer: the best candidate, else the one a copy left pending."""
    best = previous_install.best(found.candidates)
    if best is not None:
        return best.root
    return found.pending.root if found.pending else None


class Reporter(Protocol):
    def report(self, message: str, *, error: bool = False, command: str | None = None) -> None: ...

    def attempt(
        self, action: Callable[[], str], *, reload: bool = True, command: str | None = None
    ) -> None: ...


def copy_profiles(owner: Reporter, root: Path | None) -> None:
    """Preview the profiles added or edited in the checkout at ``root`` (None: this editable
    install's), then copy them on Yes, as `profiles migrate --checkout` does."""
    try:
        root = profiles_cmd._checkout(None if root is None else str(root))
        source = root / profile_service.prompts_path(root)
        pristine = profile_service.git_pristine_profiles(root)
        changed = profile_service.changed_profiles(source, pristine)
    except EXPECTED as exc:
        owner.report(f"error: {exc}", error=True)
        return
    if not changed:
        owner.report(profile_service.NOTHING_CHANGED)
        return
    dest = user_profiles_dir()
    preview = "\n".join(
        [f"From {root}:"]
        + [f"{name}.md ({change}) -> {dest / f'{name}.md'}" for name, change in changed.items()]
    )

    command = teach.equivalent("profiles", "migrate", "--checkout", str(root))

    def done(yes: bool | None) -> None:
        if yes:
            owner.attempt(lambda: _copy(source, pristine, dest, list(changed)), command=command)

    cast("Widget", owner).app.push_screen(
        ConfirmModal("Copy these profiles? (copies only, never overwrites)", preview), done
    )


def _copy(source: Path, pristine: dict[str, str], dest: Path, names: list[str]) -> str:
    lines = [
        f"{item.name}: {item.status}"
        for item in profile_service.migrate_profiles(source, pristine, dest)
    ]
    hint = profile_service.overrides_hint(names, common.load_layers()[1].profile_overrides)
    if hint:
        lines.append(hint)
    return "\n".join(lines)


# --- Triggers -----------------------------------------------------------------------------


class TriggersPane(Pane):
    def compose(self) -> ComposeResult:
        yield Static(
            "Each trigger names its provider in its command line, so PROMPT_PROVIDER does not "
            "change these. An empty PROMPT_PRO_PROFILE means PROMPT_PROFILE.",
            classes="note",
            markup=False,
        )
        yield DataTable(id="triggers", cursor_type="none", zebra_stripes=True)
        yield Static("", markup=False, id="deploy-target")
        yield _buttons(
            ("show-diff", "Show diff"),
            ("deploy", "Deploy or repair"),
            ("detach", "Detach"),
        )
        yield self.result()

    def setup(self) -> None:
        self.query_one("#triggers", DataTable).add_columns(
            "Trigger", "Provider", "Tier", "Profile", "State", "File"
        )

    def show(self, state: State) -> None:
        states = {s.name: s.state for s in state.plan.steps} if state.plan else {}
        table = self.query_one("#triggers", DataTable)
        table.clear()
        for t in state.triggers:
            if t.command == "improve":
                provider = t.provider or "PROMPT_PROVIDER"
                profile = t.profile or (
                    "PROMPT_PRO_PROFILE" if t.tier == "pro" else "PROMPT_PROFILE"
                )
                tier = t.tier or "standard"
            else:
                provider = "prints PROMPT_PERSONA" if t.command == "persona" else "static snippet"
                profile = tier = "-"
            where = states.get(t.file, "unknown") if t.active else "commented out"
            _add_row(table, t.trigger, provider, tier, profile, where, t.file)
        target = self.query_one("#deploy-target", Static)
        if state.plan is None:
            target.update(f"Cannot compare with Espanso: {state.plan_error}")
        else:
            text = (
                f"Espanso match folder: {state.plan.espanso_dir / 'match'}\n"
                f"Launcher: {state.plan.launcher}"
            )
            if state.plan.yours:
                names = ", ".join(p.name for p in state.plan.yours)
                text += f"\nYour own match files (never touched by deploy or detach): {names}"
            espanso = next((c for c in state.report.checks if c.id == "espanso"), None)
            if espanso and (espanso.data.get("found") is False or espanso.data.get("query_failed")):
                # Deploy and detach then use the default folder: never silently (#115).
                text += f"\n{espanso.message}"
            target.update(text)

    @on(Button.Pressed, "#show-diff")
    def _diff(self) -> None:
        plan = self.state.plan if self.state else None
        if plan is None:
            self.report("No plan: Espanso's folder or this install's launcher was not found.")
            return
        diff = "".join(s.diff() for s in plan.steps if s.state != deploy.IN_SYNC)
        self.app.push_screen(
            TextModal("What deploy would change", diff or "Every match file is in sync.")
        )

    @on(Button.Pressed, "#deploy")
    def _deploy(self) -> None:
        self.start_deploy()

    def start_deploy(self) -> None:
        """Read the plan in a worker, then preview it in a dialog (also the previous install
        screen's step 3)."""
        if self.claim():
            self.report("Reading the match files…")
            self.background(lambda: self.app.call_from_thread(self._ask_deploy, current_plan()))

    def claim(self) -> bool:
        """Start a deploy or detach unless one is already open: a second dialog would apply
        a plan the first one already changed."""
        if self.busy:
            self.report("A deploy or detach is already in progress; finish or cancel it first.")
            return False
        self.busy = True
        return True

    def _ask_deploy(self, plan: deploy.Plan) -> None:
        if plan.is_noop:
            self.busy = False
            self.report("Nothing to do: every match file is in sync.")
            return
        if plan.only_forgets:  # the manifest only: no file is written, so nothing to ask
            self.report("Forgetting deploy records of files that are gone…")
            self.background(lambda: self._apply(plan, {}))
            return
        lines = [f"{s.state:<9} {s.name}" for s in plan.steps]
        if plan.legacy is not None:
            lines.append(f"legacy    {plan.legacy.name} will be retired, with a backup")
        lines += [f"forget    {key} (already gone)" for key in plan.orphans]
        diffs = [s.diff() for s in plan.steps if s.state not in (deploy.IN_SYNC, deploy.MISSING)]
        preview = "\n".join(lines) + ("\n\n" + "".join(diffs) if diffs else "")
        conflicts = plan.conflicts
        fields = [
            Field(
                f"choice-{i}",
                f"{s.name} is {s.state}: keep yours, take ours (yours backed up) or write ours "
                "side by side",
                deploy.CHOICES,
                value=deploy.KEEP,
            )
            for i, s in enumerate(conflicts)
        ]

        def done(values: dict[str, str] | None) -> None:
            if values is None:
                self.busy = False
                return
            choices = {s.name: values[f"choice-{i}"] for i, s in enumerate(conflicts)}
            self.report("Deploying…")
            self.background(lambda: self._apply(plan, choices))

        self.app.push_screen(
            FormModal("Deploy the match files", fields, submit="Deploy", preview=preview), done
        )

    def _apply(self, plan: deploy.Plan, choices: dict[str, str]) -> None:
        """In the worker: apply the plan the person saw, unless the files changed since."""
        if _plan_digest(current_plan()) != _plan_digest(plan):
            raise deploy.DeployError(
                "the match files or the deploy record changed since the preview; nothing was "
                "written. Press Deploy again to see the new plan"
            )
        outcome = deploy.apply(plan, choices)
        lines = list(outcome.lines)
        if outcome.changed and not deploy.restart_espanso():
            lines.append("Could not restart Espanso; run `espanso restart` yourself.")
        if outcome.kept:
            names = ", ".join(p.name for p in outcome.kept)
            lines.append(f"WARNING: kept as you have them and NOT updated: {names}.")
        else:
            lines.append("The match files are up to date.")
        self.app.call_from_thread(
            self._done, "\n".join(lines), bool(outcome.kept), teach.equivalent("espanso", "deploy")
        )

    def _done(self, message: str, error: bool, command: str | None = None) -> None:
        self.busy = False
        self.report(message, error=error, command=command)
        self.manage.reload()

    @on(Button.Pressed, "#detach")
    def _ask_detach_start(self) -> None:
        self.ask_detach()

    def ask_detach(self, *, remove_all: bool = False) -> None:
        """Read the deploy record in a worker, then ask (`espanso detach`); ``remove_all``
        picks that mode first."""
        if self.claim():
            self.background(
                lambda: self.app.call_from_thread(
                    self._ask_detach,
                    deploy.Manifest.load(),
                    deploy.espanso_dir().resolve(),
                    remove_all,
                )
            )

    def _ask_detach(self, manifest: deploy.Manifest, root: Path, remove_all: bool = False) -> None:
        if not manifest.entries:
            self.busy = False
            self.report("Nothing to do: promptmend has no deployed match files on record.")
            return
        preview = "Detach removes the files below that you have not edited:\n" + "\n".join(
            f"  {target}" for target in sorted(manifest.entries)
        )
        fields = [
            Field(
                "mode",
                "keep-static: only the matches that call the CLI; remove-all: every file",
                ("keep-static", "remove-all"),
                value="remove-all" if remove_all else "keep-static",
            )
        ]

        def done(values: dict[str, str] | None) -> None:
            if values is None:
                self.busy = False
                return
            remove_all = values["mode"] == "remove-all"
            self.background(lambda: self._detach_now(manifest, root, remove_all))

        self.app.push_screen(FormModal("Detach", fields, submit="Detach", preview=preview), done)

    def _detach_now(self, manifest: deploy.Manifest, root: Path, remove_all: bool) -> None:
        if _manifest_digest(deploy.Manifest.load()) != _manifest_digest(manifest):
            raise deploy.DeployError(
                "the deploy record changed since the preview; nothing was removed. Press "
                "Detach again"
            )
        outcome = deploy.detach(manifest, root, remove_all=remove_all)
        lines = list(outcome.lines)
        if outcome.changed and not deploy.restart_espanso():
            lines.append("Could not restart Espanso; run `espanso restart` yourself.")
        argv = ("espanso", "detach", *(["--remove-all"] if remove_all else []))
        self.app.call_from_thread(self._done, "\n".join(lines), False, teach.equivalent(*argv))


def _manifest_digest(manifest: deploy.Manifest) -> list[tuple[str, dict[str, Any]]]:
    return sorted((key, vars(entry)) for key, entry in manifest.entries.items())


def _plan_digest(plan: deploy.Plan) -> tuple[object, ...]:
    """What a deploy preview showed: each file's state and content, the legacy file, the
    launcher and the manifest it was compared with."""
    return (
        plan.espanso_dir,
        plan.launcher,
        plan.legacy,
        [(s.name, s.state, s.current) for s in plan.steps],
        _manifest_digest(plan.manifest),
        plan.orphans,
    )


# --- History ------------------------------------------------------------------------------


def _cost(row: history.StatsRow) -> str:
    badges = []
    if row.reported:
        badges.append(f"[reported] {usage._money(dict(row.reported))}")
    if row.estimated:
        badges.append(f"[estimated] {usage._money(dict(row.estimated))}")
    if row.unknown_cost_attempts:
        badges.append(f"[unknown] {row.unknown_cost_attempts}")
    if row.not_applicable_attempts:
        badges.append(f"[local] {row.not_applicable_attempts}")
    return "; ".join(badges) or "none recorded"


class HistoryPane(Pane):
    def compose(self) -> ComposeResult:
        yield Static(
            " ".join(usage.CAVEATS),
            classes="note",
            markup=False,
        )
        yield Static("", markup=False, id="disclosure")
        yield Select(
            [(f"By {g}", g) for g in history.GROUP_BY],
            value="trigger",
            allow_blank=False,
            id="group-by",
        )
        yield DataTable(id="stats", cursor_type="none", zebra_stripes=True)
        yield Static("", markup=False, id="stats-note")
        yield _buttons(
            ("export", "Export"),
            ("prune", "Prune"),
            ("reset", "Reset"),
        )
        yield self.result()

    def setup(self) -> None:
        self.query_one("#stats", DataTable).add_columns(
            "Group",
            "Calls",
            "Requests",
            "Last used, UTC",
            "p50 ms",
            "p95 ms",
            "Tok in",
            "Tok out",
            "Cost",
        )

    def show(self, state: State) -> None:
        self.query_one("#disclosure", Static).update("\n".join(usage.disclosure(state.settings)))
        table = self.query_one("#stats", DataTable)
        table.clear()
        for row in state.stats:
            tokens_in, tokens_out = usage._tokens(row)
            _add_row(
                table,
                row.key or "(none)",
                str(row.operations),
                str(row.attempts),
                row.last_used_utc[:16].replace("T", " "),
                usage._ms(row.latency_p50_ms),
                usage._ms(row.latency_p95_ms),
                "-" if tokens_in is None else str(tokens_in),
                "-" if tokens_out is None else str(tokens_out),
                _cost(row),
            )
        note = state.stats_error and f"error: {state.stats_error}"
        if not note and not state.stats:
            note = "No usage recorded yet."
        self.query_one("#stats-note", Static).update(note or "")

    @on(Select.Changed, "#group-by")
    def _group(self, event: Select.Changed) -> None:
        if isinstance(event.value, str) and event.value != self.manage.group_by:
            self.manage.group_by = event.value
            self.manage.reload()

    def _store(self) -> history.HistoryStore:
        _, settings = common.load_layers()
        return usage.store(settings)

    @on(Button.Pressed, "#export")
    def _export(self) -> None:
        fields = [
            Field("format", "Format", ("json", "csv")),
            Field("path", "Write to a new file (an existing one is never replaced)"),
        ]
        preview = "Metadata only: no draft, rewrite, persona or key is ever recorded."
        self.app.push_screen(
            FormModal("Export the usage history", fields, submit="Export", preview=preview),
            self._write_export,
        )

    def _write_export(self, values: dict[str, str] | None) -> None:
        if values is None:
            return

        def export() -> str:
            raw = values["path"].strip()
            if not raw:
                raise common.CommandError("enter the file to write")
            common.no_key(raw, "the file")
            path = Path(raw).expanduser()
            fmt: Any = values["format"]
            with path.open("x", encoding="utf-8", newline="") as out:
                count = self._store().export(out, fmt)
            return f"Exported {count} call(s) to {path}"

        fmt, raw = values["format"], values["path"].strip()
        argv = ("history", "export", "--format", fmt, "-o", shown_arg(raw) if raw else "<FILE>")
        self.attempt(export, reload=False, command=teach.equivalent(*argv))

    @on(Button.Pressed, "#prune")
    def _prune(self) -> None:
        self.prune()

    def prune(self, days: str | None = None) -> None:
        """Ask how old (``days`` filled in, else the retention), then confirm (`history
        prune`)."""
        if days is None:
            days = str(self.state.settings.history_retention_days) if self.state else ""
        fields = [Field("days", "Delete the records older than this many days", value=days)]
        self.app.push_screen(
            FormModal("Prune the usage history", fields, submit="Next…"), self._ask_prune
        )

    def _ask_prune(self, values: dict[str, str] | None) -> None:
        if values is None:
            return
        found = usage._AGE.fullmatch(values["days"].strip())
        if found is None:
            self.report("error: enter a number of days, e.g. 30", error=True)
            return
        days = int(found.group(1))
        cutoff = history.HistoryStore._cutoff(timedelta(days=days))[:16].replace("T", " ")

        def prune() -> str:
            deleted = self._store().prune(timedelta(days=days))
            return f"Deleted {deleted} call(s) older than {days} day(s)."

        def done(yes: bool | None) -> None:
            if yes:
                self.attempt(
                    prune, command=teach.equivalent("history", "prune", "--older-than", str(days))
                )

        self.app.push_screen(
            ConfirmModal(
                f"Delete every record older than {days} day(s)?",
                f"Everything recorded before {cutoff} UTC, with its requests, is deleted; this "
                "cannot be undone.",
                confirm="Delete",
            ),
            done,
        )

    @on(Button.Pressed, "#reset")
    def _reset(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Confirm, then delete every usage record (`history reset`)."""
        target = self._store()

        def reset() -> str:
            target.reset()
            return "The usage history is empty."

        def done(yes: bool | None) -> None:
            if yes:
                self.attempt(reset, command=teach.equivalent("history", "reset"))

        self.app.push_screen(
            ConfirmModal(
                f"Delete every usage record in {target.path}?",
                "This also clears the lost-write counter, and cannot be undone.",
                confirm="Delete all",
            ),
            done,
        )


# --- Diagnostics --------------------------------------------------------------------------


class DiagnosticsPane(Pane):
    def compose(self) -> ComposeResult:
        yield Static("", markup=False, id="layers")
        yield DataTable(id="settings", cursor_type="none", zebra_stripes=True)
        yield Static("", markup=False, id="findings")
        yield Static("", markup=False, id="all-checks")
        yield Static("", markup=False, id="store")
        yield _buttons(("import-check", "Check the trigger's import time"))
        yield self.result()

    def setup(self) -> None:
        self.query_one("#settings", DataTable).add_columns("Setting", "Value", "From", "Note")

    def show(self, state: State) -> None:
        layers = state.layers
        self.query_one("#layers", Static).update(
            "\n".join(settings_cmd._layers_lines(layers, raw=False))
        )
        table = self.query_one("#settings", DataTable)
        table.clear()
        for name in env_names():
            entry = layers.entries[name]
            notes = []
            if entry.shadows:
                notes.append("overrides " + ", ".join(map(common.short_label, entry.shadows)))
            if entry.rejected:
                bad = ", ".join(map(common.short_label, entry.rejected))
                notes.append(f"invalid value in {bad} ignored")
            _add_row(
                table,
                name,
                common.shown_value(name, entry),
                common.short_label(entry.source),
                "; ".join(notes),
                key=name,
            )
        findings = [f"{common.source_label(f.source)}: {f.message}" for f in layers.findings]
        self.query_one("#findings", Static).update(
            "Findings:\n" + "\n".join(f"  {line}" for line in findings)
            if findings
            else "No problems found in the settings files."
        )
        self.query_one("#all-checks", Static).update(_checks(state.report))
        data = {c.id: c.data for c in state.report.checks}
        stored, sqlite = data.get("history", {}), data.get("sqlite", {})
        lost = stored.get("lost_writes") or 0
        last = stored.get("last_lost_utc")
        self.query_one("#store", Static).update(
            f"SQLite {sqlite.get('version') or 'unknown'}; WAL reset bug (before 3.51.3): "
            f"{'yes' if sqlite.get('wal_reset_bug') else 'no'}\n"
            f"Lost history writes: {lost}{f' (last {last})' if last else ''}; tracking "
            f"{'incomplete' if stored.get('tracking_incomplete') else 'complete'}"
        )

    @on(Button.Pressed, "#import-check")
    def _import_check(self) -> None:
        self.report("Importing the CLI in a fresh interpreter…")
        self.background(self._import_now)

    def _import_now(self) -> None:
        found = doctor.import_check()
        self.app.call_from_thread(self.report, found.message, error=not found.ok)
