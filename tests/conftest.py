from __future__ import annotations

import os

import httpx
import pytest

_ENV_PREFIXES = ("PROMPT_", "OLLAMA_", "OPENROUTER_", "LMSTUDIO_", "ANTHROPIC_")
_ENV_EXACT = ("ALLOW_CLOUD_OVERRIDE", "PROMPT_WORKFLOW_ENV")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Prevent the real .env / shell environment from leaking into tests.

    Without this, Settings.load()'s os.environ.setdefault(...) permanently
    pollutes the test process on whichever test happens to run first
    alphabetically, making later assertions depend on the developer's
    real .env contents.
    """
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES) or key in _ENV_EXACT:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    # Point the loader at a per-test file so the developer's real repo .env never loads.
    monkeypatch.setenv("PROMPT_WORKFLOW_ENV", str(tmp_path / ".env"))
    yield


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, bad_json=False):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self._bad_json = bad_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=httpx.Request("POST", "http://x"), response=self
            )

    def json(self):
        if self._bad_json:
            raise ValueError("Expecting value: line 1 column 1")
        return self._json


class FakeHttp:
    """Stands in for httpx.Client: records each POST and replays one response or error."""

    def __init__(self):
        self.response = FakeResponse()
        self.exc = None
        self.calls = []
        self.client_kwargs = []

    def __call__(self, *args, **kwargs):
        self.client_kwargs.append(kwargs)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.exc:
            raise self.exc
        return self.response

    def reply(self, json_data=None, **kwargs):
        self.response = FakeResponse(json_data=json_data, **kwargs)
        return self


@pytest.fixture
def fake_http(monkeypatch):
    """Patch httpx.Client for every provider; no test may reach the network."""
    fake = FakeHttp()
    monkeypatch.setattr(httpx, "Client", fake)
    return fake
