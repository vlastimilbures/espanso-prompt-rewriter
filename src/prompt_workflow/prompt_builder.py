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
