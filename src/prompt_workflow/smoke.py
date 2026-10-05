"""The set-up smoke test: run a real `improve` against a stub provider on 127.0.0.1, so the
settings, the provider, the gate, the HTTP call and the output path are all exercised without
a paid call or a key leaving the machine.

A child process runs `python -m prompt_workflow.cli improve --provider <name> ...` (the same
code a trigger runs) with every provider base URL pointed at the stub and a placeholder key
in place of the real one, set as environment variables, which outrank every settings file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# What the stub answers, and so what improve must print.
REPLY = "prompt-workflow setup check: ok"
DRAFT = "Check that prompt-workflow can rewrite a draft end to end."
# Sent instead of the real key, which never reaches even the stub.
PLACEHOLDER_KEY = "setup-check-placeholder"
TIMEOUT = 60

# Runs argv with env; returns (exit code, stdout). Tests may replace it.
Runner = Callable[[Sequence[str], Mapping[str, str]], tuple[int, bytes]]


def run_cli(argv: Sequence[str], env: Mapping[str, str]) -> tuple[int, bytes]:
    proc = subprocess.run(  # noqa: S603 - our own CLI, fixed argv, no shell
        list(argv), env=dict(env), capture_output=True, timeout=TIMEOUT, check=False
    )
    return proc.returncode, proc.stdout


def _reply(path: str) -> dict[str, Any]:
    if path.endswith("/api/chat"):  # Ollama
        return {"message": {"content": REPLY}, "done_reason": "stop"}
    if path.endswith("/v1/messages"):  # Anthropic
        return {"content": [{"type": "text", "text": REPLY}], "stop_reason": "end_turn"}
    # OpenAI-compatible: OpenRouter, LM Studio.
    return {"choices": [{"message": {"content": REPLY}, "finish_reason": "stop"}]}


class _Handler(BaseHTTPRequestHandler):
    server: _StubServer

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)  # never kept: only the path is recorded
        self.server.paths.append(self.path)
        body = json.dumps(_reply(self.path)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class _StubServer(ThreadingHTTPServer):
    paths: list[str]


@dataclass(frozen=True)
class SmokeResult:
    ok: bool
    output: str
    requests: int
    message: str


def stub_env(port: int, environ: Mapping[str, str]) -> dict[str, str]:
    """``environ`` with every provider pointed at the stub and placeholder keys."""
    base = f"http://127.0.0.1:{port}"
    return {
        **environ,
        "OPENROUTER_BASE_URL": f"{base}/api/v1",
        "ANTHROPIC_BASE_URL": base,
        "OLLAMA_BASE_URL": base,
        "LMSTUDIO_BASE_URL": f"{base}/v1",
        "OPENROUTER_API_KEY": PLACEHOLDER_KEY,
        "ANTHROPIC_API_KEY": PLACEHOLDER_KEY,
    }


def run(provider: str, *, runner: Runner | None = None) -> SmokeResult:
    """Run improve with ``provider`` against the stub; ok when it printed the stub's reply."""
    server = _StubServer(("127.0.0.1", 0), _Handler)
    server.paths = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        argv = [
            sys.executable,
            "-m",
            "prompt_workflow.cli",
            "improve",
            "--provider",
            provider,
            "--source",
            "argument",
            "--text",
            DRAFT,
            "--timeout",
            "20",
        ]
        try:
            code, out = (runner or run_cli)(argv, stub_env(int(port), os.environ))
        except (OSError, subprocess.SubprocessError) as exc:
            return SmokeResult(False, "", len(server.paths), f"could not run the CLI: {exc}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    output = out.decode("utf-8", "replace")
    if code == 0 and output == REPLY and server.paths:
        return SmokeResult(
            True, output, len(server.paths), "improve reached the stub and printed its reply"
        )
    return SmokeResult(False, output, len(server.paths), output or f"exit code {code}, no output")
