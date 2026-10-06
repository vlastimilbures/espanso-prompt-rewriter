"""The install scripts, read as text only (never run: they install a uv tool, and the
contributor ones deploy into the real Espanso), and the rendering of the Windows user
installer, scripts/install.ps1, that the release workflow attaches (#186)."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
OLD_TOOL = "espanso-prompt-rewriter"


# #169: the tool's old name installs a `prompt-workflow` launcher where promptmend puts its
# alias. Left installed, uv refuses the install (or, with --force, a later uninstall of the
# old tool deletes the alias), so each script uninstalls it first, only when uv lists it, and
# says so.
@pytest.mark.parametrize("name", ["install_macos.sh", "install_windows.ps1"])
def test_scripts_uninstall_the_old_tool_before_installing(name: str) -> None:
    text = (SCRIPTS / name).read_text("utf-8")
    listed = text.index("uv tool list")
    uninstall = text.index(f"uv tool uninstall {OLD_TOOL}")
    install = text.index("uv tool install --editable . --force")
    assert listed < uninstall < install
    guard = text[listed:uninstall]
    assert re.search(rf"\^{re.escape(OLD_TOOL)} ", guard), "uninstall only what uv lists"
    assert f"Uninstalling {OLD_TOOL}" in guard
    assert len(re.findall(rf"(?m)^\s*uv tool uninstall {OLD_TOOL}$", text)) == 1


def test_windows_script_checks_the_uninstall() -> None:
    text = (SCRIPTS / "install_windows.ps1").read_text("utf-8")
    uninstall = text.index(f"uv tool uninstall {OLD_TOOL}")
    assert text[uninstall:].lstrip().split("\n", 2)[1].strip().startswith("Assert-Exit")


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if TYPE_CHECKING:
    import render_installer
else:
    render_installer = _load("render_installer")

INSTALL = SCRIPTS / "install.ps1"
ASSET = "install.ps1"


def _code_lines() -> list[str]:
    """install.ps1 without comments and the lines that only print."""
    lines = [line.split("#", 1)[0].strip() for line in INSTALL.read_text("utf-8").splitlines()]
    return [line for line in lines if line and not line.startswith(("Write-", "throw"))]


# The release workflow fills in the version: the placeholder is there exactly once, and the
# script refuses to run unrendered (its own check spells the placeholder in two parts).
def test_placeholder() -> None:
    text = INSTALL.read_text("utf-8")
    assert text.count(render_installer.PLACEHOLDER) == 1
    assert "'__PROMPTMEND_' + 'VERSION__'" in text
    rendered = render_installer.render(text, "1.2.3")
    assert "$ReleaseVersion = '1.2.3'" in rendered
    assert render_installer.PLACEHOLDER not in rendered


@pytest.mark.parametrize("version", ["1.2", "v1.2.3", "1.2.3; rm", ""])
def test_render_bad_version(version: str) -> None:
    with pytest.raises(ValueError, match=r"X\.Y\.Z"):
        render_installer.render(INSTALL.read_text("utf-8"), version)


def test_render_needs_one_placeholder() -> None:
    with pytest.raises(ValueError, match="0 times"):
        render_installer.render("nothing", "1.2.3")


def test_render_cli(tmp_path: Path) -> None:
    out = tmp_path / ASSET
    render_installer.main(["0.20.0", str(out)])
    assert "$ReleaseVersion = '0.20.0'" in out.read_text("ascii")
    with pytest.raises(SystemExit, match="render_installer: "):
        render_installer.main(["0.20", str(out)])


# Windows PowerShell 5.1 reads a file without a byte order mark in the ANSI code page.
def test_ascii() -> None:
    INSTALL.read_bytes().decode("ascii")


def test_release_renders() -> None:
    workflow = (REPO / ".github" / "workflows" / "release.yml").read_text("utf-8")
    assert f'scripts/render_installer.py "$VERSION" dist/{ASSET}' in workflow
    # dist/* is what the release job attests and attaches.
    assert "dist/*" in workflow


def test_readme_url() -> None:
    readme = (REPO / "README.md").read_text("utf-8")
    url = "https://github.com/vlastimilbures/promptmend/releases/latest/download/"
    assert f'powershell -ExecutionPolicy ByPass -c "irm {url}{ASSET} | iex"' in readme
    assert f"{url}{ASSET}" in INSTALL.read_text("utf-8")


# Setup and the deploy are only named as the next step, never run.
def test_never_deploys() -> None:
    code = "\n".join(_code_lines()).replace(OLD_TOOL, "")
    assert "doctor" in code, "the filter dropped the code lines"
    for word in ("setup", "deploy", "espanso", "detach"):
        assert word not in code.lower(), word
    printed = INSTALL.read_text("utf-8")
    assert "promptmend setup" in printed
    assert "promptmend espanso deploy" in printed


def test_install_command() -> None:
    code = "\n".join(_code_lines())
    assert "@('tool', 'install', '--force', $Spec, '-c', $Constraints)" in code
    assert "@('tool', 'update-shell')" in code
    assert "@('doctor', '--no-clipboard')" in code
    assert "'--id', 'astral-sh.uv', '-e'" in code
    assert "irm https://astral.sh/uv/install.ps1 | iex" in code
    assert '$Constraints = "$Repo/releases/download/v$Version/constraints.txt"' in code
    for override in ("PROMPTMEND_VERSION", "PROMPTMEND_WHEEL", "PROMPTMEND_CONSTRAINTS"):
        assert f"$env:{override}" in code
    assert "Set-StrictMode -Version Latest" in code
    assert "$ErrorActionPreference = 'Stop'" in code
    # `exit` would close the window of a caller who ran `irm | iex` in their own session.
    assert not re.search(r"(?m)^exit\b", code)
