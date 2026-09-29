from __future__ import annotations

from dataclasses import dataclass

from .base import ProviderError, chat_messages, finalize_content, post_json


@dataclass
class OllamaProvider:
    """Chat provider for Ollama's native /api/chat endpoint."""

    base_url: str
    default_model: str
    timeout: float = 30
    # Off by default: disables reasoning for thinking-capable models such as qwen3.
    think: bool = False

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")

    def generate(self, prompt: str, system_prompt: str, model: str | None = None) -> str:
        payload = {
            "model": model or self.default_model,
            "messages": chat_messages(system_prompt, prompt),
            "stream": False,
            "think": self.think,
        }
        data = post_json("Ollama", f"{self.base_url}/api/chat", self.timeout, json=payload)

        try:
            content = data["message"]["content"]  # type: ignore[index]
        except (KeyError, TypeError) as exc:
            raise ProviderError("Ollama response was malformed") from exc

        truncated = data.get("done_reason") == "length"
        return finalize_content(content, "Ollama", truncated=truncated)
