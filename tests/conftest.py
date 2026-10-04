from __future__ import annotations

import httpx
import pytest

from prompt_workflow.config import env_names


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Prevent the real .env / shell environment from leaking into tests.

    Without this, Settings.load()'s os.environ.setdefault(...) permanently
    pollutes the test process on whichever test happens to run first
    alphabetically, making later assertions depend on the developer's
    real .env contents.
    """
    for key in env_names():
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    # Point the loader at a per-test file so the developer's real repo .env never loads.
    monkeypatch.setenv("PROMPT_WORKFLOW_ENV", str(tmp_path / ".env"))


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


class MockHttp:
    """Answers every request through httpx.MockTransport, so httpx still builds and sends
    real requests. Records each one; replies with ``reply`` as JSON and ``status``."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.reply: object = {}
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.reply)


@pytest.fixture
def mock_transport(monkeypatch):
    """Patch httpx.Client so every client uses a MockTransport; no test may reach the network."""
    mock = MockHttp()
    real_client = httpx.Client

    def client(**kwargs):
        return real_client(**{**kwargs, "transport": httpx.MockTransport(mock.handler)})

    monkeypatch.setattr(httpx, "Client", client)
    return mock


class StubProvider:
    """Stands in for cli.make_provider and the provider it builds: records each build
    and generate() call, and returns ``result`` or raises ``exc``."""

    def __init__(self):
        self.result = "improved"
        self.exc = None
        self.built = []
        self.calls = []

    def __call__(self, name, cfg):
        self.built.append((name, cfg))
        return self

    def generate(self, prompt, system_prompt):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def stub_provider(monkeypatch):
    """Replace the CLI's make_provider, so CLI tests never build a real provider."""
    import prompt_workflow.cli as cli

    stub = StubProvider()
    monkeypatch.setattr(cli, "make_provider", stub)
    return stub
