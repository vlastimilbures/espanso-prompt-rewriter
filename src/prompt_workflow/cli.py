from __future__ import annotations

import io
import re
import sys
import unicodedata
from collections.abc import Callable

import pyperclip
import typer

from .config import EFFORTS, KEEP, TIERS, Settings
from .factory import PROVIDER_NAMES, make_provider
from .gate import GatedProvider
from .prompt_builder import TEMPLATE_MARKER, repair_template_tags, system_prompt
from .providers.base import ProviderError
from .redaction import DEFAULT_IGNORABLE

app = typer.Typer(add_completion=False, no_args_is_help=True)

# A rough draft is a few hundred words; anything far larger is an accidental copy (a log,
# a whole document) that should neither go to the cloud nor stall the gate's scan.
MAX_DRAFT_CHARS = 50_000

# Characters no prompt needs that do harm where Espanso pastes the text: C0/C1 controls other
# than tab and newline (an escape sequence can end a terminal's bracketed paste, so the lines
# after it run as commands), and every default-ignorable code point (invisible text that can
# smuggle instructions to the next model, or make text read differently than it is: zero-width
# and bidi characters, Unicode tags, Hangul fillers, variation selectors). The joiners and the
# emoji presentation selectors are judged by _keep_run() instead.
_UNSAFE_CHARS = re.compile(
    f"(?![\u200c\u200d\ufe0e\ufe0f])[\x00-\x08\x0b-\x1f\x7f-\x9f{DEFAULT_IGNORABLE}]"
)
# Other format characters (category Cf) are dropped as a class, so ones Unicode adds later are
# too, except the visible Arabic and Kaithi prepended concatenation marks.
_KEEP_CF = frozenset("\u200c\u200d\u0600\u0601\u0602\u0603\u0604\u0605\u06dd\u070f") | {
    "\u0890",
    "\u0891",
    "\u08e2",
    "\U000110bd",
    "\U000110cd",
}
# Line breaks other than \n would join or split lines differently where the text is pasted.
_LINE_BREAKS = str.maketrans(dict.fromkeys("\r\x0b\x0c\x85\u2028\u2029", "\n"))
# After a visible character, emoji need at most one text/emoji selector and one joiner
# (❤️‍🔥 is U+2764 U+FE0F U+200D U+1F525); a longer run could encode hidden bytes.
_SELECTOR_RUN = re.compile(r"[\u200c\u200d\ufe0e\ufe0f]+")
_ALLOWED_RUN = re.compile(r"[\ufe0e\ufe0f]?[\u200c\u200d]?")
# The only ASCII characters an emoji selector follows: keycaps (#️⃣, 1️⃣).
_KEYCAP_BASES = frozenset("#*0123456789")


def _keep_run(match: re.Match[str]) -> str:
    """Keep a selector/joiner run only in the form real text uses: one selector and one
    joiner, right after a visible character. After an ASCII character only a keycap's
    selector is kept, since no ASCII text needs a joiner, so English prose carries none."""
    run, start = match.group(), match.start()
    prev = match.string[start - 1] if start else " "
    if prev.isspace() or prev == "\u2800" or not _ALLOWED_RUN.fullmatch(run):
        return ""
    if prev.isascii() and (prev not in _KEYCAP_BASES or run not in ("\ufe0e", "\ufe0f")):
        return ""
    return run


def _clean(text: str) -> str:
    text = _UNSAFE_CHARS.sub("", text.replace("\r\n", "\n").translate(_LINE_BREAKS))
    if not text.isascii():  # ASCII holds no format characters
        text = "".join(c for c in text if c in _KEEP_CF or unicodedata.category(c) != "Cf")
    # After every other removal, so a dropped character cannot split a run into allowed pieces.
    return _SELECTOR_RUN.sub(_keep_run, text)


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


def _sent_despite_note(built: object) -> str:
    """A draft the gate let through with --allow-flagged says so where it is pasted, even when
    the call failed after the gate, naming only the findings."""
    if isinstance(built, GatedProvider) and built.sent_despite:
        return f"[prompt-workflow: sent despite: {', '.join(built.sent_despite)}]\n\n"
    return ""


# Options are plain strings, not Enum choices: Typer would reject a bad value with a usage
# error on stderr and exit 2, which Espanso cannot show. make_provider() reports it inline.
@app.command()
def improve(
    provider: str | None = typer.Option(None, help=", ".join(PROVIDER_NAMES)),
    profile: str | None = typer.Option(
        None,
        help=(
            "Defaults to PROMPT_PROFILE (PROMPT_PRO_PROFILE, if set, with --tier pro on the pro "
            "model)"
        ),
    ),
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
    allow_flagged: bool = typer.Option(
        False,
        "--allow-flagged",
        help="Send this draft once despite labels, IDs, emails or IBANs the gate flagged "
        "(never keys, cards or passwords); the output says so",
    ),
) -> None:
    """Improve a draft prompt. Errors are printed inline so Espanso shows them."""
    built: object = None
    try:
        cfg = Settings.load().for_call(
            tier, model=model, effort=effort, max_tokens=max_tokens, timeout=timeout
        )
        raw = _read_input(source, text)
        # Checked before cleaning, which would otherwise run over a pasted multi-megabyte log.
        if len(raw) > MAX_DRAFT_CHARS:
            raise ProviderError(f"Input is too long ({len(raw)} chars, max {MAX_DRAFT_CHARS})")
        draft = _clean(raw)
        if not draft.strip():
            raise ProviderError("Input is empty")

        # Data-protection gate: make_provider wraps anything that can send the draft off this
        # machine in GatedProvider, so it cannot be bypassed. The result is cleaned here too
        # because --copy puts it on the clipboard.
        system = system_prompt(profile or cfg.profile, cfg.persona)
        built = make_provider(provider or cfg.provider, cfg, allow_flagged=allow_flagged)
        result = built.generate(draft, system)
        if TEMPLATE_MARKER in system:
            result = repair_template_tags(result)
        result = _clean(result)
        if copy:
            _clipboard(pyperclip.copy, result)
    except Exception as exc:
        # Nothing may traceback or exit nonzero: Espanso cannot surface stderr, so emit a
        # visible bracketed marker instead of a blank expansion.
        expected = isinstance(exc, ProviderError | ValueError)
        error = f"[prompt-workflow: {'' if expected else 'unexpected error: '}{exc}]"
        _emit(_sent_despite_note(built) + error)
        raise typer.Exit(0) from None

    _emit(_sent_despite_note(built) + result)


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
