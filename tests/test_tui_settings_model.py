"""The Settings tab's model (#199), without the interface: every setting listed once, every
choice one the CLI accepts, the dot rules, and the persona never shown."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from promptmend import config
from promptmend.config import ConfigLayers, env_names, secret_names
from promptmend.prompt_builder import PROFILES, UserProfile
from promptmend.tui import settings_model as model
from promptmend.tui.state import State

PERSONA = "I am the head of a secret project"


def _state(environ: dict[str, str], profiles: list[UserProfile] | None = None) -> State:
    layers = ConfigLayers.resolve(environ, strict=False)
    return State(
        layers=layers,
        settings=layers.settings(),
        report=None,  # type: ignore[arg-type]  # rows() reads only the layers and profiles
        triggers=[],
        plan=None,
        plan_error=None,
        profiles=profiles or [],
        group_by="trigger",
        stats=[],
        stats_error=None,
    )


def test_every_setting_is_listed_exactly_once() -> None:
    names = [meta.name for meta in model.SETTINGS]
    assert sorted(names) == sorted(env_names())
    assert len(names) == len(set(names))
    assert {m.name for m in model.SETTINGS if m.kind == model.KEY} == set(secret_names())
    assert {m.group for m in model.SETTINGS} == set(model.GROUPS)
    # Each group's rows are together, in the groups' order.
    order = [m.group for m in model.SETTINGS]
    assert sorted(order, key=model.GROUPS.index) == order
    assert all(m.help and "\n" not in m.help for m in model.SETTINGS)


@pytest.mark.parametrize("meta", model.SETTINGS, ids=lambda m: m.name.lower())
def test_every_choice_and_default_parses(meta: model.Meta) -> None:
    profiles = [UserProfile("mine", Path("mine.md"), "added")]
    for choice in model.choices(meta, _state({}, profiles)):
        model.parse(meta.name, choice)
    if meta.kind == model.BOOL:
        assert model.default(meta.name) in ("true", "false")
    if meta.kind == model.CHOICE:
        assert model.choices(meta, None)
    model.parse(meta.name, model.default(meta.name))


def test_profile_choices_are_the_builtins_and_your_added_ones() -> None:
    profiles = [
        UserProfile("mine", Path("mine.md"), "added"),
        UserProfile("default", Path("default.md"), "shadowed"),
    ]
    state = _state({}, profiles)
    assert model.choices(model.BY_NAME["PROMPT_PROFILE"], state) == (*PROFILES, "mine")
    assert model.choices(model.BY_NAME["PROMPT_PRO_PROFILE"], state) == ("", *PROFILES, "mine")
    assert model.choices(model.BY_NAME["PROMPT_PROFILE"], None) == tuple(PROFILES)
    assert model.choices(model.BY_NAME["PROMPT_OUTPUT"], state) == config.OUTPUTS


def test_a_bad_value_is_refused_naming_the_setting() -> None:
    with pytest.raises(ValueError, match="PROMPT_HISTORY_RETENTION_DAYS must be"):
        model.parse("PROMPT_HISTORY_RETENTION_DAYS", "0")


def test_dot_rules() -> None:
    environ = {
        "PROMPT_OUTPUT": "clipboard",
        "PROMPT_LOCAL_ONLY": "false",
        "PROMPT_HISTORY": "false",
        "OPENROUTER_API_KEY": "-".join(("some", "key")),
        "PROMPT_PERSONA": PERSONA,
    }
    rows = {r.name: r for r in model.rows(_state(environ))}

    def seen(name: str) -> tuple[str, bool, str]:
        return rows[name].value, rows[name].on, rows[name].source

    # A switch: green when on, whatever its default; grey when off.
    assert seen("PROMPT_UPDATE_CHECK") == ("on", True, "default")
    assert seen("PROMPT_HISTORY") == ("off", False, "environment")
    assert seen("PROMPT_LOCAL_ONLY") == ("off", False, "environment")
    # A value: green when not the default or set somewhere; grey at its default.
    assert seen("PROMPT_OUTPUT") == ("clipboard", True, "environment")
    assert seen("PROMPT_PROVIDER") == ("openrouter", False, "default")
    assert seen("PROMPT_EXTRA_PATTERNS") == ("(empty)", False, "default")
    # Keys: set or not set, never the value; an unset key has no source.
    assert seen("OPENROUTER_API_KEY") == ("set", True, "environment")
    assert seen("ANTHROPIC_API_KEY") == ("not set", False, "")
    assert rows["ANTHROPIC_API_KEY"].where == ""
    # The persona: set, hidden.
    assert seen("PROMPT_PERSONA") == ("<set, hidden>", True, "environment")
    assert all(PERSONA not in r.value for r in rows.values())


def test_a_value_from_a_file_shows_its_file_name(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("PROMPT_PROFILE=general\n", encoding="utf-8")
    state = _state({"PROMPTMEND_ENV": str(env_file)})
    row = next(r for r in model.rows(state) if r.name == "PROMPT_PROFILE")
    assert (row.source, row.on) == (".env", True)
    assert row.where == str(env_file)
    assert model.source_name("file:/x/config.toml") == "config.toml"


def test_a_value_set_to_its_default_is_still_green() -> None:
    rows = {r.name: r for r in model.rows(_state({"PROMPT_PROVIDER": "openrouter"}))}
    assert rows["PROMPT_PROVIDER"].on


def test_the_filter_matches_name_and_group_in_any_case() -> None:
    state = _state({})
    assert [r.name for r in model.rows(state, "HiSt")] == [
        "PROMPT_HISTORY",
        "PROMPT_HISTORY_RETENTION_DAYS",
    ]
    assert {r.meta.group for r in model.rows(state, "privacy")} == {"Privacy"}
    assert model.rows(state, "  ") == model.rows(state)
    assert model.rows(state, "nothing-like-this") == []


def test_rows_are_frozen() -> None:
    row = model.rows(_state({}))[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.value = "x"  # type: ignore[misc]
