"""Detecting an earlier checkout install (#110, previous_install.py), offline: the runner is
faked, and every checkout, receipt and match folder is built under tmp_path."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from prompt_workflow import assets, config, deploy, doctor, previous_install
from prompt_workflow.previous_install import ENTERED, LAUNCHER, MANIFEST, RECEIPT

WINDOWS = os.name == "nt"
UV_BIN = "/Users/me/.local/bin/prompt-workflow"


class FakeRunner:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.answers.get(" ".join(argv))


def checkout(tmp_path: Path, name: str = previous_install.PROJECT_NAME, *, env=True) -> Path:
    """A checkout as an editable install left it: pyproject.toml, a .env with one setting and
    one key (built at runtime, so no scanner takes it for a credential), an edited profile."""
    root = tmp_path / "Projects" / "epr"
    (root / "src" / "prompt_workflow" / "prompts").mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n', "utf-8")
    if env:
        key = "sk-or-v1-" + "0" * 8
        (root / ".env").write_text(f"PROMPT_TIMEOUT=45\nOPENROUTER_API_KEY={key}\n", "utf-8")
    (root / "src" / "prompt_workflow" / "prompts" / "mine.md").write_text("Mine.\n", "utf-8")
    return root


def venv_launcher(root: Path) -> Path:
    if WINDOWS:
        return root / ".venv" / "Scripts" / "prompt-workflow.exe"
    return root / ".venv" / "bin" / "prompt-workflow"


def deploy_old(espanso: Path, launcher: str) -> None:
    """The match files as the old install script rendered them for ``launcher``."""
    (espanso / "match").mkdir(parents=True, exist_ok=True)
    # install_windows.ps1 turned backslashes into slashes; install_macos.sh kept the path.
    path = launcher.replace("\\", "/") if WINDOWS else launcher
    for name in assets.match_names():
        text = assets.read_match(name).replace(deploy.PLACEHOLDER, path)
        (espanso / "match" / name).write_bytes(text.encode("utf-8"))


def receipt(tools: Path, **source) -> None:
    folder = tools / previous_install.PROJECT_NAME
    folder.mkdir(parents=True, exist_ok=True)
    ((key, value),) = source.items()
    item = f'{{ name = "{previous_install.PROJECT_NAME}", {key} = "{value}" }}'
    text = f"[tool]\nrequirements = [{item}]\n"
    (folder / "uv-receipt.toml").write_text(text.replace("\\", "\\\\"), "utf-8")


@pytest.fixture
def espanso(tmp_path):
    root = tmp_path / "espanso"
    (root / "match").mkdir(parents=True)
    return root


@pytest.fixture
def env(monkeypatch):
    """The process environment the tests run under (conftest's temp dirs), without a legacy
    PROMPT_WORKFLOW_ENV and with a PATH that holds no prompt-workflow."""
    monkeypatch.delenv("PROMPT_WORKFLOW_ENV", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(previous_install.shutil, "which", lambda *a, **k: None)
    return os.environ


def _roots(found):
    return {c.root: c.signals for c in found.candidates}


# --- Root from a launcher -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("launcher", "root"),
    [
        ("/Users/me/epr/.venv/bin/prompt-workflow", PurePosixPath("/Users/me/epr")),
        ("C:/Users/me/epr/.venv/Scripts/prompt-workflow.exe", PureWindowsPath("C:/Users/me/epr")),
        (
            "C:\\Users\\me\\epr\\.venv\\Scripts\\prompt-workflow.exe",
            PureWindowsPath("C:/Users/me/epr"),
        ),
        ("C:/Users/me/epr/.VENV/scripts/Prompt-Workflow.EXE", PureWindowsPath("C:/Users/me/epr")),
        (UV_BIN, None),  # a uv tool bin: the same path for the editable install and the wheel
        ("/opt/homebrew/bin/prompt-workflow", None),
        ("C:/Users/me/scoop/shims/prompt-workflow.exe", None),
        ("/Users/me/epr/.venv/lib/prompt-workflow", None),
        (".venv/bin/prompt-workflow", None),  # relative: never resolved against the cwd
    ],
)
def test_checkout_root_of(launcher, root):
    assert previous_install.checkout_root_of(launcher) == root


# --- Signals ------------------------------------------------------------------------------


def test_detects_the_launcher_in_old_match_files(tmp_path, espanso, env):
    root = checkout(tmp_path)
    launcher = str(venv_launcher(root))
    deploy_old(espanso, launcher)
    found = previous_install.detect(runner=FakeRunner(), espanso_dir=espanso)
    (candidate,) = found.candidates
    assert candidate.root == root.resolve()
    assert candidate.signals == {LAUNCHER}
    assert candidate.env_file == root.resolve() / ".env"
    assert candidate.profiles_dir == root.resolve() / "src" / "prompt_workflow" / "prompts"
    assert candidate.launchers_in_root == (launcher.replace("\\", "/") if WINDOWS else launcher,)
    assert found.gated is None


def test_detects_a_manifest_entry(tmp_path, espanso, env):
    root = checkout(tmp_path)
    launcher = str(venv_launcher(root)).replace("\\", "/")
    deploy.apply(deploy.plan(espanso, launcher, deploy.Manifest.load()))
    found = previous_install.detect(runner=FakeRunner(), espanso_dir=espanso)
    assert _roots(found) == {root.resolve(): {LAUNCHER, MANIFEST}}


def test_a_manifest_entry_whose_file_is_gone_is_no_signal(tmp_path, espanso, env):
    root = checkout(tmp_path)
    launcher = str(venv_launcher(root)).replace("\\", "/")
    deploy.apply(deploy.plan(espanso, launcher, deploy.Manifest.load()))
    for name in assets.match_names():
        (espanso / "match" / name).unlink()
    assert previous_install.detect(runner=FakeRunner(), espanso_dir=espanso).candidates == ()


@pytest.mark.parametrize("kind", ["editable", "directory"])
def test_detects_the_uv_receipt(tmp_path, espanso, env, kind):
    root = checkout(tmp_path)
    tools = tmp_path / "uv" / "tools"
    receipt(tools, **{kind: str(root)})
    runner = FakeRunner({"uv tool dir": f"{tools}\n"})
    found = previous_install.detect(runner=runner, espanso_dir=espanso)
    assert _roots(found) == {root.resolve(): {RECEIPT}}


def test_a_wheel_receipt_gives_nothing(tmp_path, espanso, env):
    checkout(tmp_path)
    tools = tmp_path / "uv" / "tools"
    receipt(tools, url="https://example.invalid/espanso_prompt_rewriter-0.16.1-py3-none-any.whl")
    runner = FakeRunner({"uv tool dir": str(tools)})
    assert previous_install.detect(runner=runner, espanso_dir=espanso).candidates == ()


@pytest.mark.parametrize("text", ["not toml [", "[tool]\nrequirements = 3\n", ""])
def test_a_damaged_receipt_gives_nothing(tmp_path, espanso, env, text):
    tools = tmp_path / "uv" / "tools"
    (tools / previous_install.PROJECT_NAME).mkdir(parents=True)
    (tools / previous_install.PROJECT_NAME / "uv-receipt.toml").write_text(text, "utf-8")
    assert previous_install.receipt_root(FakeRunner({"uv tool dir": str(tools)})) is None


def test_detects_an_entered_path(tmp_path, espanso, env):
    root = checkout(tmp_path)
    found = previous_install.detect(runner=FakeRunner(), espanso_dir=espanso, entered=root)
    assert _roots(found) == {root.resolve(): {ENTERED}}


def test_signals_for_one_root_merge(tmp_path, espanso, env):
    root = checkout(tmp_path)
    deploy_old(espanso, str(venv_launcher(root)))
    tools = tmp_path / "uv" / "tools"
    receipt(tools, editable=str(root))
    runner = FakeRunner({"uv tool dir": str(tools)})
    found = previous_install.detect(runner=runner, espanso_dir=espanso, entered=root)
    assert _roots(found) == {root.resolve(): {LAUNCHER, RECEIPT, ENTERED}}


def test_a_uv_bin_launcher_alone_gives_nothing(tmp_path, espanso, env):
    checkout(tmp_path)
    deploy_old(espanso, UV_BIN)
    assert previous_install.detect(runner=FakeRunner(), espanso_dir=espanso).candidates == ()


@pytest.mark.parametrize("name", ["something-else", None])
def test_a_folder_that_is_not_this_project_gives_nothing(tmp_path, espanso, env, name):
    root = checkout(tmp_path, name or "x")
    if name is None:
        (root / "pyproject.toml").unlink()
    deploy_old(espanso, str(venv_launcher(root)))
    found = previous_install.detect(runner=FakeRunner(), espanso_dir=espanso, entered=root)
    assert found.candidates == ()


def test_the_running_checkout_gives_nothing(tmp_path, espanso, env, monkeypatch):
    root = checkout(tmp_path)
    monkeypatch.setattr(config, "_PROJECT_ROOT", root)
    found = previous_install.detect(runner=FakeRunner(), espanso_dir=espanso, entered=root)
    assert found.candidates == ()


def test_detection_never_reads_the_env_file(tmp_path, espanso, env, monkeypatch):
    root = checkout(tmp_path)
    read = Path.read_text

    def guarded(self, *args, **kwargs):
        assert self.name != ".env", "detection read the .env"
        return read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    found = previous_install.detect(runner=FakeRunner(), espanso_dir=espanso, entered=root)
    assert found.candidates[0].env_file is not None


def test_without_espanso_dir_it_asks_espanso(tmp_path, espanso, env):
    root = checkout(tmp_path)
    deploy_old(espanso, str(venv_launcher(root)))
    runner = FakeRunner({"espanso path config": str(espanso)})
    assert _roots(previous_install.detect(runner=runner)) == {root.resolve(): {LAUNCHER}}
    assert ["espanso", "path", "config"] in runner.calls


# --- Gate (D-MIG-4) -----------------------------------------------------------------------


def test_legacy_mode_detects_nothing(tmp_path, espanso, monkeypatch):
    root = checkout(tmp_path)
    monkeypatch.setenv("PROMPT_WORKFLOW_ENV", str(tmp_path / "legacy.env"))
    found = previous_install.detect(runner=FakeRunner(), espanso_dir=espanso, entered=root)
    assert (found.candidates, found.gated) == ((), previous_install.LEGACY)


def test_a_saved_config_detects_nothing(tmp_path, espanso, env):
    root = checkout(tmp_path)
    deploy_old(espanso, str(venv_launcher(root)))
    path = config.settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("config_version = 1\n", "utf-8")
    found = previous_install.detect(runner=FakeRunner(), espanso_dir=espanso, entered=root)
    assert (found.candidates, found.gated) == ((), previous_install.SAVED)


def test_a_secret_store_detects_nothing(tmp_path, espanso, env):
    root = checkout(tmp_path)
    store = config.settings_file().with_name("secrets.toml")
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("", "utf-8")
    found = previous_install.detect(runner=FakeRunner(), espanso_dir=espanso, entered=root)
    assert (found.candidates, found.gated) == ((), previous_install.SECRETS)


def test_a_config_toml_folder_is_not_a_saved_config(tmp_path, espanso, env):
    root = checkout(tmp_path)
    config.settings_file().mkdir(parents=True)
    found = previous_install.detect(runner=FakeRunner(), espanso_dir=espanso, entered=root)
    assert found.gated is None
    assert found.candidates


def test_a_manifest_alone_does_not_close_the_gate(tmp_path, espanso, env):
    """The editable install deployed (with a manifest) before the switch, so a manifest
    pointing into the checkout must still be found."""
    root = checkout(tmp_path)
    launcher = str(venv_launcher(root)).replace("\\", "/")
    deploy.apply(deploy.plan(espanso, launcher, deploy.Manifest.load()))
    found = previous_install.detect(runner=FakeRunner(), espanso_dir=espanso)
    assert found.gated is None
    assert _roots(found)


# --- Skip marker (D-MIG-3) ----------------------------------------------------------------


def test_a_skipped_root_is_offered_only_when_entered(tmp_path, espanso, env):
    root = checkout(tmp_path)
    deploy_old(espanso, str(venv_launcher(root)))
    previous_install.skip(root.resolve())
    assert previous_install.detect(runner=FakeRunner(), espanso_dir=espanso).candidates == ()
    found = previous_install.detect(runner=FakeRunner(), espanso_dir=espanso, entered=root)
    assert _roots(found) == {root.resolve(): {LAUNCHER, ENTERED}}


def test_another_checkout_is_offered_after_a_skip(tmp_path, espanso, env):
    previous_install.skip(tmp_path / "elsewhere")
    root = checkout(tmp_path)
    deploy_old(espanso, str(venv_launcher(root)))
    assert previous_install.detect(runner=FakeRunner(), espanso_dir=espanso).candidates


@pytest.mark.parametrize("text", ["{", "[]", '{"skipped": 3}', '{"skipped": [3, {"root": 4}]}'])
def test_a_damaged_skip_marker_counts_as_empty(tmp_path, espanso, env, text):
    path = previous_install.skip_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    assert previous_install.skipped_roots() == set()
    root = checkout(tmp_path)
    deploy_old(espanso, str(venv_launcher(root)))
    assert previous_install.detect(runner=FakeRunner(), espanso_dir=espanso).candidates
    previous_install.skip(root)
    assert previous_install.skipped_roots() == {str(root)}


# --- Shadowed CLI -------------------------------------------------------------------------


@pytest.fixture
def installed(tmp_path, monkeypatch):
    launcher = tmp_path / "uv-bin" / "prompt-workflow"
    launcher.parent.mkdir()
    launcher.write_text("", "utf-8")
    monkeypatch.setattr(deploy, "resolve_launcher", lambda **_: deploy.Launcher(launcher, "uv"))
    return launcher


def _which(monkeypatch, path):
    monkeypatch.setattr(previous_install.shutil, "which", lambda *a, **k: str(path))


def test_an_active_venv_shadows_the_launcher(tmp_path, installed, monkeypatch):
    root = checkout(tmp_path)
    old = venv_launcher(root)
    old.parent.mkdir(parents=True)
    old.write_text("", "utf-8")
    _which(monkeypatch, old)
    environ = {"VIRTUAL_ENV": str(root / ".venv"), "PATH": str(old.parent)}
    found = previous_install.shadow(environ, FakeRunner())
    assert found is not None
    assert (found.path, found.launcher) == (str(old), str(installed))
    assert "deactivate" in found.hint


def test_a_venv_on_path_without_virtual_env(tmp_path, installed, monkeypatch):
    old = venv_launcher(checkout(tmp_path))
    old.parent.mkdir(parents=True)
    old.write_text("", "utf-8")
    _which(monkeypatch, old)
    found = previous_install.shadow({"PATH": str(old.parent)}, FakeRunner())
    assert found is not None
    assert str(old.parent) in found.hint


def test_another_cli_on_path(tmp_path, installed, monkeypatch):
    other = tmp_path / "pipx" / "prompt-workflow"
    other.parent.mkdir()
    other.write_text("", "utf-8")
    _which(monkeypatch, other)
    found = previous_install.shadow({"PATH": str(other.parent)}, FakeRunner())
    assert found is not None
    assert "uninstall" in found.hint


def test_no_shadow_when_path_finds_the_launcher(installed, monkeypatch):
    _which(monkeypatch, installed)
    assert previous_install.shadow({"PATH": str(installed.parent)}, FakeRunner()) is None


def test_no_shadow_without_a_launcher(tmp_path, monkeypatch):
    _which(monkeypatch, tmp_path / "prompt-workflow")

    def nothing(**_):
        raise deploy.DeployError("none")

    monkeypatch.setattr(deploy, "resolve_launcher", nothing)
    assert previous_install.shadow({}, FakeRunner()) is None


# --- doctor -------------------------------------------------------------------------------


def _check(report):
    return next(c for c in report.checks if c.id == "previous_install")


@pytest.fixture
def no_clipboard(monkeypatch):
    import pyperclip

    from prompt_workflow import clipboard_guard

    monkeypatch.setattr(clipboard_guard, "is_concealed", lambda: None)
    monkeypatch.setattr(pyperclip, "paste", lambda: "")


def test_doctor_reports_a_previous_install(tmp_path, espanso, env, no_clipboard):
    root = checkout(tmp_path)
    deploy_old(espanso, str(venv_launcher(root)))
    report = doctor.run(espanso_dir=espanso, launcher=UV_BIN, runner=FakeRunner())
    check = _check(report)
    assert check.status == "info"
    assert str(root.resolve()) in check.message
    assert "not migrated" in check.message
    data = report.to_json()["checks"]["previous_install"]["data"]
    assert data["roots"] == [str(root.resolve())]
    assert data["signals"] == [LAUNCHER]
    assert data["env_file"] == str(root.resolve() / ".env")
    assert data["gated"] is None
    assert data["shadow"] is None
    # Only that the .env exists: never a value from it.
    assert "sk-or" not in repr(report.to_json())


def test_doctor_with_nothing_found(espanso, env, no_clipboard):
    report = doctor.run(espanso_dir=espanso, launcher=UV_BIN, runner=FakeRunner())
    assert (_check(report).status, _check(report).message) == ("ok", "no previous install found")


def test_doctor_after_migration_looks_for_nothing(tmp_path, espanso, env, no_clipboard):
    root = checkout(tmp_path)
    deploy_old(espanso, str(venv_launcher(root)))
    path = config.settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("config_version = 1\n", "utf-8")
    report = doctor.run(espanso_dir=espanso, launcher=UV_BIN, runner=FakeRunner())
    assert _check(report).status == "ok"
    assert report.to_json()["checks"]["previous_install"]["data"]["gated"] == "saved"


def test_doctor_warns_about_a_shadowed_cli(
    tmp_path, espanso, env, installed, no_clipboard, monkeypatch
):
    old = venv_launcher(checkout(tmp_path))
    old.parent.mkdir(parents=True)
    old.write_text("", "utf-8")
    _which(monkeypatch, old)
    report = doctor.run(espanso_dir=espanso, launcher=UV_BIN, runner=FakeRunner())
    check = _check(report)
    assert check.status == "warn"
    assert str(old) in check.message
    assert report.to_json()["checks"]["previous_install"]["data"]["shadow"] == str(old)
