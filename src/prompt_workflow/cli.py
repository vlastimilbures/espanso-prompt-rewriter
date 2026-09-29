from __future__ import annotations

import sys
from collections.abc import Callable

import pyperclip
import typer

from .config import EFFORTS, KEEP, TIERS, Settings, split_model_spec
from .factory import PROVIDER_NAMES, make_provider
from .prompt_builder import system_prompt
from .providers.base import ProviderError

app = typer.Typer(add_completion=False, no_args_is_help=True)

# A rough draft is a few hundred words; anything far larger is an accidental copy (a log,
# a whole document) that should neither go to the cloud nor stall the gate's scan.
MAX_DRAFT_CHARS = 50_000


@app.callback()
def _main() -> None:
    """Espanso-invoked prompt rewriter. Keeps ``improve`` as an explicit subcommand
    so the Espanso match files and docs (``prompt-workflow improve ...``) resolve."""


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
        model, endpoint = split_model_spec(model)
        cfg = (
            Settings.load()
            .for_tier(tier)
            .with_overrides(
                endpoint=endpoint, effort=effort, max_tokens=max_tokens, timeout=timeout
            )
        )
        draft = _read_input(source, text)
        if not draft.strip():
            raise ProviderError("Input is empty")
        if len(draft) > MAX_DRAFT_CHARS:
            raise ProviderError(f"Input is too long ({len(draft)} chars, max {MAX_DRAFT_CHARS})")

        # Data-protection gate: applied inside make_provider via GatedProvider
        # for openrouter/anthropic, so it cannot be bypassed.
        result = make_provider(provider or cfg.provider, cfg).generate(
            draft, system_prompt(profile or cfg.profile, cfg.persona), model=model
        )
        if copy:
            _clipboard(pyperclip.copy, result)
    except Exception as exc:
        # Nothing may traceback or exit nonzero: Espanso cannot surface stderr, so emit a
        # visible bracketed marker instead of a blank expansion.
        expected = isinstance(exc, ProviderError | ValueError)
        typer.echo(f"[prompt-workflow: {'' if expected else 'unexpected error: '}{exc}]", nl=False)
        raise typer.Exit(0) from None

    # No trailing newline: Espanso inserts stdout verbatim.
    typer.echo(result, nl=False)


PERSONA_PLACEHOLDER = "I am working as [role] in [company]."


@app.command()
def persona() -> None:
    """Print PROMPT_PERSONA for the -p- snippet, or a fill-in placeholder when unset."""
    try:
        text = Settings.load().persona or PERSONA_PLACEHOLDER
    except Exception:
        # Same contract as improve: never a traceback or blank expansion in Espanso.
        text = PERSONA_PLACEHOLDER
    typer.echo(text, nl=False)


if __name__ == "__main__":
    app()
