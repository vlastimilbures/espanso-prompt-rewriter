import pytest
from typer.testing import CliRunner

from prompt_workflow.cli import _read_input, app
from prompt_workflow.providers.base import ProviderError

runner = CliRunner()


# _read_input reads from the requested source, and rejects unknown sources.
def test_read_input_stdin_and_argument():
    assert _read_input("argument", "hello") == "hello"


def test_read_input_invalid_source():
    with pytest.raises(ProviderError, match="source must be"):
        _read_input("bogus", None)


# _read_input surfaces a pyperclip failure as a ProviderError (regression test for 1.1).
def test_read_input_clipboard_unavailable(monkeypatch):
    import pyperclip

    import prompt_workflow.cli as mod

    def _boom():
        raise pyperclip.PyperclipException("no clipboard mechanism")

    monkeypatch.setattr(mod.pyperclip, "paste", _boom)
    with pytest.raises(ProviderError, match="Clipboard unavailable"):
        _read_input("clipboard", None)


# A clipboard failure at the CLI level surfaces inline instead of a traceback.
def test_cli_clipboard_unavailable_reports_inline(monkeypatch):
    import pyperclip

    import prompt_workflow.cli as mod

    def _boom():
        raise pyperclip.PyperclipException("no clipboard mechanism")

    monkeypatch.setattr(mod.pyperclip, "paste", _boom)
    result = runner.invoke(app, ["improve", "--provider", "ollama", "--source", "clipboard"])
    assert "[prompt-workflow: Clipboard unavailable" in result.stdout
    assert result.exit_code == 0


