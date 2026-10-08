"""local_probe (#220): one short GET to a loopback Ollama or LM Studio, offline through
fake_http; conftest stubs the GET in every other test."""

from __future__ import annotations

import httpx
import pytest
from conftest import FakeHttp

from promptmend import local_probe

# Kept at import, before conftest's autouse stub replaces it in each test.
REAL_GET = local_probe._get


@pytest.fixture
def real_get(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_probe, "_get", REAL_GET)


@pytest.mark.usefixtures("real_get")
def test_any_answer_counts(fake_http: FakeHttp) -> None:
    fake_http.reply({"models": []})
    assert local_probe.answers("http://localhost:11434/api/tags") is True
    fake_http.reply(None, status_code=404)
    assert local_probe.answers("http://127.0.0.1:1234/v1/models") is True
    assert [c["url"] for c in fake_http.calls] == [
        "http://localhost:11434/api/tags",
        "http://127.0.0.1:1234/v1/models",
    ]
    assert fake_http.client_kwargs[0]["timeout"] == local_probe.TIMEOUT
    assert isinstance(fake_http.client_kwargs[0]["transport"], httpx.HTTPTransport)


@pytest.mark.usefixtures("real_get")
def test_no_answer(fake_http: FakeHttp) -> None:
    fake_http.exc = httpx.ConnectError("refused")
    assert local_probe.answers("http://localhost:11434/api/tags") is False


@pytest.mark.usefixtures("real_get")
def test_another_host_is_never_asked(fake_http: FakeHttp) -> None:
    assert local_probe.answers("https://ollama.example.com/api/tags") is None
    assert fake_http.calls == []
