import os
import subprocess
import sys

import pyperclip
import pytest
from typer.testing import CliRunner

import prompt_workflow.cli as cli
from prompt_workflow.cli import _read_input, app
from prompt_workflow.prompt_builder import system_prompt
from prompt_workflow.providers.base import ProviderError

runner = CliRunner()


def improve(*args, input=None):
    return runner.invoke(app, ["improve", *args], input=input)


def _no_clipboard(*args):
    raise pyperclip.PyperclipException("no clipboard mechanism")


# _read_input reads from the requested source, and rejects unknown sources.
def test_read_input_argument():
    assert _read_input("argument", "hello") == "hello"


def test_read_input_invalid_source():
    with pytest.raises(ProviderError, match="source must be"):
        _read_input("bogus", None)


# _read_input surfaces a pyperclip failure as a ProviderError.
def test_read_input_clipboard_unavailable(monkeypatch):
    monkeypatch.setattr(cli.pyperclip, "paste", _no_clipboard)
    with pytest.raises(ProviderError, match="Clipboard unavailable"):
        _read_input("clipboard", None)


# A clipboard failure at the CLI level surfaces inline instead of a traceback.
def test_cli_clipboard_unavailable_reports_inline(monkeypatch):
    monkeypatch.setattr(cli.pyperclip, "paste", _no_clipboard)
    result = improve("--provider", "ollama", "--source", "clipboard")
    assert "[prompt-workflow: Clipboard unavailable" in result.stdout
    assert result.exit_code == 0


# Each cloud call is blocked when the draft matches the redaction gate.
@pytest.mark.parametrize("provider", ["openrouter", "anthropic"])
def test_cloud_blocked_on_sensitive_content(monkeypatch, provider):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    draft = "customer data 4111 1111 1111 1111"
    result = improve("--provider", provider, "--source", "argument", "--text", draft)
    assert "Blocked cloud call" in result.stdout


# ALLOW_CLOUD_OVERRIDE=true lets sensitive content through to the cloud provider.
def test_anthropic_cloud_override_allows_sensitive_content(monkeypatch, stub_provider):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ALLOW_CLOUD_OVERRIDE", "true")
    draft = "customer data 4111 1111 1111 1111"
    result = improve("--provider", "anthropic", "--source", "argument", "--text", draft)
    assert result.stdout == "improved"


# Blank input surfaces as an inline message instead of a blank expansion.
def test_empty_input_reports_inline():
    result = improve("--provider", "ollama", "--source", "argument", "--text", "   ")
    assert "Input is empty" in result.stdout


# An oversized draft (an accidental copy of a log or document) is refused inline.
def test_too_long_input_reports_inline():
    result = improve("--provider", "ollama", "--source", "stdin", input="x" * 50_001)
    assert result.exit_code == 0
    assert result.stdout == "[prompt-workflow: Input is too long (50001 chars, max 50000)]"


# CLI output has no trailing newline, since Espanso inserts stdout verbatim.
def test_output_has_no_trailing_newline(stub_provider):
    stub_provider.result = "clean output"
    result = improve("--provider", "ollama", "--source", "argument", "--text", "draft")
    assert result.stdout == "clean output"


# An unknown --provider still yields an inline marker with exit 0, not a Typer usage
# error on stderr (why the options are plain strings rather than Enum choices).
def test_unknown_provider_reports_inline():
    result = improve("--provider", "bogus", "--source", "argument", "--text", "draft")
    assert result.exit_code == 0
    assert result.stdout.startswith("[prompt-workflow: Unknown provider 'bogus'")


# An unknown profile is a ValueError, reported inline without the "unexpected" prefix.
def test_unknown_profile_reports_inline(stub_provider):
    result = improve("--profile", "nope", "--source", "argument", "--text", "draft")
    assert result.exit_code == 0
    assert result.stdout.startswith("[prompt-workflow: Unknown profile: 'nope'")


FAKE_KEY = "sk-or-v1-" + "cd" * 32


# A provider or profile name that is really two .env lines run together is reported without
# repeating the value.
@pytest.mark.parametrize("setting", ["PROMPT_PROVIDER", "PROMPT_PROFILE"])
def test_bad_name_error_never_echoes_key(monkeypatch, fake_http, setting):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv(setting, f"openrouterOPENROUTER_API_KEY={FAKE_KEY}")
    result = improve("--source", "argument", "--text", "draft")
    assert result.exit_code == 0
    assert result.stdout.startswith("[prompt-workflow: Unknown pro")
    assert "<redacted," in result.stdout
    assert "cdcd" not in result.stdout
    assert fake_http.calls == []


