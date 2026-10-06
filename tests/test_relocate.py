"""The user folders move from `prompt-workflow` to `promptmend` (#169): the folder rule
(config.folder_in_use), relocate.migrate_folders(), when the CLI runs it, doctor's `folders`
check, and PROMPTMEND_ENV with its alias PROMPT_WORKFLOW_ENV. Every folder is under conftest's
per-test XDG_CONFIG_HOME/APPDATA and XDG_DATA_HOME/LOCALAPPDATA."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner, Result

from prompt_workflow import config, config_files, config_store, deploy, doctor, relocate
from prompt_workflow.cli import app
from prompt_workflow.config import Settings
from prompt_workflow.config_files import FileSecretStore

if TYPE_CHECKING:
    from conftest import FakeHttp, HistoryRows

runner = CliRunner()
# Built at runtime, so no key-shaped literal lands in the repo (gitleaks).
KEY = "-".join(["sk", "or", "v1", "5e17e1" * 10])
MOVED_SETTINGS = "moved the settings folder"


class Folders:
    """The new and legacy config and data folders of this test."""

    def __init__(self) -> None:
        self.config, self.old_config = config.config_folders()
        self.data, self.old_data = config.data_folders()

    def legacy(self) -> Folders:
        """Set up what 0.18.0 left: config.toml and a private secrets.toml in the legacy config
        folder, a file in the legacy data folder."""
        self.old_config.mkdir(parents=True)
        (self.old_config / "config.toml").write_text(
            'config_version = 1\nOLLAMA_MODEL = "from-legacy"\n', encoding="utf-8"
        )
        FileSecretStore(self.old_config / "secrets.toml").set("OPENROUTER_API_KEY", KEY)
        self.old_data.mkdir(parents=True)
        (self.old_data / "history.lost").write_text("1\n", encoding="utf-8")
        return self


@pytest.fixture
def folders(monkeypatch: pytest.MonkeyPatch) -> Folders:
    """Saved mode (no PROMPTMEND_ENV), so the config folder is read."""
    monkeypatch.delenv("PROMPTMEND_ENV")
    return Folders()


def _tree(*roots: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()}


# --- the folder rule ------------------------------------------------------------------------


def test_new_folder_unless_only_the_legacy_one_exists(folders: Folders) -> None:
    assert config._user_config_dir() == folders.config
    assert config.user_data_dir() == folders.data
    assert not folders.config.exists()  # deciding never creates anything
    folders.legacy()
    assert config._user_config_dir() == folders.old_config
    assert config.user_data_dir() == folders.old_data
    assert not folders.config.exists()
    folders.config.mkdir()
    assert config._user_config_dir() == folders.config
    assert config.user_data_dir() == folders.old_data


def test_folders_follow_the_platform_variables(tmp_path: Path) -> None:
    if os.name == "nt":
        environ = {"APPDATA": str(tmp_path / "roaming"), "LOCALAPPDATA": str(tmp_path / "local")}
        assert config.config_folders(environ) == (
            tmp_path / "roaming" / "promptmend",
            tmp_path / "roaming" / "prompt-workflow",
        )
        assert config.data_folders(environ)[0] == tmp_path / "local" / "promptmend"
    else:
        environ = {"XDG_CONFIG_HOME": str(tmp_path / "c"), "XDG_DATA_HOME": str(tmp_path / "d")}
        assert config.config_folders(environ) == (
            tmp_path / "c" / "promptmend",
            tmp_path / "c" / "prompt-workflow",
        )
        assert config.data_folders(environ)[1] == tmp_path / "d" / "prompt-workflow"


# --- migrate_folders --------------------------------------------------------------------------


def test_legacy_folders_are_renamed_once(folders: Folders) -> None:
    folders.legacy()
    before = Settings.load()
    messages = relocate.migrate_folders()
    assert messages == [
        f"moved the settings folder {folders.old_config} to {folders.config}",
        f"moved the data folder {folders.old_data} to {folders.data}",
    ]
    assert not folders.old_config.exists()
    assert not folders.old_data.exists()
    assert (folders.data / "history.lost").read_text("utf-8") == "1\n"
    assert Settings.load() == before
    assert before.ollama_model == "from-legacy"
    if os.name != "nt":
        # A rename, not a copy: the key file keeps its mode 600.
        mode = stat.S_IMODE((folders.config / "secrets.toml").stat().st_mode)
        assert mode == 0o600
    # The second run finds nothing to do and changes nothing.
    tree = _tree(folders.config, folders.data)
    assert relocate.migrate_folders() == []
    assert _tree(folders.config, folders.data) == tree


def test_nothing_to_move_without_legacy_folders(folders: Folders) -> None:
    assert relocate.migrate_folders() == []
    assert not folders.config.exists()
    assert not folders.data.exists()


def test_merge_moves_what_is_missing_and_never_overwrites(folders: Folders) -> None:
    folders.legacy()
    (folders.old_config / "profiles").mkdir()
    (folders.old_config / "profiles" / "mine.md").write_text("mine", encoding="utf-8")
    folders.config.mkdir(parents=True)
    (folders.config / "config.toml").write_text("config_version = 1\n", encoding="utf-8")

    messages = relocate.migrate_folders()
    assert messages[:2] == [
        f"moved profiles, secrets.toml from the settings folder {folders.old_config} "
        f"to {folders.config}",
        f"kept in {folders.old_config}, since {folders.config} has its own "
        "(never overwritten): config.toml",
    ]
    assert (folders.config / "config.toml").read_text("utf-8") == "config_version = 1\n"
    assert "from-legacy" in (folders.old_config / "config.toml").read_text("utf-8")
    assert (folders.config / "profiles" / "mine.md").read_text("utf-8") == "mine"
    assert (folders.config / "secrets.toml").is_file()
    # Only conflicts left: silent from now on (doctor reports them), and still kept.
    assert relocate.migrate_folders() == []
    assert (folders.old_config / "config.toml").is_file()
    # Once the conflict is resolved by hand, the empty legacy folder goes.
    (folders.old_config / "config.toml").unlink()
    assert relocate.migrate_folders() == []
    assert not folders.old_config.exists()


def test_a_file_with_the_new_name_keeps_the_legacy_folder(folders: Folders) -> None:
    folders.legacy()
    folders.config.parent.mkdir(parents=True, exist_ok=True)
    folders.config.write_text("not a folder", encoding="utf-8")
    assert MOVED_SETTINGS not in " ".join(relocate.migrate_folders())
    assert config._user_config_dir() == folders.old_config


def test_a_failed_move_is_reported_and_retried(
    folders: Folders, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders.legacy()
    tree = _tree(folders.old_config, folders.old_data)

    def locked(source: object, target: object) -> None:
        raise PermissionError(13, "Permission denied")

    rename = os.rename
    monkeypatch.setattr(os, "rename", locked)
    messages = relocate.migrate_folders()
    assert messages == [
        f"could not move {folders.old_config} to {folders.config} (Permission denied); "
        "the next command retries",
        f"could not move {folders.old_data} to {folders.data} (Permission denied); "
        "the next command retries",
    ]
    assert _tree(folders.old_config, folders.old_data) == tree
    assert config._user_config_dir() == folders.old_config
    monkeypatch.setattr(os, "rename", rename)
    assert len(relocate.migrate_folders()) == 2
    assert config._user_config_dir() == folders.config


def test_the_environ_parameter_picks_the_folders(tmp_path: Path, folders: Folders) -> None:
    other = {"XDG_CONFIG_HOME": str(tmp_path / "c"), "XDG_DATA_HOME": str(tmp_path / "d")}
    other |= {"APPDATA": other["XDG_CONFIG_HOME"], "LOCALAPPDATA": other["XDG_DATA_HOME"]}
    (tmp_path / "c" / "prompt-workflow").mkdir(parents=True)
    folders.legacy()
    assert relocate.migrate_folders(other) == [
        f"moved the settings folder {tmp_path / 'c' / 'prompt-workflow'} "
        f"to {tmp_path / 'c' / 'promptmend'}"
    ]
    assert folders.old_config.is_dir()  # the process's own folders were not touched


# --- migration.json -------------------------------------------------------------------------


def test_marker_paths_follow_the_move_and_rollback_still_works(folders: Folders) -> None:
    # A 0.18.0 migration of the .env in the legacy config folder, rolled back after the move.
    folders.old_config.mkdir(parents=True)
    env_text = "OLLAMA_MODEL=from-env\n"
    (folders.old_config / ".env").write_text(env_text, encoding="utf-8")
    config_store.apply_migration(consent=config_store.plan_migration().token)
    marker = folders.old_config / config_store.MARKER_FILE
    assert json.loads(marker.read_text("utf-8"))["backup"].startswith(str(folders.old_config))

    relocate.migrate_folders()
    record = json.loads((folders.config / config_store.MARKER_FILE).read_text("utf-8"))
    text = json.dumps(record)
    assert str(folders.old_config) not in text.replace("\\\\", "\\")
    assert record["backup"].startswith(str(folders.config))
    assert record["sources"][0]["from"] == str(folders.config / ".env")
    assert Settings.load().ollama_model == "from-env"

    plan = config_store.plan_rollback()
    config_store.apply_rollback(consent=plan.token)
    assert (folders.config / ".env").read_text("utf-8") == env_text
    assert not (folders.config / config_store.MARKER_FILE).exists()
    assert Settings.load().ollama_model == "from-env"


def test_a_damaged_marker_is_left_alone(folders: Folders) -> None:
    folders.config.mkdir(parents=True)
    damaged = f'{{"backup": "{folders.old_config}/backups"'.encode()
    (folders.config / config_store.MARKER_FILE).write_bytes(damaged)
    assert relocate.migrate_folders() == []
    assert (folders.config / config_store.MARKER_FILE).read_bytes() == damaged


def test_a_marker_write_that_fails_is_reported(
    folders: Folders, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders.config.mkdir(parents=True)
    record = {"backup": str(folders.old_config / "backups"), "sources": []}
    (folders.config / config_store.MARKER_FILE).write_text(json.dumps(record), "utf-8")

    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(config_files, "write_atomic", refuse)
    assert relocate.migrate_folders() == [
        f"could not update {config_store.MARKER_FILE} (Permission denied); the next command retries"
    ]


def test_rebase_touches_only_paths_under_the_legacy_folder() -> None:
    old, new = "/h/.config/prompt-workflow", "/h/.config/promptmend"
    record = {
        "backup": f"{old}/backups/migration-1",
        "sources": [
            {"from": f"{old}/.env", "to": f"{old}/backups/m/.env", "sha256": "ab"},
            {"from": "/repo/.env", "root": "/repo", "in_place": f"{old}-x/.env", "n": 2},
        ],
        "exact": old,
    }
    assert relocate._rebase(record, old, new) == {
        "backup": f"{new}/backups/migration-1",
        "sources": [
            {"from": f"{new}/.env", "to": f"{new}/backups/m/.env", "sha256": "ab"},
            {"from": "/repo/.env", "root": "/repo", "in_place": f"{old}-x/.env", "n": 2},
        ],
        "exact": new,
    }
    roaming = r"C:\Users\me\AppData\Roaming"
    moved = relocate._rebase(
        [rf"{roaming}\prompt-workflow\.env"],
        rf"{roaming}\prompt-workflow",
        rf"{roaming}\promptmend",
    )
    assert moved == [rf"{roaming}\promptmend\.env"]


# --- the CLI: which commands move the folders -------------------------------------------------


def _improve() -> Result:
    return runner.invoke(
        app, ["improve", "--provider", "ollama", "--source", "argument", "--text", "d"]
    )


def test_the_trigger_path_stays_on_the_legacy_folders(
    folders: Folders, fake_http: FakeHttp, history_rows: HistoryRows
) -> None:
    folders.legacy()
    fake_http.reply({"message": {"content": "ok"}})
    result = _improve()
    assert (result.exit_code, result.stdout, result.stderr) == (0, "ok", "")
    # Its settings came from the legacy config.toml, its history went to the legacy folder.
    assert fake_http.calls[0]["json"]["model"] == "from-legacy"
    assert len(history_rows("operations")) == 1
    assert (folders.old_data / "history.sqlite3").is_file()
    assert deploy.Manifest.load().path.parent == folders.old_data
    assert not folders.config.exists()
    assert not folders.data.exists()


@pytest.mark.parametrize(
    "args",
    [["persona"], ["--help"], ["config", "--help"], ["config", "show", "-h"], ["--version"]],
)
def test_these_commands_never_move_a_folder(folders: Folders, args: list[str]) -> None:
    folders.legacy()
    result = runner.invoke(app, args)
    assert MOVED_SETTINGS not in result.stderr
    assert folders.old_config.is_dir()
    assert not folders.config.exists()


def test_improve_never_moves_a_folder(folders: Folders, fake_http: FakeHttp) -> None:
    folders.legacy()
    fake_http.reply({"message": {"content": "ok"}})
    assert _improve().stdout == "ok"
    assert folders.old_config.is_dir()


@pytest.mark.parametrize("args", [["config", "show"], ["doctor", "--no-clipboard"]])
def test_management_commands_move_the_folders_first(
    folders: Folders, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.setattr(deploy, "run_command", lambda argv: None)
    folders.legacy()
    result = runner.invoke(app, args)
    assert result.stderr.splitlines()[:2] == [
        f"moved the settings folder {folders.old_config} to {folders.config}",
        f"moved the data folder {folders.old_data} to {folders.data}",
    ]
    assert not folders.old_config.exists()
    assert str(folders.config / "config.toml") in result.stdout
    again = runner.invoke(app, args)
    assert "moved" not in again.stderr


# A bare `prompt-workflow` on a terminal opens the interface, which moves them too.
def test_the_interface_moves_the_folders(folders: Folders, monkeypatch: pytest.MonkeyPatch) -> None:
    from prompt_workflow.commands import common
    from prompt_workflow.tui.app import ManageApp

    monkeypatch.setattr(common, "stdin_is_tty", lambda: True)
    monkeypatch.setattr(common, "stdout_is_tty", lambda: True)
    monkeypatch.setattr(ManageApp, "run", lambda self, *args, **kwargs: None)
    folders.legacy()
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert MOVED_SETTINGS in result.stderr
    assert folders.config.is_dir()


def test_an_unknown_command_moves_nothing(folders: Folders) -> None:
    folders.legacy()
    assert runner.invoke(app, ["bogus"]).exit_code == 2
    assert folders.old_config.is_dir()


# --- doctor's folders check --------------------------------------------------------------------


def test_doctor_folders_check(folders: Folders) -> None:
    check = doctor._folders_check()
    assert (check.id, check.status) == ("folders", doctor.OK)
    assert check.data["legacy"] == check.data["conflicts"] == []

    folders.legacy()
    check = doctor._folders_check()
    assert check.status == doctor.WARN
    assert check.data["legacy"] == [str(folders.old_config), str(folders.old_data)]
    assert "the move failed" in check.message

    folders.config.mkdir(parents=True)
    (folders.config / "config.toml").write_text("config_version = 1\n", encoding="utf-8")
    relocate.migrate_folders()
    check = doctor._folders_check()
    assert check.status == doctor.WARN
    assert check.data["config_dir"] == str(folders.config)
    assert check.data["legacy"] == []
    assert check.data["conflicts"] == [str(folders.old_config / "config.toml")]


# --- PROMPTMEND_ENV and its alias ------------------------------------------------------------


def _env_file(tmp_path: Path, name: str, model: str) -> str:
    path = tmp_path / name
    path.write_text(f"OLLAMA_MODEL={model}\n", encoding="utf-8")
    return str(path)


def test_the_old_variable_still_names_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PROMPTMEND_ENV")
    monkeypatch.setenv("PROMPT_WORKFLOW_ENV", _env_file(tmp_path, "old.env", "old"))
    assert Settings.load().ollama_model == "old"
    assert config.env_file_var() == "PROMPT_WORKFLOW_ENV"
    # Messages name the variable in effect.
    result = runner.invoke(app, ["config", "set", "OLLAMA_MODEL", "x"])
    assert "PROMPT_WORKFLOW_ENV is set" in result.stderr
    # An empty new name counts as unset.
    monkeypatch.setenv("PROMPTMEND_ENV", "")
    assert Settings.load().ollama_model == "old"


def test_the_new_variable_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTMEND_ENV", _env_file(tmp_path, "new.env", "new"))
    monkeypatch.setenv("PROMPT_WORKFLOW_ENV", _env_file(tmp_path, "old.env", "old"))
    assert Settings.load().ollama_model == "new"
    assert config.env_file_override() == str(tmp_path / "new.env")
    result = runner.invoke(app, ["config", "set", "OLLAMA_MODEL", "x"])
    assert "PROMPTMEND_ENV is set" in result.stderr


# --- review round: the edge cases ------------------------------------------------------------


def test_an_env_file_inside_the_legacy_folder_keeps_it(
    folders: Folders, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders.legacy()
    named = folders.old_config / ".env"
    named.write_text("OLLAMA_MODEL=named\n", encoding="utf-8")
    monkeypatch.setenv("PROMPT_WORKFLOW_ENV", str(named))
    messages = relocate.migrate_folders()
    assert messages == [
        f"PROMPT_WORKFLOW_ENV names a file inside {folders.old_config}, so that folder was not "
        f"moved: point PROMPT_WORKFLOW_ENV at the same file under {folders.config}, then run "
        "the command again",
        f"moved the data folder {folders.old_data} to {folders.data}",
    ]
    assert named.is_file()
    assert Settings.load().ollama_model == "named"
    check = doctor._folders_check()
    assert check.status == doctor.WARN
    assert "PROMPT_WORKFLOW_ENV names a file inside" in check.message
    # Repointed (the file moved by hand), the next command moves the folder.
    monkeypatch.setenv("PROMPT_WORKFLOW_ENV", str(folders.config / ".env"))
    assert relocate.migrate_folders() == [
        f"moved the settings folder {folders.old_config} to {folders.config}"
    ]
    assert Settings.load().ollama_model == "named"


def test_an_env_file_elsewhere_does_not_hold_the_move(
    folders: Folders, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders.legacy()
    monkeypatch.setenv("PROMPTMEND_ENV", str(tmp_path / "elsewhere.env"))
    assert MOVED_SETTINGS in relocate.migrate_folders()[0]


def _files(folder: Path, *names: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_text(name, encoding="utf-8")


def test_sqlite_and_lock_companions_move_with_their_file(folders: Folders) -> None:
    unit = ("history.sqlite3", "history.sqlite3-wal", "history.sqlite3-shm")
    lost = ("history.lost", "history.lost.lock", "history.lost.123.tmp")
    _files(folders.old_data, *unit, *lost, "espanso-manifest.json")
    # The new folder already has its own database and a stale lock of its lost counter.
    _files(folders.data, "history.sqlite3", "history.lost.lock.ab12.stale")
    relocate.migrate_folders()
    assert sorted(p.name for p in folders.old_data.iterdir()) == sorted((*unit, *lost))
    assert (folders.data / "history.sqlite3").read_text("utf-8") == "history.sqlite3"
    assert not (folders.data / "history.sqlite3-wal").exists()
    assert (folders.data / "espanso-manifest.json").is_file()


def test_an_orphan_journal_never_joins_another_database(folders: Folders) -> None:
    _files(folders.old_data, "history.sqlite3-wal", "history.sqlite3-journal")
    _files(folders.data, "history.sqlite3")
    assert relocate.migrate_folders() == []
    assert not (folders.data / "history.sqlite3-wal").exists()


def test_a_whole_unit_moves_when_the_new_folder_lacks_it(folders: Folders) -> None:
    _files(folders.old_data, "history.sqlite3", "history.sqlite3-wal", "history.lost")
    _files(folders.data, "other")
    relocate.migrate_folders()
    assert not folders.old_data.exists()
    assert {p.name for p in folders.data.iterdir()} == {
        "other",
        "history.sqlite3",
        "history.sqlite3-wal",
        "history.lost",
    }


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlinked_legacy_folder_is_left_alone(folders: Folders, tmp_path: Path) -> None:
    real = tmp_path / "elsewhere"
    _files(real, "config.toml")
    folders.old_config.parent.mkdir(parents=True)
    folders.old_config.symlink_to(real, target_is_directory=True)
    assert relocate.migrate_folders() == []
    assert folders.old_config.is_symlink()
    assert config._user_config_dir() == folders.old_config  # still the triggers' folder
    check = doctor._folders_check()
    assert check.status == doctor.WARN
    assert f"{folders.old_config} is a symlink" in check.message
    # Even next to a new folder: no merge through it, no removal.
    _files(folders.config, "secrets.toml")
    assert relocate.migrate_folders() == []
    assert folders.old_config.is_symlink()
    assert (real / "config.toml").is_file()
    assert not (folders.config / "config.toml").exists()


def test_a_concurrent_move_is_not_reported(
    folders: Folders, monkeypatch: pytest.MonkeyPatch
) -> None:
    folders.legacy()

    def gone(source: object, target: object) -> None:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(os, "rename", gone)
    assert relocate.migrate_folders() == []
    folders.config.mkdir(parents=True)
    assert relocate.migrate_folders() == []


def test_doctor_names_a_file_in_the_way(folders: Folders) -> None:
    folders.legacy()
    folders.config.write_text("in the way", encoding="utf-8")
    check = doctor._folders_check()
    assert check.status == doctor.WARN
    assert f"{folders.config} is not a folder, so {folders.old_config} stays in use" in (
        check.message
    )
    assert "the move failed" not in check.message.split(";")[0]
