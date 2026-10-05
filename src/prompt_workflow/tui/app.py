"""The interface's app: six tabs over one State, read again in a worker thread after every
change, a high-contrast theme, and key bindings that every terminal delivers (letters, digits
and Ctrl; no Alt or Cmd). Textual honours NO_COLOR itself."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.screen import Screen
from textual.theme import Theme
from textual.widgets import Footer, Header, TabbedContent, TabPane

from .panes import (
    DiagnosticsPane,
    HistoryPane,
    HomePane,
    Pane,
    ProfilesPane,
    ProvidersPane,
    TriggersPane,
)
from .state import State, gather

DEFAULT_THEME = "textual-dark"
HIGH_CONTRAST = Theme(
    name="high-contrast",
    primary="#ffff00",
    secondary="#00ffff",
    accent="#ff00ff",
    foreground="#ffffff",
    background="#000000",
    surface="#000000",
    panel="#1a1a1a",
    success="#00ff00",
    warning="#ffff00",
    error="#ff6060",
    dark=True,
)

# Tab id -> (label, pane). The digit in each label is its key.
TABS: dict[str, tuple[str, type[Pane]]] = {
    "home": ("1 Home", HomePane),
    "providers": ("2 Providers & keys", ProvidersPane),
    "profiles": ("3 Profiles", ProfilesPane),
    "triggers": ("4 Triggers", TriggersPane),
    "history": ("5 History", HistoryPane),
    "diagnostics": ("6 Diagnostics", DiagnosticsPane),
}

CSS = """
Pane { padding: 0 1; }
.note { color: $text-muted; margin-bottom: 1; }
DataTable { height: auto; max-height: 14; margin-bottom: 1; }
#policy, #deploy-target, #disclosure, #layers, #findings, #all-checks, #profile-settings {
    margin-bottom: 1;
}
#group-by { width: 30; margin-bottom: 1; }
.buttons { height: auto; margin-top: 1; }
.buttons Button { margin-right: 1; }
.result { margin-top: 1; }
ModalScreen { align: center middle; }
.dialog {
    width: 100; max-width: 95%; height: auto; max-height: 90%;
    border: thick $primary; background: $surface; padding: 1 2;
}
.dialog-title { text-style: bold; margin-bottom: 1; }
.preview { height: auto; max-height: 18; border: round $panel-lighten-2; margin-bottom: 1; }
.dialog Select, .dialog Input { margin-bottom: 1; }
"""


class MainScreen(Screen[None]):
    # On this screen, not the app, so no letter reaches the app while a dialog is open.
    BINDINGS: ClassVar[list[BindingType]] = [
        *(
            Binding(str(n), f"show('{tab}')", label, show=False)
            for n, (tab, (label, _)) in enumerate(TABS.items(), start=1)
        ),
        Binding("r", "app.reload", "Reload"),
        Binding("t", "app.toggle_contrast", "High contrast"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="home"):
            for tab, (label, pane) in TABS.items():
                with TabPane(label, id=tab):
                    yield pane(id=f"{tab}-pane")
        yield Footer()

    def action_show(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab


class ManageApp(App[int]):
    TITLE = "prompt-workflow"
    SUB_TITLE = "set up and manage"
    CSS = CSS
    # The palette would offer screenshots written to the working directory and other extras
    # with no headless counterpart.
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, *, loader: Callable[[str], State] = gather) -> None:
        super().__init__()
        self.loader = loader
        self.group_by = "trigger"
        self.state: State | None = None
        self.main = MainScreen()
        self.register_theme(HIGH_CONTRAST)
        self.theme = DEFAULT_THEME

    def on_mount(self) -> None:
        self.push_screen(self.main)
        self.reload()

    def reload(self) -> None:
        """Read everything again (settings, doctor, plan, stats) and refill every tab."""
        self._load()

    def action_reload(self) -> None:
        self.reload()

    def action_toggle_contrast(self) -> None:
        self.theme = DEFAULT_THEME if self.theme == HIGH_CONTRAST.name else HIGH_CONTRAST.name

    @work(thread=True, exclusive=True, group="load", exit_on_error=False)
    def _load(self) -> None:
        try:
            state = self.loader(self.group_by)
        except Exception as exc:
            self.call_from_thread(
                self.notify,
                f"Could not read the state: {type(exc).__name__}: {exc}",
                severity="error",
                markup=False,
            )
            return
        self.call_from_thread(self._show, state)

    def _show(self, state: State) -> None:
        self.state = state
        for pane in self.main.query(Pane):
            if pane.ready:
                pane.show(state)
