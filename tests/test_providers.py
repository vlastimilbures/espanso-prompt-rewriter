import httpx
import pytest

from prompt_workflow.config import Settings
from prompt_workflow.factory import make_provider
from prompt_workflow.providers.anthropic import ANTHROPIC_VERSION, AnthropicProvider
from prompt_workflow.providers.base import TRUNCATED_NOTE, Provider, ProviderError
from prompt_workflow.providers.ollama import OllamaProvider
from prompt_workflow.providers.openai_compatible import OpenAICompatibleProvider

# mypy verifies each provider actually conforms to the Provider protocol.
_ollama_conforms: Provider = OllamaProvider("http://x", "m")
_openai_compatible_conforms: Provider = OpenAICompatibleProvider("http://x", "m")
_anthropic_conforms: Provider = AnthropicProvider("http://x", "m", "key")


def _ollama_body(text):
    return {"message": {"content": text}}


def _openai_body(text):
    return {"choices": [{"message": {"content": text}}]}


def _anthropic_body(text):
    return {"content": [{"type": "text", "text": text}]}


# (provider factory, success-body builder, error label) for every provider.
PROVIDERS = {
    "ollama": (lambda: OllamaProvider("http://x/", "m", timeout=1), _ollama_body, "Ollama"),
    "openai_compatible": (
        lambda: OpenAICompatibleProvider("http://x/v1/", "m", timeout=1, label="LM Studio"),
        _openai_body,
        "LM Studio",
    ),
    "anthropic": (
        lambda: AnthropicProvider("http://x/", "m", "key", timeout=1),
        _anthropic_body,
        "Anthropic",
    ),
}
ALL = pytest.mark.parametrize("name", list(PROVIDERS))


# --- Wire format: the exact request each provider sends. --------------------------------


# Ollama speaks /api/chat with system+user messages, no streaming, and the think flag.
def test_ollama_request_shape(fake_http):
    fake_http.reply(_ollama_body("ok"))
    OllamaProvider("http://x/", "m", timeout=7, think=True).generate("draft", "sys")
    assert fake_http.client_kwargs == [{"timeout": 7}]
    assert fake_http.calls == [
        {
            "url": "http://x/api/chat",
            "json": {
                "model": "m",
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "draft"},
                ],
                "stream": False,
                "think": True,
            },
            "headers": None,
        }
    ]


# OpenAI-compatible sends bearer auth, extra headers, max_tokens, temperature, extra_body.
def test_openai_compatible_request_shape(fake_http):
    fake_http.reply(_openai_body("ok"))
    OpenAICompatibleProvider(
        "http://x/v1/",
        "m",
        api_key="k",
        timeout=7,
        max_tokens=100,
        temperature=0.2,
        extra_headers={"X-Title": "t"},
        extra_body={"provider": {"order": ["p"]}},
    ).generate("draft", "sys", model="override")
    assert fake_http.calls == [
        {
            "url": "http://x/v1/chat/completions",
            "json": {
                "model": "override",
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "draft"},
                ],
                "max_tokens": 100,
                "temperature": 0.2,
                "provider": {"order": ["p"]},
            },
            "headers": {
                "Content-Type": "application/json",
                "X-Title": "t",
                "Authorization": "Bearer k",
            },
        }
    ]


# Without a key (LM Studio) no Authorization header is sent, and unset optional
# fields (max_tokens, temperature) are omitted: some models reject temperature.
def test_openai_compatible_minimal_request(fake_http):
    fake_http.reply(_openai_body("ok"))
    OpenAICompatibleProvider("http://x/v1", "m").generate("d", "s")
    call = fake_http.calls[0]
    assert call["headers"] == {"Content-Type": "application/json"}
    assert set(call["json"]) == {"model", "messages"}


# Anthropic puts the system prompt top-level, requires max_tokens and a version header.
def test_anthropic_request_shape(fake_http):
    fake_http.reply(_anthropic_body("ok"))
    AnthropicProvider("http://x/", "m", "key", timeout=7, temperature=0.2).generate("draft", "sys")
    assert fake_http.calls == [
        {
            "url": "http://x/v1/messages",
            "json": {
                "model": "m",
                "max_tokens": 1200,
                "system": "sys",
                "messages": [{"role": "user", "content": "draft"}],
                "temperature": 0.2,
            },
            "headers": {
                "x-api-key": "key",
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        }
    ]


# The CLI's OpenRouter provider sends the pinned route, X-Title, key and max_tokens.
def test_openrouter_request_shape(fake_http, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake_http.reply(_openai_body("ok"))
    make_provider("openrouter", Settings()).generate("draft", "sys")
    call = fake_http.calls[0]
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["headers"] == {
        "Content-Type": "application/json",
        "X-Title": "espanso-prompt-rewriter",
        "Authorization": "Bearer test-key",
    }
    assert call["json"] == {
        "model": "google/gemini-3.5-flash-lite",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "draft"},
        ],
        "max_tokens": 2400,
        "temperature": 0.2,
        "provider": {"order": ["google-ai-studio/flex"]},
        "reasoning": {"effort": "minimal", "exclude": True},
    }


