"""`setup`: the first-run flow. Settings and the default profile, the API key, a deploy
preview (applied only when you agree or pass --deploy), and a smoke test against a stub on
127.0.0.1, never a paid call. A .env in use is migrated only with your consent."""

from __future__ import annotations

from dataclasses import fields

import typer

from .. import config_files, config_store, deploy, smoke
from ..config import Settings
from ..factory import PROVIDER_NAMES
from ..prompt_builder import PROFILES, system_prompt
from ..redaction import safe_repr
from . import common
from .common import guard
from .usage import disclosure

app = typer.Typer(name="setup", add_completion=False)

# The key each provider needs.
PROVIDER_KEYS = {"openrouter": "OPENROUTER_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
_DEFAULTS = {f.metadata["env"]: f.metadata["default"] for f in fields(Settings)}


class _Steps:
    """Each step's outcome, for the summary; any failure makes setup exit 1."""

    def __init__(self) -> None:
        self.failed: list[str] = []
        self.todo: list[str] = []

    def heading(self, text: str) -> None:
        typer.echo("")
        typer.echo(common.paint(text, "cyan"))

    def fail(self, step: str, message: str) -> None:
        typer.echo(f"  failed: {message}", err=True)
        self.failed.append(step)

    def later(self, message: str) -> None:
        typer.echo(f"  {message}")
        self.todo.append(message)


def _settings_writable(steps: _Steps, interactive: bool) -> bool:
    """Whether setup may write config.toml and the secret store. A .env in use is migrated
    first, with consent; without it nothing is written, since config.toml would make the
    .env unread."""
    if common.legacy_env():
        steps.later(
            "PROMPT_WORKFLOW_ENV is set, so settings and keys stay in that file; setup "
            "does not write config.toml or the secret store"
        )
        return False
    try:
        plan = config_store.plan_migration()
    except config_store.MigrationError as exc:
        steps.fail("settings", f"{exc}")
        return False
    if plan.status != "ready":
        return True
    typer.echo("  Your settings are in a .env. Moving them to config.toml and the secret store:")
    for line in plan.describe():
        typer.echo(f"    {line}")
    if not interactive:
        steps.later("run `prompt-workflow config migrate` to move them (setup changed nothing)")
        return False
    if not typer.confirm(
        "  Migrate now (with a backup; `config rollback` undoes it)?", default=False
    ):
        steps.later("settings stay in the .env; run `prompt-workflow config migrate` later")
        return False
    result = config_store.apply_migration(consent=plan.token)
    typer.echo(f"  Migrated; backup in {result.backup}")
    return True


def _choose(option: str | None, question: str, current: str, interactive: bool) -> str:
    if option is not None:
        return option
    return common.ask(question, current) if interactive else current


def _save_choices(steps: _Steps, provider: str, profile: str) -> None:
    changes: dict[str, str | None] = {}
    for name, value in (("PROMPT_PROVIDER", provider), ("PROMPT_PROFILE", profile)):
        # A default is not pinned, so a later change of the default still applies.
        changes[name] = None if value == _DEFAULTS[name] else value
    try:
        saved = config_store.save_settings(config_store.read_settings(), changes)
    except (ValueError, config_store.ConfigStoreError) as exc:
        steps.fail("settings", str(exc))
        return
    typer.echo(f"  Saved in {saved.path}")


def _key_step(
    steps: _Steps, provider: str, *, writable: bool, from_stdin: bool, interactive: bool
) -> None:
    name = PROVIDER_KEYS.get(provider)
    if name is None:
        typer.echo(f"  {provider} runs locally and needs no key (the OpenRouter triggers do).")
        if from_stdin:
            steps.fail("key", f"--api-key-stdin was given, but {provider} takes no key")
        return
    layers, _ = common.load_layers()
    entry = layers.entries[name]
    if not writable:
        if from_stdin:
            steps.fail("key", "the key was not saved: the settings cannot be written (see above)")
        return
    if from_stdin:
        value = common.read_secret(name, from_stdin=True)
    elif interactive:
        if entry.value:
            where = common.source_label(entry.source)
            if not typer.confirm(f"  {name} is set (from {where}). Replace it?", default=False):
                return
        value = common._getpass(f"  {name} (input hidden, empty to skip): ").strip()
        if not value:
            steps.later(f"{name} not set; `prompt-workflow secrets set {name}` sets it")
            return
    else:
        if not entry.value:
            steps.later(f"{name} is not set; `prompt-workflow secrets set {name} --stdin` sets it")
        else:
            typer.echo(f"  {name} is set (from {common.source_label(entry.source)}).")
        return
    try:
        config_store.save_secret(name, value)
    except (ValueError, config_files.SecretStoreError) as exc:
        steps.fail("key", str(exc))
        return
    typer.echo(f"  {name} saved in the secret store.")


def _deploy_step(
    steps: _Steps,
    *,
    apply: bool | None,
    interactive: bool,
    espanso_dir: str | None,
    launcher: str | None,
    no_restart: bool,
) -> None:
    from ..cli import _make_plan, _restart

    try:
        the_plan = _make_plan(espanso_dir, launcher)
    except (deploy.DeployError, ValueError, OSError) as exc:
        steps.fail("deploy", str(exc))
        return
    for step in the_plan.steps:
        typer.echo(f"  {step.state:<9} {step.name}")
    if the_plan.is_noop:
        typer.echo("  Every match file is in sync.")
        return
    if apply is None and interactive:
        apply = typer.confirm("  Write these match files now?", default=True)
    if not apply:
        typer.echo("  Dry run: nothing was written.")
        steps.later("deploy the match files: `prompt-workflow espanso deploy`")
        return
    try:
        outcome = deploy.apply(the_plan, {})  # an edited file is kept: no silent overwrite
    except (deploy.DeployError, OSError) as exc:
        steps.fail("deploy", str(exc))
        return
    for line in outcome.lines:
        typer.echo(f"  {line}")
    if outcome.changed:
        _restart(no_restart)
    if outcome.kept:
        steps.later("files you edited were kept: `prompt-workflow espanso deploy` to choose")


@app.command(
    "setup", short_help="First run: settings, key, match files, and a smoke test on a local stub."
)
@guard
def setup(
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Ask nothing; use the options and current settings"
    ),
    provider: str | None = typer.Option(
        None, "--provider", help=f"PROMPT_PROVIDER: {', '.join(PROVIDER_NAMES)}"
    ),
    profile: str | None = typer.Option(None, "--profile", help="PROMPT_PROFILE"),
    api_key_stdin: bool = typer.Option(
        False, "--api-key-stdin", help="Read the provider's API key from the first line of stdin"
    ),
    deploy_files: bool | None = typer.Option(
        None,
        "--deploy/--no-deploy",
        help="Write the match files without asking, or skip the deploy step; default: ask "
        "(--non-interactive: preview only)",
        show_default=False,
    ),
    espanso_dir: str | None = typer.Option(
        None, "--espanso-dir", help="Default: `espanso path config`"
    ),
    launcher: str | None = typer.Option(
        None, "--launcher", help="CLI path to write into the matches; default: this install's"
    ),
    no_restart: bool = typer.Option(False, "--no-restart", help="Do not restart Espanso"),
    smoke_test: bool = typer.Option(
        True, "--smoke-test/--no-smoke-test", help="Run improve against a local stub at the end"
    ),
) -> None:
    """First run: settings, the API key, the Espanso match files and a smoke test against a
    stub on 127.0.0.1 (never a paid call). Re-run it any time; it changes only what you
    choose."""
    interactive = not non_interactive
    if interactive:
        common.require_terminal("setup", "run it in a terminal or pass --non-interactive")
    if api_key_stdin and interactive:
        raise typer.BadParameter("--api-key-stdin needs --non-interactive (stdin holds the key)")
    if provider is not None and provider not in PROVIDER_NAMES:
        raise typer.BadParameter(
            f"--provider must be one of {', '.join(PROVIDER_NAMES)}, got {safe_repr(provider)}"
        )
    for value, option in (
        (espanso_dir, "--espanso-dir"),
        (launcher, "--launcher"),
        (profile, "--profile"),
    ):
        common.no_key(value, option)
    steps = _Steps()
    layers, settings = common.load_layers()
    common.show_findings(layers)

    steps.heading("Usage history")
    for line in disclosure(settings):
        typer.echo(f"  {line}")

    steps.heading("Settings")
    writable = _settings_writable(steps, interactive)
    if writable:
        layers, settings = common.load_layers()
    chosen = _choose(provider, "  Provider", settings.provider, interactive)
    while chosen not in PROVIDER_NAMES:
        if not interactive:
            raise typer.BadParameter(
                f"PROMPT_PROVIDER {safe_repr(chosen)} is not a provider; pass --provider"
            )
        typer.echo(f"  Choose one of {', '.join(PROVIDER_NAMES)}.")
        chosen = common.ask("  Provider", settings.provider)
    choices = ", ".join(PROFILES)
    picked = _choose(
        profile, f"  Default profile ({choices}, or one of yours)", settings.profile, interactive
    )
    try:
        system_prompt(picked, "", settings.profile_overrides)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.echo(f"  Provider: {chosen}; profile: {picked}")
    if writable:
        _save_choices(steps, chosen, picked)
    elif (chosen, picked) != (settings.provider, settings.profile):
        steps.later(f"set PROMPT_PROVIDER={chosen} and PROMPT_PROFILE={picked} yourself")

    steps.heading("API key")
    _key_step(steps, chosen, writable=writable, from_stdin=api_key_stdin, interactive=interactive)

    steps.heading("Espanso match files")
    if deploy_files is False:
        typer.echo("  Skipped (--no-deploy).")
    else:
        _deploy_step(
            steps,
            apply=deploy_files,
            interactive=interactive,
            espanso_dir=espanso_dir,
            launcher=launcher,
            no_restart=no_restart,
        )

    steps.heading("Smoke test (a stub on 127.0.0.1; no provider is called)")
    if smoke_test:
        result = smoke.run(chosen)
        if result.ok:
            typer.echo(f"  ok: {result.message}")
        else:
            steps.fail("smoke test", f"improve --provider {chosen}: {result.message}")
    else:
        typer.echo("  Skipped (--no-smoke-test).")

    steps.heading("Summary")
    for item in steps.todo:
        typer.echo(f"  to do: {item}")
    if steps.failed:
        common.fail(f"setup did not finish: {', '.join(steps.failed)} failed (see above)")
    typer.echo("  Setup finished. `prompt-workflow doctor` checks everything again.")
