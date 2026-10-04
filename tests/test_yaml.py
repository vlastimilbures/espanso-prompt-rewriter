import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from prompt_workflow.cli import PERSONA_PLACEHOLDER
from prompt_workflow.config import EFFORTS, KEEP, TIERS, env_names, split_model_spec
from prompt_workflow.factory import PROVIDER_NAMES
from prompt_workflow.prompt_builder import PROFILES

ESPANSO_ROOT = Path(__file__).parents[1] / "espanso"
MATCH_FILES = sorted((ESPANSO_ROOT / "match").glob("*.yml"))
KNOWN_PROVIDERS = set(PROVIDER_NAMES)


# Every YAML file under espanso/ parses to something.
def test_espanso_yaml_parses():
    for path in ESPANSO_ROOT.rglob("*.yml"):
        assert yaml.safe_load(path.read_text(encoding="utf-8")) is not None


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# Every match file has a top-level matches: list.
def test_match_files_have_matches_list():
    for path in MATCH_FILES:
        data = _load(path)
        assert isinstance(data.get("matches"), list), f"{path.name} has no matches: list"


# Triggers are unique across every match file (the class of bug that let a
# duplicate or dead trigger silently shadow another one).
def test_triggers_are_unique_across_files():
    seen: dict[str, str] = {}
    for path in MATCH_FILES:
        for match in _load(path)["matches"]:
            trigger = match["trigger"]
            assert trigger not in seen, (
                f"{trigger!r} defined in both {seen[trigger]} and {path.name}"
            )
            seen[trigger] = path.name


CLI = "__PROMPT_WORKFLOW__"
# Espanso's own pattern allows spaces inside the braces.
FORM_FIELD = re.compile(r"\{\{\s*(\w+)\.(\w+)\s*\}\}")


def _cli_calls(path: Path):
    """(trigger, args, form params by form name) for every script var that runs the CLI."""
    for match in _load(path)["matches"]:
        vars_ = match.get("vars", [])
        forms = {v["name"]: v["params"] for v in vars_ if v.get("type") == "form"}
        for var in vars_:
            args = var.get("params", {}).get("args")
            if var.get("type") == "script" and args and args[0] == CLI:
                yield match["trigger"], args, forms


CLI_CALLS = [call for path in MATCH_FILES for call in _cli_calls(path)]


def _option(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


# Every var that runs the CLI is a `type: script` var: Espanso then starts args[0] itself,
# with no shell (on Windows the default shell is PowerShell, which cannot run a command line
# that starts with a quoted path), and passes each item as one argument. args[0] is the bare
# placeholder, and every item is a non-empty string without quotes: Espanso drops
# non-string items, and a quote would reach the CLI literally.
def test_cli_vars_run_without_a_shell():
    calls = 0
    for path in MATCH_FILES:
        for match in _load(path)["matches"]:
            for var in match.get("vars", []):
                params = var.get("params", {})
                if CLI not in str(params):
                    continue
                calls += 1
                trigger = match["trigger"]
                assert var["type"] == "script", f"{trigger} must use a script var"
                args = params["args"]
                assert args[0] == CLI, f"{trigger} must start with the CLI placeholder"
                assert all(isinstance(a, str) and a and '"' not in a for a in args), trigger
                assert "__REPO_DIR__" not in str(params), f"{trigger} still uses __REPO_DIR__"
    assert calls == len(CLI_CALLS) > 0


# Every --profile passed to the CLI is a profile prompt_builder.PROFILES actually defines,
# so deleting or renaming a profile cannot leave a trigger pointing at nothing.
def test_cli_calls_use_known_profiles():
    for trigger, args, _ in CLI_CALLS:
        profile = _option(args, "--profile")
        assert profile is None or profile in PROFILES, f"{trigger} uses unknown profile {profile!r}"


# Every --provider passed to the CLI is a name make_provider() accepts.
def test_cli_calls_use_known_providers():
    for trigger, args, _ in CLI_CALLS:
        provider = _option(args, "--provider")
        assert provider is None or provider in KNOWN_PROVIDERS, f"{trigger}: {provider!r}"


# Every --tier passed to the CLI is one Settings.for_tier() accepts.
def test_cli_calls_use_known_tiers():
    for trigger, args, _ in CLI_CALLS:
        tier = _option(args, "--tier")
        assert tier is None or tier in TIERS, f"{trigger} uses unknown tier {tier!r}"


# The installers replace the placeholder with the CLI's absolute path as plain text. A path
# with spaces (Windows paths are written with forward slashes) is still one argument.
@pytest.mark.parametrize(
    "cli",
    [
        "C:/Users/Jane Doe/.local/bin/prompt-workflow.exe",
        "/Users/Jane Doe/.local/bin/prompt-workflow",
    ],
)
def test_installed_cli_path_is_one_argument(cli):
    for path in MATCH_FILES:
        data = yaml.safe_load(path.read_text(encoding="utf-8").replace(CLI, cli))
        for match in data["matches"]:
            for var in match.get("vars", []):
                if var.get("type") == "script":
                    assert var["params"]["args"][0] == cli


# Marker each provider's call ends in when every argument was accepted: no API key, or the
# closed local port. Any other marker means the CLI rejected an argument.
RUN_CLEAN_MARKERS = {
    "openrouter": "[prompt-workflow: OPENROUTER_API_KEY is not configured]",
    "ollama": "[prompt-workflow: Ollama request failed: ",
    "lmstudio": "[prompt-workflow: LM Studio request failed: ",
}


# Each CLI call, run through the console-script entry point with the listed argv and empty
# stdin, exits 0 with the expected output on stdout and nothing on stderr, which a script var
# treats as a failure. Form fields take their defaults, the draft comes from an argument,
# there is no API key and the local endpoints point at a closed port, so nothing leaves the
# machine. (This starts Python itself, not the installed launcher.)
@pytest.mark.parametrize(("trigger", "args", "forms"), CLI_CALLS, ids=[c[0] for c in CLI_CALLS])
def test_cli_calls_run_clean(tmp_path, trigger, args, forms):
    argv = [
        FORM_FIELD.sub(lambda f: str(forms[f[1]]["fields"][f[2]]["default"]), a) for a in args[1:]
    ]
    if "--source" in argv:
        at = argv.index("--source")
        argv[at : at + 2] = ["--source", "argument", "--text", "draft"]
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in env_names() and not k.lower().endswith("_proxy")
    }
    env |= {
        "PROMPT_WORKFLOW_ENV": str(tmp_path / ".env"),
        # Show every warning, as a user's PYTHONWARNINGS could; the entry point must still
        # keep stderr empty.
        "PYTHONWARNINGS": "always",
        "OLLAMA_BASE_URL": "http://127.0.0.1:9",
        "LMSTUDIO_BASE_URL": "http://127.0.0.1:9/v1",
    }
    proc = subprocess.run(
        [sys.executable, "-c", "from prompt_workflow.entry import main; main()", *argv],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env=env,
        timeout=60,
        check=False,
    )
    out = proc.stdout.decode("utf-8")
    assert (proc.returncode, proc.stderr) == (0, b""), trigger
    provider = _option(args, "--provider")
    if provider is None:
        assert out == PERSONA_PLACEHOLDER, (trigger, out)
    else:
        assert out.startswith(RUN_CLEAN_MARKERS[provider]), (trigger, out)


