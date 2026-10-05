"""The interface's dialogs: a confirmation, a read-only text view (a diff) and a small form.

A destructive action always goes through one of them: nothing is deleted, overwritten or
deployed until its button is pressed, and Cancel (or Escape, or n) is the focused default.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static


@dataclass(frozen=True)
class Field:
    """One form field: a choice when ``options`` is set, else a text input (``secret`` hides
    what is typed)."""

    id: str
    label: str
    options: Sequence[str] = ()
    value: str = ""
    secret: bool = False
    placeholder: str = ""


class _Dialog[T](ModalScreen[T]):
    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, preview: str, cancel_value: T) -> None:
        super().__init__()
        self.dialog_title = title
        self.preview = preview
        self.cancel_value = cancel_value

    def heading(self) -> ComposeResult:
        yield Label(self.dialog_title, classes="dialog-title")
        if self.preview:
            with VerticalScroll(classes="preview"):
                yield Static(self.preview, markup=False)

    def action_cancel(self) -> None:
        self.dismiss(self.cancel_value)


class ConfirmModal(_Dialog[bool]):
    """Asks before a change; True only when the confirm button (or y) is pressed."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("n", "cancel", "No"),
        Binding("y", "confirm", "Yes"),
    ]

    def __init__(self, title: str, preview: str = "", confirm: str = "Yes") -> None:
        super().__init__(title, preview, False)
        self.confirm_label = confirm

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield from self.heading()
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self.confirm_label, id="confirm", variant="error")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def action_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#confirm")
    def _confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(False)


class TextModal(_Dialog[None]):
    """Shows text, such as a diff, to read; changes nothing."""

    def __init__(self, title: str, text: str) -> None:
        super().__init__(title, text, None)

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield from self.heading()
            with Horizontal(classes="buttons"):
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        self.query_one("#close", Button).focus()

    @on(Button.Pressed, "#close")
    def _close(self) -> None:
        self.dismiss(None)


class FormModal(_Dialog[dict[str, str] | None]):
    """A few fields and a submit button; returns the values by field id, or None when
    cancelled. The submit button is the confirmation for what the preview describes."""

    def __init__(
        self, title: str, fields: Sequence[Field], *, submit: str = "Save", preview: str = ""
    ) -> None:
        super().__init__(title, preview, None)
        self.fields = fields
        self.submit = submit

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield from self.heading()
            for item in self.fields:
                yield Label(item.label)
                if item.options:
                    yield Select(
                        [(option, option) for option in item.options],
                        value=item.value if item.value in item.options else item.options[0],
                        allow_blank=False,
                        id=item.id,
                    )
                else:
                    yield Input(
                        item.value,
                        placeholder=item.placeholder,
                        password=item.secret,
                        id=item.id,
                    )
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self.submit, id="submit", variant="primary")

    def values(self) -> dict[str, str]:
        found = {}
        for item in self.fields:
            if item.options:
                found[item.id] = str(self.query_one(f"#{item.id}", Select).value)
            else:
                found[item.id] = self.query_one(f"#{item.id}", Input).value
        return found

    @on(Button.Pressed, "#submit")
    @on(Input.Submitted)
    def _submit(self) -> None:
        self.dismiss(self.values())

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)
