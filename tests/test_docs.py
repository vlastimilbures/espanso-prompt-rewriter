from __future__ import annotations

import itertools
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from bench_module import bench

from promptmend.config import ENV_FILE_VARS, env_names

if TYPE_CHECKING:
    from typer._click import Command, Context

REPO = Path(__file__).resolve().parents[1]


def _readme_config_rows() -> set[str]:
    readme = (REPO / "README.md").read_text("utf-8")
    return set(re.findall(r"^\| `([A-Z_]+)`", readme, re.MULTILINE))


# Every setting is documented in README's configuration table and in .env.example (commented
# out with its default, except the key and persona), so a new variable cannot ship
# undocumented.
@pytest.mark.parametrize("name", env_names())
def test_setting_is_documented(name: str) -> None:
    assert name in _readme_config_rows(), f"{name} missing from README configuration table"
    example = (REPO / ".env.example").read_text("utf-8")
    assert re.search(rf"^#? ?{name}=", example, re.MULTILINE), f"{name} missing from .env.example"


# The README table documents nothing Settings does not read (PROMPTMEND_ENV and its alias
# PROMPT_WORKFLOW_ENV are read by the .env loader itself).
def test_readme_documents_no_stale_settings() -> None:
    assert _readme_config_rows() - {*env_names(), *ENV_FILE_VARS} == set()


# Every bench output directory the docs mention, and the bench's own default, is gitignored, so
# following the docs cannot commit outputs (with --persona env they hold a private persona).
def test_documented_bench_outdirs_are_ignored() -> None:
    git = shutil.which("git")
    inside = git and subprocess.run([git, "rev-parse"], cwd=REPO, capture_output=True).returncode
    if git is None or inside != 0:
        pytest.skip("not a git checkout")
    docs = [REPO / "README.md", REPO / "CONTRIBUTING.md", *(REPO / "docs").rglob("*.md")]
    found = {m for p in docs for m in re.findall(r"--outdir[=\s]+(\S+)", p.read_text("utf-8"))}
    assert found, "the docs no longer suggest any --outdir; drop or adapt this test"
    for outdir in [*found, f"{bench.DEFAULT_OUTDIR}/20261004T120000000000Z"]:
        probe = f"{outdir.rstrip('/')}/results.json"
        ignored = subprocess.run([git, "check-ignore", "-q", probe], cwd=REPO, check=False)
        assert ignored.returncode == 0, outdir


# CONTRIBUTING's project tree names every module of the package (commands/ and tui/ are listed
# as folders), so a new module cannot be left out of the map.
def test_contributing_tree_lists_every_module() -> None:
    contributing = (REPO / "CONTRIBUTING.md").read_text("utf-8")
    tree = contributing[contributing.index("├── src/promptmend/") :]
    tree = tree[: tree.index("├── scripts/")]
    package = REPO / "src" / "promptmend"
    modules = [*package.glob("*.py"), *(package / "providers").glob("*.py")]
    missing = [p.name for p in modules if p.stem != "__init__" and f"── {p.name} " not in tree]
    assert missing == [], f"add {missing} to the project tree in CONTRIBUTING.md"


_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
# `prompt-workflow` is the deprecated alias (#169) of the same command tree: checked too.
_INVOCATION = re.compile(r"(?<![\w/\\-])(?:promptmend|prompt-workflow)(?![\w:=-])([^`\n#]*)")
_FLAG = re.compile(r"(?<![\w-])(--?[a-zA-Z][\w-]*)")
# `promptmend config get NAME` / `set NAME VALUE`: the later spans are siblings.
_SIBLINGS = re.compile(r"`(?:promptmend|prompt-workflow) ([^`]+)`((?:\s*/\s*`[^`]+`)+)")
# Options the README names for other tools: uv, the bench, the retired installer option.
_OTHER_TOOLS_FLAGS = {"--force", "--persona", "--suite", "--with-config"}


def _spans(text: str) -> list[str]:
    """Each inline code span outside code blocks, a line break in it read as a space."""
    return [s.replace("\n", " ") for s in re.findall(r"`([^`]+)`", _FENCE.sub("", text))]


def _documented_invocations(text: str) -> list[str]:
    """What follows `promptmend` in each code block line and inline code span (a
    comment cut off), e.g. ` history export [--format json|csv] [-o FILE]`, plus each
    `/ x` sibling span after one, under the first span's parent command."""
    lines = [line for b in _FENCE.findall(text) for line in b.splitlines()]
    found = [m for span in [*lines, *_spans(text)] for m in _INVOCATION.findall(span)]
    for first, rest in _SIBLINGS.findall(_FENCE.sub("", text).replace("\n", " ")):
        path = list(itertools.takewhile(lambda w: re.fullmatch(r"[a-z]+", w), first.split()))
        parent = " ".join(path[:-1])
        found += [f" {parent} {sibling}" for sibling in re.findall(r"`([^`]+)`", rest)]
    return found


# The Unreleased notes plus the newest release's, so the check never runs on an empty or
# command-free Unreleased alone (right after a release, or a first entry naming no command).
def _changelog_notes(changelog: str) -> str:
    sections = re.split(r"^## ", changelog, flags=re.MULTILINE)[1:]
    notes = []
    for section in sections:
        title, _, body = section.partition("\n")
        notes.append(body)
        if title.strip() != "Unreleased":
            return "".join(notes)
    raise AssertionError("CHANGELOG.md has no released version")


