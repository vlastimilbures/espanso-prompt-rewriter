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