# on_response receives the raw body (scripts/bench_models.py reads usage/cost from it).
def test_openai_compatible_on_response_gets_raw_body(fake_http):
    body = {**_openai_body("ok"), "usage": {"cost": 0.1}}
    fake_http.reply(body)
    seen = []
    OpenAICompatibleProvider("http://x/v1", "m", on_response=seen.append).generate("d", "s")
    assert seen == [body]


# --- Behaviour shared by every provider. -----------------------------------------------


# Every provider returns the text content, with <think> reasoning stripped.
@ALL
def test_success_strips_thinking(fake_http, name):
    build, body, _ = PROVIDERS[name]
    fake_http.reply(body("<think>reason</think>\n improved prompt "))
    assert build().generate("d", "s") == "improved prompt"


@pytest.mark.parametrize(
    "exc,message",
    [
        (httpx.TimeoutException("slow"), "timed out after 1s"),
        (httpx.ConnectError("refused"), "request failed"),
        (httpx.InvalidURL("bad url"), "base URL invalid"),
    ],
)
@ALL
def test_transport_errors_become_provider_errors(fake_http, name, exc, message):
    build, _, label = PROVIDERS[name]
    fake_http.exc = exc
    with pytest.raises(ProviderError, match=f"^{label} {message}"):
        build().generate("d", "s")


@ALL
def test_http_error_status(fake_http, name):
    build, _, label = PROVIDERS[name]
    fake_http.reply(status_code=401)
    with pytest.raises(ProviderError, match=f"^{label} returned HTTP 401"):
        build().generate("d", "s")


# A non-JSON body is a ProviderError, not a raw JSONDecodeError.
@ALL
def test_invalid_json(fake_http, name):
    build, _, label = PROVIDERS[name]
    fake_http.reply(bad_json=True)
    with pytest.raises(ProviderError, match=f"^{label} returned invalid JSON"):
        build().generate("d", "s")


@pytest.mark.parametrize(
    "name,body",
    [
        ("ollama", {"unexpected": True}),
        ("openai_compatible", {"choices": []}),
        ("anthropic", {"content": None}),
        # A non-dict block used to escape as an "unexpected error".
        ("anthropic", {"content": ["text"]}),
    ],
)
def test_malformed_response(fake_http, name, body):
    build, _, label = PROVIDERS[name]
    fake_http.reply(body)
    with pytest.raises(ProviderError, match=f"^{label} response was malformed"):
        build().generate("d", "s")


# Whitespace-only (or think-only) content is an error, not a blank Espanso expansion.
@ALL
@pytest.mark.parametrize("text", ["   ", "<think>only reasoning</think>"])
def test_empty_content(fake_http, name, text):
    build, body, label = PROVIDERS[name]
    fake_http.reply(body(text))
    with pytest.raises(ProviderError, match=f"^{label} returned empty content"):
        build().generate("d", "s")


# A null content field (some reasoning models) raises ProviderError, not TypeError.
@pytest.mark.parametrize("name", ["ollama", "openai_compatible", "anthropic"])
def test_null_content(fake_http, name):
    build, body, label = PROVIDERS[name]
    fake_http.reply(body(None))
    with pytest.raises(ProviderError, match=f"^{label} returned no text content"):
        build().generate("d", "s")


# Anthropic picks the first text block, skipping thinking blocks.
def test_anthropic_skips_non_text_blocks(fake_http):
    fake_http.reply(
        {"content": [{"type": "thinking", "thinking": "reasoning"}, {"type": "text", "text": "ok"}]}
    )
    assert AnthropicProvider("http://x", "m", "key").generate("d", "s") == "ok"


# A response with no text block at all (only thinking) has no text content.
@pytest.mark.parametrize("blocks", [[], [{"type": "thinking", "thinking": "x"}]])
def test_anthropic_without_text_block(fake_http, blocks):
    fake_http.reply({"content": blocks})
    with pytest.raises(ProviderError, match=r"^Anthropic returned no text content"):
        AnthropicProvider("http://x", "m", "key").generate("d", "s")


def _truncated(name, text):
    body = PROVIDERS[name][1](text)
    if name == "ollama":
        body["done_reason"] = "length"
    elif name == "openai_compatible":
        body["choices"][0]["finish_reason"] = "length"
    else:
        body["stop_reason"] = "max_tokens"
    return body


# A rewrite cut off at the token cap is pasted with a visible note, never as if complete.
@ALL
def test_truncated_output_is_marked(fake_http, name):
    build, _, _ = PROVIDERS[name]
    fake_http.reply(_truncated(name, "<CONTEXT>\nhalf a prompt"))
    assert build().generate("d", "s") == "<CONTEXT>\nhalf a prompt" + TRUNCATED_NOTE


# A reasoning model that spent the whole budget thinking gets a fix-it hint, not a
# generic "no text content".
@ALL
@pytest.mark.parametrize("text", [None, "<think>still thinking"])
def test_truncated_without_text_asks_for_more_tokens(fake_http, name, text):
    build, _, label = PROVIDERS[name]
    body = _truncated(name, text)
    if name == "anthropic" and text is None:
        body["content"] = [{"type": "thinking", "thinking": "x"}]
    fake_http.reply(body)
    with pytest.raises(ProviderError, match=f"^{label} used the whole max-tokens budget"):
        build().generate("d", "s")
