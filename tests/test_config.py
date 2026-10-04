import os
from pathlib import Path

import pytest

from prompt_workflow import config
from prompt_workflow.config import Settings, _load_dotenv, split_model_spec


# Settings() reads env vars at instantiation, not at import time.
def test_settings_read_at_instantiation(monkeypatch):
    monkeypatch.setenv("PROMPT_PROVIDER", "lmstudio")
    monkeypatch.setenv("PROMPT_TIMEOUT_SECONDS", "12")
    settings = Settings()
    assert settings.provider == "lmstudio"
    assert settings.timeout == 12.0


# Default values apply when the relevant env vars are unset.
def test_defaults():
    settings = Settings()
    assert settings.provider == "openrouter"
    assert settings.profile == "default"
    assert settings.timeout == 30.0
    assert settings.temperature == 0.2
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.ollama_model == "qwen3:8b"
    assert settings.ollama_think is False
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.openrouter_model == "google/gemini-3.5-flash-lite"
    assert settings.openrouter_api_key == ""
    assert settings.openrouter_max_tokens == 2400
    assert settings.openrouter_provider == "google-ai-studio/flex"
    assert settings.openrouter_allow_fallbacks is True
    assert settings.openrouter_reasoning_effort == "minimal"
    assert settings.openrouter_pro_model == "openai/gpt-6-luna"
    assert settings.openrouter_pro_provider == "openai"
    assert settings.openrouter_pro_reasoning_effort == "low"
    assert settings.pro_timeout == 60.0
    assert settings.lmstudio_base_url == "http://localhost:1234/v1"
    assert settings.lmstudio_model == "local-model"
    assert settings.anthropic_base_url == "https://api.anthropic.com"
    assert settings.anthropic_model == "claude-sonnet-5"
    assert settings.anthropic_api_key == ""
    assert settings.anthropic_max_tokens == 2400
    assert settings.allow_cloud_override is False


# for_tier("pro") swaps in the pro model, endpoint, effort, timeout and profile; standard is
# a no-op.
def test_for_tier(monkeypatch):
    monkeypatch.setenv("OPENROUTER_PRO_MODEL", "x/pro")
    settings = Settings()
    assert settings.for_tier("standard") is settings
    pro = settings.for_tier("pro")
    assert (pro.openrouter_model, pro.openrouter_provider) == ("x/pro", "openai")
    assert (pro.openrouter_reasoning_effort, pro.timeout) == ("low", 60.0)
    assert pro.persona == settings.persona
    assert pro.profile == "default-pro"
    monkeypatch.setenv("PROMPT_PRO_PROFILE", "")
    assert Settings().for_tier("pro").profile == settings.profile  # empty: same profile
    monkeypatch.setenv("PROMPT_PRO_PROFILE", "general")
    assert Settings().for_tier("pro").profile == "general"
    assert Settings().for_tier("standard").profile == settings.profile
    with pytest.raises(ValueError, match="Unknown tier"):
        settings.for_tier("ultra")


# split_model_spec: a bare slug has no pin, slug@endpoint pins it, @auto unpins.
def test_split_model_spec():
    assert split_model_spec(None) == (None, None)
    assert split_model_spec("x/m") == ("x/m", None)
    assert split_model_spec("x/m@openai/flex") == ("x/m", "openai/flex")
    assert split_model_spec("x/m@auto") == ("x/m", "")
    for bad in ("x/m@", "@openai"):
        with pytest.raises(ValueError, match="slug@endpoint"):
            split_model_spec(bad)


# with_overrides: None and `default` keep the settings; real values replace them.
def test_with_overrides():
    settings = Settings()
    assert settings.with_overrides() is settings
    assert settings.with_overrides(effort="default", max_tokens="default") is settings
    custom = settings.with_overrides(effort="high", max_tokens="8000", timeout="120")
    assert custom.openrouter_provider == settings.openrouter_provider
    assert custom.openrouter_reasoning_effort == "high"
    assert (custom.openrouter_max_tokens, custom.anthropic_max_tokens) == (8000, 8000)
    assert custom.timeout == 120.0
    assert custom.openrouter_model == settings.openrouter_model


