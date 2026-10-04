from __future__ import annotations

import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from .redaction import safe_repr


def _parse_value(raw: str) -> str:
    """A .env value: the text inside a leading quote pair, else the text before an inline
    ` # comment` (so `OPENROUTER_PROVIDER=openai  # note` is just `openai`)."""
    value = raw.strip()
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)
        if end > 0:
            return value[1:end]
    return re.split(r"\s+#", value, maxsplit=1)[0]


# Root of an editable install (the repo checkout), where the user keeps their .env.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _user_config_dir() -> Path:
    if os.name == "nt":
        return Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming") / "prompt-workflow"
    return Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config") / "prompt-workflow"


def _env_file_candidates() -> list[Path]:
    """Where .env may live, in priority order.

    Deliberately never the current directory or its parents: running the CLI inside an
    untrusted checkout must not let a planted .env redirect OPENROUTER_BASE_URL (and so the
    API key) or switch on ALLOW_CLOUD_OVERRIDE.
    """
    explicit = os.getenv("PROMPT_WORKFLOW_ENV")
    if explicit:
        return [Path(explicit).expanduser()]
    candidates = [_user_config_dir() / ".env"]
    if (_PROJECT_ROOT / "pyproject.toml").is_file():
        candidates.insert(0, _PROJECT_ROOT / ".env")
    return candidates


