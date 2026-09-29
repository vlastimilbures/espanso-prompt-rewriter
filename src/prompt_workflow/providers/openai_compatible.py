from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .base import ProviderError, chat_messages, finalize_content, post_json


@dataclass
class OpenAICompatibleProvider:
    """Chat provider for any OpenAI-compatible /chat/completions endpoint.

    Covers OpenRouter, LM Studio, and other compatible servers. The API key is
    optional so local servers (LM Studio) work without one.
    """

    base_url: str
    default_model: str
    api_key: str | None = None
    timeout: float = 30
    max_tokens: int | None = None
    temperature: float | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    label: str = "OpenAI-compatible endpoint"
    # Both default to inert; used by scripts/bench_models.py to ask OpenRouter for
    # real token counts and cost (`{"usage": {"include": True}}`) and read them back.
    extra_body: dict[str, object] = field(default_factory=dict)
    on_response: Callable[[dict[str, object]], None] | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    def generate(self, prompt: str, system_prompt: str, model: str | None = None) -> str:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, object] = {
            "model": model or self.default_model,
            "messages": chat_messages(system_prompt, prompt),
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        # Omitted when None: some models (e.g. openai/gpt-5-nano) reject the field.
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        payload.update(self.extra_body)

        data = post_json(
            self.label,
            f"{self.base_url}/chat/completions",
            self.timeout,
            headers=headers,
            json=payload,
        )
        if self.on_response is not None:
            self.on_response(data)

        try:
            choice = data["choices"][0]  # type: ignore[index]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"{self.label} response was malformed") from exc

        truncated = choice.get("finish_reason") == "length"
        return finalize_content(content, self.label, truncated=truncated)
