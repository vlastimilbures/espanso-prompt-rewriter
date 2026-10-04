from __future__ import annotations

import math
import os
import re
from collections.abc import Callable, Mapping
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


def _user_config_dir(environ: Mapping[str, str] = os.environ) -> Path:
    if os.name == "nt":
        appdata = environ.get("APPDATA")
        return Path(appdata or Path.home() / "AppData" / "Roaming") / "prompt-workflow"
    return Path(environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "prompt-workflow"


def _env_file_candidates(environ: Mapping[str, str] = os.environ) -> list[Path]:
    """Where .env may live, in priority order.

    Deliberately never the current directory or its parents: running the CLI inside an
    untrusted checkout must not let a planted .env redirect OPENROUTER_BASE_URL (and so the
    API key) or switch on ALLOW_CLOUD_OVERRIDE.
    """
    explicit = environ.get("PROMPT_WORKFLOW_ENV")
    if explicit:
        return [Path(explicit).expanduser()]
    candidates = [_user_config_dir(environ) / ".env"]
    if (_PROJECT_ROOT / "pyproject.toml").is_file():
        candidates.insert(0, _PROJECT_ROOT / ".env")
    return candidates


def _parse_env_text(text: str) -> tuple[dict[str, str], list[int]]:
    """KEY=VALUE pairs of .env text, and the numbers of the lines skipped for lacking `=`."""
    pairs = {}
    skipped = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip().removeprefix("export ")
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            skipped.append(number)
            continue
        key, _, value = line.partition("=")
        pairs[key.strip()] = _parse_value(value)
    return pairs, skipped


def read_env_file(path: Path) -> dict[str, str] | None:
    """KEY=VALUE pairs of a .env file, or None when it is missing or unreadable."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return _parse_env_text(raw_text)[0]


def _find_env_file(
    environ: Mapping[str, str], note: Callable[[str, str], None]
) -> tuple[Path, dict[str, str]] | None:
    """The first readable .env candidate and its pairs, or None when there is none. A
    candidate that exists but cannot be read is skipped, as is a line without `=`; each is
    passed to ``note`` (by line number, never content)."""
    for candidate in _env_file_candidates(environ):
        source = f"file:{candidate}"
        try:
            raw_text = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            note(source, ".env is not UTF-8 text; it was skipped")
            continue
        except OSError:
            if candidate.exists():
                note(source, ".env cannot be read; it was skipped")
            continue
        pairs, skipped = _parse_env_text(raw_text)
        for number in skipped:
            note(source, f"line {number} of .env has no '=' and was ignored")
        return candidate, pairs
    return None


def _merged_lines(pairs: Mapping[str, str]) -> list[str]:
    """Keys whose value holds another assignment (a setting name in any case, or any
    `UPPER_CASE` name, then `=`): a .env saved without the newline between two lines, whose
    rest would otherwise become part of this setting's value. PROMPT_EXTRA_PATTERNS is
    exempt, since a user regex may well match such text."""
    known = "|".join(re.escape(name) for name in env_names())
    assignment = re.compile(rf"(?:(?i:{known})|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\s*=")
    return [
        key
        for key, value in pairs.items()
        if key != "PROMPT_EXTRA_PATTERNS" and assignment.search(value)
    ]


def _merged_line_error(key: str) -> str:
    # An unknown key is shown only through safe_repr: a key line merged in front of it
    # would otherwise be repeated.
    shown = key if key in env_names() else safe_repr(key)
    return f"{shown} in .env runs into the next line; add the missing newline"


def _parse_setting(name: str, parse: Callable[[str], Any], raw: str) -> Any:
    try:
        return parse(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be {exc}, got {safe_repr(raw)}") from None


def _env(
    name: str, default: str, parse: Callable[[str], Any] = str, *, secret: bool = False
) -> Any:
    """A dataclass field read from env var `name` when Settings() is instantiated (no .env:
    Settings.load() builds from ConfigLayers instead). A ``secret`` stays out of repr(), so
    a traceback or test failure cannot print it."""

    def read() -> Any:
        return _parse_setting(name, parse, os.getenv(name, default))

    metadata = {"env": name, "default": default, "parse": parse}
    return field(default_factory=read, repr=not secret, metadata=metadata)


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


def _builtin_profiles(raw: str) -> tuple[str, ...]:
    """A comma-separated list of built-in profile names, duplicates dropped."""
    names = tuple(dict.fromkeys(name.strip() for name in raw.split(",") if name.strip()))
    if not names:
        return ()
    from .prompt_builder import PROFILES  # deferred: prompt_builder imports this module

    if any(name not in PROFILES for name in names):
        raise ValueError(f"empty or a comma-separated list of {', '.join(PROFILES)}")
    return names


TIERS = ("standard", "pro")
# OpenRouter `reasoning.effort` values accepted by --effort.
EFFORTS = ("none", "minimal", "low", "medium", "high")


def _effort(raw: str) -> str:
    # A typo would otherwise reach OpenRouter and come back as an opaque HTTP 400.
    if raw.lower() not in ("", *EFFORTS):
        raise ValueError(f"empty or one of {', '.join(EFFORTS)}")
    return raw.lower()


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
    openrouter_reasoning_effort: str = _env("OPENROUTER_REASONING_EFFORT", "minimal", _effort)
    # Preference, not constraint: a pinned endpoint can be down, and in Espanso that
    # surfaces as an error marker pasted into the editor. Set false for a hard pin.
    openrouter_allow_fallbacks: bool = _env("OPENROUTER_ALLOW_FALLBACKS", "true", _bool)
    # The `pro` tier (-ip-): a reasoning model for hard, multi-part drafts. It
    # swaps in these OpenRouter settings; everything else is shared with the default tier.
    openrouter_pro_model: str = _env("OPENROUTER_PRO_MODEL", "openai/gpt-6-luna")
    openrouter_pro_provider: str = _env("OPENROUTER_PRO_PROVIDER", "openai")
    openrouter_pro_reasoning_effort: str = _env("OPENROUTER_PRO_REASONING_EFFORT", "low", _effort)
    pro_timeout: float = _env("PROMPT_PRO_TIMEOUT_SECONDS", "60", _positive_float)
    # Profile for the pro tier; empty (the default) uses PROMPT_PROFILE, so both tiers send
    # the same prompt. An escape hatch for a prompt tuned to OPENROUTER_PRO_MODEL.
    pro_profile: str = _env("PROMPT_PRO_PROFILE", "")
    # Built-in profiles that a same-named file in the user profile directory replaces
    # (prompt_builder.user_profiles_dir()). Empty: such a file is ignored, never a silent swap.
    profile_overrides: tuple[str, ...] = _env("PROMPT_PROFILE_OVERRIDES", "", _builtin_profiles)
    lmstudio_base_url: str = _env("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
    lmstudio_model: str = _env("LMSTUDIO_MODEL", "local-model")
    anthropic_base_url: str = _env("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    anthropic_model: str = _env("ANTHROPIC_MODEL", "claude-sonnet-5")
    anthropic_api_key: str = _env("ANTHROPIC_API_KEY", "", str.strip, secret=True)
    anthropic_max_tokens: int = _env("ANTHROPIC_MAX_TOKENS", "2400", _positive_int)
    allow_cloud_override: bool = _env("ALLOW_CLOUD_OVERRIDE", "false", _bool)
    # Refuse every provider that can send the draft off this machine (factory.make_provider),
    # whatever --provider a trigger passes.
    local_only: bool = _env("PROMPT_LOCAL_ONLY", "false", _bool)
    # Extra `;`-separated regexes the data-protection gate blocks on, e.g. internal project
    # code names or customer-ID formats. Compiled by redaction.compile_extra().
    extra_patterns: str = _env("PROMPT_EXTRA_PATTERNS", "")

    @classmethod
    def load(cls) -> Settings:
        """Settings from ConfigLayers.resolve(): default < .env < real environment. Each call
        reads the .env again, and os.environ is never written."""
        return ConfigLayers.resolve().settings()

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
        """Settings for one CLI call: for_tier(), then the per-call overrides. A set
        PROMPT_PRO_PROFILE is meant for OPENROUTER_PRO_MODEL, so a pro call that runs another
        model (one picked in the -if- popup) uses PROMPT_PROFILE instead."""
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
        slug, endpoint = split_model_spec(None if model == KEEP else model)
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


# Provenance of a setting value: a built-in default, a .env file (`file:<path>`) or the
# real environment.
DEFAULT_SOURCE = "default"
ENV_SOURCE = "env"


@dataclass(frozen=True)
class Layer:
    """One source of setting values, as unparsed strings, keyed by env var name."""

    source: str
    values: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True)
class Entry:
    """A setting's effective value and the layer it came from. ``shadows`` lists the other
    layers (not the default) that also set it and lost, e.g. a .env value overridden by a
    real environment variable. ``rejected`` lists the higher layers whose value repair mode
    refused (see ConfigLayers.findings), so the value fell back to this one."""

    value: str = field(repr=False)  # may be an API key
    source: str
    shadows: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()


@dataclass(frozen=True)
class Finding:
    """A problem repair mode skipped instead of raising; ``message`` is strict mode's error."""

    source: str
    message: str


@dataclass(frozen=True)
class ConfigLayers:
    """The layers that make up the settings, lowest precedence first, and the effective
    value of every setting in env_names().

    A pure merge: building it reads the environment mapping and the .env file but never
    writes os.environ, so a second resolve sees an edited file and child processes inherit
    nothing from it. More layers (a user TOML file) can slot into ``layers`` later.
    """

    layers: tuple[Layer, ...]
    entries: Mapping[str, Entry]
    findings: tuple[Finding, ...] = ()

    @classmethod
    def resolve(
        cls, environ: Mapping[str, str] | None = None, *, strict: bool = True
    ) -> ConfigLayers:
        """Merge default < first readable .env < ``environ`` (os.environ by default).

        Only env_names() keys are taken from either source: any other key (HTTP_PROXY,
        SSL_CERT_FILE, ...) would change how httpx connects, which is not what a settings
        file is for. Strict mode (``improve``, ``persona``) raises the first problem as a
        ValueError. Repair mode records each as a Finding instead and falls back to the next
        lower layer, so management commands still run on a broken config. Repair mode also
        notes what strict mode skips silently: a .env that exists but cannot be read, and a
        line without `=` (by number, never its text).
        """
        environ = os.environ if environ is None else environ
        findings: list[Finding] = []

        def note(source: str, message: str) -> None:
            # Repair mode only: strict mode has always skipped these silently.
            if not strict:
                findings.append(Finding(source, message))

        def fail(source: str, message: str) -> None:
            if strict:
                raise ValueError(message)
            findings.append(Finding(source, message))

        known = env_names()
        defaults = {f.metadata["env"]: f.metadata["default"] for f in fields(Settings)}
        layers = [Layer(DEFAULT_SOURCE, defaults)]
        found = _find_env_file(environ, note)
        if found is not None:
            path, pairs = found
            source = f"file:{path}"
            merged = _merged_lines(pairs)
            for key in merged:
                fail(source, _merged_line_error(key))
            values = {k: v for k, v in pairs.items() if k in known and k not in merged}
            layers.append(Layer(source, values))
        layers.append(Layer(ENV_SOURCE, {k: environ[k] for k in known if k in environ}))

        entries: dict[str, Entry] = {}
        for f in fields(Settings):
            name = f.metadata["env"]
            setters = [layer for layer in reversed(layers) if name in layer.values]
            rejected: list[str] = []
            for index, layer in enumerate(setters):
                raw = layer.values[name]
                try:
                    _parse_setting(name, f.metadata["parse"], raw)
                except ValueError as exc:
                    fail(layer.source, str(exc))
                    rejected.append(layer.source)
                    continue
                lost = setters[index + 1 :]
                shadows = tuple(s.source for s in lost if s.source != DEFAULT_SOURCE)
                entries[name] = Entry(raw, layer.source, shadows, tuple(rejected))
                break
        return cls(tuple(layers), entries, tuple(findings))

    def settings(self) -> Settings:
        """Settings built from the effective values."""
        return Settings(
            **{
                f.name: _parse_setting(
                    f.metadata["env"], f.metadata["parse"], self.entries[f.metadata["env"]].value
                )
                for f in fields(Settings)
            }
        )
