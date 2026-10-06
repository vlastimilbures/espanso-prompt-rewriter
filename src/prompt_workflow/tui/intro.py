"""The intro shown as the interface opens and the About screen on `a` (#112). Both are
static text (no animation) in a Textual border. The intro stays until any key or click
(#173), with a hint naming Enter and the command that turns it off; the key that closes it
does nothing else."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from . import brand, teach

# Under the splash (#173): the setting, not a hidden toggle, so `config show` lists it.
INTRO_HINT = f"Enter to continue · turn off: {teach.equivalent(*teach.INTRO_OFF)}"


class IntroScreen(ModalScreen[None]):
    """The wordmark, name, version and tagline over the main screen while the state loads,
    and the hint (muted). No timer: a person reads it, then presses a key."""

    DEFAULT_CSS = """
    IntroScreen { align: center middle; }
    #intro { width: auto; max-width: 95%; height: auto; border: round $primary;
             padding: 1 3; background: $surface; }
    """

    def __init__(self, version: str | None = None) -> None:
        super().__init__()
        self.version = version

    def compose(self) -> ComposeResult:
        text = Text(brand.splash(self.app.size.width, self.version))
        text.append(f"\n\n{INTRO_HINT}", style="dim")
        yield Static(text, id="intro")

    def close(self) -> None:
        # A second key or click may arrive before the first dismiss has taken effect.
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
