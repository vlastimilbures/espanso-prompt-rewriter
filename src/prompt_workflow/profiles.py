"""Copy the profiles a checkout added or edited under src/prompt_workflow/prompts into the user
profile directory, where an upgrade cannot replace them (the `profiles migrate` service).

Nothing here runs on the trigger path. It only ever copies: no file is deleted, and an existing
user file is never overwritten.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .prompt_builder import PROFILE_NAME, user_profiles_dir

# Where a checkout keeps the built-in profiles, relative to its root.
PROMPTS_PATH = "src/prompt_workflow/prompts"

# Migration.status values.
COPIED = "copied"
EXISTS = "exists"  # a user file of that name is already there and differs: left alone
IDENTICAL = "identical"  # already copied
INVALID_NAME = "invalid name"  # no --profile could select it


@dataclass(frozen=True)
class Migration:
    name: str
    change: str  # "added" (no pristine copy) or "modified"
    source: Path
    target: Path
    status: str


def changed_profiles(source_dir: Path, pristine: Mapping[str, str]) -> dict[str, str]:
    """name -> "added" or "modified" for each `*.md` in ``source_dir`` that is not in
    ``pristine`` (name -> text) or whose text differs from it (surrounding whitespace aside,
    as the loader strips it)."""
    changed = {}
    for path in sorted(source_dir.glob("*.md")):
        name = path.stem
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"Cannot read profile {name}.md ({type(exc).__name__})") from None
        if name not in pristine:
            changed[name] = "added"
        elif text.strip() != pristine[name].strip():
            changed[name] = "modified"
    return changed


def git_pristine_profiles(checkout: Path, rev: str | None = None) -> dict[str, str]:
    """The built-in profiles as git has them at ``rev``, by name. Without ``rev``, the commit
    the current branch shares with its upstream (so local commits count as changes too), or
    HEAD when there is no upstream."""
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        raise ValueError("git is not installed")

    def run(*args: str) -> str:
        return subprocess.run(  # noqa: S603 - git with fixed subcommands, no shell
            [git, "-C", str(checkout), *args],
            capture_output=True,
            check=True,
            encoding="utf-8",
            timeout=10,
        ).stdout

    try:
        if rev is None:
            try:
                rev = run("merge-base", "HEAD", "@{upstream}").strip()
            except subprocess.CalledProcessError:
                rev = "HEAD"
        listing = run("ls-tree", "--name-only", f"{rev}:{PROMPTS_PATH}").split("\n")
        pristine = {}
        for name in (n for n in listing if n.endswith(".md")):
            try:
                pristine[name.removesuffix(".md")] = run("show", f"{rev}:{PROMPTS_PATH}/{name}")
            except UnicodeDecodeError:
                raise ValueError(f"Cannot read profile {name} at {rev} (not UTF-8)") from None
        return pristine
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        raise ValueError(f"git could not read {PROMPTS_PATH} at {rev}") from exc


def migrate_profiles(
    source_dir: Path, pristine: Mapping[str, str], dest: Path | None = None
) -> list[Migration]:
    """Copy each profile changed_profiles() finds into ``dest`` (user_profiles_dir()). A
    target that exists is skipped and reported, never overwritten; the source stays. A copied
    `default.md` is used only once `default` is in PROMPT_PROFILE_OVERRIDES."""
    dest = user_profiles_dir() if dest is None else dest
    report = []
    for name, change in changed_profiles(source_dir, pristine).items():
        source, target = source_dir / f"{name}.md", dest / f"{name}.md"
        if not PROFILE_NAME.fullmatch(name):
            status = INVALID_NAME
        else:
            data = source.read_bytes()
            dest.mkdir(parents=True, exist_ok=True)
            try:
                # Exclusive create: even a file appearing meanwhile is not overwritten.
                with target.open("xb") as out:
                    out.write(data)
                status = COPIED
            except FileExistsError:
                # Whatever is there (a dangling link, a folder) is left alone and reported.
                try:
                    same = target.read_bytes() == data
                except OSError:
                    same = False
                status = IDENTICAL if same else EXISTS
        report.append(Migration(name, change, source, target, status))
    return report
