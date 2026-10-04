from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from .config import Settings
from .gate import GatedProvider
from .providers.anthropic import AnthropicProvider
from .providers.base import Provider, ProviderError, is_loopback
from .providers.ollama import OllamaProvider
from .providers.openai_compatible import OpenAICompatibleProvider
from .redaction import compile_extra, safe_repr

PROVIDER_NAMES = ("ollama", "lmstudio", "openrouter", "anthropic")
# Sent as OpenRouter's X-Title so calls are attributed to this app in its dashboard.
APP_TITLE = "espanso-prompt-rewriter"


def openrouter_routing(pin: str, allow_fallbacks: bool) -> dict[str, object]:
    """OpenRouter `provider` preferences pinning one endpoint tag; {} for blended routing."""
    if not pin:
        return {}
    routing: dict[str, object] = {"order": [pin]}
    if not allow_fallbacks:
        routing["allow_fallbacks"] = False
    return routing


def openrouter_body(cfg: Settings) -> dict[str, object]:
    """OpenRouter-only request fields from settings: endpoint routing and reasoning effort.

    `exclude`: the reasoning trace is billed but never returned as text to paste.
    """
    body: dict[str, object] = {}
    routing = openrouter_routing(cfg.openrouter_provider, cfg.openrouter_allow_fallbacks)
    if routing:
        body["provider"] = routing
    if cfg.openrouter_reasoning_effort:
        body["reasoning"] = {"effort": cfg.openrouter_reasoning_effort, "exclude": True}
    return body


def _require(value: str | None, env_name: str) -> str:
    if not value:
        raise ProviderError(f"{env_name} is not configured")
    return value


def _require_https(url: str, env_name: str) -> str:
    """Refuse to send an API key over plaintext HTTP; a loopback proxy is the exception."""
    scheme = urlsplit(url).scheme
    if scheme == "https" or (scheme == "http" and is_loopback(url)):
        return url
    raise ProviderError(f"{env_name} must be an https:// URL")


def _is_ollama_cloud(model: str) -> bool:
    """Ollama forwards models tagged `cloud` or `*-cloud` (e.g. gpt-oss:120b-cloud) to
    ollama.com instead of running them locally."""
    tag = model.partition(":")[2]
    return tag == "cloud" or tag.endswith("-cloud")


def _leaves_machine(name: str, cfg: Settings) -> bool:
    """Whether the named provider can send the draft off this machine: the cloud providers
    always, Ollama or LM Studio when the base URL is not loopback or (Ollama) the model is a
    cloud model. The gate and PROMPT_LOCAL_ONLY both follow this one decision."""
    if name == "ollama":
        return not is_loopback(cfg.ollama_base_url) or _is_ollama_cloud(cfg.ollama_model)
    if name == "lmstudio":
        return not is_loopback(cfg.lmstudio_base_url)
    return name in ("openrouter", "anthropic")


def _gate(inner: Provider, cfg: Settings) -> GatedProvider:
    # make_provider refuses earlier with a clearer message; this keeps the guarantee for any
    # provider added later that returns through _gate().
    if cfg.local_only:
        raise ProviderError(
            "PROMPT_LOCAL_ONLY=true: this provider would send the draft off this machine"
        )
    return GatedProvider(
        inner,
        allow_override=cfg.allow_cloud_override,
        extra_patterns=compile_extra(cfg.extra_patterns),
    )


def make_provider(
    name: str,
    cfg: Settings,
    *,
    extra_body: dict[str, object] | None = None,
    on_response: Callable[[dict[str, object]], None] | None = None,
    title: str = APP_TITLE,
) -> Provider:
    """Build the named provider from settings. Anything that can send the draft off this
    machine (see _leaves_machine) comes wrapped in the data-protection gate, and with
    PROMPT_LOCAL_ONLY=true it is refused before anything is built. Nothing built here can
    skip either.

    ``extra_body``, ``on_response`` and ``title`` only apply to OpenRouter; they let
    scripts/bench_models.py request usage/cost data through the same construction path.
    """
    remote = _leaves_machine(name, cfg)
    if remote and cfg.local_only:
        raise ProviderError(f"PROMPT_LOCAL_ONLY=true: {name} would send the draft off this machine")
    if name == "ollama":
        ollama = OllamaProvider(
            base_url=cfg.ollama_base_url,
            model=cfg.ollama_model,
            timeout=cfg.timeout,
            think=cfg.ollama_think,
            temperature=cfg.temperature,
        )
        return _gate(ollama, cfg) if remote else ollama
    if name == "lmstudio":
        lmstudio = OpenAICompatibleProvider(
            base_url=cfg.lmstudio_base_url,
            model=cfg.lmstudio_model,
            timeout=cfg.timeout,
            temperature=cfg.temperature,
            label="LM Studio",
        )
        return _gate(lmstudio, cfg) if remote else lmstudio
    if name == "openrouter":
        return _gate(
            OpenAICompatibleProvider(
                base_url=_require_https(cfg.openrouter_base_url, "OPENROUTER_BASE_URL"),
                model=cfg.openrouter_model,
                api_key=_require(cfg.openrouter_api_key, "OPENROUTER_API_KEY"),
                timeout=cfg.timeout,
                max_tokens=cfg.openrouter_max_tokens,
                temperature=cfg.temperature,
                extra_headers={"X-Title": title},
                label="OpenRouter",
                extra_body={**openrouter_body(cfg), **(extra_body or {})},
                on_response=on_response,
            ),
            cfg,
        )
    if name == "anthropic":
        return _gate(
            AnthropicProvider(
                base_url=_require_https(cfg.anthropic_base_url, "ANTHROPIC_BASE_URL"),
                model=cfg.anthropic_model,
                api_key=_require(cfg.anthropic_api_key, "ANTHROPIC_API_KEY"),
                timeout=cfg.timeout,
                max_tokens=cfg.anthropic_max_tokens,
                temperature=cfg.temperature,
            ),
            cfg,
        )
    raise ProviderError(f"Unknown provider {safe_repr(name)}. Use {', '.join(PROVIDER_NAMES)}.")