# Two .env lines run together into the persona are an inline error for improve, and the -p-
# snippet falls back to its placeholder.
def test_merged_env_line_is_reported_not_pasted(tmp_path):
    (tmp_path / ".env").write_text(f"PROMPT_PERSONA=I am a tester.OPENROUTER_API_KEY={FAKE_KEY}\n")
    result = improve("--source", "argument", "--text", "draft")
    assert result.stdout == (
        "[prompt-workflow: PROMPT_PERSONA in .env runs into the next line; add the missing newline]"
    )
    persona = runner.invoke(app, ["persona"])
    assert persona.stdout == "I am working as [role] in [company]."


# With PROMPT_LOCAL_ONLY=true each cloud trigger's command pastes a marker and makes no
# request at all, whatever --provider it names; a local trigger still runs.
@pytest.mark.parametrize(
    "args",
    [
        ["--provider", "openrouter"],
        ["--provider", "openrouter", "--tier", "pro"],
        [
            "--provider",
            "openrouter",
            "--tier",
            "pro",
            "--model",
            "google/gemini-3.5-flash-lite@google-ai-studio/flex",
            "--effort",
            "default",
            "--max-tokens",
            "default",
            "--timeout",
            "default",
        ],
        ["--provider", "anthropic", "--profile", "general"],
    ],
    ids=["-i-", "-ip-", "-if-", "-ic-"],
)
def test_local_only_blocks_cloud_triggers(monkeypatch, mock_transport, args):
    monkeypatch.setenv("PROMPT_LOCAL_ONLY", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    result = improve(*args, "--source", "argument", "--text", "draft")
    assert result.exit_code == 0
    assert result.stdout.startswith("[prompt-workflow: PROMPT_LOCAL_ONLY=true: ")
    assert mock_transport.requests == []


# A mistyped PROMPT_LOCAL_ONLY fails closed: an inline error, no request.
def test_local_only_bad_value_fails_closed(monkeypatch, mock_transport):
    monkeypatch.setenv("PROMPT_LOCAL_ONLY", "yes")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    result = improve("--provider", "openrouter", "--source", "argument", "--text", "d")
    assert result.stdout.startswith("[prompt-workflow: PROMPT_LOCAL_ONLY must be true or false")
    assert mock_transport.requests == []


def test_local_only_allows_local_trigger(monkeypatch, mock_transport):
    monkeypatch.setenv("PROMPT_LOCAL_ONLY", "true")
    mock_transport.reply = {"message": {"content": "improved"}}
    result = improve(
        "--provider", "ollama", "--profile", "general", "--source", "argument", "--text", "d"
    )
    assert result.stdout == "improved"
    assert [str(r.url) for r in mock_transport.requests] == ["http://localhost:11434/api/chat"]


# Any other exception is still caught and marked as unexpected, never a traceback.
def test_unexpected_error_reports_inline(stub_provider):
    stub_provider.exc = KeyError("kaboom")
    result = improve("--source", "argument", "--text", "draft")
    assert result.exit_code == 0
    assert result.stdout == "[prompt-workflow: unexpected error: 'kaboom']"


# --copy writes the result to the clipboard and still prints it.
def test_copy_writes_clipboard(monkeypatch, stub_provider):
    copied = []
    monkeypatch.setattr(cli.pyperclip, "copy", copied.append)
    result = improve("--copy", "--source", "argument", "--text", "draft")
    assert result.stdout == "improved"
    assert copied == ["improved"]


# A clipboard failure on --copy surfaces inline instead of a traceback.
def test_copy_clipboard_unavailable_reports_inline(monkeypatch, stub_provider):
    monkeypatch.setattr(cli.pyperclip, "copy", _no_clipboard)
    result = improve("--copy", "--source", "argument", "--text", "draft")
    assert result.stdout.startswith("[prompt-workflow: Clipboard unavailable")


# --source stdin reads the draft from standard input.
def test_stdin_source(stub_provider):
    result = improve("--source", "stdin", input="from stdin")
    assert result.stdout == "improved"
    assert stub_provider.calls[0]["prompt"] == "from stdin"


# improve builds the system prompt with the configured persona.
def test_improve_passes_persona(monkeypatch, stub_provider):
    monkeypatch.setenv("PROMPT_PERSONA", "I am a tester.")
    improve("--profile", "default", "--source", "argument", "--text", "d")
    assert 'open with "I am a tester."' in stub_provider.calls[0]["system_prompt"]


# --tier pro hands make_provider the OPENROUTER_PRO_* settings and sends PROMPT_PRO_PROFILE.
def test_improve_pro_tier(stub_provider):
    assert improve("--tier", "pro", "--source", "argument", "--text", "d").stdout == "improved"
    _, cfg = stub_provider.built[0]
    assert cfg.openrouter_model == cfg.openrouter_pro_model
    assert cfg.openrouter_reasoning_effort == cfg.openrouter_pro_reasoning_effort
    assert stub_provider.calls[0]["system_prompt"] == system_prompt("default-pro")


# The -if- popup's options reach make_provider as settings: the model slug without its
# @endpoint, the endpoint as the pin (@auto: none), effort, max tokens and timeout.
def test_improve_per_call_overrides(stub_provider):
    args = [
        "--tier", "pro", "--model", "x/m@auto", "--effort", "high",
        "--max-tokens", "8000", "--timeout", "default", "--source", "argument", "--text", "d",
    ]  # fmt: skip
    assert improve(*args).stdout == "improved"
    _, cfg = stub_provider.built[0]
    assert cfg.openrouter_model == "x/m"
    assert (cfg.openrouter_provider, cfg.openrouter_reasoning_effort) == ("", "high")
    assert cfg.openrouter_max_tokens == 8000
    assert cfg.timeout == cfg.pro_timeout


# A bad popup value is reported inline with exit code 0, like any other bad option.
def test_improve_bad_effort_reports_inline():
    result = improve("--effort", "extreme", "--source", "argument", "--text", "d")
    assert result.exit_code == 0
    assert result.stdout.startswith("[prompt-workflow: --effort must be one of")


# An unknown tier is reported inline, like any other bad option, with exit code 0.
def test_improve_unknown_tier():
    result = improve("--tier", "ultra", "--source", "argument", "--text", "d")
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


# --model applies to whichever provider runs, not only OpenRouter.
def test_model_override_reaches_local_provider(stub_provider):
    improve("--provider", "ollama", "--model", "llama4:8b", "--source", "argument", "--text", "d")
    name, cfg = stub_provider.built[0]
    assert (name, cfg.ollama_model) == ("ollama", "llama4:8b")


# Characters that could hijack the app Espanso types into (an escape sequence ending a
# terminal's bracketed paste, bidi overrides, invisible tag characters) never reach stdout.
# Tabs and newlines survive.
def test_output_strips_unsafe_characters(stub_provider):
    stub_provider.result = "a\x1b[201~b\u202ec\U000e0041d\r\n\te\x07"
    result = improve("--source", "argument", "--text", "draft")
    assert result.stdout == "a[201~bcd\n\te"


# The same characters are stripped from the draft before the gate and the model see it.
def test_draft_strips_unsafe_characters(stub_provider):
    improve("--source", "argument", "--text", "sum\x1bmarize \U000e0049\U000e0047this")
    assert stub_provider.calls[0]["prompt"] == "summarize this"


# Error markers go through the same sink.
def test_error_marker_strips_unsafe_characters(stub_provider):
    stub_provider.exc = ProviderError("bad\x1b[0m thing")
    assert (
        improve("--source", "argument", "--text", "d").stdout == "[prompt-workflow: bad[0m thing]"
    )


# Output is UTF-8 whatever the locale: on Windows a piped stdout defaults to the ANSI code
# page, where printing a Czech or Vietnamese rewrite used to crash into a blank expansion.
def test_output_is_utf8_under_legacy_code_page(tmp_path):
    persona = "Jsem ř — người dùng"
    env = {
        **os.environ,
        "PYTHONIOENCODING": "cp1252",
        "PROMPT_PERSONA": persona,
        "PROMPT_WORKFLOW_ENV": str(tmp_path / ".env"),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "prompt_workflow.cli", "persona"],
        capture_output=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.decode("utf-8") == persona
