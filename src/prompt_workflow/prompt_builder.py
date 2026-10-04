from __future__ import annotations

import re
from importlib.resources import files

from .redaction import safe_repr

_PROMPT_DIR = files(__package__) / "prompts"


def _load_profiles() -> dict[str, str]:
    return {
        p.name.removesuffix(".md"): p.read_text(encoding="utf-8").strip()
        for p in _PROMPT_DIR.iterdir()
        if p.name.endswith(".md")
    }


PROFILES: dict[str, str] = _load_profiles()

# Retired profile names that still resolve, so an existing .env or --profile keeps working.
# `default-pro` was `default` minus one clause; one prompt now serves both tiers (#44).
ALIASES = {"default-pro": "default"}

# Replaced in a profile by the rule for how CONTEXT opens (see PROMPT_PERSONA).
PERSONA_TOKEN = "{{PERSONA_RULE}}"  # noqa: S105 - a template placeholder, not a secret


def persona_rule(persona: str) -> str:
    if persona:
        return (
            f'open with "{persona}" If the draft states a different role, company, or '
            "persona, use that one instead."
        )
    return (
        "if the draft explicitly states the user's role, company, or persona, open with it; "
        "otherwise do not state or guess any role and open directly with what is wanted."
    )


def render(template: str, persona: str = "") -> str:
    """Fill a profile's tokens. Plain replace, not str.format: templates contain braces."""
    return template.replace(PERSONA_TOKEN, persona_rule(persona))


def system_prompt(profile: str, persona: str = "") -> str:
    try:
        template = PROFILES[profile if profile in PROFILES else ALIASES.get(profile, profile)]
    except KeyError as exc:
        known = ", ".join(PROFILES)
        raise ValueError(f"Unknown profile: {safe_repr(profile)}. Choose from: {known}") from exc
    return render(template, persona)


# Present in every profile that emits the golden template (CONTEXT ... OUTPUTS).
TEMPLATE_MARKER = "<output_template>"

# flash-lite sometimes closes CONTEXT with the next section's tag, "<CONTEXT>...</GOAL>" right
# before "<GOAL>", a decoding slip that comes and goes with unrelated wording changes. Only
# that exact pattern is repaired, and only in the rewrite's first section, with each tag on its
# own line: any other malformed output, and tags inside pasted material, stay as they are.
_CONTEXT_CLOSED_AS_GOAL = re.compile(
    r"(<CONTEXT>(?:(?!</CONTEXT>|</?GOAL>).)*?\n[ \t]*)</GOAL>([ \t]*\r?\n\s*<GOAL>[ \t]*\r?\n)",
    re.DOTALL,
)


def repair_template_tags(text: str) -> str:
    """Close a CONTEXT section that the model closed with </GOAL> just before <GOAL>."""
    start = text.find("<CONTEXT>")
    slip = _CONTEXT_CLOSED_AS_GOAL.match(text, start) if start >= 0 else None
    if slip is None:
        return text
    return text[: slip.end(1)] + "</CONTEXT>" + text[slip.start(2) :]


# A fence line: three or more backticks or tildes, optionally followed by an info string
# (a language tag, maybe with attributes).
_FENCE = re.compile(r"(`{3,}|~{3,})[ \t]*([^`\n]{0,80})")


def strip_outer_fence(text: str) -> str:
    """Remove a code fence wrapped around the whole reply, which Espanso would otherwise paste
    as-is. Only when the first and last lines open and close one block: fences inside it
    (tagged, or longer than the outer one) are kept, and a reply with text outside the fence,
    two blocks, an untagged inner block, an unclosed fence or nothing inside is returned
    unchanged."""
    lines = text.strip().splitlines()
    opening = _FENCE.fullmatch(lines[0].strip()) if len(lines) > 2 else None
    if opening is None:
        return text
    fence = opening.group(1)
    closing = lines[-1].strip()
    if closing[:1] != fence[0] or closing.strip(fence[0]) or len(closing) < len(fence):
        return text
    body = lines[1:-1]
    depth = 0
    for line in body:
        inner = _FENCE.fullmatch(line.strip())
        if inner is None or inner.group(1)[0] != fence[0]:
            continue
        if inner.group(2):  # a tagged fence opens a nested block
            depth += 1
        elif depth:
            depth -= 1
        elif len(inner.group(1)) >= len(fence):  # closes the outer block early: two blocks
            return text
    unwrapped = "\n".join(body)
    # An empty block would paste nothing: keep the reply, so the user sees what came back.
    return unwrapped if unwrapped.strip() else text
