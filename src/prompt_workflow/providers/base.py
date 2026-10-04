from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from ..redaction import safe_repr, scan


class ProviderError(RuntimeError):
    """Raised when a provider cannot produce a result.

    ``status`` is the HTTP status, or the code of an error reported inside a 200 response,
    when there is one. ``transient`` marks a failure the same call may not hit again: a
    timeout, a dropped connection, a rate limit or an unavailable provider.
    """

    def __init__(self, message: str, *, status: int | None = None, transient: bool = False):
        super().__init__(message)
        self.status = status
        self.transient = transient


class Provider(Protocol):
    def generate(self, prompt: str, system_prompt: str) -> str: ...


def chat_messages(system_prompt: str, prompt: str) -> list[dict[str, str]]:
    """System + user message list shared by the Ollama and OpenAI-compatible chat APIs."""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]


def is_loopback(url: str) -> bool:
    """Whether the URL's host is this machine: localhost, 127.0.0.0/8 or ::1."""
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# Statuses the same call may not hit again; 529 is Anthropic's "overloaded".
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
# What the user can do about a status, shown before the provider's own reason.
_HINTS = {
    400: "bad request (check the model and settings)",
    401: "check the API key",
    402: "out of credits",
    403: "access denied",
    404: "not found (check the model and base URL)",
    429: "rate limited, try again shortly",
}
# The provider's reason is pasted into the user's editor, so it is kept to one short line.
SERVER_MESSAGE_MAX = 160


def error_detail(body: object) -> str:
    """The provider's own reason for an error, cleaned to one short printable line.

    Reads {"error": {"message": ...}} (OpenAI, OpenRouter, Anthropic) or {"error": "..."}
    (Ollama). Returns "" when there is none, or when it matches a sensitive pattern: some
    providers quote part of the rejected API key.
    """
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    if not isinstance(message, str):
        return ""
    # Bounded before the scan, which then sees everything that could be shown.
    words = " ".join(message[: SERVER_MESSAGE_MAX * 4].split())
    # Drops control, bidi, zero-width and tag characters (Unicode "Other"/"Separator").
    text = "".join(char for char in words if char.isprintable())
    if scan(text):
        return ""
    if len(text) > SERVER_MESSAGE_MAX:
        return text[: SERVER_MESSAGE_MAX - 1] + "…"
    return text


def http_error(label: str, status: int, body: object) -> ProviderError:
    """A non-2xx response as a ProviderError: the status, a hint and the provider's reason."""
    hint = _HINTS.get(status) or ("provider unavailable, try again" if status >= 500 else "")
    detail = "; ".join(part for part in (hint, error_detail(body)) if part)
    return ProviderError(
        f"{label} returned HTTP {status}" + (f": {detail}" if detail else ""),
        status=status,
        transient=status in TRANSIENT_STATUSES,
    )


def body_error(label: str, body: object) -> ProviderError | None:
    """An error object inside a 200 response, or None. OpenRouter reports an error raised
    after generation started this way, at the top level or inside the choice."""
    error = body.get("error") if isinstance(body, dict) else None
    if not error:
        return None
    code = error.get("code") if isinstance(error, dict) else None
    status = code if isinstance(code, int) and not isinstance(code, bool) else None
    where = f" (code {status})" if status is not None else ""
    return ProviderError(
        f"{label} returned an error{where}: {error_detail(body) or 'no details'}",
        status=status,
        transient=status in TRANSIENT_STATUSES,
    )


