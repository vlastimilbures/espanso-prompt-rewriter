from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .base import ProviderError, body_error, chat_messages, finalize_content, post_json
from .usage import Meter, UsageObserver, openai_usage, openrouter_usage


@dataclass
class OpenAICompatibleProvider:
    """Chat provider for any OpenAI-compatible /chat/completions endpoint.

    Covers OpenRouter, LM Studio, and other compatible servers. The API key is
    optional so local servers (LM Studio) work without one.
    """

    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False)
    timeout: float = 30
    max_tokens: int | None = None
    temperature: float | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    label: str = "OpenAI-compatible endpoint"
    # Both default to inert; used by scripts/bench_models.py to add request fields and read
    # the raw body of a successful reply.
    extra_body: dict[str, object] = field(default_factory=dict)
    on_response: Callable[[dict[str, object]], None] | None = None
    # Receives one AttemptUsage per HTTP attempt; see providers/usage.py. ``name`` is the
    # provider in each record; "openrouter" also reads OpenRouter's cost and serving provider.
    # ``local`` says the model runs on this machine, so it has no cost to report.
    observer: UsageObserver | None = field(default=None, repr=False, compare=False)
    name: str = "openai-compatible"
    local: bool = False

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    def generate(self, prompt: str, system_prompt: str) -> str:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, object] = {
            "model": self.model,
            "messages": chat_messages(system_prompt, prompt),
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        # Omitted when None: some models (e.g. openai/gpt-5-nano) reject the field.
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        payload.update(self.extra_body)

        meter = None
        if self.observer is not None:
            parse = openrouter_usage if self.name == "openrouter" else openai_usage
            meter = Meter(self.observer, self.name, self.model, parse, self.local)
        data = post_json(
            self.label,
            f"{self.base_url}/chat/completions",
            self.timeout,
            headers=headers,
            json=payload,
            meter=meter,
        )
        if self.on_response is not None:
            self.on_response(data)

        try:
            choice = data["choices"][0]  # type: ignore[index]
            # OpenRouter puts an error raised mid-generation in the choice it cut short.
            if error := body_error(self.label, choice):
                raise error
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"{self.label} response was malformed") from exc

        return finalize_content(content, self.label, stop_reason=choice.get("finish_reason"))