# --model sets every provider's model, so it applies to whichever one runs; its @endpoint
# part pins OpenRouter (@auto: no pin), and a bare slug keeps the configured pin.
def test_with_overrides_model():
    settings = Settings()
    pinned = settings.with_overrides(model="x/m@auto")
    models = (
        pinned.ollama_model,
        pinned.lmstudio_model,
        pinned.openrouter_model,
        pinned.anthropic_model,
    )
    assert models == ("x/m",) * 4
    assert pinned.openrouter_provider == ""
    assert settings.with_overrides(model="x/m").openrouter_provider == settings.openrouter_provider
    with pytest.raises(ValueError, match="slug@endpoint"):
        settings.with_overrides(model="x/m@")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"effort": "extreme"}, "--effort must be one of"),
        ({"max_tokens": "lots"}, "--max-tokens must be a whole number above 0 or default"),
        ({"max_tokens": "0"}, "--max-tokens must be a whole number above 0"),
        ({"timeout": "soon"}, "--timeout must be a number above 0 or default"),
        ({"timeout": "-5"}, "--timeout must be a number above 0"),
    ],
)
def test_with_overrides_rejects_bad_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Settings().with_overrides(**kwargs)


# A bad numeric env var raises a ValueError naming the setting and what it must be,
# instead of Python's opaque "could not convert string to float". Infinite, NaN, zero and
# negative values are refused too: an `inf` timeout would hang Espanso.
@pytest.mark.parametrize(
    ("env_name", "expected"),
    [
        ("PROMPT_TIMEOUT_SECONDS", "a number above 0"),
        ("PROMPT_PRO_TIMEOUT_SECONDS", "a number above 0"),
        ("PROMPT_TEMPERATURE", "a number of 0 or more"),
        ("OPENROUTER_MAX_TOKENS", "a whole number above 0"),
        ("ANTHROPIC_MAX_TOKENS", "a whole number above 0"),
    ],
)
@pytest.mark.parametrize("raw", ["not-a-number", "inf", "nan", "-1"])
def test_bad_numeric_env_var_raises(monkeypatch, env_name, expected, raw):
    monkeypatch.setenv(env_name, raw)
    with pytest.raises(ValueError, match=f"^{env_name} must be {expected}, got '{raw}'$"):
        Settings()


# Zero is refused where it makes no sense, and allowed for temperature.
def test_zero_values(monkeypatch):
    monkeypatch.setenv("PROMPT_TEMPERATURE", "0")
    assert Settings().temperature == 0.0
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", "0")
    with pytest.raises(ValueError, match="OPENROUTER_MAX_TOKENS must be a whole number above 0"):
        Settings()


# API keys never appear in repr(), so a traceback or a failing assertion cannot print them.
def test_repr_hides_api_keys(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-secret-value")
    text = repr(Settings())
    assert "secret-value" not in text
    assert "openrouter_model=" in text


# PROMPT_WORKFLOW_ENV names the .env to load; real env vars still win over it.
def test_load_dotenv_explicit_path_does_not_override(tmp_path, monkeypatch):
    env_file = tmp_path / "custom.env"
    env_file.write_text("PROMPT_PROVIDER=ollama\nOLLAMA_MODEL=from-file\n")
    monkeypatch.setenv("PROMPT_WORKFLOW_ENV", str(env_file))
    monkeypatch.setenv("PROMPT_PROVIDER", "anthropic")

    _load_dotenv()

    assert os.environ["OLLAMA_MODEL"] == "from-file"
    assert os.environ["PROMPT_PROVIDER"] == "anthropic"


# .env only sets the settings this package reads: a proxy or CA bundle variable there would
# change how httpx connects, so it is ignored, while real environment variables still apply.
def test_load_dotenv_exports_only_known_settings(tmp_path, monkeypatch):
    others = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "FOO")
    for name in others:
        monkeypatch.setenv(name, "x")  # so monkeypatch restores the original state afterwards
        monkeypatch.delenv(name)
    lines = [f"{name}=http://127.0.0.1:9" for name in others]
    (tmp_path / ".env").write_text("\n".join([*lines, "OLLAMA_MODEL=from-file", ""]))

    _load_dotenv()

    assert os.environ["OLLAMA_MODEL"] == "from-file"
    assert not [name for name in others if name in os.environ]


def _no_explicit_env(tmp_path, monkeypatch, project: Path, user: Path) -> None:
    monkeypatch.delenv("PROMPT_WORKFLOW_ENV")
    monkeypatch.setattr(config, "_PROJECT_ROOT", project)
    monkeypatch.setattr(config, "_user_config_dir", lambda: user)


