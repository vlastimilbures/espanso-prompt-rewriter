import pytest

from prompt_workflow.gate import GatedProvider
from prompt_workflow.providers.base import ProviderError
from prompt_workflow.redaction import compile_extra


class _Stub:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, system_prompt):
        self.calls.append(prompt)
        return "improved"


# GatedProvider blocks a sensitive draft and never calls through to the inner provider.
def test_blocks_on_findings():
    inner = _Stub()
    provider = GatedProvider(inner, allow_override=False)
    with pytest.raises(ProviderError, match=r"Blocked cloud call.*payment_card"):
        provider.generate("card 4111 1111 1111 1111", "system")
    assert inner.calls == []


# ALLOW_CLOUD_OVERRIDE lets a sensitive draft through.
def test_allow_override_passes_through():
    inner = _Stub()
    provider = GatedProvider(inner, allow_override=True)
    result = provider.generate("card 4111 1111 1111 1111", "system")
    assert result == "improved"
    assert inner.calls == ["card 4111 1111 1111 1111"]


# A clean draft always passes through, override or not.
def test_clean_draft_passes_through():
    inner = _Stub()
    provider = GatedProvider(inner, allow_override=False)
    result = provider.generate("summarize the quarterly report", "system")
    assert result == "improved"


# User-defined patterns block a draft that the built-in patterns would let through.
def test_blocks_on_extra_pattern():
    inner = _Stub()
    provider = GatedProvider(inner, allow_override=False, extra_patterns=compile_extra("falcon"))
    with pytest.raises(ProviderError, match="custom_1"):
        provider.generate("Project Falcon roadmap", "system")
    assert inner.calls == []


# A password left alone on the clipboard is blocked, though no pattern names it.
def test_blocks_bare_token_draft():
    inner = _Stub()
    provider = GatedProvider(inner, allow_override=False)
    with pytest.raises(ProviderError, match="bare_token"):
        provider.generate("Winter" + "2026!x", "system")
    assert inner.calls == []


# --allow-flagged sends a draft whose findings are all soft, once, and records them.
def test_allow_flagged_sends_soft_findings():
    inner = _Stub()
    provider = GatedProvider(inner, allow_override=False, allow_flagged=True)
    assert provider.generate("CONFIDENTIAL: write to jane@example.com", "system") == "improved"
    assert provider.sent_despite == ("email", "confidential_label")


# A hard finding is never sent per call, alone or next to a soft one, and the message says
# that -iok- cannot send it.
@pytest.mark.parametrize(
    "draft",
    [
        "card 4111 1111 1111 1111",
        "CONFIDENTIAL: card 4111 1111 1111 1111",
        "Winter" + "2026!x",
        "password: hunter22",
    ],
    ids=["card", "card-and-label", "bare-token", "password"],
)
def test_allow_flagged_never_sends_hard_findings(draft):
    inner = _Stub()
    provider = GatedProvider(inner, allow_override=False, allow_flagged=True)
    with pytest.raises(ProviderError, match=r"-iok- never sends .*Remove it"):
        provider.generate(draft, "system")
    assert inner.calls == []
    assert provider.sent_despite == ()


# The user's own patterns count as hard: they name what must not leave.
def test_allow_flagged_never_sends_extra_pattern():
    inner = _Stub()
    provider = GatedProvider(
        inner, allow_override=False, extra_patterns=compile_extra("falcon"), allow_flagged=True
    )
    with pytest.raises(ProviderError, match="never sends custom_1"):
        provider.generate("Project Falcon roadmap", "system")
    assert inner.calls == []


# The block message points to the per-call trigger only when every finding is soft, and
# never recommends the global switch.
@pytest.mark.parametrize(
    ("draft", "hint"),
    [
        ("Output is CONFIDENTIAL", "send it once with -iok-"),
        ("card 4111 1111 1111 1111", "Remove it, or use a local trigger (-il-)."),
    ],
    ids=["soft", "hard"],
)
def test_block_message_hint(draft, hint):
    provider = GatedProvider(_Stub(), allow_override=False)
    with pytest.raises(ProviderError) as exc:
        provider.generate(draft, "system")
    assert hint in str(exc.value)
    assert "ALLOW_CLOUD_OVERRIDE" not in str(exc.value)
    assert ("-iok-" in str(exc.value)) == (draft == "Output is CONFIDENTIAL")


# The global override sends everything silently, as before; sent_despite is per-call only.
def test_allow_override_records_nothing():
    provider = GatedProvider(_Stub(), allow_override=True, allow_flagged=True)
    provider.generate("CONFIDENTIAL card 4111 1111 1111 1111", "system")
    assert provider.sent_despite == ()


# Each soft finding can be sent once; the hint names what applies to the provider.
@pytest.mark.parametrize(
    ("draft", "finding"),
    [
        ("Pay CZ65 0800 0000 1920 0014 5399 today", "iban"),
        ("CCCD 001099012345", "vietnam_id_12"),
        ("CMND 123456789", "vietnam_id_9"),
    ],
)
def test_allow_flagged_sends_each_soft_finding(draft, finding):
    provider = GatedProvider(_Stub(), allow_override=False, allow_flagged=True)
    provider.generate(draft, "system")
    assert finding in provider.sent_despite


# A remote Ollama or LM Studio is not told to use -iok- (an OpenRouter trigger) or -il-.
@pytest.mark.parametrize("name", ["ollama", "lmstudio", "anthropic"])
def test_block_message_hint_per_provider(name):
    provider = GatedProvider(_Stub(), allow_override=False, name=name)
    with pytest.raises(ProviderError) as exc:
        provider.generate("Output is CONFIDENTIAL", "system")
    assert "send it once with --allow-flagged" in str(exc.value)
    assert "-iok-" not in str(exc.value)
    assert ("-il-" in str(exc.value)) == (name == "anthropic")


# sent_despite describes the last call only.
def test_sent_despite_resets_per_call():
    provider = GatedProvider(_Stub(), allow_override=False, allow_flagged=True)
    provider.generate("Output is CONFIDENTIAL", "system")
    provider.generate("plain text", "system")
    assert provider.sent_despite == ()
