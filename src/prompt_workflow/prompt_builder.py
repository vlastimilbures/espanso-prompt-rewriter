from __future__ import annotations

from importlib.resources import files

_PROMPT_DIR = files(__package__) / "prompts"


def _load_profiles() -> dict[str, str]:
    return {
        p.name.removesuffix(".md"): p.read_text(encoding="utf-8").strip()
        for p in _PROMPT_DIR.iterdir()
        if p.name.endswith(".md")
    }


PROFILES: dict[str, str] = _load_profiles()

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
        template = PROFILES[profile]
    except KeyError as exc:
        known = ", ".join(PROFILES)
        raise ValueError(f"Unknown profile: {profile}. Choose from: {known}") from exc
    return render(template, persona)
