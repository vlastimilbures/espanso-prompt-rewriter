"""The Espanso match files, shipped in the wheel as package data.

The repo's `espanso/match/` stays the source of truth; Hatch's force-include copies it to
`prompt_workflow/espanso/match/` when the wheel is built. An editable install maps only
`src/prompt_workflow`, so there the files are read from the checkout instead.
"""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable

from .config import _PROJECT_ROOT
from .redaction import safe_repr


def match_dir() -> Traversable:
    """The folder holding the match files: the packaged copy, else the editable checkout's."""
    packaged = files(__package__) / "espanso" / "match"
    if packaged.is_dir():
        return packaged
    checkout = _PROJECT_ROOT / "espanso" / "match"
    if checkout.is_dir():
        return checkout
    raise FileNotFoundError("The Espanso match files are missing from this installation")


def match_names() -> list[str]:
    """File names of the match files (`prompts-llm.yml`, ...), sorted."""
    return sorted(p.name for p in match_dir().iterdir() if p.name.endswith(".yml"))


def read_match(name: str) -> str:
    """One match file's text, as shipped (the CLI path still a placeholder)."""
    if name not in match_names():
        raise ValueError(f"No match file named {safe_repr(name)}")
    return (match_dir() / name).read_text(encoding="utf-8")
