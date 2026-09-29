import re
from pathlib import Path

import pytest

from prompt_workflow.prompt_builder import PROFILES, system_prompt


# Unknown profile names raise ValueError instead of KeyError.
def test_unknown_profile():
    with pytest.raises(ValueError):
        system_prompt("missing")


# default profile emits the golden-template tags plus the [REVIEW: ...] marker.
def test_default_profile_emits_golden_template():
    text = system_prompt("default")
    for tag in ["<CONTEXT>", "<GOAL>", "<INSTRUCTIONS>", "<CONSTRAINTS>", "<INPUTS>", "<OUTPUTS>"]:
        assert tag in text
    assert "[REVIEW:" in text


# default profile's system prompt describes both the plan-first/execute-now and
# independent-review/self-review instruction branches for the model to choose between.
def test_default_profile_offers_both_branches():
    text = system_prompt("default")
    assert "Plan the task thoroughly" in text
    assert "Execute, but state assumptions up front." in text
    assert "Spin up an independent agent" in text
    assert "Review your own output against these checks" in text


# general profile returns a non-empty system prompt distinct from default's golden template.
def test_general_profile_returns_prompt():
    text = system_prompt("general")
    assert text
    assert "<CONTEXT>" not in text


PERSONA = "I am working as a Head of Data at Example Corp."


# A configured persona is the mandated opening of CONTEXT, overridable by the draft.
def test_default_profile_uses_configured_persona():
    text = system_prompt("default", PERSONA)
    assert f'<context>\nopen with "{PERSONA}" If the draft states a different role' in text


# Without a persona the model only uses a role the draft itself states.
def test_default_profile_without_persona():
    text = system_prompt("default")
    assert "<context>\nif the draft explicitly states the user's role" in text
    assert "do not state or guess any role" in text
    assert 'open with "' not in text.split("<context>\n", 1)[1].split("\n", 1)[0]


# No profile ships an unreplaced template token, with or without a persona.
@pytest.mark.parametrize("persona", ["", PERSONA])
def test_no_unreplaced_tokens(persona):
    for name in PROFILES:
        assert "{{" not in system_prompt(name, persona), name


# Profiles without the persona token are unaffected by it.
def test_general_profile_ignores_persona():
    assert system_prompt("general", PERSONA) == system_prompt("general")


# No real persona is hard-coded in shipped prompts or Espanso matches: an "I am working
# as ..." sentence must be the fill-in placeholder or the fictional Example Corp example.
# A real one belongs in PROMPT_PERSONA.
def test_no_hardcoded_persona_in_shipped_files():
    root = Path(__file__).resolve().parents[1]
    shipped = [*(root / "src").rglob("*.md"), *(root / "src").rglob("*.py")]
    shipped += list((root / "espanso").rglob("*.yml"))
    for path in shipped:
        for sentence in re.findall(r"I am working as [^.\n]*", path.read_text(encoding="utf-8")):
            allowed = sentence.endswith("[role] in [company]") or "Example Corp" in sentence
            assert allowed, (path, sentence)
