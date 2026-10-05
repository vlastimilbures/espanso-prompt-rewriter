import re
import shutil
import subprocess
from pathlib import Path

import pytest
from bench_module import bench

from prompt_workflow.config import env_names

REPO = Path(__file__).resolve().parents[1]


def _readme_config_rows() -> set[str]:
    readme = (REPO / "README.md").read_text("utf-8")
    return set(re.findall(r"^\| `([A-Z_]+)`", readme, re.MULTILINE))


# Every setting is documented in README's configuration table and in .env.example (commented
# out with its default, except the key and persona), so a new variable cannot ship
# undocumented.
@pytest.mark.parametrize("name", env_names())
def test_setting_is_documented(name):
    assert name in _readme_config_rows(), f"{name} missing from README configuration table"
    example = (REPO / ".env.example").read_text("utf-8")
    assert re.search(rf"^#? ?{name}=", example, re.MULTILINE), f"{name} missing from .env.example"


# The README table documents nothing Settings does not read (PROMPT_WORKFLOW_ENV is read
# by the .env loader itself).
def test_readme_documents_no_stale_settings():
    assert _readme_config_rows() - {*env_names(), "PROMPT_WORKFLOW_ENV"} == set()


# Every bench output directory the docs mention, and the bench's own default, is gitignored, so
# following the docs cannot commit outputs (with --persona env they hold a private persona).
def test_documented_bench_outdirs_are_ignored():
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


_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_INVOCATION = re.compile(r"(?<![\w/\\-])prompt-workflow(?![\w:-])([^`\n#]*)")
_FLAG = re.compile(r"(?<![\w-])(--?[a-zA-Z][\w-]*)")


def _documented_invocations(text: str) -> list[str]:
    """What follows `prompt-workflow` in each code block line and inline code span (a
    comment cut off), e.g. ` history export [--format json|csv] [-o FILE]`."""
    blocks = _FENCE.findall(text)
    spans = [
        *(line for b in blocks for line in b.splitlines()),
        *re.findall(r"`([^`\n]+)`", _FENCE.sub("", text)),
    ]
    return [m for span in spans for m in _INVOCATION.findall(span)]


def _changelog_unreleased() -> str:
    changelog = (REPO / "CHANGELOG.md").read_text("utf-8")
    return changelog.split("## Unreleased", 1)[1].split("\n## ", 1)[0]


# Every `prompt-workflow <command> [<subcommand>] --flag` the README (and the CHANGELOG's
# Unreleased notes) names exists in the CLI, so a renamed command or option cannot leave the
# docs behind. Walks the Click tree; the lazy commands load their modules, never textual.
@pytest.mark.parametrize("doc", ["README.md", "CHANGELOG.md"])
def test_documented_commands_and_flags_exist(doc):
    import typer

    from prompt_workflow import cli

    root = typer.main.get_command(cli.app)
    ctx = root.make_context("prompt-workflow", ["--help"], resilient_parsing=True)
    text = (REPO / doc).read_text("utf-8")
    invocations = _documented_invocations(text if doc == "README.md" else _changelog_unreleased())
    assert invocations, f"{doc} names no prompt-workflow command; drop or adapt this test"
    for rest in invocations:
        command, words = root, rest.split()
        while words and isinstance(command, typer.core.TyperGroup):
            word = words[0]
            if not re.fullmatch(r"[a-z]+", word):
                break
            sub = command.get_command(ctx, word)
            assert sub is not None, f"{doc}: `prompt-workflow{rest}`: no command {word!r}"
            command, words = sub, words[1:]
        known = {"--help", *(o for p in command.params for o in (*p.opts, *p.secondary_opts))}
        for flag in _FLAG.findall(" ".join(words)):
            assert flag in known, f"{doc}: `prompt-workflow{rest}`: no option {flag}"


def test_readme_has_no_package_placeholders():
    readme = (REPO / "README.md").read_text("utf-8")
    for placeholder in ("<owner>/<tap>", "<bucket>", "<Publisher.Package>"):
        assert placeholder not in readme
