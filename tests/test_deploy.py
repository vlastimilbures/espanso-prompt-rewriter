"""The managed Espanso deployment (#86). Every test works in temp dirs: no test reads or writes
the real Espanso folder, and the real espanso, uv and brew are never run (conftest refuses
deploy.run_command; a test passes or patches in a fake runner)."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from prompt_workflow import assets, deploy
from prompt_workflow.cli import app
from prompt_workflow.config import user_data_dir

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]
MATCH = REPO / "espanso" / "match"
NAMES = sorted(p.name for p in MATCH.glob("*.yml"))
LAUNCHER = "/Users/me/.local/bin/prompt-workflow"
VERSION = deploy.__version__
# Captured at import, before conftest swaps it for a refusal in every test.
REAL_RUN_COMMAND = deploy.run_command
EXE = "prompt-workflow.exe" if os.name == "nt" else "prompt-workflow"
STATIC = "prompts-core.yml"  # the only match file that never calls the CLI


# --- Today's install-script rendering, kept verbatim to prove deploy writes the same bytes ---


def _script_render_macos(source: str, cli_path: str) -> str:
    # install_macos.sh: python's str.replace of the placeholder, nothing else.
    return source.replace("__PROMPT_WORKFLOW__", cli_path)


def _script_render_windows(source: str, cli: str) -> str:
    # install_windows.ps1: $Cli -replace '\\', '/', then a literal .Replace().
    return source.replace("__PROMPT_WORKFLOW__", cli.replace("\\", "/"))


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "path", ["/Users/me/.local/bin/prompt-workflow", "/Users/Jan Novák/a|b&c/prompt-workflow"]
)
def test_render_matches_the_macos_script_plus_stamp(name, path):
    source = (MATCH / name).read_bytes().decode("utf-8")
    rendered = deploy.render(source, deploy.launcher_text(path, windows=False), "1.2.3")
    stamp = "# prompt-workflow 1.2.3 (managed; edit at your own risk)\n"
    assert rendered == stamp + _script_render_macos(source, path)
    # The stamp is a comment: the YAML Espanso reads is unchanged.
    assert yaml.safe_load(rendered) == yaml.safe_load(_script_render_macos(source, path))


@pytest.mark.parametrize("name", NAMES)
def test_render_matches_the_windows_script_plus_stamp(name):
    cli = r"C:\Users\Jan Novák\.local\bin\prompt-workflow.exe"
    source = (MATCH / name).read_bytes().decode("utf-8")
    rendered = deploy.render(source, deploy.launcher_text(cli, windows=True), "1.2.3")
    assert rendered.split("\n", 1)[1] == _script_render_windows(source, cli)
    assert "C:/Users/Jan Novák/.local/bin/prompt-workflow.exe" in rendered or name == STATIC


# The packaged files are the ones the scripts deployed from.
def test_assets_are_the_repo_match_files():
    assert assets.match_names() == NAMES


@pytest.mark.parametrize("bad", ['"', "$", "`", "\\"])
def test_launcher_guard_posix(bad):
    with pytest.raises(deploy.DeployError, match="quote, \\$, backtick or backslash"):
        deploy.launcher_text(f"/opt/a{bad}b/prompt-workflow", windows=False)


@pytest.mark.parametrize("bad", ['"', "%", "^", "&", "|", "<", ">"])
def test_launcher_guard_windows(bad):
    with pytest.raises(deploy.DeployError, match=r"cmd\.exe or YAML"):
        deploy.launcher_text(rf"C:\a{bad}b\prompt-workflow.exe", windows=True)


def test_launcher_windows_slashes():
    assert deploy.launcher_text(r"C:\x\prompt-workflow.exe", windows=True) == (
        "C:/x/prompt-workflow.exe"
    )


# --- Plan, apply, states ------------------------------------------------------------------


@pytest.fixture
def espanso(tmp_path):
    root = tmp_path / "espanso"
    (root / "match").mkdir(parents=True)
    return root


def _plan(espanso, launcher=LAUNCHER):
    return deploy.plan(espanso, launcher, deploy.Manifest.load())


def _states(espanso, launcher=LAUNCHER):
    return {s.name: s.state for s in _plan(espanso, launcher).steps}


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_manifest_lives_in_the_user_data_dir(espanso):
    deploy.apply(_plan(espanso))
    path = user_data_dir() / deploy.MANIFEST_NAME
    assert path.is_relative_to(Path(os.environ["XDG_DATA_HOME"]))
    data = json.loads(path.read_text("utf-8"))
    assert data["format"] == 1
    entry = data["files"][0]
    assert set(entry) == {"target", "asset_version", "digest", "launcher", "calls_cli", "backups"}
    assert entry["asset_version"] == VERSION
    assert entry["launcher"] == LAUNCHER


def test_deploy_twice_is_a_noop(espanso):
    assert set(_states(espanso).values()) == {deploy.MISSING}
    first = deploy.apply(_plan(espanso))
    assert first.changed
    for name in NAMES:
        text = (espanso / "match" / name).read_text("utf-8")
        assert text.startswith(f"# prompt-workflow {VERSION} (managed; edit at your own risk)\n")
    before = _tree(espanso)
    manifest = (user_data_dir() / deploy.MANIFEST_NAME).read_bytes()

    second = _plan(espanso)
    assert second.is_noop
    assert set(_states(espanso).values()) == {deploy.IN_SYNC}
    outcome = deploy.apply(second)
    assert (outcome.changed, outcome.lines) == (False, [])
    assert _tree(espanso) == before
    assert (user_data_dir() / deploy.MANIFEST_NAME).read_bytes() == manifest


# No code path writes to Espanso's config/ (#37), and deploy touches nothing but match/.
def test_deploy_never_touches_config(espanso):
    config = espanso / "config" / "default.yml"
    config.parent.mkdir()
    config.write_text("toggle_key: ALT\n", "utf-8")
    deploy.apply(_plan(espanso))
    changed = {k for k in _tree(espanso) if not k.startswith("match")}
    assert changed == {str(Path("config") / "default.yml")}
    assert config.read_text("utf-8") == "toggle_key: ALT\n"


def test_apply_refuses_a_target_outside_match(espanso):
    the_plan = _plan(espanso)
    the_plan.steps[0].target = espanso / "config" / "x.yml"
    with pytest.raises(deploy.DeployError, match="Refusing to write outside"):
        deploy.apply(the_plan)


def _edit(espanso, name="prompts-llm.yml"):
    target = espanso / "match" / name
    target.write_text(target.read_text("utf-8") + "# my tweak\n", "utf-8")
    return target


# A user-edited file is never overwritten without a choice.
def test_modified_file_is_kept_without_a_choice(espanso):
    deploy.apply(_plan(espanso))
    target = _edit(espanso)
    edited = target.read_bytes()
    the_plan = _plan(espanso)
    assert [s.name for s in the_plan.conflicts] == ["prompts-llm.yml"]
    assert the_plan.conflicts[0].state == deploy.MODIFIED
    assert "+# my tweak" not in the_plan.conflicts[0].diff()
    assert "-# my tweak" in the_plan.conflicts[0].diff()
    outcome = deploy.apply(the_plan)
    assert target.read_bytes() == edited
    assert not outcome.changed
    assert any("kept your modified" in line for line in outcome.lines)
    # Still modified: the manifest kept our digest.
    assert _states(espanso)["prompts-llm.yml"] == deploy.MODIFIED


def test_modified_take_ours_backs_up(espanso):
    deploy.apply(_plan(espanso))
    target = _edit(espanso)
    edited = target.read_bytes()
    deploy.apply(_plan(espanso), {"prompts-llm.yml": deploy.OURS})
    assert "# my tweak" not in target.read_text("utf-8")
    entry = deploy.Manifest.load().entries[str(target)]
    assert len(entry.backups) == 1
    backup = Path(entry.backups[0])
    assert backup.name.startswith("prompts-llm.yml.bak-")
    assert backup.read_bytes() == edited


def test_modified_side_by_side(espanso):
    deploy.apply(_plan(espanso))
    target = _edit(espanso)
    edited = target.read_bytes()
    deploy.apply(_plan(espanso), {"prompts-llm.yml": deploy.SIDE})
    assert target.read_bytes() == edited
    side = target.with_name("prompts-llm.yml" + deploy.SIDE_SUFFIX)
    assert side.read_text("utf-8") == _plan(espanso).steps[1].rendered
    assert not side.name.endswith(".yml")  # Espanso does not load it


def test_unknown_choice(espanso):
    deploy.apply(_plan(espanso))
    _edit(espanso)
    with pytest.raises(deploy.DeployError, match="Unknown choice"):
        deploy.apply(_plan(espanso), {"prompts-llm.yml": "merge"})


def test_foreign_file(espanso):
    (espanso / "match" / STATIC).write_text("matches: []\n", "utf-8")
    the_plan = _plan(espanso)
    assert {s.name: s.state for s in the_plan.conflicts} == {STATIC: deploy.FOREIGN}
    deploy.apply(the_plan)
    assert (espanso / "match" / STATIC).read_text("utf-8") == "matches: []\n"
    assert str(espanso / "match" / STATIC) not in deploy.Manifest.load().entries


# A file the install scripts wrote (same body, no stamp, no manifest) is ours and stale.
def test_script_deployed_file_is_stale(espanso):
    for name in NAMES:
        source = (MATCH / name).read_text("utf-8")
        (espanso / "match" / name).write_text(_script_render_macos(source, LAUNCHER), "utf-8")
    assert set(_states(espanso).values()) == {deploy.STALE}
    outcome = deploy.apply(_plan(espanso))
    assert all(line.startswith("updated") for line in outcome.lines)
    assert not list((espanso / "match").glob("*.bak-*"))
    assert _plan(espanso).is_noop


# The #25 drift case: an old prompts-template.yml deployed next to a newer CLI.
def test_status_reports_stale_after_an_upgrade(monkeypatch, espanso):
    old_source = "# old template\nmatches: []\n"
    real = assets.read_match
    monkeypatch.setattr(deploy, "__version__", "0.15.0")
    monkeypatch.setattr(
        assets,
        "read_match",
        lambda name: old_source if name == "prompts-template.yml" else real(name),
    )
    deploy.apply(_plan(espanso))
    monkeypatch.setattr(deploy, "__version__", "0.16.0")
    monkeypatch.setattr(assets, "read_match", real)

    states = _states(espanso)
    assert states["prompts-template.yml"] == deploy.STALE
    assert {states[n] for n in NAMES if n != "prompts-template.yml"} == {deploy.STALE}
    result = runner.invoke(
        app, ["espanso", "status", "--espanso-dir", str(espanso), "--launcher", LAUNCHER]
    )
    assert result.exit_code == 0
    assert "stale     prompts-template.yml" in result.stdout
    deploy.apply(_plan(espanso))
    target = espanso / "match" / "prompts-template.yml"
    assert target.read_text("utf-8").startswith("# prompt-workflow 0.16.0 (managed")
    assert deploy.Manifest.load().entries[str(target)].asset_version == "0.16.0"


# A new launcher path makes every CLI-calling file stale; the static one stays in sync.
def test_new_launcher_is_stale(espanso):
    deploy.apply(_plan(espanso))
    states = _states(espanso, "/opt/homebrew/bin/prompt-workflow")
    assert states == {n: deploy.IN_SYNC if n == STATIC else deploy.STALE for n in NAMES}


def test_backups_pruned_to_the_last_two_of_ours(monkeypatch, espanso):
    deploy.apply(_plan(espanso))
    target = espanso / "match" / "prompts-llm.yml"
    theirs = target.with_name("prompts-llm.yml.bak-19990101000000")
    theirs.write_text("an old backup of the user's", "utf-8")
    stamps = iter(["20260101000001", "20260101000002", "20260101000003"])
    monkeypatch.setattr(deploy, "_stamp", lambda: next(stamps))
    for _ in range(3):
        _edit(espanso)
        deploy.apply(_plan(espanso), {"prompts-llm.yml": deploy.OURS})
    left = sorted(p.name for p in target.parent.glob("prompts-llm.yml.bak-*"))
    assert left == [
        "prompts-llm.yml.bak-19990101000000",
        "prompts-llm.yml.bak-20260101000002",
        "prompts-llm.yml.bak-20260101000003",
    ]
    entry = deploy.Manifest.load().entries[str(target)]
    assert [Path(b).name for b in entry.backups] == left[1:]


def test_backup_name_never_clobbers(espanso):
    path = espanso / "match" / "a.yml"
    path.write_text("one", "utf-8")
    first = deploy._backup(path, "20260101000000")
    second = deploy._backup(path, "20260101000000")
    assert (first.name, second.name) == ("a.yml.bak-20260101000000", "a.yml.bak-20260101000000-1")


def test_legacy_base_yml_is_retired(espanso):
    legacy = espanso / "match" / "base.yml"
    legacy.write_text('matches:\n  - trigger: "-p-"\n    # prompt-workflow\n', "utf-8")
    the_plan = _plan(espanso)
    assert the_plan.legacy == legacy
    deploy.apply(the_plan)
    assert not legacy.exists()
    assert len(list(legacy.parent.glob("base.yml.bak-*"))) == 1
    assert _plan(espanso).is_noop


def test_other_base_yml_is_left_alone(espanso):
    legacy = espanso / "match" / "base.yml"
    legacy.write_text('matches:\n  - trigger: ":date"\n', "utf-8")
    assert _plan(espanso).legacy is None


def test_non_utf8_file_is_foreign(espanso):
    (espanso / "match" / STATIC).write_bytes(b"\xff\xfe")
    assert _states(espanso)[STATIC] == deploy.FOREIGN


# --- Detach -------------------------------------------------------------------------------


def test_detach_keep_static(espanso):
    deploy.apply(_plan(espanso))
    template = _edit(espanso, "prompts-template.yml")
    outcome = deploy.detach(deploy.Manifest.load())
    match = espanso / "match"
    assert sorted(p.name for p in match.iterdir()) == sorted([STATIC, "prompts-template.yml"])
    assert "# my tweak" in template.read_text("utf-8")
    assert any("edited since" in line for line in outcome.lines)
    # The static snippets and the edited file stay on record; removed ones are forgotten.
    assert sorted(Path(t).name for t in deploy.Manifest.load().entries) == sorted(
        [STATIC, "prompts-template.yml"]
    )


def test_detach_remove_all(espanso):
    deploy.apply(_plan(espanso))
    edited = _edit(espanso, STATIC)
    deploy.detach(deploy.Manifest.load(), remove_all=True)
    assert [p.name for p in (espanso / "match").iterdir()] == [STATIC]
    assert "# my tweak" in edited.read_text("utf-8")
    edited.unlink()
    outcome = deploy.detach(deploy.Manifest.load(), remove_all=True)
    assert any("already gone" in line for line in outcome.lines)
    assert not (user_data_dir() / deploy.MANIFEST_NAME).exists()


def test_detach_leaves_unowned_files(espanso):
    other = espanso / "match" / "mine.yml"
    other.write_text("matches: []\n", "utf-8")
    deploy.apply(_plan(espanso))
    deploy.detach(deploy.Manifest.load(), remove_all=True)
    assert [p.name for p in (espanso / "match").iterdir()] == ["mine.yml"]


# --- Manifest -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("{not json", "cannot be read"),
        ('{"format": 99}', "unknown format"),
        ('{"format": 1, "files": [{"target": "x"}]}', "damaged"),
    ],
)
def test_bad_manifest(content, error):
    path = user_data_dir() / deploy.MANIFEST_NAME
    path.parent.mkdir(parents=True)
    path.write_text(content, "utf-8")
    with pytest.raises(deploy.DeployError, match=error):
        deploy.Manifest.load()


# --- Launcher resolver ----------------------------------------------------------------------


class FakeRunner:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.answers.get(" ".join(argv))


def _exe(path: Path, body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n", "utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_resolve_uv(tmp_path):
    tools, bin_dir = tmp_path / "uv" / "tools", tmp_path / "bin"
    launcher = _exe(bin_dir / "prompt-workflow")
    fake = FakeRunner({"uv tool dir": f"{tools}\n", "uv tool dir --bin": f"{bin_dir}\n"})
    found = deploy.resolve_launcher(
        runner=fake, prefix=tools / "espanso-prompt-rewriter", script=tmp_path / "x", windows=False
    )
    assert found == deploy.Launcher(launcher, "uv")


def test_resolve_uv_windows_exe(tmp_path):
    tools, bin_dir = tmp_path / "tools", tmp_path / "bin"
    launcher = _exe(bin_dir / "prompt-workflow.exe")
    fake = FakeRunner({"uv tool dir": str(tools), "uv tool dir --bin": str(bin_dir)})
    found = deploy.resolve_launcher(
        runner=fake, prefix=tools / "espanso-prompt-rewriter", script=tmp_path / "x", windows=True
    )
    assert found.path == launcher


def _cellar(tmp_path, version):
    return tmp_path / "brew" / "Cellar" / "espanso-prompt-rewriter" / version / "libexec"


def test_resolve_homebrew_never_cellar(tmp_path):
    brew = tmp_path / "brew"
    launcher = _exe(brew / "bin" / "prompt-workflow")
    _exe(_cellar(tmp_path, "0.15.0") / "bin" / "prompt-workflow")
    found = deploy.resolve_launcher(
        runner=FakeRunner({"brew --prefix": f"{brew}\n"}),
        prefix=_cellar(tmp_path, "0.15.0"),
        script=_cellar(tmp_path, "0.15.0") / "bin" / "prompt-workflow",
        windows=False,
    )
    assert found == deploy.Launcher(launcher, "homebrew")


def test_resolve_homebrew_opt(tmp_path):
    brew = tmp_path / "brew"
    launcher = _exe(brew / "opt" / "espanso-prompt-rewriter" / "bin" / "prompt-workflow")
    found = deploy.resolve_launcher(
        runner=FakeRunner({"brew --prefix": str(brew)}),
        prefix=_cellar(tmp_path, "0.15.0"),
        script=tmp_path / "x",
        windows=False,
    )
    assert found.path == launcher


def test_resolve_scoop_shim(tmp_path):
    scoop = tmp_path / "scoop"
    shim = _exe(scoop / "shims" / "prompt-workflow.exe")
    found = deploy.resolve_launcher(
        runner=FakeRunner(),
        prefix=scoop / "apps" / "prompt-workflow" / "current",
        script=tmp_path / "x",
        windows=True,
    )
    assert found == deploy.Launcher(shim, "scoop")


def test_resolve_running_script(tmp_path):
    script = _exe(tmp_path / "pipx" / "bin" / "prompt-workflow")
    found = deploy.resolve_launcher(
        runner=FakeRunner(), prefix=tmp_path / "venv", script=script, windows=False
    )
    assert found == deploy.Launcher(script, "script")


# Windows runs a console script as argv[0] without the .exe.
def test_resolve_running_script_windows(tmp_path):
    exe = _exe(tmp_path / "Scripts" / "prompt-workflow.exe")
    found = deploy.resolve_launcher(
        runner=FakeRunner(),
        prefix=tmp_path / "venv",
        script=tmp_path / "Scripts" / "prompt-workflow",
        windows=True,
    )
    assert found.path == exe


@pytest.mark.parametrize(
    "parts",
    [("Cellar", "x", "bin"), ("app", "0.15.0", "bin"), ("checkout", ".venv", "bin")],
    ids=["cellar", "versioned", "project-venv"],
)
def test_resolve_refuses_an_unstable_script(tmp_path, parts):
    script = _exe(tmp_path.joinpath(*parts) / "prompt-workflow")
    with pytest.raises(deploy.DeployError, match="stable prompt-workflow launcher"):
        deploy.resolve_launcher(
            runner=FakeRunner(), prefix=tmp_path / "venv", script=script, windows=False
        )


def test_resolve_nothing(tmp_path):
    with pytest.raises(deploy.DeployError, match="pass --launcher"):
        deploy.resolve_launcher(
            runner=FakeRunner(), prefix=tmp_path, script=Path("prompt-workflow"), windows=False
        )


# Upgrade: install N, deploy, install N+1, remove N; the deployed launcher still resolves and
# runs. Simulated with fake channel layouts rather than real venvs: building two venvs needs
# the network (or a warm uv cache) and real installs, which tests must not do. What matters is
# that deploy never writes a path inside the versioned install, which this checks end to end.
@pytest.mark.skipif(sys.platform == "win32", reason="runs a POSIX shell launcher")
def test_upgrade_keeps_the_uv_launcher(tmp_path, espanso):
    tools, bin_dir = tmp_path / "tools", tmp_path / "bin"
    venv = tools / "espanso-prompt-rewriter"
    _exe(venv / "bin" / "prompt-workflow-real", "echo N")
    launcher = _exe(bin_dir / "prompt-workflow", f'exec "{venv}/bin/prompt-workflow-real"')
    fake = FakeRunner({"uv tool dir": str(tools), "uv tool dir --bin": str(bin_dir)})
    found = deploy.resolve_launcher(runner=fake, prefix=venv, script=tmp_path / "x", windows=False)
    deploy.apply(_plan(espanso, deploy.launcher_text(found.path, windows=False)))

    # uv tool install --force: the venv is removed and rebuilt, the bin entry rewritten.
    for p in sorted(venv.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    _exe(venv / "bin" / "prompt-workflow-real", "echo N+1")

    deployed = (espanso / "match" / "prompts-llm.yml").read_text("utf-8")
    assert f'\\"{launcher}\\" improve' in deployed
    out = subprocess.run([str(launcher)], capture_output=True, text=True, timeout=10, check=True)
    assert out.stdout.strip() == "N+1"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
def test_upgrade_keeps_the_homebrew_launcher(tmp_path, espanso):
    brew = tmp_path / "brew"
    old = _exe(_cellar(tmp_path, "0.15.0") / "bin" / "prompt-workflow", "echo N")
    (brew / "bin").mkdir(parents=True)
    link = brew / "bin" / "prompt-workflow"
    link.symlink_to(old)
    found = deploy.resolve_launcher(
        runner=FakeRunner({"brew --prefix": str(brew)}),
        prefix=_cellar(tmp_path, "0.15.0"),
        script=old,
        windows=False,
    )
    assert found.path == link
    deploy.apply(_plan(espanso, deploy.launcher_text(found.path, windows=False)))

    # brew upgrade: install N+1 in its own Cellar dir, relink, remove N.
    new = _exe(_cellar(tmp_path, "0.16.0") / "bin" / "prompt-workflow", "echo N+1")
    link.unlink()
    link.symlink_to(new)
    old.unlink()

    deployed = (espanso / "match" / "prompts-llm.yml").read_text("utf-8")
    assert "Cellar" not in deployed
    out = subprocess.run([str(link)], capture_output=True, text=True, timeout=10, check=True)
    assert out.stdout.strip() == "N+1"


# --- Espanso folder and restart -----------------------------------------------------------------


def test_espanso_dir_from_espanso():
    fake = FakeRunner({"espanso path config": "/x/espanso\n"})
    assert deploy.espanso_dir(fake) == Path("/x/espanso")
    assert fake.calls == [["espanso", "path", "config"]]


def test_espanso_dir_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "default_espanso_dir", lambda: tmp_path / "default")
    assert deploy.espanso_dir(FakeRunner()) == tmp_path / "default"


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("win32", {"APPDATA": "/r"}, Path("/r/espanso")),
        ("darwin", {}, Path.home() / "Library" / "Application Support" / "espanso"),
        ("linux", {"XDG_CONFIG_HOME": "/c"}, Path("/c/espanso")),
        ("linux", {}, Path.home() / ".config" / "espanso"),
    ],
)
def test_default_espanso_dir(platform, env, expected):
    assert deploy.default_espanso_dir(env, platform=platform) == expected


@pytest.mark.parametrize(
    ("answers", "ok", "calls"),
    [
        ({"espanso restart": ""}, True, [["espanso", "restart"]]),
        ({"espanso start": ""}, True, [["espanso", "restart"], ["espanso", "start"]]),
        ({}, False, [["espanso", "restart"], ["espanso", "start"]]),
    ],
)
def test_restart(answers, ok, calls):
    fake = FakeRunner(answers)
    assert deploy.restart_espanso(fake) is ok
    assert fake.calls == calls


# The real runner, tried on this test's own interpreter only.
def test_run_command():
    py = sys.executable
    assert REAL_RUN_COMMAND([py, "-c", "print('hi')"]).strip() == "hi"
    assert REAL_RUN_COMMAND([py, "-c", "raise SystemExit(3)"]) is None
    assert REAL_RUN_COMMAND([str(Path(py).parent / "no-such-binary")]) is None


# --- CLI ------------------------------------------------------------------------------------


@pytest.fixture
def fake_run(monkeypatch):
    fake = FakeRunner({"espanso restart": ""})
    monkeypatch.setattr(deploy, "run_command", fake)
    return fake


def _cli(*args, input=None):
    return runner.invoke(app, ["espanso", *args], input=input)


def _where(espanso):
    return ["--espanso-dir", str(espanso), "--launcher", LAUNCHER]


def test_cli_deploy_yes_then_noop(espanso, fake_run):
    result = _cli("deploy", "--yes", *_where(espanso))
    assert result.exit_code == 0, result.output
    assert "missing   prompts-llm.yml" in result.stdout
    assert fake_run.calls == [["espanso", "restart"]]
    result = _cli("deploy", "--yes", *_where(espanso))
    assert "Nothing to do" in result.stdout
    assert fake_run.calls == [["espanso", "restart"]]  # no second restart


def test_cli_deploy_asks_first(espanso, fake_run):
    result = _cli("deploy", *_where(espanso), input="n\n")
    assert result.exit_code == 1
    assert not list((espanso / "match").iterdir())
    assert fake_run.calls == []


def test_cli_deploy_yes_keeps_modified(espanso, fake_run):
    deploy.apply(_plan(espanso))
    target = _edit(espanso)
    result = _cli("deploy", "--yes", *_where(espanso))
    assert result.exit_code == 0
    assert "modified  prompts-llm.yml" in result.stdout
    assert "-# my tweak" in result.stdout
    assert "# my tweak" in target.read_text("utf-8")


def test_cli_deploy_interactive_choice(espanso, fake_run):
    deploy.apply(_plan(espanso))
    target = _edit(espanso)
    result = _cli("deploy", *_where(espanso), input="ours\ny\n")
    assert result.exit_code == 0, result.output
    assert "# my tweak" not in target.read_text("utf-8")
    assert "backed up to prompts-llm.yml.bak-" in result.stdout


def test_cli_deploy_on_conflict_flag(espanso, fake_run):
    deploy.apply(_plan(espanso))
    _edit(espanso)
    result = _cli("deploy", "--yes", "--on-conflict", "side", *_where(espanso))
    assert result.exit_code == 0
    assert (espanso / "match" / ("prompts-llm.yml" + deploy.SIDE_SUFFIX)).exists()
    assert fake_run.calls == []  # nothing changed, so no restart


def test_cli_deploy_no_restart(espanso, fake_run):
    result = _cli("deploy", "--yes", "--no-restart", *_where(espanso))
    assert "not restarted" in result.stdout
    assert fake_run.calls == []


def test_cli_deploy_bad_on_conflict(espanso, fake_run):
    result = _cli("deploy", "--yes", "--on-conflict", "merge", *_where(espanso))
    assert result.exit_code == 1
    assert "--on-conflict must be one of" in result.stderr


def test_cli_deploy_unsafe_launcher(espanso, fake_run):
    result = _cli("deploy", "--yes", "--espanso-dir", str(espanso), "--launcher", '/a"b/pw')
    assert result.exit_code == 1
    assert "error: The CLI path contains" in result.stderr


def test_cli_restart_failure_is_reported(espanso, monkeypatch):
    monkeypatch.setattr(deploy, "run_command", FakeRunner())
    result = _cli("deploy", "--yes", *_where(espanso))
    assert result.exit_code == 0
    assert "Could not restart Espanso" in result.stderr


def test_cli_resolves_launcher_and_espanso_dir(espanso, monkeypatch, tmp_path):
    script = _exe(tmp_path / "pipx" / "bin" / EXE)
    monkeypatch.setattr(sys, "argv", [str(script)])
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(
        deploy,
        "run_command",
        FakeRunner({"espanso path config": str(espanso), "espanso restart": ""}),
    )
    result = _cli("status")
    assert result.exit_code == 0, result.output
    assert f"Launcher (script): {script}" in result.stdout
    assert f"Espanso match folder: {espanso / 'match'}" in result.stdout


def test_cli_status_diff_and_legacy(espanso, fake_run):
    (espanso / "match" / "base.yml").write_text('trigger: "-p-" prompt-workflow', "utf-8")
    result = _cli("status", "--diff", *_where(espanso))
    assert result.exit_code == 0
    assert "+# prompt-workflow" in result.stdout
    assert "legacy    base.yml" in result.stdout
    assert fake_run.calls == []  # status never restarts or writes
    assert (espanso / "match" / "base.yml").exists()


def test_cli_status_bad_manifest(espanso, fake_run):
    path = user_data_dir() / deploy.MANIFEST_NAME
    path.parent.mkdir(parents=True)
    path.write_text("{", "utf-8")
    result = _cli("status", *_where(espanso))
    assert result.exit_code == 1
    assert "cannot be read" in result.stderr


def test_cli_detach(espanso, fake_run):
    assert "Nothing to do" in _cli("detach", "--yes").stdout
    _cli("deploy", "--yes", *_where(espanso))
    assert _cli("detach", input="n\n").exit_code == 1
    result = _cli("detach", "--yes")
    assert result.exit_code == 0, result.output
    assert [p.name for p in (espanso / "match").iterdir()] == [STATIC]
    result = _cli("detach", "--remove-all", "--yes", "--no-restart")
    assert not list((espanso / "match").iterdir())


def test_cli_detach_bad_manifest(fake_run):
    path = user_data_dir() / deploy.MANIFEST_NAME
    path.parent.mkdir(parents=True)
    path.write_text("{", "utf-8")
    result = _cli("detach", "--yes")
    assert result.exit_code == 1


def test_cli_deploy_asks_again_after_a_bad_answer(espanso, fake_run):
    deploy.apply(_plan(espanso))
    target = _edit(espanso)
    result = _cli("deploy", *_where(espanso), input="merge\n\ny\n")
    assert result.exit_code == 0, result.output
    assert "Answer one of keep, ours, side." in result.stdout
    assert "# my tweak" in target.read_text("utf-8")  # the empty answer is keep


# A file that already is today's rendering but is not on record (a lost manifest) is adopted.
def test_in_sync_file_without_manifest_is_adopted(espanso):
    deploy.apply(_plan(espanso))
    (user_data_dir() / deploy.MANIFEST_NAME).unlink()
    before = _tree(espanso)
    the_plan = _plan(espanso)
    assert set(_states(espanso).values()) == {deploy.IN_SYNC}
    assert not the_plan.is_noop
    outcome = deploy.apply(the_plan)
    assert not outcome.changed
    assert all(line.startswith("adopted") for line in outcome.lines)
    assert _tree(espanso) == before
    assert _plan(espanso).is_noop


def test_cli_deploy_retires_legacy(espanso, fake_run):
    (espanso / "match" / "base.yml").write_text('trigger: "-p-" prompt-workflow', "utf-8")
    result = _cli("deploy", "--yes", *_where(espanso))
    assert "legacy    base.yml will be retired" in result.stdout
    assert "retired legacy" in result.stdout
    assert not (espanso / "match" / "base.yml").exists()
