import pytest

from prompt_workflow.config import Settings
from prompt_workflow.factory import PROVIDER_NAMES, make_provider, openrouter_routing
from prompt_workflow.gate import GatedProvider
from prompt_workflow.providers.anthropic import AnthropicProvider
from prompt_workflow.providers.base import ProviderError
from prompt_workflow.providers.ollama import OllamaProvider
from prompt_workflow.providers.openai_compatible import OpenAICompatibleProvider


# make_provider("ollama") passes through the think flag.
def test_make_provider_ollama():
    cfg = Settings()
    provider = make_provider("ollama", cfg)
    assert isinstance(provider, OllamaProvider)
    assert provider.think == cfg.ollama_think


# make_provider("lmstudio") sends no api key and labels itself "LM Studio".
def test_make_provider_lmstudio():
    provider = make_provider("lmstudio", Settings())
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.api_key is None
    assert provider.label == "LM Studio"


# make_provider("openrouter") is gated and sets X-Title + max_tokens.
def test_make_provider_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    provider = make_provider("openrouter", Settings())
    assert isinstance(provider, GatedProvider)
    assert isinstance(provider._inner, OpenAICompatibleProvider)
    assert provider._inner.extra_headers["X-Title"] == "espanso-prompt-rewriter"
    assert provider._inner.max_tokens == Settings().openrouter_max_tokens


# The default pin is sent as a preference: no allow_fallbacks key, so a dead
# endpoint degrades to blended routing instead of pasting an error into Espanso.
def test_make_provider_openrouter_pins_endpoint(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_PROVIDER", raising=False)
    monkeypatch.delenv("OPENROUTER_ALLOW_FALLBACKS", raising=False)
    provider = make_provider("openrouter", Settings())
    assert isinstance(provider, GatedProvider)
    assert provider._inner.extra_body == {
        "provider": {"order": ["google-ai-studio/flex"]},
        "reasoning": {"effort": "minimal", "exclude": True},
    }


# An empty OPENROUTER_REASONING_EFFORT omits the reasoning field, for models without one.
def test_make_provider_openrouter_no_reasoning(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_REASONING_EFFORT", "")
    provider = make_provider("openrouter", Settings())
    assert "reasoning" not in provider._inner.extra_body


# The pro tier builds the gated OpenRouter provider on the OPENROUTER_PRO_* settings.
def test_make_provider_pro_tier(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    provider = make_provider("openrouter", Settings().for_tier("pro"))
    assert isinstance(provider, GatedProvider)
    inner = provider._inner
    assert inner.default_model == "openai/gpt-6-luna"
    assert inner.timeout == 60.0
    assert inner.extra_body == {
        "provider": {"order": ["openai"]},
        "reasoning": {"effort": "low", "exclude": True},
    }


# An empty OPENROUTER_PROVIDER restores OpenRouter's own blended routing.
def test_make_provider_openrouter_no_pin(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_PROVIDER", "")
    provider = make_provider("openrouter", Settings())
    assert isinstance(provider, GatedProvider)
    assert isinstance(provider._inner, OpenAICompatibleProvider)
    assert "provider" not in provider._inner.extra_body


# OPENROUTER_ALLOW_FALLBACKS=false turns the preference into a hard pin.
def test_make_provider_openrouter_hard_pin(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_PROVIDER", "deepinfra/fp4")
    monkeypatch.setenv("OPENROUTER_ALLOW_FALLBACKS", "false")
    provider = make_provider("openrouter", Settings())
    assert isinstance(provider, GatedProvider)
    assert provider._inner.extra_body["provider"] == {
        "order": ["deepinfra/fp4"],
        "allow_fallbacks": False,
    }


# make_provider("openrouter") errors without OPENROUTER_API_KEY.
def test_make_provider_openrouter_missing_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        make_provider("openrouter", Settings())


# make_provider("anthropic") is gated.
def test_make_provider_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    provider = make_provider("anthropic", Settings())
    assert isinstance(provider, GatedProvider)
    assert isinstance(provider._inner, AnthropicProvider)


# make_provider("anthropic") errors without ANTHROPIC_API_KEY.
def test_make_provider_anthropic_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        make_provider("anthropic", Settings())


# make_provider() errors on an unknown provider name.
def test_make_provider_unknown():
    with pytest.raises(ProviderError, match="Unknown provider"):
        make_provider("bogus", Settings())


# Every advertised provider name is buildable (keys set so cloud branches pass).
@pytest.mark.parametrize("name", PROVIDER_NAMES)
def test_every_provider_name_builds(monkeypatch, name):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert hasattr(make_provider(name, Settings()), "generate")


# Only the cloud providers are gated.
@pytest.mark.parametrize(
    "name,gated",
    [("ollama", False), ("lmstudio", False), ("openrouter", True), ("anthropic", True)],
)
def test_cloud_providers_are_gated(monkeypatch, name, gated):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert isinstance(make_provider(name, Settings()), GatedProvider) is gated


# extra_body is merged over the routing preferences, on_response and title are forwarded
# (the path scripts/bench_models.py uses for usage/cost reporting).
def test_openrouter_extra_body_on_response_and_title(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    hook = print
    provider = make_provider(
        "openrouter",
        Settings(),
        extra_body={"usage": {"include": True}},
        on_response=hook,
        title="bench",
    )
    inner = provider._inner
    assert inner.extra_body == {
        "provider": {"order": ["google-ai-studio/flex"]},
        "reasoning": {"effort": "minimal", "exclude": True},
        "usage": {"include": True},
    }
    assert inner.on_response is hook
    assert inner.extra_headers == {"X-Title": "bench"}


@pytest.mark.parametrize(
    "pin,allow,expected",
    [
        ("", True, {}),
        ("", False, {}),
        ("a/b", True, {"order": ["a/b"]}),
        ("a/b", False, {"order": ["a/b"], "allow_fallbacks": False}),
    ],
)
def test_openrouter_routing(pin, allow, expected):
    assert openrouter_routing(pin, allow) == expected


# Cloud base URLs must be https, so the API key never travels in plaintext.
@pytest.mark.parametrize(
    "name,var", [("openrouter", "OPENROUTER_BASE_URL"), ("anthropic", "ANTHROPIC_BASE_URL")]
)
@pytest.mark.parametrize("url", ["http://example.com/api", "ftp://example.com", "example.com"])
def test_cloud_base_url_requires_https(monkeypatch, name, var, url):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv(var, url)
    with pytest.raises(ProviderError, match=f"{var} must be an https:// URL"):
        make_provider(name, Settings())


# Plain http is allowed to a loopback host, e.g. a local debugging proxy.
@pytest.mark.parametrize(
    "url", ["http://localhost:8080/v1", "http://127.0.0.1/v1", "http://[::1]/v1"]
)
def test_cloud_base_url_allows_loopback_http(monkeypatch, url):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_BASE_URL", url)
    assert isinstance(make_provider("openrouter", Settings()), GatedProvider)


# An invalid PROMPT_EXTRA_PATTERNS regex is reported when the gated provider is built.
def test_invalid_extra_pattern_raises(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("PROMPT_EXTRA_PATTERNS", "a[")
    with pytest.raises(ValueError, match="PROMPT_EXTRA_PATTERNS entry 1"):
        make_provider("openrouter", Settings())
