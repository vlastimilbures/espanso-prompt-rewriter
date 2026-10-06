"""The intro shown as the interface opens and the About screen on `a` (#112). Both are
static text (no animation) in a Textual border; the intro closes by itself after a moment, or
on any key or click, and the key that closes it does nothing else."""

from __future__ import annotations

from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from . import brand

INTRO_SECONDS = 0.8


class IntroScreen(ModalScreen[None]):
    """The wordmark, name, version and tagline over the main screen while the state loads.
    ``seconds`` None keeps it open until a key or click (tests)."""

    DEFAULT_CSS = """
    IntroScreen { align: center middle; }
    #intro { width: auto; max-width: 95%; height: auto; border: round $primary;
             padding: 1 3; background: $surface; }
    """

    def __init__(self, seconds: float | None = INTRO_SECONDS, version: str | None = None) -> None:
        super().__init__()
        self.seconds = seconds
        self.version = version

    def compose(self) -> ComposeResult:
        yield Static(brand.splash(self.app.size.width, self.version), id="intro", markup=False)

    def on_mount(self) -> None:
        if self.seconds is not None:
            self.set_timer(self.seconds, self.close)

    def close(self) -> None:
        # The timer may fire after a key already closed it.
        if self.is_current:
            self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        # Consumed: the key that closes the intro must not also switch a tab or quit.
        event.stop()
        event.prevent_default()
        self.close()

    def on_click(self, event: events.Click) -> None:
        event.stop()
        event.prevent_default()
        self.close()


class AboutScreen(ModalScreen[None]):
    """Version, install channel, runtime, folders, licence and repository; Escape, `a`, `q`
    or Close leaves it."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close"),
        Binding("a", "close", "Close", show=False),
        Binding("q", "close", "Close", show=False),
    ]

    def __init__(self, facts: list[str]) -> None:
        super().__init__()
        self.facts = facts

    def compose(self) -> ComposeResult:
        width = self.app.size.width
        mark = "\n".join(brand.WORDMARK) + "\n\n" if width > brand.NARROW else ""
        with Vertical(classes="dialog", id="about"):
            yield Static(f"{mark}{brand.TAGLINE}", markup=False, classes="dialog-title")
            yield Static("\n".join(self.facts), markup=False, id="about-facts")
            yield Button("Close", id="about-close")

    def on_mount(self) -> None:
        self.query_one("#about-close", Button).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)