# A .env in the current directory or its parents is never loaded: a planted file in an
# untrusted checkout must not redirect the base URL or enable ALLOW_CLOUD_OVERRIDE.
def test_load_dotenv_ignores_cwd_and_parents(tmp_path, monkeypatch):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (tmp_path / ".env").write_text("ALLOW_CLOUD_OVERRIDE=true\n")
    (nested / ".env").write_text("ALLOW_CLOUD_OVERRIDE=true\n")
    monkeypatch.chdir(nested)
    _no_explicit_env(tmp_path, monkeypatch, tmp_path / "none", tmp_path / "none")

    _load_dotenv()

    assert "ALLOW_CLOUD_OVERRIDE" not in os.environ


# The repo .env (editable install) wins over the user config dir .env.
def test_load_dotenv_prefers_project_root(tmp_path, monkeypatch):
    project, user = tmp_path / "repo", tmp_path / "cfg"
    project.mkdir()
    user.mkdir()
    (project / "pyproject.toml").write_text("")
    (project / ".env").write_text("OLLAMA_MODEL=from-repo\n")
    (user / ".env").write_text("OLLAMA_MODEL=from-user\n")
    _no_explicit_env(tmp_path, monkeypatch, project, user)

    _load_dotenv()

    assert os.environ["OLLAMA_MODEL"] == "from-repo"


# Without a project checkout (no pyproject.toml), the user config dir .env is used.
def test_load_dotenv_falls_back_to_user_config(tmp_path, monkeypatch):
    project, user = tmp_path / "site-packages", tmp_path / "cfg"
    project.mkdir()
    user.mkdir()
    (project / ".env").write_text("OLLAMA_MODEL=from-site-packages\n")
    (user / ".env").write_text("OLLAMA_MODEL=from-user\n")
    _no_explicit_env(tmp_path, monkeypatch, project, user)

    _load_dotenv()

    assert os.environ["OLLAMA_MODEL"] == "from-user"


# Comment lines, blank lines, and lines without '=' are skipped; '=' inside a
# value is preserved via partition(); surrounding quotes are stripped.
def test_load_dotenv_parses_lines(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "# a comment\n"
        "\n"
        "not a valid line\n"
        'OPENROUTER_API_KEY="sk-abc=def"\n'
        "LMSTUDIO_MODEL='local-model'\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LMSTUDIO_MODEL", raising=False)

    _load_dotenv()

    assert os.environ["OPENROUTER_API_KEY"] == "sk-abc=def"
    assert os.environ["LMSTUDIO_MODEL"] == "local-model"


# Booleans are "true" or "false" in any case.
@pytest.mark.parametrize(("raw", "expected"), [("TRUE", True), ("true", True), ("False", False)])
def test_bool_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("ALLOW_CLOUD_OVERRIDE", raw)
    monkeypatch.setenv("OLLAMA_THINK", raw)
    monkeypatch.setenv("OPENROUTER_ALLOW_FALLBACKS", raw)
    settings = Settings()
    assert settings.allow_cloud_override is expected
    assert settings.ollama_think is expected
    assert settings.openrouter_allow_fallbacks is expected


# Anything else is an error, not a silent false: OLLAMA_THINK=1 must not mean "off".
@pytest.mark.parametrize("raw", ["1", "yes", ""])
def test_bool_parsing_rejects_other_values(monkeypatch, raw):
    monkeypatch.setenv("OLLAMA_THINK", raw)
    with pytest.raises(ValueError, match=f"^OLLAMA_THINK must be true or false, got '{raw}'$"):
        Settings()


FAKE_KEY = "sk-or-v1-" + "cd" * 32


# A rejected value that may hold a secret is never repeated in the error, which the CLI
# pastes into the focused app. Short harmless values still are (see the tests above).
@pytest.mark.parametrize(
    "raw",
    [
        "2400OPENROUTER_API_KEY=" + FAKE_KEY,
        FAKE_KEY,
        "x" * 41,
    ],
    ids=["merged-line", "key", "long"],
)
def test_parse_error_never_echoes_secret_like_values(monkeypatch, raw):
    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", raw)
    with pytest.raises(ValueError, match=r"^OPENROUTER_MAX_TOKENS must be") as caught:
        Settings()
    message = str(caught.value)
    assert "sk-or-v1" not in message
    assert "cdcd" not in message
    assert "xxxx" not in message
    assert f"<redacted, {len(raw)} chars" in message


# Per-call overrides follow the same rule.
def test_override_error_never_echoes_secret_like_values():
    with pytest.raises(ValueError, match="redacted") as caught:
        Settings().with_overrides(max_tokens=FAKE_KEY)
    assert "cdcd" not in str(caught.value)


