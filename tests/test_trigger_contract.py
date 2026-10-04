"""Freezes what every Espanso trigger depends on: the exact bytes improve and persona print,
and what running them imports. Espanso pastes stdout verbatim and starts a fresh process for
each trigger, so a changed byte reaches the user and a heavy import slows every expansion.
Parser (usage) errors exit 2 and are not part of this contract."""

import json
import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from prompt_workflow.cli import app
from prompt_workflow.config import env_names
from prompt_workflow.providers.base import ProviderError
from prompt_workflow.providers.openai_compatible import OpenAICompatibleProvider

runner = CliRunner()

PLACEHOLDER = b"I am working as [role] in [company]."


def _golden(args: list[str], expected: bytes) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert result.stdout_bytes == expected
    assert not result.stdout_bytes.endswith(b"\n")


IMPROVE = ["improve", "--provider", "ollama", "--source", "argument", "--text", "a draft"]


# The rewrite is pasted as-is, UTF-8, cleaned of control characters, with no newline added.
def test_improve_success(stub_provider):
    stub_provider.result = "Přepiš — done\x1b[201~ ✓"
    _golden(IMPROVE, "Přepiš — done[201~ ✓".encode())


# Each failure class becomes one marker; only an unexpected error gets the prefix.
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ProviderError("Ollama request failed"), b"[prompt-workflow: Ollama request failed]"),
        (
            ValueError("OLLAMA_TIMEOUT is not a number"),
            b"[prompt-workflow: OLLAMA_TIMEOUT is not a number]",
        ),
        (RuntimeError("kaput"), b"[prompt-workflow: unexpected error: kaput]"),
    ],
    ids=["provider-error", "value-error", "unexpected"],
)
def test_improve_error_marker(stub_provider, exc, expected):
    stub_provider.exc = exc
    _golden(IMPROVE, expected)


# A bad setting fails before any provider is built, with the same marker shape.
def test_improve_settings_error_marker(monkeypatch, stub_provider):
    monkeypatch.setenv("PROMPT_LOCAL_ONLY", "maybe")
    result = runner.invoke(app, IMPROVE)
    assert result.exit_code == 0
    assert result.stdout_bytes.startswith(b"[prompt-workflow: PROMPT_LOCAL_ONLY ")
    assert result.stdout_bytes.endswith(b"]")
    assert not stub_provider.built


# --allow-flagged opens the paste with the note, on success and when the call fails after
# the gate let the draft through.
DRAFT = "CONFIDENTIAL: summarise the board minutes for jane@example.com"
NOTE = b"[prompt-workflow: sent despite: email, confidential_label]\n\n"


FLAGGED = ["improve", "--provider", "openrouter", "--allow-flagged", "--source", "argument"]


def test_improve_sent_despite_note(monkeypatch, fake_http):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake_http.reply({"choices": [{"message": {"content": "rewrite"}}]})
    _golden([*FLAGGED, "--text", DRAFT], NOTE + b"rewrite")


def test_improve_sent_despite_note_with_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def fail(self, prompt, system_prompt):
        raise ProviderError("upstream down")

    monkeypatch.setattr(OpenAICompatibleProvider, "generate", fail)
    _golden([*FLAGGED, "--text", DRAFT], NOTE + b"[prompt-workflow: upstream down]")


def test_persona_set(monkeypatch):
    monkeypatch.setenv("PROMPT_PERSONA", "I am a risk analyst at Česká banka.")
    _golden(["persona"], "I am a risk analyst at Česká banka.".encode())


def test_persona_unset_placeholder():
    _golden(["persona"], PLACEHOLDER)


# Settings that fail to load still give the placeholder, never a marker or a blank.
def test_persona_placeholder_when_settings_fail(monkeypatch):
    monkeypatch.setenv("PROMPT_PERSONA", "never shown")
    monkeypatch.setenv("PROMPT_LOCAL_ONLY", "maybe")
    _golden(["persona"], PLACEHOLDER)


# Runs the trigger commands in a fresh interpreter, the way Espanso does, with the real
# provider and gate over httpx.MockTransport, then lists every module they imported.
_TRIGGER_RUN = """
import sys
started = set(sys.modules)
import json
from pathlib import Path

import httpx

real_client = httpx.Client


def client(*args, **kwargs):
    reply = {"message": {"content": "improved"}, "done_reason": "stop"}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=reply))
    return real_client(*args, **{**kwargs, "transport": transport})


httpx.Client = client
from prompt_workflow.cli import app

improve = ["improve", "--provider", "ollama", "--source", "argument", "--text", "x"]
codes = []
for argv in (improve, ["persona"]):
    try:
        app(argv)
    except SystemExit as exc:
        codes.append(exc.code)
    sys.stdout.write("|")
    sys.stdout.flush()
added = sorted(set(sys.modules) - started)
Path(sys.argv[1]).write_text(json.dumps({"codes": codes, "added": added}), encoding="utf-8")
"""

# Never on the trigger path: the Textual interface (#93), config writers (#84), the keyring
# (#88) and history (sqlite3, until #89 lets it in). rich is installed with Typer but not
# loaded today: Typer imports it only for help and usage errors.
FORBIDDEN = ("textual", "rich.console", "sqlite3", "_sqlite3", "tomli_w", "tomlkit", "keyring")

# Modules the trigger run adds to a bare interpreter: about 290 on macOS, Python 3.12 and 3.14
# (see "Trigger start-up budget" in CONTRIBUTING.md). A coarse ceiling, not a timing: a new
# dependency tree on the trigger path crosses it; ordinary growth does not.
MODULE_CEILING = 400


@pytest.fixture(scope="module")
def trigger_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("trigger")
    (tmp / ".env").write_text("", encoding="utf-8")
    # Module-scoped, so conftest's per-test isolation has not run yet: drop every setting here.
    env = {key: value for key, value in os.environ.items() if key not in env_names()}
    env["PROMPT_WORKFLOW_ENV"] = str(tmp / ".env")
    out = tmp / "modules.json"
    proc = subprocess.run(
        [sys.executable, "-c", _TRIGGER_RUN, str(out)],
        capture_output=True,
        env=env,
        cwd=tmp,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout, json.loads(out.read_text(encoding="utf-8"))


# The real process prints the same bytes as the in-process runner and exits 0 for both.
def test_trigger_run_output(trigger_run):
    stdout, data = trigger_run
    assert stdout == b"improved|" + PLACEHOLDER + b"|"
    assert data["codes"] == [0, 0]


@pytest.mark.parametrize("module", FORBIDDEN)
def test_trigger_path_does_not_import(trigger_run, module):
    _, data = trigger_run
    assert not [m for m in data["added"] if m == module or m.startswith(module + ".")]


def test_trigger_path_module_count(trigger_run):
    _, data = trigger_run
    assert len(data["added"]) <= MODULE_CEILING, len(data["added"])