def read_env_file(path: Path) -> dict[str, str] | None:
    """KEY=VALUE pairs of a .env file, or None when it is missing or unreadable."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    pairs = {}
    for raw in raw_text.splitlines():
        line = raw.strip().removeprefix("export ")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        pairs[key.strip()] = _parse_value(value)
    return pairs


def _load_dotenv() -> None:
    """Load the first readable .env found, without overriding existing env.

    This is intentionally dependency-free so it works even when GUI-launched
    Espanso does not inherit the interactive shell environment. Only the settings this
    package reads are exported: any other key (HTTP_PROXY, SSL_CERT_FILE, ...) would
    change how httpx connects, which is not what a settings file is for.
    """
    known = set(env_names())
    for candidate in _env_file_candidates():
        pairs = read_env_file(candidate)
        if pairs is not None:
            _reject_merged_lines(pairs)
            for key, value in pairs.items():
                if key in known:
                    os.environ.setdefault(key, value)
            return


def _reject_merged_lines(pairs: dict[str, str]) -> None:
    """Refuse a value that holds another assignment (a setting name in any case, or any
    `UPPER_CASE` name, then `=`): a .env saved without the newline between two lines, whose
    rest would otherwise become part of this setting's value. PROMPT_EXTRA_PATTERNS is
    exempt, since a user regex may well match such text."""
    names = env_names()
    known = "|".join(re.escape(name) for name in names)
    assignment = re.compile(rf"(?:(?i:{known})|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\s*=")
    for key, value in pairs.items():
        if key != "PROMPT_EXTRA_PATTERNS" and assignment.search(value):
            shown = key if key in names else safe_repr(key)
            raise ValueError(f"{shown} in .env runs into the next line; add the missing newline")


def _env(
    name: str, default: str, parse: Callable[[str], Any] = str, *, secret: bool = False
) -> Any:
    """A dataclass field read from env var `name` when Settings() is instantiated. A
    ``secret`` stays out of repr(), so a traceback or test failure cannot print it."""

    def read() -> Any:
        raw = os.getenv(name, default)
        try:
            return parse(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be {exc}, got {safe_repr(raw)}") from None

    return field(default_factory=read, repr=not secret, metadata={"env": name})


def env_names() -> tuple[str, ...]:
    """Every environment variable Settings reads, in field order."""
    return tuple(f.metadata["env"] for f in fields(Settings))


# Parsers raise ValueError(what the value must be); _env and _override name the setting.
def _bool(raw: str) -> bool:
    # Strict, so a typo such as OLLAMA_THINK=1 is reported instead of silently meaning false.
    if raw.lower() not in ("true", "false"):
        raise ValueError("true or false")
    return raw.lower() == "true"


def _number(
    cast: Callable[[str], float], expected: str, ok: Callable[[float], bool]
) -> Callable[[str], float]:
    """Parser for a finite number that passes ``ok``: an `inf` timeout would hang Espanso."""

    def parse(raw: str) -> float:
        try:
            value = cast(raw)
        except ValueError:
            value = math.nan
        if not (math.isfinite(value) and ok(value)):
            raise ValueError(expected)
        return value

    return parse


_positive_int = _number(int, "a whole number above 0", lambda v: v > 0)
_positive_float = _number(float, "a number above 0", lambda v: v > 0)
_non_negative_float = _number(float, "a number of 0 or more", lambda v: v >= 0)


TIERS = ("standard", "pro")
# OpenRouter `reasoning.effort` values accepted by --effort.
EFFORTS = ("none", "minimal", "low", "medium", "high")
# Option value meaning "keep the configured setting"; the -if- popup's choice lists
# cannot express "unset", so each one offers this word instead.
KEEP = "default"
# --model value suffix `@auto` drops the endpoint pin (OpenRouter's blended routing).
AUTO_ENDPOINT = "auto"


def split_model_spec(spec: str | None) -> tuple[str | None, str | None]:
    """Split `slug@endpoint` into (slug, endpoint pin); a bare slug has no pin (None).

    The popup's model list carries the endpoint with the model because the configured pin
    (e.g. google-ai-studio/flex) only serves one vendor's models. `@auto` maps to "".
    """
    if not spec or "@" not in spec:
        return spec, None
    model, _, endpoint = spec.partition("@")
    if not model or not endpoint:
        raise ValueError(f"--model must be slug or slug@endpoint, got {safe_repr(spec)}")
    return model, "" if endpoint == AUTO_ENDPOINT else endpoint


def _override[T](raw: str | None, option: str, parse: Callable[[str], T]) -> T | None:
    """Parse an optional per-call override; None or `default` means keep the setting."""
    if raw is None or raw == KEEP:
        return None
    try:
        return parse(raw)
    except ValueError as exc:
        raise ValueError(f"{option} must be {exc} or {KEEP}, got {safe_repr(raw)}") from None


@dataclass(frozen=True)
class Settings:
    """Runtime settings resolved at instantiation, not import time."""

    provider: str = _env("PROMPT_PROVIDER", "openrouter")
    profile: str = _env("PROMPT_PROFILE", "default")
    # Opening sentence of the default profile's CONTEXT and of the -p- snippet, e.g.
    # "I am working as a Head of Data at Example Corp." Empty: no fixed persona.
    persona: str = _env("PROMPT_PERSONA", "")
    timeout: float = _env("PROMPT_TIMEOUT_SECONDS", "30", _positive_float)
    # Low by default: the rewrite must reproduce fixed template wordings verbatim.
    temperature: float = _env("PROMPT_TEMPERATURE", "0.2", _non_negative_float)
    ollama_base_url: str = _env("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = _env("OLLAMA_MODEL", "qwen3:8b")
    ollama_think: bool = _env("OLLAMA_THINK", "false", _bool)
    openrouter_base_url: str = _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_model: str = _env("OPENROUTER_MODEL", "google/gemini-3.5-flash-lite")
    # Stripped: a space pasted along with a key is never part of it.
    openrouter_api_key: str = _env("OPENROUTER_API_KEY", "", str.strip, secret=True)
    openrouter_max_tokens: int = _env("OPENROUTER_MAX_TOKENS", "2400", _positive_int)
    # OpenRouter routes one model slug across many hosts, and the host drives latency,
    # cost and template fidelity (see the benchmark section in README.md). Pin one
    # endpoint tag; empty string restores OpenRouter's own blended routing.
    openrouter_provider: str = _env("OPENROUTER_PROVIDER", "google-ai-studio/flex")
    # OpenRouter `reasoning.effort` (none/minimal/low/medium/high); empty omits the field
    # for models without a reasoning control. Gemini 3.x cannot switch thinking off, and
    # minimal keeps the inline rewrite at ~2 s.
    openrouter_reasoning_effort: str = _env("OPENROUTER_REASONING_EFFORT", "minimal")
    # Preference, not constraint: a pinned endpoint can be down, and in Espanso that
    # surfaces as an error marker pasted into the editor. Set false for a hard pin.
    openrouter_allow_fallbacks: bool = _env("OPENROUTER_ALLOW_FALLBACKS", "true", _bool)
    # The `pro` tier (-ip-): a reasoning model for hard, multi-part drafts. It
    # swaps in these OpenRouter settings; everything else is shared with the default tier.
    openrouter_pro_model: str = _env("OPENROUTER_PRO_MODEL", "openai/gpt-6-luna")
    openrouter_pro_provider: str = _env("OPENROUTER_PRO_PROVIDER", "openai")
    openrouter_pro_reasoning_effort: str = _env("OPENROUTER_PRO_REASONING_EFFORT", "low")
    pro_timeout: float = _env("PROMPT_PRO_TIMEOUT_SECONDS", "60", _positive_float)
    # Profile for the pro tier; empty uses PROMPT_PROFILE. The two tiers' models react to
    # the same prompt wording differently: `default-pro` is `default` without the clause that
    # flash-lite needs and gpt-6-luna over-applies (see tests/test_prompts.py).
    pro_profile: str = _env("PROMPT_PRO_PROFILE", "default-pro")
    lmstudio_base_url: str = _env("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
    lmstudio_model: str = _env("LMSTUDIO_MODEL", "local-model")
    anthropic_base_url: str = _env("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    anthropic_model: str = _env("ANTHROPIC_MODEL", "claude-sonnet-5")
    anthropic_api_key: str = _env("ANTHROPIC_API_KEY", "", str.strip, secret=True)
    anthropic_max_tokens: int = _env("ANTHROPIC_MAX_TOKENS", "2400", _positive_int)
    allow_cloud_override: bool = _env("ALLOW_CLOUD_OVERRIDE", "false", _bool)
    # Extra `;`-separated regexes the data-protection gate blocks on, e.g. internal project
    # code names or customer-ID formats. Compiled by redaction.compile_extra().
    extra_patterns: str = _env("PROMPT_EXTRA_PATTERNS", "")

    @classmethod
    def load(cls) -> Settings:
        """Load .env (without overriding real env) then build settings."""
        _load_dotenv()
        return cls()

    def for_tier(self, tier: str) -> Settings:
        """Settings for a quality tier: `standard` as-is, `pro` with the OPENROUTER_PRO_*
        model, endpoint and effort, and PROMPT_PRO_PROFILE if set. Only OpenRouter has a
        pro tier."""
        if tier == "standard":
            return self
        if tier == "pro":
            return replace(
                self,
                openrouter_model=self.openrouter_pro_model,
                openrouter_provider=self.openrouter_pro_provider,
                openrouter_reasoning_effort=self.openrouter_pro_reasoning_effort,
                timeout=self.pro_timeout,
                profile=self.pro_profile or self.profile,
            )
        raise ValueError(f"Unknown tier: {tier}. Choose from: {', '.join(TIERS)}")

    def for_call(
        self,
        tier: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: str | None = None,
        timeout: str | None = None,
    ) -> Settings:
        """Settings for one CLI call: for_tier(), then the per-call overrides. The pro
        profile is tuned for OPENROUTER_PRO_MODEL, so a pro call that runs another model
        (one picked in the -if- popup) uses PROMPT_PROFILE instead."""
        cfg = self.for_tier(tier).with_overrides(
            model=model, effort=effort, max_tokens=max_tokens, timeout=timeout
        )
        if tier == "pro" and cfg.openrouter_model != self.openrouter_pro_model:
            return replace(cfg, profile=self.profile)
        return cfg

    def with_overrides(
        self,
        *,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: str | None = None,
        timeout: str | None = None,
    ) -> Settings:
        """Per-call overrides from the CLI (the -if- popup). Values arrive as strings; None
        or `default` keeps the setting. ``model`` applies to whichever provider runs, and its
        `slug@endpoint` form also sets the OpenRouter pin. Bad values raise ValueError, which
        the CLI prints inline."""
        if effort not in (None, KEEP, *EFFORTS):
            raise ValueError(f"--effort must be one of {', '.join((KEEP, *EFFORTS))}")
        slug, endpoint = split_model_spec(model)
        tokens = _override(max_tokens, "--max-tokens", _positive_int)
        seconds = _override(timeout, "--timeout", _positive_float)
        changes: dict[str, Any] = {}
        if slug:
            models = ("ollama_model", "lmstudio_model", "openrouter_model", "anthropic_model")
            changes |= dict.fromkeys(models, slug)
        if endpoint is not None:
            changes["openrouter_provider"] = endpoint
        if effort not in (None, KEEP):
            changes["openrouter_reasoning_effort"] = effort
        if tokens is not None:
            changes["openrouter_max_tokens"] = tokens
            changes["anthropic_max_tokens"] = tokens
        if seconds is not None:
            changes["timeout"] = seconds
        return replace(self, **changes) if changes else self
