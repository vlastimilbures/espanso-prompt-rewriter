"""The user profile directory (#85): new names, explicit overrides of a built-in, the report
a doctor command shows, the migration out of a checkout, and the packaged match files."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner, Result

from prompt_workflow import assets, profiles, prompt_builder
from prompt_workflow.cli import app
from prompt_workflow.config import Settings
from prompt_workflow.prompt_builder import (
    PROFILES,
    UserProfile,
    system_prompt,
    user_profiles,
    user_profiles_dir,
)

if TYPE_CHECKING:
    from conftest import StubProvider

REPO = Path(__file__).resolve().parents[1]
runner = CliRunner()
MINE = "Rewrite the draft as a regulation question. {{PERSONA_RULE}}"


def _write(name: str, text: str = MINE) -> Path:
    folder = user_profiles_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _improve(*args: str) -> Result:
    return runner.invoke(app, ["improve", *args, "--source", "argument", "--text", "draft"])


# The directory sits next to the user's .env, in the config dir conftest points at tmp_path.
def test_user_profiles_dir_is_in_the_config_dir(tmp_path: Path) -> None:
    assert user_profiles_dir() == tmp_path / "config" / "promptmend" / "profiles"


# A new name is usable with --profile and gets the persona rule like a built-in.
def test_new_user_profile_is_selectable(stub_provider: StubProvider) -> None:
    _write("regulation")
    result = _improve("--profile", "regulation")
    assert (result.exit_code, result.stdout) == (0, "improved")
    assert stub_provider.calls[0]["system_prompt"] == prompt_builder.render(MINE)
    assert system_prompt("regulation", "I test.") == prompt_builder.render(MINE, "I test.")


# PROMPT_PROFILE may name a user profile too.
def test_prompt_profile_may_name_a_user_profile(
    monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    _write("regulation")
    monkeypatch.setenv("PROMPT_PROFILE", "regulation")
    assert _improve().exit_code == 0
    assert stub_provider.calls[0]["system_prompt"] == prompt_builder.render(MINE)


# A user default.md never silently replaces the built-in: only PROMPT_PROFILE_OVERRIDES lets it.
def test_user_default_needs_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    _write("default")
    assert _improve().exit_code == 0
    assert stub_provider.calls[-1]["system_prompt"] == system_prompt("default")
    assert "regulation question" not in stub_provider.calls[-1]["system_prompt"]

    monkeypatch.setenv("PROMPT_PROFILE_OVERRIDES", "default")
    assert _improve().exit_code == 0
    assert stub_provider.calls[-1]["system_prompt"] == prompt_builder.render(MINE)
    # The pro tier keeps the opt-in, and the alias resolves to the overridden profile.
    assert _improve("--tier", "pro").exit_code == 0
    assert stub_provider.calls[-1]["system_prompt"] == prompt_builder.render(MINE)
    assert system_prompt("default-pro", "", ("default",)) == prompt_builder.render(MINE)
    # Only the listed built-in is replaced.
    _write("general")
    assert _improve("--profile", "general").exit_code == 0
    assert stub_provider.calls[-1]["system_prompt"] == system_prompt("general")


# Opting in without a file keeps the built-in; built-in files are never written.
def test_override_without_a_file_keeps_the_builtin() -> None:
    builtin = PROFILES["default"]
    assert system_prompt("default", "", ("default",)) == system_prompt("default")
    _write("default")
    system_prompt("default", "", ("default",))
    assert PROFILES["default"] == builtin
    shipped = (REPO / "src" / "prompt_workflow" / "prompts" / "default.md").read_text("utf-8")
    assert shipped.strip() == builtin


# --profile cannot climb out of the directory, and a dotted name is not a profile.
@pytest.mark.parametrize("name", ["../secret", "..", "a/b", "a.b", ".hidden", "a b", ""])
def test_profile_name_cannot_leave_the_directory(tmp_path: Path, name: str) -> None:
    (tmp_path / "config" / "promptmend" / "secret.md").parent.mkdir(parents=True)
    (tmp_path / "config" / "promptmend" / "secret.md").write_text("leak", "utf-8")
    with pytest.raises(ValueError, match="Unknown profile"):
        system_prompt(name)


# An unknown name lists the user's selectable profiles after the built-ins.
def test_unknown_profile_lists_user_profiles() -> None:
    _write("regulation")
    _write("default")
    _write("aaa")
    # Built-ins keep the order the message always had; the user's own follow, sorted.
    known = ", ".join([*PROFILES, "aaa", "regulation"])
    with pytest.raises(ValueError, match=re.escape(f"Choose from: {known}") + "$"):
        system_prompt("nope")
    for path in user_profiles_dir().iterdir():
        path.unlink()
    with pytest.raises(ValueError, match="Unknown profile") as info:
        system_prompt("nope")
    assert str(info.value) == f"Unknown profile: 'nope'. Choose from: {', '.join(PROFILES)}"


# Names are lower case and matched exactly, so every OS behaves like Linux: `Default` or
# `DEFAULT-PRO` never opens a user default.md on a case-insensitive file system, and `Mine.md`
# or `mine.MD` is not the profile `mine`.
@pytest.mark.parametrize("name", ["Default", "DEFAULT", "DEFAULT-PRO", "General", "Mine"])
def test_profile_names_are_case_sensitive_everywhere(
    name: str, stub_provider: StubProvider
) -> None:
    _write("default")
    _write("Mine")
    with pytest.raises(ValueError, match="Unknown profile"):
        system_prompt(name, "", ("default",) if name == "Default" else ())
    result = _improve("--profile", name)
    assert result.stdout.startswith("[prompt-workflow: Unknown profile")
    assert stub_provider.calls == []


def test_exact_file_name_is_required() -> None:
    _write("Mine")
    (user_profiles_dir() / "other.MD").write_text(MINE, "utf-8")
    for name in ("mine", "other"):
        with pytest.raises(ValueError, match="Unknown profile"):
            system_prompt(name)
    assert {p.name: p.status for p in user_profiles()} == {
        "Mine": "invalid name",
        "other": "invalid name",
    }


# Windows device names are never profile names (`con.md` opens the console there).
@pytest.mark.parametrize("name", ["con", "prn", "aux", "nul", "com1", "com9", "lpt1", "lpt9"])
def test_windows_device_names_are_rejected(name: str) -> None:
    assert not prompt_builder.PROFILE_NAME.fullmatch(name)
    with pytest.raises(ValueError, match="Unknown profile"):
        system_prompt(name)


@pytest.mark.parametrize("name", ["console", "com10", "lpt", "nul-x", "aux_1", "a", "my-2"])
def test_names_near_device_names_are_allowed(name: str) -> None:
    assert prompt_builder.PROFILE_NAME.fullmatch(name)


# An empty or unreadable user profile is an inline error naming the file, not the path.
def test_bad_user_profile_is_reported(tmp_path: Path) -> None:
    _write("blank", "  \n")
    with pytest.raises(ValueError, match=r"User profile blank\.md is empty"):
        system_prompt("blank")
    _write("latin", "").write_bytes(b"caf\xe9")
    with pytest.raises(ValueError, match=r"Cannot read user profile latin\.md \(UnicodeDecode"):
        system_prompt("latin")
    (user_profiles_dir() / "folder.md").mkdir()
    with pytest.raises(ValueError, match=r"Cannot read user profile folder\.md") as info:
        system_prompt("folder")
    assert str(tmp_path) not in str(info.value)


# The report a doctor/profiles command shows: every file and how it is treated.
def test_user_profiles_report() -> None:
    assert user_profiles() == []
    folder = user_profiles_dir()
    for name in ("regulation", "default", "general", "default-pro", "bad name"):
        _write(name)
    (folder / "notes.txt").write_text("not a profile", "utf-8")

    report = {p.name: p.status for p in user_profiles(("default", "general"))}
    assert report == {
        "regulation": "added",
        "default": "overrides",
        "general": "overrides",
        "default-pro": "shadowed",
        "bad name": "invalid name",
    }
    assert {p.name: p.status for p in user_profiles()}["default"] == "shadowed"
    (folder / "general.md").unlink()
    assert user_profiles(("general",))[-1] == UserProfile(
        "general", folder / "general.md", "missing"
    )


# PROMPT_PROFILE_OVERRIDES is parsed strictly: built-in names only, comma-separated.
def test_profile_overrides_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings().profile_overrides == ()
    monkeypatch.setenv("PROMPT_PROFILE_OVERRIDES", " default , general,default,")
    assert Settings().profile_overrides == ("default", "general")
    for bad in ("regulation", "default-pro", "default;general"):
        monkeypatch.setenv("PROMPT_PROFILE_OVERRIDES", bad)
        with pytest.raises(ValueError, match="PROMPT_PROFILE_OVERRIDES must be empty or a comma"):
            Settings()


# A bad value reaches Espanso as a marker, like every other setting.
def test_bad_profile_overrides_reports_inline(
    monkeypatch: pytest.MonkeyPatch, stub_provider: StubProvider
) -> None:
    monkeypatch.setenv("PROMPT_PROFILE_OVERRIDES", "regulation")
    result = _improve()
    assert result.exit_code == 0
    assert result.stdout.startswith("[prompt-workflow: PROMPT_PROFILE_OVERRIDES must be")
    assert stub_provider.calls == []


def _checkout(tmp_path: Path) -> Path:
    prompts = tmp_path / "checkout" / profiles.PROMPTS_PATH
    prompts.mkdir(parents=True)
    for name, text in PROFILES.items():
        (prompts / f"{name}.md").write_text(text + "\n", "utf-8")
    return prompts


# migrate copies added and modified profiles, never overwrites, never deletes.
def test_migrate_profiles_copies_only(tmp_path: Path) -> None:
    prompts = _checkout(tmp_path)
    (prompts / "general.md").write_text("My own general.", "utf-8")
    (prompts / "regulation.md").write_text("Regulation.", "utf-8")
    (prompts / "bad.name.md").write_text("Never selectable.", "utf-8")
    dest = user_profiles_dir()

    report = profiles.migrate_profiles(prompts, PROFILES)
    assert [(m.name, m.change, m.status) for m in report] == [
        ("bad.name", "added", "invalid name"),
        ("general", "modified", "copied"),
        ("regulation", "added", "copied"),
    ]
    assert (dest / "general.md").read_text("utf-8") == "My own general."
    assert not (dest / "bad.name.md").exists()
    assert not (dest / "default.md").exists()
    assert sorted(p.name for p in prompts.iterdir()) == [
        "bad.name.md",
        "default.md",
        "general.md",
        "regulation.md",
    ]

    (dest / "regulation.md").write_text("Edited since.", "utf-8")
    again = {m.name: m.status for m in profiles.migrate_profiles(prompts, PROFILES)}
    assert again == {"bad.name": "invalid name", "general": "identical", "regulation": "exists"}
    assert (dest / "regulation.md").read_text("utf-8") == "Edited since."


# A copied general.md stays shadowed until the user opts in.
def test_migrated_builtin_needs_opt_in(tmp_path: Path) -> None:
    prompts = _checkout(tmp_path)
    (prompts / "general.md").write_text("My own general.", "utf-8")
    profiles.migrate_profiles(prompts, PROFILES)
    assert system_prompt("general") == system_prompt("general", "", ())
    assert system_prompt("general", "", ("general",)) == "My own general."


def _git(cwd: Path, *args: str) -> None:
    git = shutil.which("git")
    assert git
    ident = [
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run([git, *ident, *args], cwd=cwd, check=True, capture_output=True, timeout=30)


# Against git: an untracked file counts as added, a local commit or an uncommitted edit as
# modified, compared with the commit the branch shares with its upstream.
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_pristine_profiles(tmp_path: Path) -> None:
    prompts = _checkout(tmp_path)
    upstream = prompts.parents[2]
    _git(upstream, "init", "-q")
    _git(upstream, "add", ".")
    _git(upstream, "commit", "-qm", "base")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(upstream), str(clone))
    cloned = clone / profiles.PROMPTS_PATH
    (cloned / "general.md").write_text("Committed locally.", "utf-8")
    _git(clone, "commit", "-qam", "mine")
    (cloned / "default.md").write_text("Edited, not committed.", "utf-8")
    (cloned / "regulation.md").write_text("Untracked.", "utf-8")

    pristine = profiles.git_pristine_profiles(clone)
    assert {k: v.strip() for k, v in pristine.items()} == PROFILES
    assert profiles.changed_profiles(cloned, pristine) == {
        "default": "modified",
        "general": "modified",
        "regulation": "added",
    }
    # Without an upstream, HEAD is the reference: the local commit is no longer a change.
    _git(clone, "branch", "--unset-upstream")
    pristine = profiles.git_pristine_profiles(clone)
    assert set(profiles.changed_profiles(cloned, pristine)) == {"default", "regulation"}
    with pytest.raises(ValueError, match="git could not read"):
        profiles.git_pristine_profiles(clone, "no-such-rev")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_pristine_profiles_rejects_non_utf8(tmp_path: Path) -> None:
    prompts = _checkout(tmp_path)
    (prompts / "latin.md").write_bytes(b"caf\xe9")
    repo = prompts.parents[2]
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    with pytest.raises(ValueError, match=r"Cannot read profile latin\.md at HEAD") as info:
        profiles.git_pristine_profiles(repo)
    assert str(tmp_path) not in str(info.value)


# A source the loader could not read either is an error naming the profile, not a crash.
def test_changed_profiles_reports_unreadable_source(tmp_path: Path) -> None:
    prompts = _checkout(tmp_path)
    (prompts / "latin.md").write_bytes(b"caf\xe9")
    with pytest.raises(ValueError, match=r"Cannot read profile latin\.md \(UnicodeDecode"):
        profiles.changed_profiles(prompts, PROFILES)
    (prompts / "latin.md").unlink()
    (prompts / "folder.md").mkdir()
    with pytest.raises(ValueError, match=r"Cannot read profile folder\.md") as info:
        profiles.changed_profiles(prompts, PROFILES)
    assert str(tmp_path) not in str(info.value)


# Whatever already holds a target's name (a folder, a dangling link) is left alone and
# reported, and the other profiles are still copied.
def test_migrate_skips_odd_targets(tmp_path: Path) -> None:
    prompts = _checkout(tmp_path)
    for name in ("alpha", "beta", "gamma"):
        (prompts / f"{name}.md").write_text(name, "utf-8")
    dest = user_profiles_dir()
    (dest / "alpha.md").mkdir(parents=True)
    expected = {"alpha": "exists", "beta": "exists", "gamma": "copied"}
    try:
        os.symlink(tmp_path / "nowhere.md", dest / "beta.md")
    except OSError:  # Windows without the symlink privilege
        expected["beta"] = "copied"

    report = {m.name: m.status for m in profiles.migrate_profiles(prompts, PROFILES)}
    assert report == expected
    assert not (tmp_path / "nowhere.md").exists()  # the dangling link was not followed
    assert (dest / "alpha.md").is_dir()
    assert (dest / "gamma.md").read_text("utf-8") == "gamma"
    assert not (tmp_path / "nowhere.md").exists()


def test_git_pristine_profiles_needs_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="git is not installed"):
        profiles.git_pristine_profiles(tmp_path)


# An editable install reads the match files from the checkout's espanso/match/.
def test_match_files_from_the_checkout() -> None:
    expected = sorted(p.name for p in (REPO / "espanso" / "match").glob("*.yml"))
    assert assets.match_names() == expected
    for name in expected:
        assert assets.read_match(name) == (REPO / "espanso" / "match" / name).read_text("utf-8")
    with pytest.raises(ValueError, match=r"No match file named 'default\.yml'"):
        assets.read_match("default.yml")


# A wheel carries them at prompt_workflow/espanso/match/, which wins over the checkout.
def test_match_files_from_the_package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    packaged = tmp_path / "pkg" / "espanso" / "match"
    packaged.mkdir(parents=True)
    (packaged / "only.yml").write_text("matches: []\n", "utf-8")
    monkeypatch.setattr(assets, "files", lambda package: tmp_path / "pkg")
    assert assets.match_names() == ["only.yml"]
    assert assets.read_match("only.yml") == "matches: []\n"


def test_match_files_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(assets, "files", lambda package: tmp_path)
    monkeypatch.setattr(assets, "_PROJECT_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError, match="missing from this installation"):
        assets.match_dir()


# The wheel ships the match files and nothing else from espanso/ (#37 removed espanso/config/).
def test_wheel_declares_match_files_only() -> None:
    import tomllib

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))
    force = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force == {"espanso/match": "prompt_workflow/espanso/match"}


# A refusal with nothing at the target (a read-only folder) is a real error, not "exists".
def test_migrate_reraises_when_nothing_is_there(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prompts = _checkout(tmp_path)
    (prompts / "alpha.md").write_text("alpha", "utf-8")
    real_open = Path.open

    def refuse(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        if mode == "xb":
            raise PermissionError(13, "Permission denied")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse)
    with pytest.raises(PermissionError):
        profiles.migrate_profiles(prompts, PROFILES)