# OpenRouter call is blocked when the draft matches the redaction gate.
def test_cloud_blocked_on_sensitive_content(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("ALLOW_CLOUD_OVERRIDE", raising=False)
    result = runner.invoke(
        app,
        [
            "improve",
            "--provider",
            "openrouter",
            "--source",
            "argument",
            "--text",
            "customer data 4111 1111 1111 1111",
        ],
    )
    assert "Blocked cloud call" in result.stdout


# Anthropic call is also blocked when the draft matches the redaction gate.
def test_anthropic_cloud_blocked_on_sensitive_content(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("ALLOW_CLOUD_OVERRIDE", raising=False)
    result = runner.invoke(
        app,
        [
            "improve",
            "--provider",
            "anthropic",
            "--source",
            "argument",
            "--text",
            "customer data 4111 1111 1111 1111",
        ],
    )
    assert "Blocked cloud call" in result.stdout


# ALLOW_CLOUD_OVERRIDE=true lets sensitive content through to the cloud provider.
def test_anthropic_cloud_override_allows_sensitive_content(monkeypatch):
    import prompt_workflow.cli as mod

    class _Stub:
        def generate(self, prompt, system_prompt, model=None):
            return "improved"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ALLOW_CLOUD_OVERRIDE", "true")
    monkeypatch.setattr(mod, "make_provider", lambda name, cfg: _Stub())
    result = runner.invoke(
        app,
        [
            "improve",
            "--provider",
            "anthropic",
            "--source",
            "argument",
            "--text",
            "customer data 4111 1111 1111 1111",
        ],
    )
    assert result.stdout == "improved"


# Blank input surfaces as an inline message instead of a blank expansion.
def test_empty_input_reports_inline():
    result = runner.invoke(
        app, ["improve", "--provider", "ollama", "--source", "argument", "--text", "   "]
    )
    assert "Input is empty" in result.stdout


# CLI output has no trailing newline, since Espanso inserts stdout verbatim.
def test_output_has_no_trailing_newline(monkeypatch):
    import prompt_workflow.cli as mod

    class _Stub:
        def generate(self, prompt, system_prompt, model=None):
            return "clean output"

    monkeypatch.setattr(mod, "make_provider", lambda name, cfg: _Stub())
    result = runner.invoke(
        app, ["improve", "--provider", "ollama", "--source", "argument", "--text", "draft"]
    )
    assert result.stdout == "clean output"


class _Stub:
    def generate(self, prompt, system_prompt, model=None):
        return "improved"


# An unknown --provider still yields an inline marker with exit 0, not a Typer usage
# error on stderr (why the options are plain strings rather than Enum choices).
def test_unknown_provider_reports_inline():
    result = runner.invoke(
        app, ["improve", "--provider", "bogus", "--source", "argument", "--text", "draft"]
    )
    assert result.exit_code == 0
    assert result.stdout.startswith("[prompt-workflow: Unknown provider 'bogus'")


# An unknown profile is a ValueError, reported inline without the "unexpected" prefix.
def test_unknown_profile_reports_inline(monkeypatch):
    import prompt_workflow.cli as mod

    monkeypatch.setattr(mod, "make_provider", lambda name, cfg: _Stub())
    result = runner.invoke(
        app, ["improve", "--profile", "nope", "--source", "argument", "--text", "draft"]
    )
    assert result.exit_code == 0
    assert result.stdout.startswith("[prompt-workflow: Unknown profile: nope")


# Any other exception is still caught and marked as unexpected, never a traceback.
def test_unexpected_error_reports_inline(monkeypatch):
    import prompt_workflow.cli as mod

    class _Boom:
        def generate(self, prompt, system_prompt, model=None):
            raise KeyError("kaboom")

    monkeypatch.setattr(mod, "make_provider", lambda name, cfg: _Boom())
    result = runner.invoke(app, ["improve", "--source", "argument", "--text", "draft"])
    assert result.exit_code == 0
    assert result.stdout == "[prompt-workflow: unexpected error: 'kaboom']"


# --copy writes the result to the clipboard and still prints it.
def test_copy_writes_clipboard(monkeypatch):
    import prompt_workflow.cli as mod

    copied = []
    monkeypatch.setattr(mod, "make_provider", lambda name, cfg: _Stub())
    monkeypatch.setattr(mod.pyperclip, "copy", copied.append)
    result = runner.invoke(app, ["improve", "--copy", "--source", "argument", "--text", "draft"])
    assert result.stdout == "improved"
    assert copied == ["improved"]


# A clipboard failure on --copy surfaces inline instead of a traceback.
def test_copy_clipboard_unavailable_reports_inline(monkeypatch):
    import pyperclip

    import prompt_workflow.cli as mod

    def _boom(text):
        raise pyperclip.PyperclipException("no clipboard mechanism")

    monkeypatch.setattr(mod, "make_provider", lambda name, cfg: _Stub())
    monkeypatch.setattr(mod.pyperclip, "copy", _boom)
    result = runner.invoke(app, ["improve", "--copy", "--source", "argument", "--text", "draft"])
    assert result.stdout.startswith("[prompt-workflow: Clipboard unavailable")


# --source stdin reads the draft from standard input.
def test_stdin_source(monkeypatch):
    import prompt_workflow.cli as mod

    seen = []

    class _Echo:
        def generate(self, prompt, system_prompt, model=None):
            seen.append(prompt)
            return "ok"

    monkeypatch.setattr(mod, "make_provider", lambda name, cfg: _Echo())
    result = runner.invoke(app, ["improve", "--source", "stdin"], input="from stdin")
    assert result.stdout == "ok"
    assert seen == ["from stdin"]


# improve builds the system prompt with the configured persona.
def test_improve_passes_persona(monkeypatch):
    import prompt_workflow.cli as mod

    seen = []

    class _Capture:
        def generate(self, prompt, system_prompt, model=None):
            seen.append(system_prompt)
            return "ok"

    monkeypatch.setenv("PROMPT_PERSONA", "I am a tester.")
    monkeypatch.setattr(mod, "make_provider", lambda name, cfg: _Capture())
    runner.invoke(app, ["improve", "--profile", "default", "--source", "argument", "--text", "d"])
    assert 'open with "I am a tester."' in seen[0]


# --tier pro hands make_provider the OPENROUTER_PRO_* settings.
def test_improve_pro_tier(monkeypatch):
    import prompt_workflow.cli as mod

    seen = []

    class _Echo:
        def generate(self, prompt, system_prompt, model=None):
            return "ok"

    def fake(name, cfg):
        seen.append(cfg)
        return _Echo()

    monkeypatch.setattr(mod, "make_provider", fake)
    args = ["improve", "--tier", "pro", "--source", "argument", "--text", "d"]
    assert runner.invoke(app, args).stdout == "ok"
    assert seen[0].openrouter_model == seen[0].openrouter_pro_model
    assert seen[0].openrouter_reasoning_effort == seen[0].openrouter_pro_reasoning_effort


# The -if- popup's options reach make_provider as settings, and the model slug
# (without its @endpoint) reaches generate().
def test_improve_per_call_overrides(monkeypatch):
    import prompt_workflow.cli as mod

    seen = []

    class _Echo:
        def generate(self, prompt, system_prompt, model=None):
            seen.append(model)
            return "ok"

    def fake(name, cfg):
        seen.append(cfg)
        return _Echo()

    monkeypatch.setattr(mod, "make_provider", fake)
    args = [
        "improve", "--tier", "pro", "--model", "x/m@auto", "--effort", "high",
        "--max-tokens", "8000", "--timeout", "default", "--source", "argument", "--text", "d",
    ]  # fmt: skip
    assert runner.invoke(app, args).stdout == "ok"
    cfg, model = seen
    assert model == "x/m"
    assert (cfg.openrouter_provider, cfg.openrouter_reasoning_effort) == ("", "high")
    assert cfg.openrouter_max_tokens == 8000
    assert cfg.timeout == cfg.pro_timeout


# A bad popup value is reported inline with exit code 0, like any other bad option.
def test_improve_bad_effort_reports_inline():
    args = ["improve", "--effort", "extreme", "--source", "argument", "--text", "d"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert result.stdout.startswith("[prompt-workflow: --effort must be one of")


# An unknown tier is reported inline, like any other bad option, with exit code 0.
def test_improve_unknown_tier():
    args = ["improve", "--tier", "ultra", "--source", "argument", "--text", "d"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert result.stdout.startswith("[prompt-workflow: Unknown tier: ultra")


# `persona` prints PROMPT_PERSONA for the -p- snippet, with no trailing newline.
def test_persona_command_prints_persona(monkeypatch):
    monkeypatch.setenv("PROMPT_PERSONA", "I am a tester.")
    result = runner.invoke(app, ["persona"])
    assert result.exit_code == 0
    assert result.stdout == "I am a tester."


# Unset persona prints a fill-in placeholder instead of an empty expansion.
def test_persona_command_placeholder_when_unset():
    result = runner.invoke(app, ["persona"])
    assert result.stdout == "I am working as [role] in [company]."


# A broken config still yields the placeholder, never a traceback in the snippet.
def test_persona_command_placeholder_on_config_error(monkeypatch):
    monkeypatch.setenv("PROMPT_TIMEOUT_SECONDS", "not-a-number")
    result = runner.invoke(app, ["persona"])
    assert result.exit_code == 0
    assert result.stdout == "I am working as [role] in [company]."
