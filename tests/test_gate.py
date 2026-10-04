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