# A .env saved without the newline between two lines is refused by setting name, whatever
# the second line's name, case or spacing.
@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("OPENROUTER_MAX_TOKENS=2400", "OPENROUTER_API_KEY="),
        ("PROMPT_PERSONA=I am a tester.", "OPENROUTER_API_KEY="),
        ("PROMPT_PERSONA=I am a tester.", "OPENROUTER_API_KEY = "),
        ("PROMPT_PERSONA=I am a tester.", "openrouter_api_key="),
        ("PROMPT_PERSONA=I am a tester.", "OPENAI_API_KEY="),
    ],
    ids=["number", "persona", "spaced", "lowercase", "other-name"],
)
def test_load_rejects_merged_env_lines(tmp_path, first, second):
    (tmp_path / ".env").write_text(f"{first}{second}{FAKE_KEY}\n")
    key = first.partition("=")[0]
    with pytest.raises(ValueError, match=f"^{key} in .env runs into the next line") as caught:
        Settings.load()
    assert "cdcd" not in str(caught.value)


# The error names an unknown key only through safe_repr: a key line merged in front of it
# would otherwise be repeated.
def test_load_merged_line_error_redacts_unknown_key(tmp_path):
    (tmp_path / ".env").write_text(f"{FAKE_KEY}PROMPT_PERSONA=x OPENROUTER_MODEL=y\n")
    with pytest.raises(ValueError, match=r"^<redacted, ") as caught:
        Settings.load()
    assert "cdcd" not in str(caught.value)


# User regexes may match assignment-like text, so PROMPT_EXTRA_PATTERNS is not checked.
def test_load_allows_assignments_in_extra_patterns(tmp_path):
    (tmp_path / ".env").write_text("PROMPT_EXTRA_PATTERNS=OPENROUTER_API_KEY=\\S+;DB_PASS\n")
    assert Settings.load().extra_patterns == "OPENROUTER_API_KEY=\\S+;DB_PASS"


# A space pasted along with an API key is dropped.
def test_api_keys_are_stripped(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", f" {FAKE_KEY} ")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-key\t")
    settings = Settings()
    assert (settings.openrouter_api_key, settings.anthropic_api_key) == (FAKE_KEY, "ant-key")


# An empty key in .env (as in .env.example) counts as unset for the provider check.
def test_empty_api_key_is_kept_as_empty(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    assert not Settings().openrouter_api_key


# Shell-style `export KEY=value` lines are accepted, since .env files are often sourced too.
def test_load_dotenv_accepts_export_prefix(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("export OLLAMA_MODEL=exported\n")
    monkeypatch.chdir(tmp_path)
    _load_dotenv()
    assert os.environ["OLLAMA_MODEL"] == "exported"


# Only a matching pair of surrounding quotes is removed; inner or lone quotes survive.
def test_load_dotenv_quote_handling(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        'OLLAMA_MODEL="it\'s"\nLMSTUDIO_MODEL=\'a"\nPROMPT_PROFILE="general"  \n'
    )
    monkeypatch.chdir(tmp_path)
    _load_dotenv()
    assert os.environ["OLLAMA_MODEL"] == "it's"
    assert os.environ["LMSTUDIO_MODEL"] == "'a\""
    assert os.environ["PROMPT_PROFILE"] == "general"


# An inline ` # comment` after an unquoted value is not part of the value; a quoted value
# keeps its '#', and a '#' without whitespace before it is data.
def test_load_dotenv_strips_inline_comments(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "OPENROUTER_PROVIDER=openai  # pinned for the pro tier\n"
        'PROMPT_PERSONA="I am #1 here" # note\n'
        "LMSTUDIO_MODEL=model#v2\n"
        "OLLAMA_MODEL=#\n"
    )
    monkeypatch.chdir(tmp_path)
    _load_dotenv()
    assert os.environ["OPENROUTER_PROVIDER"] == "openai"
    assert os.environ["PROMPT_PERSONA"] == "I am #1 here"
    assert os.environ["LMSTUDIO_MODEL"] == "model#v2"
    assert os.environ["OLLAMA_MODEL"] == "#"


# PROMPT_PERSONA is empty unless configured.
def test_persona_default_and_override(monkeypatch):
    assert Settings().persona == ""
    monkeypatch.setenv("PROMPT_PERSONA", "I am a tester.")
    assert Settings().persona == "I am a tester."
