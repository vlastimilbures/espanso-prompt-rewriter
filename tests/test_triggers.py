"""Replays every Espanso trigger's real command line through the CLI and checks what it
would send: provider, profile (via the system prompt) and tier settings. Testing each option
on its own missed that a trigger's explicit --profile overrode the tier's profile."""

import re
import shlex
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from prompt_workflow.cli import app
from prompt_workflow.config import Settings
from prompt_workflow.prompt_builder import PROFILES, system_prompt

MATCH_DIR = Path(__file__).parents[1] / "espanso" / "match"
runner = CliRunner()


def _improve_commands() -> dict[str, tuple[str, dict]]:
    """trigger -> (shell cmd, form fields by form name) for every match that runs improve."""
    commands = {}
    for path in sorted(MATCH_DIR.glob("*.yml")):
        for match in yaml.safe_load(path.read_text(encoding="utf-8"))["matches"]:
            vars_ = match.get("vars", [])
            forms = {v["name"]: v["params"]["fields"] for v in vars_ if v.get("type") == "form"}
            for var in vars_:
                cmd = var.get("params", {}).get("cmd", "")
                if var.get("type") == "shell" and " improve " in cmd:
                    commands[match["trigger"]] = (cmd, forms)
    return commands


COMMANDS = _improve_commands()

# trigger -> (provider, profile, tier) with no profile settings in the environment.
EXPECTED = {
    "-i-": ("openrouter", "default", "standard"),
    "-iok-": ("openrouter", "default", "standard"),
    "-ip-": ("openrouter", "default", "pro"),
    "-if-": ("openrouter", "default", "pro"),
    "-il-": ("ollama", "general", "standard"),
    "-ilm-": ("lmstudio", "general", "standard"),
}


def _argv(trigger: str, **picks: str) -> list[str]:
    """The trigger's arguments after the CLI path, as Espanso runs them: each form field
    set to ``picks`` or its default, and the draft passed as an argument, not the clipboard."""
    cmd, forms = COMMANDS[trigger]

    def fill(field: re.Match[str]) -> str:
        form, name = field.groups()
        return picks.get(name, str(forms[form][name]["default"]))

    argv = shlex.split(re.sub(r"\{\{(\w+)\.(\w+)\}\}", fill, cmd))
    assert argv[0] == "__PROMPT_WORKFLOW__"
    source = argv.index("--source")
    assert argv[source + 1] == "clipboard"
    argv[source : source + 2] = ["--source", "argument", "--text", "draft"]
    return argv[1:]


def _replay(stub, trigger: str, **picks: str) -> tuple[str, Settings, str]:
    """(provider name, settings, system prompt) the trigger's command hands the provider."""
    stub.built.clear()
    stub.options.clear()
    stub.calls.clear()
    result = runner.invoke(app, _argv(trigger, **picks))
    assert (result.exit_code, result.stdout) == (0, "improved")
    ((name, cfg),) = stub.built
    (call,) = stub.calls
    return name, cfg, call["system_prompt"]


def _profile(prompt: str) -> str:
    names = [name for name in PROFILES if prompt == system_prompt(name)]
    assert len(names) == 1, "the system prompt matches no single profile"
    return names[0]


# Every trigger that runs improve has an expected row, so a new one cannot skip this test.
def test_every_improve_trigger_is_expected():
    assert set(COMMANDS) == set(EXPECTED)


# Each trigger builds its provider with the expected profile and tier settings.
@pytest.mark.parametrize("trigger", list(EXPECTED))
def test_trigger_request(stub_provider, trigger):
    provider, profile, tier = EXPECTED[trigger]
    name, cfg, prompt = _replay(stub_provider, trigger)
    base = Settings()
    assert name == provider
    assert _profile(prompt) == profile
    if tier == "pro":
        assert cfg.openrouter_model == base.openrouter_pro_model
        assert cfg.openrouter_provider == base.openrouter_pro_provider
        assert cfg.openrouter_reasoning_effort == base.openrouter_pro_reasoning_effort
        assert cfg.timeout == base.pro_timeout
    else:
        assert cfg.openrouter_model == base.openrouter_model
        assert cfg.openrouter_reasoning_effort == base.openrouter_reasoning_effort
        assert cfg.timeout == base.timeout


