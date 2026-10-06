"""SVG snapshots of every tab of the interface (#93), from a fixed State at a fixed terminal
size and theme, compared with the committed SVGs in tests/snapshots/. The Home and Try snapshots
are also the README's screenshots (docs/interface.svg, docs/try.svg).

After an intended change, regenerate and review them:

    UPDATE_SNAPSHOTS=1 uv run pytest tests/test_tui_snapshots.py

Linux and macOS only: Windows renders the same screens, but its snapshots were never checked,
so its Pilot tests (tests/test_tui.py) cover it instead.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Button

from promptmend import assets, deploy, doctor, previous_install
from promptmend.config import ConfigLayers
from promptmend.history import StatsRow
from promptmend.prompt_builder import UserProfile
from promptmend.tui import app as app_module
from promptmend.tui import brand
from promptmend.tui.app import HIGH_CONTRAST, ManageApp
from promptmend.tui.console import CommandLine, Ran
from promptmend.tui.state import State

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="snapshots are kept for Linux and macOS only (#93)"
)

REPO = Path(__file__).resolve().parents[1]
SNAPSHOTS = Path(__file__).parent / "snapshots"
README_SHOTS = {"home": REPO / "docs" / "interface.svg", "try": REPO / "docs" / "try.svg"}
SIZE = (110, 36)
HOME = "/home/me"
ESPANSO = Path(f"{HOME}/.config/espanso")
LAUNCHER = f"{HOME}/.local/bin/promptmend"
# The header's version (#112), pinned so a release regenerates no snapshot: the release that
# ships the README's screenshots.
VERSION = "0.20.0"

_MESSAGES = {
    "version": (doctor.INFO, f"promptmend {VERSION}"),
    "cli": (doctor.INFO, f"running {LAUNCHER}"),
    "install": (doctor.OK, f"uv: {LAUNCHER}"),
    "config": (doctor.OK, f"valid (saved: {HOME}/.config/promptmend/config.toml)"),
    "keys": (doctor.OK, "OPENROUTER_API_KEY: set, ANTHROPIC_API_KEY: not set"),
    "persona": (doctor.OK, "PROMPT_PERSONA matches no data-protection pattern"),
    "espanso": (doctor.OK, f"running (config: {ESPANSO})"),
    "match_files": (doctor.WARN, "some files are not ours as deployed"),
    "launcher": (doctor.OK, f"the deployed matches call {LAUNCHER}"),
    "history": (doctor.OK, f"57 call(s) recorded in {HOME}/.local/share/promptmend"),
    "sqlite": (doctor.OK, "SQLite 3.51.3"),
    "clipboard": (doctor.INFO, "skipped (--no-clipboard)"),
    "profiles": (doctor.OK, "PROMPT_PROFILE default resolves"),
    "previous_install": (doctor.OK, "settings are in place; nothing to look for"),
}
_DATA: dict[str, dict[str, Any]] = {
    "install": {"channel": "uv", "launcher": LAUNCHER, "editable": False},
    "espanso": {"found": True, "running": True, "query_failed": False},
    "history": {"lost_writes": 0, "last_lost_utc": None, "tracking_incomplete": False},
    "sqlite": {"version": "3.51.3", "wal_reset_bug": False},
}
_STATES = {
    "prompts-core.yml": deploy.IN_SYNC,
    "prompts-llm.yml": deploy.MODIFIED,
    "prompts-template.yml": deploy.IN_SYNC,
}


def _stats() -> list[StatsRow]:
    def tokens(inputs: int | None, output: int | None) -> dict[str, int | None]:
        return {
            "input_uncached": inputs,
            "cache_read": None,
            "cache_write": None,
            "output": output,
            "reasoning": None,
        }

    return [
        StatsRow(
            "-i-",
            42,
            43,
            "2026-10-04T09:12:31.000000Z",
            1840.0,
            3120.0,
            tokens(52_400, 9_150),
            reported={"credits": Decimal("0.0412")},
        ),
        StatsRow(
            "-ip-",
            9,
            9,
            "2026-10-03T16:40:02.000000Z",
            6210.0,
            9480.0,
            tokens(11_300, 6_020),
            reported={"credits": Decimal("0.0870")},
            unknown_cost_attempts=1,
        ),
        StatsRow(
            "-il-",
            6,
            6,
            "2026-10-01T08:05:44.000000Z",
            4105.0,
            5230.0,
            tokens(None, None),
            not_applicable_attempts=6,
        ),
    ]


def fixed_state(
    group_by: str = "trigger", previous: previous_install.Detection | None = None
) -> State:
    environ = {
        "XDG_CONFIG_HOME": f"{HOME}/.config",
        "HOME": HOME,
        # Built at runtime, so no key-shaped literal lands in the repo; shown only as "set".
        "OPENROUTER_API_KEY": "-".join(("snapshot", "key")),
    }
    layers = ConfigLayers.resolve(environ, strict=False)
    report = doctor.Report(
        tuple(
            doctor.Check(id_, status, message, _DATA.get(id_, {}))
            for id_, (status, message) in _MESSAGES.items()
        )
    )
    steps = [
        deploy.FileStep(name, ESPANSO / "match" / name, state, None, "", True, None)
        for name, state in _STATES.items()
    ]
    plan = deploy.Plan(
        ESPANSO,
        LAUNCHER,
        steps,
        None,
        deploy.Manifest(Path("manifest.json")),
        yours=[ESPANSO / "match" / "base.yml"],
    )
    profiles = [UserProfile("mine", Path(f"{HOME}/.config/promptmend/profiles/mine.md"), "added")]
    return State(
        layers=layers,
        settings=layers.settings(),
        report=report,
        triggers=assets.triggers(),
        plan=plan,
        plan_error=None,
        profiles=profiles,
        group_by=group_by,
        stats=_stats(),
        stats_error=None,
        previous=previous or previous_install.Detection(),
    )


def previous_state(group_by: str = "trigger") -> State:
    """An earlier checkout install found through the old match files (#110)."""
    root = Path(f"{HOME}/Projects/promptmend")
    launcher = f"{root}/.venv/bin/promptmend"
    candidate = previous_install.Candidate(
        root=root,
        signals=frozenset({previous_install.LAUNCHER, previous_install.RECEIPT}),
        env_file=root / ".env",
        launchers_in_root=(launcher,),
        profiles_dir=root / "src" / "promptmend" / "prompts",
    )
    return fixed_state(group_by, previous_install.Detection(candidates=(candidate,)))


@pytest.fixture(autouse=True)
def fixed_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """The paths a pane reads itself (the history file, the profile folder) are fixed too,
    and so is the version in the header."""
    monkeypatch.setattr(app_module, "__version__", VERSION)
    # About's runtime line (#112) differs by CI runner.
    monkeypatch.setattr(brand, "runtime", lambda: {"python": "3.14.0", "textual": "8.2.8"})
    monkeypatch.delenv("PROMPTMEND_ENV")
    monkeypatch.setenv("XDG_CONFIG_HOME", f"{HOME}/.config")
    monkeypatch.setenv("XDG_DATA_HOME", f"{HOME}/.local/share")


def _normalize(svg: str) -> str:
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


def _check(name: str, svg: str, *also: Path) -> None:
    svg = _normalize(svg)
    targets = [SNAPSHOTS / f"{name}.svg", *also]
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(svg, encoding="utf-8", newline="\n")
        return
    for target in targets:
        assert target.is_file(), f"{target} is missing; run UPDATE_SNAPSHOTS=1"
        assert target.read_text("utf-8") == svg, (
            f"{target.name} differs: review the change, then regenerate with "
            "UPDATE_SNAPSHOTS=1 uv run pytest tests/test_tui_snapshots.py"
        )


def _shoot(
    key: str | None,
    theme: str | None = None,
    loader: Callable[[str], State] = fixed_state,
    size: tuple[int, int] = SIZE,
    intro: bool = False,
    typed: str = "",
    ran: Ran | None = None,
    button: str | None = None,
) -> str:
    app = ManageApp(loader=loader, intro=intro)
    # The active tab's underline slides into place; a snapshot must not catch it midway.
    app.animation_level = "none"
    shots: list[str] = []

    async def main() -> None:
        async with app.run_test(size=size) as pilot:
            for _ in range(3):
                await pilot.pause()
                await app.workers.wait_for_complete()
            if theme:
                app.theme = theme
            if key:
                await pilot.press(key)
            if typed:
                # A steady cursor, so the shot does not depend on the blink.
                line = app.main.query_one(CommandLine)
                line.cursor_blink = False
                if ran is not None:  # Enter runs the line through a fake: no child
                    line.runner = lambda argv: ran
                await pilot.press(*typed, *(["enter"] if ran is not None else []))
                for _ in range(3):
                    await pilot.pause()
                    await app.workers.wait_for_complete()
            if button:
                app.main.query_one(button, Button).press()
                for _ in range(3):
                    await pilot.pause()
                    await app.workers.wait_for_complete()
            await pilot.pause()
            shots.append(app.export_screenshot(title=brand.NAME))

    asyncio.run(main())
    return shots[0]


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("home", "1"),
        ("providers", "2"),
        ("profiles", "3"),
        ("triggers", "4"),
        ("history", "5"),
        ("diagnostics", "6"),
        ("try", "7"),
    ],
)
def test_snapshot(name: str, key: str) -> None:
    _check(name, _shoot(key), *([README_SHOTS[name]] if name in README_SHOTS else []))


