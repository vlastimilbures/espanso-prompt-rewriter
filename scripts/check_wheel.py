"""Check what a built wheel really contains, from a venv outside the checkout.

A declaration in pyproject.toml does not prove what lands in the wheel, so CI installs the
built wheel into a clean venv and runs this with that venv's Python (not from the repo, so
`src/` is not importable):

    <venv python> scripts/check_wheel.py --repo . --wheel dist/<name>.whl

It checks that the package was imported from the venv, that every match file under the repo's
espanso/match/ and every profile under src/prompt_workflow/prompts/ resolves through
importlib.resources, that espanso/config/ was not shipped, and that `prompt-workflow persona`
runs with an empty config. Exits nonzero on the first failure.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import prompt_workflow
from prompt_workflow import assets
from prompt_workflow.cli import PERSONA_PLACEHOLDER
from prompt_workflow.prompt_builder import PROFILES, system_prompt


def _fail(message: str) -> None:
    sys.exit(f"check_wheel: {message}")


def _check_import(repo: Path) -> Path:
    package = Path(prompt_workflow.__file__).resolve().parent
    if package.is_relative_to(repo):
        _fail(f"prompt_workflow was imported from the checkout ({package}), not the wheel")
    return package


def _check_match_files(repo: Path, package: Path) -> None:
    folder = Path(str(assets.match_dir())).resolve()
    if not folder.is_relative_to(package):
        _fail(f"match files resolve to {folder}, outside the installed package")
    expected = sorted(p.name for p in (repo / "espanso" / "match").glob("*.yml"))
    if assets.match_names() != expected:
        _fail(f"match files {assets.match_names()} != repo {expected}")
    for name in expected:
        if assets.read_match(name) != (repo / "espanso" / "match" / name).read_text("utf-8"):
            _fail(f"{name} differs from the repo copy")


def _check_profiles(repo: Path) -> None:
    expected = sorted(p.stem for p in (repo / "src" / "prompt_workflow" / "prompts").glob("*.md"))
    if sorted(PROFILES) != expected:
        _fail(f"profiles {sorted(PROFILES)} != repo {expected}")
    for name in expected:
        if not system_prompt(name).strip():
            _fail(f"profile {name} is empty")


def _check_wheel_contents(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        shipped = [n for n in archive.namelist() if "/espanso/config/" in n]
    if shipped:
        _fail(f"espanso/config must not ship, found {shipped}")


def _check_persona() -> None:
    exe = Path(sys.executable).parent / (
        "prompt-workflow.exe" if os.name == "nt" else "prompt-workflow"
    )
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        env_file = home / "empty.env"
        env_file.write_text("", encoding="utf-8")
        env = {
            **os.environ,
            "HOME": str(home),
            "USERPROFILE": str(home),
            "XDG_CONFIG_HOME": str(home / "config"),
            "APPDATA": str(home / "appdata"),
            "PROMPT_WORKFLOW_ENV": str(env_file),
        }
        env.pop("PROMPT_PERSONA", None)
        result = subprocess.run(  # noqa: S603 - the venv's own entry point
            [str(exe), "persona"], capture_output=True, env=env, cwd=home, check=False, timeout=60
        )
    stdout = result.stdout.decode("utf-8")
    if (result.returncode, stdout) != (0, PERSONA_PLACEHOLDER):
        _fail(f"persona exited {result.returncode} with {stdout!r} {result.stderr!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo", type=Path, required=True, help="the checkout the wheel was built from"
    )
    parser.add_argument("--wheel", type=Path, required=True, help="the built .whl file")
    args = parser.parse_args()
    repo = args.repo.resolve()
    package = _check_import(repo)
    _check_match_files(repo, package)
    _check_profiles(repo)
    _check_wheel_contents(args.wheel)
    _check_persona()
    print(f"check_wheel: ok ({len(assets.match_names())} match files, {len(PROFILES)} profiles)")


if __name__ == "__main__":
    main()