# -if- model choice -> profile with the shipped settings: one prompt for every model. A set
# PROMPT_PRO_PROFILE applies to the pro model only (test_profile_settings_reach_triggers).
IF_MODEL_PROFILES = {
    "openai/gpt-6-luna@openai": "default",
    "openai/gpt-6-luna@auto": "default",
    "google/gemini-3.8-flash@google-ai-studio": "default",
    "google/gemini-3.5-flash-lite@google-ai-studio/flex": "default",
}


@pytest.mark.parametrize(("model", "profile"), list(IF_MODEL_PROFILES.items()))
def test_if_profile_follows_model(stub_provider, model, profile):
    _, cfg, prompt = _replay(stub_provider, "-if-", model=model)
    slug, _, endpoint = model.partition("@")
    assert _profile(prompt) == profile
    assert cfg.openrouter_model == slug
    assert cfg.openrouter_provider == ("" if endpoint == "auto" else endpoint)


# The table above covers every model the -if- popup offers.
def test_if_model_choices_are_covered():
    _, forms = COMMANDS["-if-"]
    assert set(forms["form1"]["model"]["values"]) == set(IF_MODEL_PROFILES)


# The profile settings reach the triggers: PROMPT_PROFILE for -i- (and a non-pro -if- model),
# PROMPT_PRO_PROFILE for the pro tier, which falls back to PROMPT_PROFILE when empty.
@pytest.mark.parametrize(
    ("env", "trigger", "picks", "profile"),
    [
        ({"PROMPT_PROFILE": "general"}, "-i-", {}, "general"),
        ({"PROMPT_PRO_PROFILE": "general"}, "-ip-", {}, "general"),
        ({"PROMPT_PRO_PROFILE": ""}, "-ip-", {}, "default"),
        ({"PROMPT_PRO_PROFILE": "", "PROMPT_PROFILE": "general"}, "-ip-", {}, "general"),
        ({"PROMPT_PRO_PROFILE": "general"}, "-if-", {}, "general"),
        (
            {"PROMPT_PROFILE": "general"},
            "-if-",
            {"model": "google/gemini-3.5-flash-lite@google-ai-studio/flex"},
            "general",
        ),
        (
            {"PROMPT_PRO_PROFILE": "general"},
            "-if-",
            {"model": "google/gemini-3.8-flash@google-ai-studio"},
            "default",
        ),
        ({"PROMPT_PRO_PROFILE": "default-pro"}, "-ip-", {}, "default"),
        ({"PROMPT_PROFILE": "default-pro"}, "-il-", {}, "general"),
    ],
    ids=[
        "i-profile",
        "ip-pro-profile",
        "ip-empty-pro-profile",
        "ip-empty-pro-profile-falls-back",
        "if-pro-profile",
        "if-other-model-profile",
        "if-other-model-loses-pro-profile",
        "ip-retired-default-pro",
        "il-keeps-general",
    ],
)
def test_profile_settings_reach_triggers(monkeypatch, stub_provider, env, trigger, picks, profile):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    _, _, prompt = _replay(stub_provider, trigger, **picks)
    assert _profile(prompt) == profile


# An explicit --profile beats the tier's profile, so no pro-tier trigger may pass one.
def test_pro_triggers_pass_no_profile():
    for trigger, (cmd, _) in COMMANDS.items():
        if re.search(r"--tier[ =]pro\b", cmd):
            assert "--profile" not in cmd, trigger


# Only -iok- sends a flagged draft per call; every other trigger keeps the gate's block.
@pytest.mark.parametrize("trigger", list(EXPECTED))
def test_only_iok_allows_flagged(stub_provider, trigger):
    _replay(stub_provider, trigger)
    assert stub_provider.options == [{"allow_flagged": trigger == "-iok-"}]
