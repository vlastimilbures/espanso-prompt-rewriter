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


# for_tier("pro") swaps in the pro model, endpoint, effort and timeout; standard is a no-op.
def test_for_tier(monkeypatch):
    monkeypatch.setenv("OPENROUTER_PRO_MODEL", "x/pro")
    settings = Settings()
    assert settings.for_tier("standard") is settings
    pro = settings.for_tier("pro")
    assert (pro.openrouter_model, pro.openrouter_provider) == ("x/pro", "openai")
    assert (pro.openrouter_reasoning_effort, pro.timeout) == ("low", 60.0)
    assert pro.persona == settings.persona
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
    custom = settings.with_overrides(endpoint="", effort="high", max_tokens="8000", timeout="120")
    assert custom.openrouter_provider == ""
    assert custom.openrouter_reasoning_effort == "high"
    assert (custom.openrouter_max_tokens, custom.anthropic_max_tokens) == (8000, 8000)
    assert custom.timeout == 120.0
    assert custom.openrouter_model == settings.openrouter_model


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"effort": "extreme"}, "--effort must be one of"),
        ({"max_tokens": "lots"}, "--max-tokens must be a number"),
        ({"timeout": "soon"}, "--timeout must be a number"),
    ],
)
def test_with_overrides_rejects_bad_values(kwargs, message):
    with pytest.raises(ValueError, match=message):
        Settings().with_overrides(**kwargs)


# A non-numeric timeout/temperature/max-tokens env var raises a clear ValueError
# instead of the CLI leaking Python's opaque "could not convert string to float" message.
@pytest.mark.parametrize(
    "env_name,attr",
    [
        ("PROMPT_TIMEOUT_SECONDS", "timeout"),
        ("PROMPT_PRO_TIMEOUT_SECONDS", "pro_timeout"),
        ("PROMPT_TEMPERATURE", "temperature"),
        ("OPENROUTER_MAX_TOKENS", "openrouter_max_tokens"),
        ("ANTHROPIC_MAX_TOKENS", "anthropic_max_tokens"),
    ],
)
def test_bad_numeric_env_var_raises(monkeypatch, env_name, attr):
    monkeypatch.setenv(env_name, "not-a-number")
    with pytest.raises(ValueError, match=env_name):
        getattr(Settings(), attr)


# PROMPT_WORKFLOW_ENV names the .env to load; real env vars still win over it.
def test_load_dotenv_explicit_path_does_not_override(tmp_path, monkeypatch):
    env_file = tmp_path / "custom.env"
    env_file.write_text("PROMPT_PROVIDER=ollama\nOLLAMA_MODEL=from-file\n")
    monkeypatch.setenv("PROMPT_WORKFLOW_ENV", str(env_file))
    monkeypatch.setenv("PROMPT_PROVIDER", "anthropic")

    _load_dotenv()

    assert os.environ["OLLAMA_MODEL"] == "from-file"
    assert os.environ["PROMPT_PROVIDER"] == "anthropic"


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


# Booleans are case-insensitive "true"; anything else (including empty) is false.
@pytest.mark.parametrize(
    "raw,expected", [("TRUE", True), ("true", True), ("false", False), ("", False), ("yes", False)]
)
def test_bool_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("ALLOW_CLOUD_OVERRIDE", raw)
    monkeypatch.setenv("OLLAMA_THINK", raw)
    monkeypatch.setenv("OPENROUTER_ALLOW_FALLBACKS", raw)
    settings = Settings()
    assert settings.allow_cloud_override is expected
    assert settings.ollama_think is expected
    assert settings.openrouter_allow_fallbacks is expected


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


# PROMPT_PERSONA is empty unless configured.
def test_persona_default_and_override(monkeypatch):
    assert Settings().persona == ""
    monkeypatch.setenv("PROMPT_PERSONA", "I am a tester.")
    assert Settings().persona == "I am a tester."
