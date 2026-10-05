from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import Settings
from .gate import GateBlocked, GatedProvider
from .providers.anthropic import AnthropicProvider
from .providers.base import Provider, ProviderError, is_loopback
from .providers.ollama import OllamaProvider
from .providers.openai_compatible import OpenAICompatibleProvider
from .providers.usage import UsageObserver
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
    # A key is printable ASCII; anything else came from a rich-text paste and cannot be sent
    # in a header. The key itself is never shown.
    if not (value.isascii() and value.isprintable()):
        raise ProviderError(
            f"{env_name} contains a non-ASCII or invisible character (a smart quote?); "
            "paste the key again"
        )
    return value


def _require_https(url: str, env_name: str) -> str:
    """Refuse to send an API key over plaintext HTTP; a loopback proxy is the exception."""
    scheme = urlsplit(url).scheme
    if scheme == "https" or (scheme == "http" and is_loopback(url)):
        return url
    raise GateBlocked(f"{env_name} must be an https:// URL")


def _is_ollama_cloud(model: str) -> bool:
    """Ollama forwards models tagged `cloud` or `*-cloud` (e.g. gpt-oss:120b-cloud) to
    ollama.com instead of running them locally. Ollama's names are case-insensitive and a
    reference may pin a digest (`name:tag@sha256:…`), so the tag is compared in lower case
    without it. Only the tag counts: a name such as `cloudy-llama:7b` stays local, and a
    `host:port/` registry prefix is not a tag."""
    _, colon, tag = model.partition("@")[0].rpartition(":")
    if not colon or "/" in tag:
        return False
    tag = tag.lower()
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


# The key setting each provider needs; the local ones need none.
PROVIDER_KEYS = {"openrouter": "OPENROUTER_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


@dataclass(frozen=True)
class Route:
    """Where a provider would send a draft, as the settings say: for a screen to show, not
    to call. ``remote`` is _leaves_machine(); ``refused`` is PROMPT_LOCAL_ONLY refusing it."""

    name: str
    # The settings that hold the model and the base URL, so a screen can show their values
    # the way `config show` does (a URL with a password in it only as set).
    model_setting: str
    url_setting: str
    key: str | None
    remote: bool
    refused: bool


def routes(cfg: Settings) -> list[Route]:
    """Every provider in PROVIDER_NAMES with its model and base URL settings, key setting and
    whether it can send the draft off this machine. Builds no provider and makes no call."""
    found = []
    for name in PROVIDER_NAMES:
        prefix = name.upper()
        remote = _leaves_machine(name, cfg)
        found.append(
            Route(
                name,
                f"{prefix}_MODEL",
                f"{prefix}_BASE_URL",
                PROVIDER_KEYS.get(name),
                remote,
                remote and cfg.local_only,
            )
        )
    return found


def _gate(
    inner: Provider,
    cfg: Settings,
    allow_flagged: bool = False,
    name: str = "openrouter",
    *,
    remote: bool = True,
) -> GatedProvider:
    # make_provider refuses earlier with a clearer message; this keeps the guarantee for any
    # provider added later that returns through _gate(). ``remote=False`` is only for a
    # loopback provider gated by PROMPT_GATE_LOCAL, which PROMPT_LOCAL_ONLY allows.
    if remote and cfg.local_only:
        raise GateBlocked(
            "PROMPT_LOCAL_ONLY=true: this provider would send the draft off this machine"
        )
    return GatedProvider(
        inner,
        allow_override=cfg.allow_cloud_override,
        extra_patterns=compile_extra(cfg.extra_patterns),
        allow_flagged=allow_flagged,
        name=name,
        relay=not remote,
        gate_local=cfg.gate_local,
    )


def make_provider(
    name: str,
    cfg: Settings,
    *,
    allow_flagged: bool = False,
    extra_body: dict[str, object] | None = None,
    on_response: Callable[[dict[str, object]], None] | None = None,
    title: str = APP_TITLE,
    observer: UsageObserver | None = None,
) -> Provider:
    """Build the named provider from settings. Anything that can send the draft off this
    machine (see _leaves_machine) comes wrapped in the data-protection gate, and with
    PROMPT_LOCAL_ONLY=true it is refused before anything is built. Nothing built here can
    skip either.

    ``allow_flagged`` (--allow-flagged) lets the gate send a draft whose findings are all
    soft, once; it never touches PROMPT_LOCAL_ONLY or a provider that stays on this machine.

    ``PROMPT_GATE_LOCAL=true`` gates a loopback Ollama or LM Studio too (a localhost relay to a
    cloud API), with the same override rules; it is still local for PROMPT_LOCAL_ONLY, routes()
    and its ``not_applicable`` cost.

    ``extra_body``, ``on_response`` and ``title`` only apply to OpenRouter; they let
    scripts/bench_models.py request usage/cost data through the same construction path.

    ``observer`` goes to every provider, inside the gate, and receives one AttemptUsage per
    HTTP attempt (providers/usage.py); a draft the gate blocks makes no attempt, so no record.
    A loopback Ollama or LM Studio that is not a cloud model reports cost ``not_applicable``.

    The ``-> Provider`` return type is also what makes mypy check that every provider class
    conforms to the Provider protocol.
    """
    remote = _leaves_machine(name, cfg)
    if remote and cfg.local_only:
        raise GateBlocked(f"PROMPT_LOCAL_ONLY=true: {name} would send the draft off this machine")
    if name == "ollama":
        ollama = OllamaProvider(
            base_url=cfg.ollama_base_url,
            model=cfg.ollama_model,
            timeout=cfg.timeout,
            think=cfg.ollama_think,
            temperature=cfg.temperature,
            max_tokens=cfg.call_max_tokens,
            observer=observer,
            local=not remote,
        )
        if remote or cfg.gate_local:
            return _gate(ollama, cfg, allow_flagged, name, remote=remote)
        return ollama
    if name == "lmstudio":
        lmstudio = OpenAICompatibleProvider(
            base_url=cfg.lmstudio_base_url,
            model=cfg.lmstudio_model,
            timeout=cfg.timeout,
            max_tokens=cfg.call_max_tokens,
            temperature=cfg.temperature,
            label="LM Studio",
            observer=observer,
            name=name,
            local=not remote,
        )
        if remote or cfg.gate_local:
            return _gate(lmstudio, cfg, allow_flagged, name, remote=remote)
        return lmstudio
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
                observer=observer,
                name=name,
            ),
            cfg,
            allow_flagged,
            name,
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
                observer=observer,
            ),
            cfg,
            allow_flagged,
            name,
        )
    raise ProviderError(f"Unknown provider {safe_repr(name)}. Use {', '.join(PROVIDER_NAMES)}.")
