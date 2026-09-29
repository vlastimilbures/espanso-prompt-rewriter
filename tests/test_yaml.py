import re
from pathlib import Path

import yaml

from prompt_workflow.config import EFFORTS, KEEP, TIERS, split_model_spec
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


def _shell_commands(path: Path):
    for match in _load(path)["matches"]:
        for var in match.get("vars", []):
            if var.get("type") == "shell":
                yield match["trigger"], var["params"]["cmd"]


# Every shell command starts with the quoted CLI placeholder (a path with spaces must
# work) and holds no other quotes: cmd.exe strips the outer pair of a command line that
# starts with a quote and contains more than two, breaking the call.
def test_shell_commands_start_with_quoted_cli():
    for trigger, cmd in (cmd for path in MATCH_FILES for cmd in _shell_commands(path)):
        assert cmd.startswith('"__PROMPT_WORKFLOW__" '), f"{trigger} must start with the CLI"
        assert cmd.count('"') == 2, f"{trigger} must quote only the CLI path"
        assert "__REPO_DIR__" not in cmd, f"{trigger} still uses __REPO_DIR__"


# Every --profile passed to the CLI is a profile prompt_builder.PROFILES actually defines
# (this is exactly the class of regression the 0.3.0 profile deletion should have caught).
def test_shell_commands_use_known_profiles():
    for trigger, cmd in (cmd for path in MATCH_FILES for cmd in _shell_commands(path)):
        match = re.search(r"--profile\s+(\S+)", cmd)
        if match:
            assert match.group(1) in PROFILES, f"{trigger} uses unknown profile {match.group(1)!r}"


# Every --provider passed to the CLI is a name make_provider() accepts.
def test_shell_commands_use_known_providers():
    for trigger, cmd in (cmd for path in MATCH_FILES for cmd in _shell_commands(path)):
        match = re.search(r"--provider\s+(\S+)", cmd)
        if match:
            assert match.group(1) in KNOWN_PROVIDERS, (
                f"{trigger} uses unknown provider {match.group(1)!r}"
            )


# Every --tier passed to the CLI is one Settings.for_tier() accepts.
def test_shell_commands_use_known_tiers():
    for trigger, cmd in (cmd for path in MATCH_FILES for cmd in _shell_commands(path)):
        match = re.search(r"--tier\s+(\S+)", cmd)
        if match:
            assert match.group(1) in TIERS, f"{trigger} uses unknown tier {match.group(1)!r}"


# form: blocks must interpolate at least one {{var}} — otherwise Espanso pops an
# empty dialog instead of expanding text (regression test for the -p- template form fix in 2.2).
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


# Every {{formN.field}} a shell command uses is a field that form declares, so a renamed
# field cannot leave an unexpanded placeholder in the command.
def test_form_fields_used_in_shell_commands_exist():
    for path in MATCH_FILES:
        for match, forms in _form_vars(path):
            for var in match["vars"]:
                if var.get("type") != "shell":
                    continue
                for form, name in re.findall(r"\{\{(\w+)\.(\w+)\}\}", var["params"]["cmd"]):
                    assert name in forms[form]["fields"], f"{match['trigger']}: {form}.{name}"
                    assert f"[[{name}]]" in forms[form]["layout"], f"{match['trigger']}: {name}"


# Choice fields interpolated into a command must be fixed lists whose default is one of
# the values, and each value must be one the CLI accepts, so the popup cannot produce an
# error marker (or inject shell text: no free-text field ever reaches a command).
def test_form_choices_are_valid_cli_values():
    checks = {
        "--model": lambda v: split_model_spec(v)[0] and re.fullmatch(r"[\w.\-/:@]+", v),
        "--effort": lambda v: v in (KEEP, *EFFORTS),
        "--max-tokens": lambda v: v == KEEP or v.isdigit(),
        "--timeout": lambda v: v == KEEP or v.isdigit(),
    }
    for path in MATCH_FILES:
        for match, forms in _form_vars(path):
            cmd = next(v for v in match["vars"] if v.get("type") == "shell")["params"]["cmd"]
            for option, form, name in re.findall(r"(--[\w-]+) \"?\{\{(\w+)\.(\w+)\}\}", cmd):
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