def post_json(
    label: str,
    url: str,
    timeout: float,
    *,
    json: object = None,
    headers: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """POST and return the parsed JSON body; every failure is a ProviderError.

    A loopback URL is reached directly, ignoring HTTP(S)_PROXY/ALL_PROXY and the
    macOS/Windows system proxy: a call to this machine has no reason to go anywhere else.
    httpx applies those only to a client without its own transport, so giving it one keeps
    the rest of the environment (SSL_CERT_FILE for a local https server) in effect. Other
    URLs keep using the proxy, which corporate networks need.
    """
    try:
        # httpx encodes header values as ASCII; a key pasted with a smart quote or an accented
        # letter fails here, before anything is sent. The error quotes the key, so it is dropped.
        encoded = httpx.Headers(headers)
    except UnicodeEncodeError:
        raise ProviderError(
            f"{label} request failed: invalid header value (check the API key)"
        ) from None
    transport = httpx.HTTPTransport() if is_loopback(url) else None
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.post(url, json=json, headers=encoded)
    except httpx.TimeoutException as exc:
        raise ProviderError(f"{label} timed out after {timeout}s", transient=True) from exc
    except httpx.InvalidURL as exc:
        raise ProviderError(f"{label} base URL invalid: {safe_repr(str(exc))}") from exc
    except httpx.LocalProtocolError as exc:
        # Raised for a header value with characters HTTP forbids; its message quotes the
        # value, which is the API key, so it is not repeated.
        raise ProviderError(
            f"{label} request failed: invalid header value (check the API key)"
        ) from exc
    except httpx.ConnectError as exc:
        raise ProviderError(f"{label} request failed: {exc}", transient=True) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"{label} request failed: {exc}") from exc

    try:
        body: object = response.json()
    except ValueError:
        body = None
    if not response.is_success:
        raise http_error(label, response.status_code, body)
    if body is None:
        raise ProviderError(f"{label} returned invalid JSON")
    if error := body_error(label, body):
        raise error
    return body  # type: ignore[return-value]


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# A block the model never closed (cut off at the token cap) is reasoning to the end.
_UNCLOSED_BLOCK = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)
_ORPHAN_TAG = re.compile(r"</?think>", re.IGNORECASE)


def strip_thinking(text: str) -> str:
    """Remove reasoning blocks emitted by models such as the qwen3 family.

    Handles complete <think>...</think> spans, a block left open when a response is
    truncated (dropped to the end), and stray closing tags. Leading and trailing
    whitespace is normalized.
    """
    cleaned = _THINK_BLOCK.sub("", text)
    cleaned = _UNCLOSED_BLOCK.sub("", cleaned)
    cleaned = _ORPHAN_TAG.sub("", cleaned)
    return cleaned.strip()


# Appended to a rewrite the model stopped at its output cap, so a cut-off prompt is never
# pasted as if it were complete.
TRUNCATED_NOTE = "\n\n[prompt-workflow: output truncated at max tokens]"
# Appended to a rewrite a content filter or a refusal stopped.
FILTERED_NOTE = (
    "\n\n[prompt-workflow: the model stopped early ({reason}); the rewrite may be incomplete]"
)

# Stop reasons (OpenRouter finish_reason, Anthropic stop_reason, Ollama done_reason) for a
# reply that did not end normally.
_TRUNCATING = frozenset({"length", "max_tokens"})
_FAILED = frozenset({"error"})
_FILTERED = frozenset({"content_filter", "refusal"})


def finalize_content(content: object, label: str, *, stop_reason: object = None) -> str:
    """Validate a provider's raw response content, strip <think> blocks, require non-empty text.

    ``stop_reason`` is the provider's raw reason for ending the reply:
    - "error": generation failed part way, so no partial text is pasted.
    - "length"/"max_tokens": the token cap. With no text, a reasoning model spent the whole
      budget thinking, which only a bigger cap fixes; otherwise TRUNCATED_NOTE is appended.
    - "content_filter"/"refusal": with no text the request was declined; otherwise
      FILTERED_NOTE is appended.
    """
    reason = stop_reason if isinstance(stop_reason, str) else None
    if reason in _FAILED:
        raise ProviderError(f"{label} stopped with an error before finishing", transient=True)
    result = strip_thinking(content) if isinstance(content, str) else ""
    if reason in _TRUNCATING and not result:
        raise ProviderError(f"{label} used the whole max-tokens budget; raise --max-tokens")
    if reason in _FILTERED and not result:
        raise ProviderError(f"{label} declined the request ({reason})")
    if not isinstance(content, str):
        raise ProviderError(f"{label} returned no text content")
    if not result:
        raise ProviderError(f"{label} returned empty content")
    if reason in _TRUNCATING:
        return result + TRUNCATED_NOTE
    if reason in _FILTERED:
        return result + FILTERED_NOTE.format(reason=reason)
    return result
