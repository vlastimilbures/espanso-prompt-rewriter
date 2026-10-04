import json
import socket
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from prompt_workflow.config import Settings
from prompt_workflow.factory import make_provider
from prompt_workflow.gate import GatedProvider
from prompt_workflow.providers.anthropic import ANTHROPIC_VERSION, AnthropicProvider
from prompt_workflow.providers.base import (
    FILTERED_NOTE,
    SERVER_MESSAGE_MAX,
    TRUNCATED_NOTE,
    Provider,
    ProviderError,
    error_detail,
    is_loopback,
    post_json,
)
from prompt_workflow.providers.ollama import OllamaProvider
from prompt_workflow.providers.openai_compatible import OpenAICompatibleProvider


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


# Ollama speaks /api/chat with system+user messages, no streaming, the think flag and
# the temperature as an option.
def test_ollama_request_shape(fake_http):
    fake_http.reply(_ollama_body("ok"))
    OllamaProvider("http://x/", "m", timeout=7, think=True, temperature=0.2).generate(
        "draft", "sys"
    )
    assert fake_http.client_kwargs == [{"timeout": 7, "transport": None}]
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
                "options": {"temperature": 0.2},
            },
            # Set by httpx from json=; Ollama needs no other header.
            "headers": {"Content-Type": "application/json"},
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
    ).generate("draft", "sys")
    assert fake_http.calls == [
        {
            "url": "http://x/v1/chat/completions",
            "json": {
                "model": "m",
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
    ("exc", "message", "transient"),
    [
        (httpx.TimeoutException("slow"), "timed out after 1s", True),
        (httpx.ConnectError("refused"), "request failed: refused", True),
        (httpx.ReadError("reset"), "request failed: reset", False),
        (httpx.InvalidURL("bad url"), "base URL invalid", False),
    ],
)
@ALL
def test_transport_errors_become_provider_errors(fake_http, name, exc, message, transient):
    build, _, label = PROVIDERS[name]
    fake_http.exc = exc
    with pytest.raises(ProviderError, match=f"^{label} {message}") as caught:
        build().generate("d", "s")
    assert caught.value.transient is transient


@ALL
def test_http_error_status(fake_http, name):
    build, _, label = PROVIDERS[name]
    fake_http.reply(status_code=401)
    with pytest.raises(ProviderError, match=f"^{label} returned HTTP 401"):
        build().generate("d", "s")


# A non-2xx reply names the status, what the user can do, and the provider's own reason.
@ALL
@pytest.mark.parametrize(
    ("status", "hint", "transient"),
    [
        (400, "bad request (check the model and settings)", False),
        (401, "check the API key", False),
        (402, "out of credits", False),
        (403, "access denied", False),
        (404, "not found (check the model and base URL)", False),
        (429, "rate limited, try again shortly", True),
        (500, "provider unavailable, try again", True),
        (502, "provider unavailable, try again", True),
        (529, "provider unavailable, try again", True),
    ],
)
def test_http_error_carries_hint_and_reason(fake_http, name, status, hint, transient):
    build, _, label = PROVIDERS[name]
    fake_http.reply({"error": {"message": "Upstream said no."}}, status_code=status)
    with pytest.raises(ProviderError) as caught:
        build().generate("d", "s")
    assert str(caught.value) == f"{label} returned HTTP {status}: {hint}; Upstream said no."
    assert caught.value.status == status
    assert caught.value.transient is transient


# Ollama reports errors as a plain string.
def test_ollama_string_error(fake_http):
    fake_http.reply({"error": "model 'x' not found"}, status_code=404)
    with pytest.raises(ProviderError) as caught:
        OllamaProvider("http://x", "m").generate("d", "s")
    assert str(caught.value) == (
        "Ollama returned HTTP 404: not found (check the model and base URL); model 'x' not found"
    )


# An error page that is not JSON still gets the status and the hint.
def test_http_error_without_json_body(fake_http):
    fake_http.reply(bad_json=True, status_code=502)
    with pytest.raises(ProviderError, match=r"^Ollama returned HTTP 502: provider unavailable"):
        OllamaProvider("http://x", "m").generate("d", "s")


# The provider's reason is cut to one short printable line before it reaches the paste.
def test_error_detail_is_short_and_printable():
    long = error_detail({"error": {"message": "x" * 500}})
    assert len(long) == SERVER_MESSAGE_MAX
    assert long.endswith("…")
    cleaned = error_detail({"error": {"message": "bad\u202e model\u200b\n id\x07\x1b[0m"}})
    assert cleaned == "bad model id[0m"


# A reason that quotes something secret-shaped (some providers echo the rejected key) is
# dropped; the status and hint still show.
def test_error_detail_drops_sensitive_text(fake_http):
    key = "sk-or-v1-" + "cd" * 32
    assert error_detail({"error": {"message": f"Incorrect API key provided: {key}"}}) == ""
    fake_http.reply({"error": {"message": f"Incorrect API key provided: {key}"}}, status_code=401)
    with pytest.raises(ProviderError) as caught:
        OllamaProvider("http://x", "m").generate("d", "s")
    assert str(caught.value) == "Ollama returned HTTP 401: check the API key"


@pytest.mark.parametrize("body", [None, [], {}, {"error": None}, {"error": {"code": 1}}])
def test_error_detail_without_a_reason(body):
    assert error_detail(body) == ""


# OpenRouter reports an error raised after generation started as HTTP 200 with an error
# object; it is reported as that error, not as a malformed response.
@ALL
def test_ok_status_with_error_body(fake_http, name):
    build, _, label = PROVIDERS[name]
    fake_http.reply({"error": {"code": 502, "message": "Upstream provider error"}})
    with pytest.raises(ProviderError) as caught:
        build().generate("d", "s")
    assert str(caught.value) == f"{label} returned an error (code 502): Upstream provider error"
    assert caught.value.status == 502
    assert caught.value.transient is True


def test_ok_status_with_error_without_code(fake_http):
    fake_http.reply({"error": "boom"})
    with pytest.raises(ProviderError, match=r"^Ollama returned an error: boom$") as caught:
        OllamaProvider("http://x", "m").generate("d", "s")
    assert caught.value.status is None
    assert caught.value.transient is False


# The error can also sit inside the choice it cut short; its partial text is not pasted.
def test_choice_level_error(fake_http):
    fake_http.reply(
        {
            "choices": [
                {
                    "finish_reason": "error",
                    "error": {"code": 503, "message": "Provider disconnected"},
                    "message": {"content": "<CONTEXT>\nhalf a prompt"},
                }
            ]
        }
    )
    with pytest.raises(ProviderError) as caught:
        OpenAICompatibleProvider("http://x/v1", "m", label="OpenRouter").generate("d", "s")
    assert str(caught.value) == "OpenRouter returned an error (code 503): Provider disconnected"
    assert caught.value.transient is True


# A key with a character httpx cannot put in a header (a smart quote, an accented letter)
# fails before anything is sent, without being repeated, and is not called invalid JSON.
@pytest.mark.parametrize("key", ["sk-or-v1-t\u00ebst", "sk-or-v1-\u201ctest\u201d"])
def test_non_ascii_key_is_a_header_error(fake_http, key):
    for provider in (
        OpenAICompatibleProvider("http://x/v1", "m", api_key=key),
        AnthropicProvider("http://x", "m", key),
    ):
        with pytest.raises(ProviderError, match="invalid header value") as caught:
            provider.generate("d", "s")
        assert "test" not in str(caught.value)
        assert "tëst" not in str(caught.value)
    assert fake_http.requests == []


# A non-JSON body is a ProviderError, not a raw JSONDecodeError.
@ALL
def test_invalid_json(fake_http, name):
    build, _, label = PROVIDERS[name]
    fake_http.reply(bad_json=True)
    with pytest.raises(ProviderError, match=f"^{label} returned invalid JSON"):
        build().generate("d", "s")


@pytest.mark.parametrize(
    ("name", "body"),
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


# A <think> tag inside the answer is text, not reasoning: the rewrite is not cut short.
@ALL
def test_mid_text_think_survives(fake_http, name):
    build, body, _ = PROVIDERS[name]
    text = "<INSTRUCTIONS>\n1/ Reason inside <think> tags.\n2/ Keep it short.\n</INSTRUCTIONS>"
    fake_http.reply(body(text))
    assert build().generate("d", "s") == text


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


def _stopped(name, text, reason):
    body = PROVIDERS[name][1](text)
    if name == "ollama":
        body["done_reason"] = reason
    elif name == "openai_compatible":
        body["choices"][0]["finish_reason"] = reason
    else:
        body["stop_reason"] = reason
    return body


def _truncated(name, text):
    return _stopped(name, text, "max_tokens" if name == "anthropic" else "length")


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


# A reply that ended normally is pasted as is.
@ALL
@pytest.mark.parametrize("reason", ["stop", "end_turn", None, {"unexpected": "shape"}])
def test_normal_stop_reason(fake_http, name, reason):
    build, _, _ = PROVIDERS[name]
    fake_http.reply(_stopped(name, "a prompt", reason))
    assert build().generate("d", "s") == "a prompt"


# A reply that stopped on an error is a marker, never the partial text.
@ALL
def test_error_stop_reason_pastes_nothing(fake_http, name):
    build, _, label = PROVIDERS[name]
    fake_http.reply(_stopped(name, "<CONTEXT>\nhalf a", "error"))
    with pytest.raises(ProviderError, match=f"^{label} stopped with an error before") as caught:
        build().generate("d", "s")
    assert caught.value.transient is True


# A filtered or refused reply keeps its text but says it may be incomplete.
@ALL
@pytest.mark.parametrize("reason", ["content_filter", "refusal"])
def test_filtered_stop_reason_is_marked(fake_http, name, reason):
    build, _, _ = PROVIDERS[name]
    fake_http.reply(_stopped(name, "<CONTEXT>\nhalf a", reason))
    assert build().generate("d", "s") == "<CONTEXT>\nhalf a" + FILTERED_NOTE.format(reason=reason)


# With no text at all, a refusal says so instead of "no text content".
@ALL
@pytest.mark.parametrize("text", [None, ""])
def test_filtered_without_text_is_declined(fake_http, name, text):
    build, _, label = PROVIDERS[name]
    body = _stopped(name, text, "refusal")
    if name == "anthropic" and text is None:
        body["content"] = []
    fake_http.reply(body)
    with pytest.raises(ProviderError, match=rf"^{label} declined the request \(refusal\)$"):
        build().generate("d", "s")


# API keys never appear in a provider's repr().
def test_provider_repr_hides_api_key():
    assert "sekret" not in repr(OpenAICompatibleProvider("http://x", "m", api_key="sekret"))
    assert "sekret" not in repr(AnthropicProvider("http://x", "m", "sekret"))


# --- Proxies: loopback is reached directly, anything else may use a proxy. ---------------

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


@contextmanager
def _http_server(reply: dict) -> Iterator[tuple[str, list[str]]]:
    """A loopback HTTP server that answers every POST with ``reply`` and records the
    request lines it saw. Used both as a local model server and as a stand-in proxy."""
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(self.requestline)
            body = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    poll = {"poll_interval": 0.01}  # shutdown() waits up to one poll interval
    threading.Thread(target=server.serve_forever, kwargs=poll, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


def _route_through_proxy(monkeypatch, route: str, proxy: str) -> None:
    """Point httpx at ``proxy`` the way a machine can: an env var, or the macOS/Windows
    system proxy that urllib.request.getproxies() reports (patched where httpx calls it)."""
    for var in _PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(var.lower(), raising=False)
    if route == "system":
        monkeypatch.setattr(httpx._utils, "getproxies", lambda: {"http": proxy, "https": proxy})
    else:
        # Env vars only, so this machine's own system proxy cannot leak into the test.
        monkeypatch.setattr(httpx._utils, "getproxies", urllib.request.getproxies_environment)
        monkeypatch.setenv(route, proxy)


# (base URL setting, path under the server root, success body) for every provider whose
# base URL may be loopback; OpenRouter accepts a plain-http loopback URL (a local proxy).
_LOOPBACK = {
    "ollama": ("OLLAMA_BASE_URL", "", _ollama_body("ok")),
    "lmstudio": ("LMSTUDIO_BASE_URL", "/v1", _openai_body("ok")),
    "openrouter": ("OPENROUTER_BASE_URL", "/api/v1", _openai_body("ok")),
    "anthropic": ("ANTHROPIC_BASE_URL", "", _anthropic_body("ok")),
}


# A loopback provider connects straight to this machine, whatever proxy is configured.
@pytest.mark.parametrize("route", ["HTTP_PROXY", "ALL_PROXY", "system"])
@pytest.mark.parametrize("name", list(_LOOPBACK))
def test_loopback_provider_bypasses_proxy(monkeypatch, name, route):
    setting, path, body = _LOOPBACK[name]
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    with _http_server(body) as (target, target_seen), _http_server(body) as (proxy, proxy_seen):
        _route_through_proxy(monkeypatch, route, proxy)
        monkeypatch.setenv(setting, target + path)
        assert make_provider(name, Settings()).generate("draft", "sys") == "ok"
    assert proxy_seen == []
    assert len(target_seen) == 1


# A remote endpoint still goes through the configured proxy, which corporate networks need.
@pytest.mark.parametrize("route", ["HTTP_PROXY", "system"])
def test_remote_provider_keeps_proxy(monkeypatch, route):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.example.test:11434")
    with _http_server(_ollama_body("ok")) as (proxy, proxy_seen):
        _route_through_proxy(monkeypatch, route, proxy)
        provider = make_provider("ollama", Settings())
        assert isinstance(provider, GatedProvider)
        assert provider.generate("draft", "sys") == "ok"
    assert proxy_seen == ["POST http://ollama.example.test:11434/api/chat HTTP/1.1"]


# post_json decides per URL: only a loopback call gets its own transport, which is what
# makes httpx skip the env and system proxies (covered end to end above).
@pytest.mark.parametrize(
    ("url", "remote"),
    [
        ("http://localhost:11434/api/chat", False),
        ("http://127.0.0.2:1234/v1/chat/completions", False),
        ("http://[::1]:11434/api/chat", False),
        ("https://openrouter.ai/api/v1/chat/completions", True),
        ("http://192.168.1.20:11434/api/chat", True),
        ("http://localhost.example.com/api/chat", True),
    ],
)
def test_post_json_direct_only_on_loopback(fake_http, url, remote):
    post_json("X", url, 5, json={})
    (kwargs,) = fake_http.client_kwargs
    assert kwargs["timeout"] == 5
    assert (kwargs["transport"] is None) is remote
    assert is_loopback(url) is not remote


# An API key with a character HTTP forbids in a header (a stray newline) fails without the
# error repeating the key: httpx's own message quotes the whole header value.
@pytest.mark.parametrize("name", ["openrouter", "anthropic"])
def test_bad_header_value_is_not_repeated(monkeypatch, name):
    for var in ("HTTP_PROXY", "ALL_PROXY", "http_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    key = "sk-or-v1-" + "cd" * 32 + "\n"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        if name == "openrouter":
            provider: Provider = OpenAICompatibleProvider(url, "m", api_key=key, timeout=2)
        else:
            provider = AnthropicProvider(url, "m", key, timeout=2)
        with pytest.raises(ProviderError, match="invalid header value") as caught:
            provider.generate("d", "s")
    assert "cdcd" not in str(caught.value)
