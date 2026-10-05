"""What the interface shows, read in one go from the services, so every screen shows the same
moment. It is read again after every change, so no screen keeps a stale value (#83)."""

from __future__ import annotations

from dataclasses import dataclass

from .. import assets, deploy, doctor, history
from ..commands import common
from ..commands.usage import store
from ..config import ConfigLayers, Settings
from ..prompt_builder import UserProfile, user_profiles


@dataclass(frozen=True)
class State:
    layers: ConfigLayers
    settings: Settings
    report: doctor.Report
    triggers: list[assets.Trigger]
    # None, with plan_error saying why, when the launcher or Espanso's folder is not found.
    plan: deploy.Plan | None
    plan_error: str | None
    profiles: list[UserProfile]
    group_by: str
    stats: list[history.StatsRow]
    stats_error: str | None


def current_plan() -> deploy.Plan:
    """The deploy plan as `espanso status` and `espanso deploy` make it, with no --launcher
    or --espanso-dir: this install's launcher and `espanso path config`."""
    launcher = deploy.resolve_launcher()
    target = deploy.espanso_dir().resolve()
    return deploy.plan(target, deploy.launcher_text(launcher.path), deploy.Manifest.load())


def gather(group_by: str = "trigger") -> State:
    """Everything the screens show. Read-only: doctor without the clipboard, the deploy plan,
    the profiles and the usage stats, each failure kept to show instead of raised."""
    layers, settings = common.load_layers()
    report = doctor.run(clipboard=False)
    plan: deploy.Plan | None = None
    plan_error = None
    try:
        plan = current_plan()
    except (deploy.DeployError, OSError, ValueError) as exc:
        plan_error = str(exc)
    stats: list[history.StatsRow] = []
    stats_error = None
    try:
        stats = store(settings).stats(group_by)
    except (history.HistoryError, OSError, ValueError) as exc:
        stats_error = str(exc) or type(exc).__name__
    return State(
        layers=layers,
        settings=settings,
        report=report,
        triggers=assets.triggers(),
        plan=plan,
        plan_error=plan_error,
        profiles=user_profiles(settings.profile_overrides),
        group_by=group_by,
        stats=stats,
        stats_error=stats_error,
    )