# form: blocks must interpolate at least one {{var}} — otherwise Espanso pops an
# empty dialog instead of expanding text.
def test_form_blocks_interpolate_a_variable():
    for path in MATCH_FILES:
        for match in _load(path)["matches"]:
            form = match.get("form")
            if form is not None:
                assert re.search(r"\{\{\w+\}\}", form), (
                    f"{match['trigger']} form: has no {{{{var}}}}"
                )


TRIGGER_SHAPE = re.compile(r"^-[a-z0-9]+(-[a-z0-9]+)*-$")


# Every enabled trigger uses the dash-delimited -name- form, not the old leading-colon form.
def test_triggers_use_dash_delimited_form():
    for path in MATCH_FILES:
        for match in _load(path)["matches"]:
            trigger = match["trigger"]
            assert TRIGGER_SHAPE.match(trigger), f"{trigger!r} in {path.name} is not -name- shaped"


# No enabled trigger is a strict prefix of another — Espanso expands on the shortest
# matching trigger, so a prefix collision would make the longer trigger unreachable.
def test_no_trigger_is_a_prefix_of_another():
    triggers = [match["trigger"] for path in MATCH_FILES for match in _load(path)["matches"]]
    for a in triggers:
        for b in triggers:
            if a != b:
                assert not b.startswith(a), f"{a!r} is a prefix of {b!r}, which is unreachable"


def _form_vars(path: Path):
    for match in _load(path)["matches"]:
        forms = {v["name"]: v["params"] for v in match.get("vars", []) if v.get("type") == "form"}
        if forms:
            yield match, forms


# Every {{formN.field}} a CLI call uses is a field that form declares, so a renamed field
# cannot leave an unexpanded placeholder in the arguments.
def test_form_fields_used_in_cli_calls_exist():
    for trigger, args, forms in CLI_CALLS:
        for form, name in (f.groups() for a in args for f in FORM_FIELD.finditer(a)):
            assert name in forms[form]["fields"], f"{trigger}: {form}.{name}"
            assert f"[[{name}]]" in forms[form]["layout"], f"{trigger}: {name}"


# A form field is always a whole argument, so its value is exactly one fixed choice (checked
# below) and never part of a longer argument such as --timeout={{form1.timeout}}.
def test_form_fields_are_whole_arguments():
    for trigger, args, _ in CLI_CALLS:
        for arg in args:
            if "{{" in arg:
                assert FORM_FIELD.fullmatch(arg), f"{trigger}: {arg!r}"


# Choice fields passed to the CLI must be fixed lists whose default is one of the values,
# and each value must be one the CLI accepts, so the popup cannot produce an error marker.
# A field is a whole argument right after its option.
def test_form_choices_are_valid_cli_values():
    checks = {
        "--model": lambda v: split_model_spec(v)[0] and re.fullmatch(r"[\w.\-/:@]+", v),
        "--effort": lambda v: v in (KEEP, *EFFORTS),
        "--max-tokens": lambda v: v == KEEP or v.isdigit(),
        "--timeout": lambda v: v == KEEP or v.isdigit(),
    }
    for path in MATCH_FILES:
        for match, forms in _form_vars(path):
            args = next(v for v in match["vars"] if v.get("type") == "script")["params"]["args"]
            fields = [
                (args[i - 1], *f.groups())
                for i, a in enumerate(args)
                if (f := FORM_FIELD.fullmatch(a))
            ]
            assert fields, match["trigger"]
            for option, form, name in fields:
                field = forms[form]["fields"][name]
                assert field.get("type") == "choice", f"{match['trigger']}: {name} not a choice"
                values = [str(v) for v in field["values"]]
                assert str(field["default"]) in values, f"{match['trigger']}: {name} default"
                for value in values:
                    assert checks[option](value), f"{match['trigger']}: {option} {value!r}"


# Match files are UTF-8 without a BOM: the installers read and write them as such, and
# Espanso would treat a BOM as part of the first key.
def test_match_files_are_utf8_without_bom():
    for path in ESPANSO_ROOT.rglob("*.yml"):
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{path.name} has a BOM"
        raw.decode("utf-8")
