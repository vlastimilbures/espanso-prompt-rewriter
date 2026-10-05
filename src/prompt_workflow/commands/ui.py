"""`ui`: the full-screen interface (#93), also what a bare `prompt-workflow` opens on a
terminal (D-UI-1). Textual is imported here only once the interface opens, so --help, the
triggers and every other command never load it."""

from __future__ import annotations

import sys

import typer

from . import common

app = typer.Typer(name="ui", add_completion=False)


def on_a_terminal() -> bool:
    """Both stdin and stdout are a terminal: a person is there to use the interface."""
    return common.stdin_is_tty() and common.stdout_is_tty()


@app.command("ui", short_help="Open the full-screen interface to set up and manage (a terminal).")
def ui() -> None:
    """Open the full-screen interface: status, providers and keys, profiles, triggers, usage
    history and diagnostics. It needs a terminal; every screen has a headless command (see
    --help), which scripts and screen readers can use instead."""
    if not on_a_terminal():
        common.fail(
            "the interface needs a terminal (stdin and stdout); use the headless commands "
            "instead (see --help)",
            common.NEEDS_TERMINAL,
        )
    from ..tui.app import ManageApp

    interface = ManageApp()
    interface.run()
    sys.exit(interface.return_code or 0)
