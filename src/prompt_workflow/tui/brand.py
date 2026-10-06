"""The interface's wordmark and About facts (#112). Plain ASCII only, so every terminal, font,
NO_COLOR and screen reader shows it the same; the frame around it is a Textual border, not
box-drawing text. No Textual here, so a test can check it without the interface."""

from __future__ import annotations

import platform
from collections.abc import Mapping

from .. import __version__, config, config_store

NAME = "prompt-workflow"
TAGLINE = "A rough draft in, a precise prompt out: type -i- in any text field."
REPO_URL = "https://github.com/vlastimilbures/espanso-prompt-rewriter"
LICENCE = "MIT"
# A terminal this narrow (or narrower) gets the text alone: the mark would wrap.
NARROW = 70

WORDMARK = (
    " .-------.                                 .----------.",
    " | draft |  -->  [ prompt-workflow ]  -->  |  prompt  |",
    " '-------'                                 '----------'",
)


def splash(width: int, version: str | None = None) -> str:
    """The intro's text: the wordmark (when ``width`` columns hold it), the name and version,
    and the tagline."""
    title = f"{NAME} {version or __version__}"
    lines = [title, "", TAGLINE]
    if width > NARROW:
        lines = [*WORDMARK, "", *lines]
    return "\n".join(lines)


def runtime() -> dict[str, str]:
    """The Python and Textual versions this interface runs on. Textual is imported here only
    when the About screen asks, and the interface is already running on it."""
    import textual

    return {"python": platform.python_version(), "textual": textual.__version__}


def about_facts(install: Mapping[str, object] | None, version: str | None = None) -> list[str]:
    """What the About screen lists: version, install channel (doctor's install check, from
    the State; unknown before it is read), runtime, where settings and history live, licence
    and repository. Paths only, never a setting's value."""
    channel = str((install or {}).get("channel") or "unknown")
    found = runtime()
    return [
        f"Version:   {NAME} {version or __version__}",
        f"Installed: {channel}",
        f"Runs on:   Python {found['python']}, Textual {found['textual']}",
        f"Settings:  {config_store.config_dir()}",
        f"History:   {config.user_data_dir()}",
        f"Licence:   {LICENCE}",
        f"Source:    {REPO_URL}",
    ]
