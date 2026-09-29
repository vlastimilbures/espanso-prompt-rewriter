import tomllib
from pathlib import Path

from prompt_workflow import __version__


# __version__ must match pyproject.toml's version — it drifted once already (commit 62c843b).
def test_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    assert __version__ == data["project"]["version"]
