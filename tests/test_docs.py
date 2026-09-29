import re
from pathlib import Path

import pytest

from prompt_workflow.config import env_names

REPO = Path(__file__).resolve().parents[1]


def _readme_config_rows() -> set[str]:
    readme = (REPO / "README.md").read_text("utf-8")
    return set(re.findall(r"^\| `([A-Z_]+)`", readme, re.MULTILINE))


# Every setting is documented in README's configuration table and in .env.example, so a
# new variable cannot ship undocumented.
@pytest.mark.parametrize("name", env_names())
def test_setting_is_documented(name):
    assert name in _readme_config_rows(), f"{name} missing from README configuration table"
    example = (REPO / ".env.example").read_text("utf-8")
    assert re.search(rf"^{name}=", example, re.MULTILINE), f"{name} missing from .env.example"


# The README table documents nothing Settings does not read (PROMPT_WORKFLOW_ENV is read
# by the .env loader itself).
def test_readme_documents_no_stale_settings():
    assert _readme_config_rows() - {*env_names(), "PROMPT_WORKFLOW_ENV"} == set()
