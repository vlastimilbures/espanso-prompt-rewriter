from __future__ import annotations

import json

import httpx
import pytest

from prompt_workflow.config import env_names
from prompt_workflow.providers import base


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Prevent the real .env / shell environment from leaking into tests.

    Settings.load() reads the real environment and the first .env it finds (it never
    writes os.environ), so without this a test's result would depend on the developer's
    shell and real .env contents.
    """
    for key in env_names():
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    # Point the loader at a per-test file so the developer's real repo .env never loads.
    monkeypatch.setenv("PROMPT_WORKFLOW_ENV", str(tmp_path / ".env"))
    # ...and its real user profile directory is never read either (prompt_builder).
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    # The usage history and price table go to per-test dirs, never the developer's real ones.
    for key in ("XDG_DATA_HOME", "LOCALAPPDATA"):
        monkeypatch.setenv(key, str(tmp_path / "data"))
    # The home the config dir would derive from, and the editable-install root, are per-test
    # temp dirs too: a test that unsets PROMPT_WORKFLOW_ENV can never read, write or migrate
    # the developer's real files (config.toml, secrets.toml, a repository .env).
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path / "home"))
    from prompt_workflow import config

    monkeypatch.setattr(config, "_PROJECT_ROOT", tmp_path / "project")
    # Never probe the developer's real clipboard: a concealed item there would fail tests.
    import prompt_workflow.cli as cli

    monkeypatch.setattr(cli, "is_concealed", lambda: None)


# Headers httpx adds to every request on its own; calls[i]["headers"] leaves them out so a
# test sees what the provider set (plus the Content-Type httpx derives from json=).
_HTTPX_OWN_HEADERS = frozenset(
    {"host", "accept", "accept-encoding", "connection", "user-agent", "content-length"}
)


def _response(json_data=None, *, status_code=200, bad_json=False, headers=None) -> httpx.Response:
    if bad_json:
        return httpx.Response(status_code, content=b"<html>not json", headers=headers)
    return httpx.Response(status_code, json={} if json_data is None else json_data, headers=headers)


class FakeHttp:
    """Answers every request through httpx.MockTransport, so httpx still builds, encodes and
    sends each real request (headers, JSON body, timeouts) while nothing reaches the network.

    Replies come from ``queue()`` in order while it lasts, then from ``exc`` if set, then from
    the last ``reply()``. A queued item is ``_response()`` keyword arguments or an exception.
    """

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.client_kwargs: list[dict] = []
        # Each wait post_json made before a retry, in seconds; nothing really sleeps.
        self.sleeps: list[float] = []
        self.exc: Exception | None = None
        self._queue: list = []
        self._reply: dict = {}

    def reply(self, json_data=None, **kwargs):
        self._reply = {"json_data": json_data, **kwargs}
        return self

    def queue(self, *items):
        self._queue.extend(items)
        return self

    @property
    def calls(self) -> list[dict]:
        """Each request as {"url", "json", "headers"}; headers keep the case they were set in."""
        return [
            {
                "url": str(request.url),
                "json": json.loads(request.content) if request.content else None,
                "headers": {
                    key.decode(): value.decode()
                    for key, value in request.headers.raw
                    if key.decode().lower() not in _HTTPX_OWN_HEADERS
                },
            }
            for request in self.requests
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._queue:
            item = self._queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return _response(**item)
        if self.exc:
            raise self.exc
        # A fresh Response per request: httpx consumes and closes each one.
        return _response(**self._reply)


@pytest.fixture
def fake_http(monkeypatch):
    """Patch httpx.Client so every client uses a MockTransport; no test may reach the network.

    The patched client replaces any transport the code passes (post_json gives loopback URLs
    their own), and refuses a proxy or mount, which would take precedence over the transport.
    ``client_kwargs`` records the arguments as the code passed them.
    """
    fake = FakeHttp()
    real_client = httpx.Client

    def client(*args, **kwargs):
        assert not {"proxy", "mounts"} & kwargs.keys(), "fake_http cannot honour a proxy"
        fake.client_kwargs.append(kwargs)
        return real_client(*args, **{**kwargs, "transport": httpx.MockTransport(fake.handler)})

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(base, "_sleep", fake.sleeps.append)
    return fake


class StubProvider:
    """Stands in for cli.make_provider and the provider it builds: records each build
    and generate() call, and returns ``result`` or raises ``exc``."""

    def __init__(self):
        self.result = "improved"
        self.exc = None
        self.built = []
        self.options = []
        self.calls = []

    def __call__(self, name, cfg, **options):
        self.built.append((name, cfg))
        self.options.append(options)
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