def test_snapshot_high_contrast() -> None:
    _check("home-high-contrast", _shoot("1", HIGH_CONTRAST.name))


def test_snapshot_previous_install() -> None:
    _check("previous-install", _shoot(None, loader=previous_state))


# Home on the smallest common terminal (#112): a row's text is cut, never wrapped.
def test_snapshot_home_80_columns() -> None:
    _check("home-80", _shoot("1", size=(80, 24)))


# The header's status pill comes first (#174), so a 70-column terminal still shows it.
def test_snapshot_home_70_columns() -> None:
    _check("home-70", _shoot("1", size=(70, 24)))


# Home's command line (#111): `c`, a half-typed command, its suggestion and help.
def test_snapshot_home_console() -> None:
    _check("home-console", _shoot("c", typed="espanso st"))


# A run from the command line (#111): its output below the help line, logged in the session.
SECRETS_STATUS = (
    "OPENROUTER_API_KEY: set (secret store)\nANTHROPIC_API_KEY: not set\nGROQ_API_KEY: not set\n"
)


def test_snapshot_home_output() -> None:
    _check("home-output", _shoot("c", typed="secrets status", ran=Ran(0, SECRETS_STATUS)))


# Home's recipes (#111): picking one puts it on the command line; Cancel has the focus.
def test_snapshot_home_recipes() -> None:
    _check("home-recipes", _shoot("1", button="#home-recipes"))


# The intro (#112), wide enough for the wordmark and on a narrow terminal (text only), with
# its hint (#173). It stays up for the shot: no key is pressed.
@pytest.mark.parametrize(("name", "size"), [("intro", SIZE), ("intro-narrow", (64, 20))])
def test_snapshot_intro(name: str, size: tuple[int, int]) -> None:
    _check(name, _shoot(None, size=size, intro=True))


def test_snapshot_about() -> None:
    _check("about", _shoot("a"))
