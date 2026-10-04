import re
from pathlib import Path

import pytest

from prompt_workflow.prompt_builder import (
    PROFILES,
    TEMPLATE_MARKER,
    repair_template_tags,
    system_prompt,
)


# Unknown profile names raise ValueError instead of KeyError.
def test_unknown_profile():
    with pytest.raises(ValueError, match="Unknown profile: 'missing'"):
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


# The retired default-pro profile (an old .env's PROMPT_PRO_PROFILE) renders default.
def test_default_pro_alias():
    assert "default-pro" not in PROFILES
    assert system_prompt("default-pro", "I test.") == system_prompt("default", "I test.")


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


# Every golden-template profile carries the marker the CLI keys the tag repair on, so an edit
# that drops it cannot switch the repair off unnoticed.
@pytest.mark.parametrize("name", list(PROFILES))
def test_template_marker(name):
    assert (TEMPLATE_MARKER in PROFILES[name]) == ("<CONTEXT>" in PROFILES[name])
    assert (TEMPLATE_MARKER in PROFILES[name]) == name.startswith("default")


SLIP = "<CONTEXT>\nI want a memo.\n</GOAL>\n\n<GOAL>\nA memo.\n</GOAL>\n\n<INSTRUCTIONS>"


FIXED = SLIP.replace("memo.\n</GOAL>", "memo.\n</CONTEXT>", 1)


# flash-lite's <CONTEXT>...</GOAL> slip is closed with </CONTEXT>; nothing else changes.
def test_repair_template_tags():
    assert repair_template_tags(SLIP) == FIXED
    assert repair_template_tags("Note.\n" + SLIP) == "Note.\n" + FIXED
    crlf = SLIP.replace("\n", "\r\n")
    assert repair_template_tags(crlf) == FIXED.replace("\n", "\r\n")


# Only the rewrite's own first section is repaired: the same slip in pasted material (a user
# asking why a rewrite looks broken) is copied as it is.
def test_repair_leaves_pasted_slip():
    inputs = f"<INPUTS>\n{SLIP}\n</INPUTS>"
    assert repair_template_tags(FIXED + "\n" + inputs) == FIXED + "\n" + inputs
    assert repair_template_tags(SLIP + "\n" + inputs) == FIXED + "\n" + inputs


@pytest.mark.parametrize(
    "text",
    [
        SLIP.replace("memo.\n</GOAL>", "memo.\n</CONTEXT>", 1),  # well formed
        SLIP.replace("\n\n<GOAL>", "\n\nmore\n<GOAL>", 1),  # not right before <GOAL>
        SLIP.replace("I want a memo.", "I want a memo.\n</CONTEXT>"),  # CONTEXT already closed
        SLIP.replace("</GOAL>\n\n<GOAL>", "</INPUTS>\n\n<GOAL>", 1),  # another wrong tag
        # A well-formed CONTEXT that mentions the tags.
        "<CONTEXT>\nIt writes </GOAL>\n<GOAL>\nthere.\n</CONTEXT>\n\n<GOAL>\nA.\n</GOAL>",
        "<CONTEXT>\nI want </GOAL>\n\n<GOAL>\nA.\n</GOAL>",  # tag not on its own line
        SLIP.replace("I want a memo.", "<GOAL>\nI want a memo."),  # another malformation
    ],
)
def test_repair_template_tags_leaves_other_text(text):
    assert repair_template_tags(text) == text
