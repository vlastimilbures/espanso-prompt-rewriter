from __future__ import annotations

import io
import re
import sys
from collections.abc import Callable

import pyperclip
import typer

from .config import EFFORTS, KEEP, TIERS, Settings
from .factory import PROVIDER_NAMES, make_provider
from .prompt_builder import system_prompt
from .providers.base import ProviderError

app = typer.Typer(add_completion=False, no_args_is_help=True)

# A rough draft is a few hundred words; anything far larger is an accidental copy (a log,
# a whole document) that should neither go to the cloud nor stall the gate's scan.
MAX_DRAFT_CHARS = 50_000

# Characters no prompt needs that do harm where Espanso types the text: C0/C1 controls other
# than tab and newline (an escape sequence can end a terminal's bracketed paste, so the lines
# after it run as commands), bidi overrides (text that reads differently than it is), and
# Unicode tag characters (invisible text that can smuggle instructions to the next model).
_UNSAFE_CHARS = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069\U000e0000-\U000e007f]"
)


def _clean(text: str) -> str:
    return _UNSAFE_CHARS.sub("", text)


def _emit(text: str) -> None:
    """The only way anything reaches Espanso: unsafe characters stripped, and no trailing
    newline, since Espanso inserts stdout verbatim."""
    typer.echo(_clean(text), nl=False)


@app.callback()
def _main() -> None:
    """Espanso-invoked prompt rewriter. Keeps ``improve`` as an explicit subcommand
    so the Espanso match files and docs (``prompt-workflow improve ...``) resolve."""
    # A piped stdout uses the ANSI code page on Windows, which lacks many letters (Czech ř,
    # Vietnamese ố): printing such a rewrite would crash into a blank expansion.
    for stream in (sys.stdin, sys.stdout):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _clipboard[T](op: Callable[..., T], *args: str) -> T:
    try:
        return op(*args)
    except pyperclip.PyperclipException as exc:
        raise ProviderError(f"Clipboard unavailable: {exc}") from exc


def _read_input(source: str, text: str | None) -> str:
    if source == "clipboard":
        return str(_clipboard(pyperclip.paste))
    if source == "stdin":
        return sys.stdin.read()
    if source == "argument":
        return text or ""
    raise ProviderError("source must be clipboard, stdin, or argument")


# Options are plain strings, not Enum choices: Typer would reject a bad value with a usage
# error on stderr and exit 2, which Espanso cannot show. make_provider() reports it inline.
@app.command()
def improve(
    provider: str | None = typer.Option(None, help=", ".join(PROVIDER_NAMES)),
    profile: str | None = typer.Option(None, help="Defaults to PROMPT_PROFILE, e.g. default"),
    model: str | None = typer.Option(
        None, help="Override the model for this call; slug@endpoint also pins the endpoint"
    ),
    tier: str = typer.Option(
        "standard", help=f"{', '.join(TIERS)}; pro uses the OPENROUTER_PRO_* reasoning model"
    ),
    effort: str | None = typer.Option(
        None, help=f"Reasoning effort for this call: {', '.join((KEEP, *EFFORTS))}"
    ),
    max_tokens: str | None = typer.Option(None, help=f"Output cap for this call, or {KEEP}"),
    timeout: str | None = typer.Option(None, help=f"Request timeout in seconds, or {KEEP}"),
    source: str = typer.Option("clipboard", help="clipboard, stdin, or argument"),
    text: str | None = typer.Option(None, help="Input when source is argument"),
    copy: bool = typer.Option(
        False, help="Also copy output to clipboard (overwrites --source clipboard's draft)"
    ),
) -> None:
    """Improve a draft prompt. Errors are printed inline so Espanso shows them."""
    try:
        cfg = (
            Settings.load()
            .for_tier(tier)
            .with_overrides(model=model, effort=effort, max_tokens=max_tokens, timeout=timeout)
        )
        draft = _clean(_read_input(source, text))
        if not draft.strip():
            raise ProviderError("Input is empty")
        if len(draft) > MAX_DRAFT_CHARS:
            raise ProviderError(f"Input is too long ({len(draft)} chars, max {MAX_DRAFT_CHARS})")

        # Data-protection gate: make_provider wraps anything that can send the draft off this
        # machine in GatedProvider, so it cannot be bypassed. The result is cleaned here too
        # because --copy puts it on the clipboard.
        result = _clean(
            make_provider(provider or cfg.provider, cfg).generate(
                draft, system_prompt(profile or cfg.profile, cfg.persona)
            )
        )
        if copy:
            _clipboard(pyperclip.copy, result)
    except Exception as exc:
        # Nothing may traceback or exit nonzero: Espanso cannot surface stderr, so emit a
        # visible bracketed marker instead of a blank expansion.
        expected = isinstance(exc, ProviderError | ValueError)
        _emit(f"[prompt-workflow: {'' if expected else 'unexpected error: '}{exc}]")
        raise typer.Exit(0) from None

    _emit(result)


PERSONA_PLACEHOLDER = "I am working as [role] in [company]."


@app.command()
def persona() -> None:
    """Print PROMPT_PERSONA for the -p- snippet, or a fill-in placeholder when unset."""
    try:
        text = Settings.load().persona or PERSONA_PLACEHOLDER
    except Exception:
        # Same contract as improve: never a traceback or blank expansion in Espanso.
        text = PERSONA_PLACEHOLDER
    _emit(text)


if __name__ == "__main__":
    app()
