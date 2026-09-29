from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Protocol

import httpx


class ProviderError(RuntimeError):
    """Raised when a provider cannot produce a result."""


class Provider(Protocol):
    def generate(self, prompt: str, system_prompt: str, model: str | None = None) -> str: ...


def chat_messages(system_prompt: str, prompt: str) -> list[dict[str, str]]:
    """System + user message list shared by the Ollama and OpenAI-compatible chat APIs."""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]


def post_json(
    label: str,
    url: str,
    timeout: float,
    *,
    json: object = None,
    headers: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """POST and return the parsed JSON body, wrapping httpx errors as ProviderError."""
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=json, headers=headers)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
    except httpx.TimeoutException as exc:
        raise ProviderError(f"{label} timed out after {timeout}s") from exc
    except httpx.HTTPStatusError as exc:
        raise ProviderError(f"{label} returned HTTP {exc.response.status_code}") from exc
    except httpx.InvalidURL as exc:
        raise ProviderError(f"{label} base URL invalid: {exc}") from exc
    except ValueError as exc:
        raise ProviderError(f"{label} returned invalid JSON") from exc
    except httpx.HTTPError as exc:
        raise ProviderError(f"{label} request failed: {exc}") from exc


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


def finalize_content(content: object, label: str, *, truncated: bool = False) -> str:
    """Validate a provider's raw response content, strip <think> blocks, require non-empty text.

    ``truncated`` is the provider's own "stopped at the token cap" signal. With no text, that
    means a reasoning model spent the whole budget thinking, which only a bigger cap fixes.
    """
    result = strip_thinking(content) if isinstance(content, str) else ""
    if truncated and not result:
        raise ProviderError(f"{label} used the whole max-tokens budget; raise --max-tokens")
    if not isinstance(content, str):
        raise ProviderError(f"{label} returned no text content")
    if not result:
        raise ProviderError(f"{label} returned empty content")
    return result + TRUNCATED_NOTE if truncated else result