def test_changelog_notes_add_the_newest_release() -> None:
    released = "## 1.1.0 - 2026-10-02\n\n- `promptmend doctor`\n\n## 1.0.0 - 2026-10-01\n\n- y\n"
    for unreleased, expected in [
        ("## Unreleased\n\n", ["- `promptmend doctor`"]),
        ("", ["- `promptmend doctor`"]),
        ("## Unreleased\n\n- x\n\n", ["- x", "- `promptmend doctor`"]),
    ]:
        notes = _changelog_notes(f"# Changelog\n\n{unreleased}{released}")
        assert notes.split() == " ".join(expected).split()


def _cli() -> tuple[Command, Context]:
    import typer

    from promptmend import cli

    root = typer.main.get_command(cli.app)
    return root, root.make_context("promptmend", ["--help"], resilient_parsing=True)


# Every `promptmend <command> [<subcommand>] --flag` the README (and the CHANGELOG's
# newest notes) names exists in the CLI, so a renamed command or option cannot leave the
# docs behind. Walks the Click tree; the lazy commands load their modules, never textual.
@pytest.mark.parametrize("doc", ["README.md", "CHANGELOG.md"])
def test_documented_commands_and_flags_exist(doc: str) -> None:
    import typer

    root, ctx = _cli()
    text = (REPO / doc).read_text("utf-8")
    invocations = _documented_invocations(text if doc == "README.md" else _changelog_notes(text))
    assert invocations, f"{doc} names no promptmend command; drop or adapt this test"
    for rest in invocations:
        command, words = root, rest.split()
        while words and isinstance(command, typer.core.TyperGroup):
            word = words[0]
            if not re.fullmatch(r"[a-z]+", word):
                break
            sub = command.get_command(ctx, word)
            assert sub is not None, f"{doc}: `promptmend{rest}`: no command {word!r}"
            command, words = sub, words[1:]
        known = {"--help", *(o for p in command.params for o in (*p.opts, *p.secondary_opts))}
        for flag in _FLAG.findall(" ".join(words)):
            assert flag in known, f"{doc}: `promptmend{rest}`: no option {flag}"


# A code span that is only options (`--keep-static`, `--yes --preview-token <token>`) names
# options of some command: each must exist somewhere in the CLI.
def test_standalone_readme_flags_exist() -> None:
    import typer

    root, ctx = _cli()

    def options(command: Command) -> set[str]:
        names = {o for p in command.params for o in (*p.opts, *p.secondary_opts)}
        if isinstance(command, typer.core.TyperGroup):
            for name in command.list_commands(ctx):
                sub = command.get_command(ctx, name)
                assert sub is not None, name
                names |= options(sub)
        return names

    known = {"--help", *options(root), *_OTHER_TOOLS_FLAGS}
    spans = [s for s in _spans((REPO / "README.md").read_text("utf-8")) if s.startswith("--")]
    assert spans, "README names no standalone option; drop or adapt this test"
    for span in spans:
        for flag in re.findall(r"(?<![\w-])(--[a-zA-Z][\w-]*)", span):
            assert flag in known, f"README: `{span}`: no option {flag} in the CLI"


def test_readme_has_no_package_placeholders() -> None:
    readme = (REPO / "README.md").read_text("utf-8")
    for placeholder in ("<owner>/<tap>", "<bucket>", "<Publisher.Package>"):
        assert placeholder not in readme


# The release whose default prompt the benchmark's newest results score, pinned to the prompt's
# digest: a change to prompts/default.md fails here until both are updated (set the version to
# the release that ships the change) and docs/benchmark.md gets results for the new prompt.
_PROMPT_CHANGED_IN = (0, 19, 0)
_PROMPT_DIGEST = "c336e4851edba9c8b284726613bd7b3953ab317b3aca453396f0acc718bfa6e2"


def _result_headings() -> list[str]:
    text = (REPO / "docs" / "benchmark.md").read_text("utf-8")
    results = re.search(r"^## Results\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert results, "docs/benchmark.md has no '## Results' section"
    return re.findall(r"^### (.+)$", results.group(1), re.MULTILINE)


def test_benchmark_prompt_pin_is_current() -> None:
    import hashlib

    prompt = (REPO / "src" / "promptmend" / "prompts" / "default.md").read_bytes()
    assert hashlib.sha256(prompt).hexdigest() == _PROMPT_DIGEST, (
        "prompts/default.md changed: update _PROMPT_DIGEST and _PROMPT_CHANGED_IN, and add "
        "benchmark results for the new prompt to docs/benchmark.md"
    )


# Every results table names the prompt version it scored, newest first, and the newest scores
# the current prompt, so a table cannot pass for the current prompt's after a prompt change.
def test_benchmark_results_name_their_prompt_version() -> None:
    headings = _result_headings()
    assert headings, "docs/benchmark.md lists no results"
    versions = []
    for heading in headings:
        found = re.search(r"prompt of v(\d+)\.(\d+)\.(\d+)", heading, re.IGNORECASE)
        assert found, f"docs/benchmark.md: {heading!r} names no prompt version"
        versions.append(tuple(map(int, found.groups())))
    assert versions[0] >= _PROMPT_CHANGED_IN, f"{headings[0]!r} predates the current prompt"
    assert versions == sorted(versions, reverse=True), "results are not newest first"


def test_readme_benchmark_summary_links_the_details() -> None:
    readme = (REPO / "README.md").read_text("utf-8")
    section = re.search(r"^## Model benchmark\n(.*?)(?=^## )", readme, re.MULTILINE | re.DOTALL)
    assert section, "README lost its '## Model benchmark' section (#model-benchmark links)"
    assert "](docs/benchmark.md)" in section.group(1)
