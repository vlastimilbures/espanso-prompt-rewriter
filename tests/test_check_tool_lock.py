"""scripts/check_tool_lock.py: the tool venv the contributor scripts install matches uv.lock
(#34). Fixtures only: no test lists a real tool venv."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_tool_lock.py"
if TYPE_CHECKING:
    import check_tool_lock as check
else:
    _spec = importlib.util.spec_from_file_location("check_tool_lock", _SCRIPT)
    assert _spec
    assert _spec.loader
    check = importlib.util.module_from_spec(_spec)
    sys.modules["check_tool_lock"] = check
    _spec.loader.exec_module(check)

LOCK = """
version = 1
revision = 3

[[package]]
name = "espanso-prompt-rewriter"
version = "0.15.0"
source = { editable = "." }

[[package]]
name = "httpx"
version = "0.28.1"

[[package]]
name = "typing-extensions"
version = "4.12.2"

[[package]]
name = "numpy"
version = "2.0.0"

[[package]]
name = "numpy"
version = "2.1.0"
"""


@pytest.fixture
def lock_file(tmp_path: Path) -> Path:
    path = tmp_path / "uv.lock"
    path.write_text(LOCK, "utf-8")
    return path


def _installed(**versions: str) -> list[dict[str, str]]:
    return [{"name": name, "version": version} for name, version in versions.items()]


def test_matching_venv(
    lock_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installed = [
        {"name": "espanso-prompt-rewriter", "version": "0.16.0.dev0"},  # editable: skipped
        {"name": "httpx", "version": "0.28.1"},
        {"name": "Typing_Extensions", "version": "4.12.2"},  # PEP 503 names
        {"name": "numpy", "version": "2.0.0"},  # one of a fork's pins
    ]
    monkeypatch.setattr(check, "pip_list", lambda python: installed)
    assert check.main(["--python", "py", "--lock", str(lock_file)]) == 0
    assert "matches uv.lock" in capsys.readouterr().out


def test_mismatches(
    lock_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installed = _installed(httpx="0.28.0", rich="14.0.0")
    monkeypatch.setattr(check, "pip_list", lambda python: installed)
    assert check.main(["--python", "py", "--lock", str(lock_file)]) == 1
    err = capsys.readouterr().err
    assert "httpx 0.28.0 is installed, uv.lock pins 0.28.1" in err
    assert "rich 14.0.0 is installed but not in uv.lock" in err


def test_pip_list_runs_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def run(argv: list[str], **kwargs: Any) -> object:
        seen.update(argv=argv, **kwargs)
        return type("P", (), {"stdout": '[{"name": "httpx", "version": "1"}]'})()

    monkeypatch.setattr(subprocess, "run", run)
    assert check.pip_list("/venv/bin/python") == [{"name": "httpx", "version": "1"}]
    assert seen["argv"] == ["uv", "pip", "list", "--format", "json", "--python", "/venv/bin/python"]
    assert seen["timeout"] == 60


# The real lock parses and pins this project's runtime dependencies.
def test_real_lock_parses() -> None:
    import tomllib

    lock = tomllib.loads((_SCRIPT.parents[1] / "uv.lock").read_text("utf-8"))
    assert {"httpx", "typer", "pyperclip"} <= check.locked_versions(lock).keys()
