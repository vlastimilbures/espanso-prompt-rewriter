"""The contributor install scripts, read as text only (never run: they install a uv tool and
deploy into the real Espanso)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
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
