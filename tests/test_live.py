"""End-to-end checks against the real OpenRouter default model.

Opt-in: excluded by default, run with `uv run pytest -m live`. They spend a fraction of
a cent per run and need OPENROUTER_API_KEY in the environment or the repo's .env.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from bench_module import bench

from prompt_workflow.config import read_env_file

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.live


def _setting(name: str) -> str:
    return os.environ.get(name) or (read_env_file(REPO / ".env") or {}).get(name) or ""


def _has_key() -> bool:
    # PROMPT_LOCAL_ONLY=true refuses OpenRouter, so there is nothing to test live.
    return bool(_setting("OPENROUTER_API_KEY")) and _setting("PROMPT_LOCAL_ONLY").lower() != "true"


def _improve(draft: str) -> subprocess.CompletedProcess[str]:
    # Run the CLI in a fresh process, the way Espanso does. The autouse fixture stripped
    # provider env vars and pointed PROMPT_WORKFLOW_ENV at a temp file; drop that so the
    # CLI finds the repo .env, which is what configures this call.
    env = {k: v for k, v in os.environ.items() if k != "PROMPT_WORKFLOW_ENV"}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "prompt_workflow.cli",
            "improve",
            "--provider",
            "openrouter",
            "--profile",
            "default",
            "--source",
            "stdin",
        ],
        input=draft,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=90,
    )


needs_key = pytest.mark.skipif(
    not _has_key(), reason="OPENROUTER_API_KEY not configured, or PROMPT_LOCAL_ONLY=true"
)


@needs_key
def test_live_rewrite_produces_golden_template():
    draft, wants_plan, wants_independent = bench.DRAFTS["board"]
    proc = _improve(draft)
    out = proc.stdout
    assert proc.returncode == 0
    assert not out.startswith("[prompt-workflow:"), out
    assert not out.endswith("\n")
    # Model quality varies run to run; the structural checks must hold regardless.
    structural = [
        f for f in bench.check(out, wants_plan, wants_independent) if not f.startswith("wrong ")
    ]
    assert structural == [], out


# The gate blocks before any network call, so this one needs no credits, only a key.
@needs_key
def test_live_sensitive_draft_is_blocked():
    proc = _improve("customer data for card 4111 1111 1111 1111")
    assert proc.returncode == 0
    assert proc.stdout.startswith("[prompt-workflow: Blocked cloud call")
