import hashlib
import json
import re
import sys
from pathlib import Path

import httpx
import pytest
from bench_module import GOOD, bench

from prompt_workflow.config import Settings
from prompt_workflow.prompt_builder import PROFILES, system_prompt
from prompt_workflow.providers.base import ProviderError
from prompt_workflow.redaction import scan


def test_good_output_passes():
    assert bench.check(GOOD, wants_plan=True, wants_independent=True) == []


@pytest.mark.parametrize(
    ("mutate", "failure"),
    [
        (lambda t: t.replace("</GOAL>", "</CONTEXT>", 1), "mismatched closing tag"),
        (lambda t: t.replace("<INPUTS>", "", 1), "no <INPUTS>"),
        (lambda t: t + "\nThanks!", "trailing content after </OUTPUTS>"),
        (lambda t: t.replace("3/", "4/", 1), "step numbering"),
        (lambda t: t.replace("1/ ", "1. ", 1), "used 1. instead of 1/"),
        (
            lambda t: t.replace("Load and validate all inputs", "Check inputs"),
            "missing validate-inputs step",
        ),
        (lambda t: t.replace("I want", "The user wants"), "third-person CONTEXT"),
        (
            lambda t: t.replace("Spin up an independent agent", "Get someone"),
            "review branch absent or both emitted",
        ),
    ],
)
def test_check_catches_defects(mutate, failure):
    assert failure in bench.check(mutate(GOOD), wants_plan=True, wants_independent=True)


def test_check_flags_wrong_branches():
    failed = bench.check(GOOD, wants_plan=False, wants_independent=False)
    assert "wrong planning branch" in failed
    assert "wrong review branch" in failed


# The bench scores exact template wordings; they must exist verbatim in the shipped
# default profile, or every run fails for a reason unrelated to the model.
def test_bench_phrases_match_default_profile():
    template = system_prompt("default")
    phrases = [
        bench.PLAN_FIRST,
        bench.EXECUTE_NOW,
        bench.INDEPENDENT,
        bench.SELF_REVIEW,
        *bench.MANDATORY.values(),
    ]
    for phrase in phrases:
        assert phrase in template, phrase
    for tag in bench.TAGS:
        assert f"<{tag}>" in template
        assert f"</{tag}>" in template


# The static -p- snippet offers the same variants as the default profile, word for word, so
# a prompt filled in by hand reads like one the rewrite produces.
def test_bench_phrases_match_static_template():
    template = (Path(__file__).parents[1] / "espanso/match/prompts-template.yml").read_text(
        encoding="utf-8"
    )
    phrases = [
        bench.PLAN_FIRST,
        bench.EXECUTE_NOW,
        bench.INDEPENDENT,
        bench.SELF_REVIEW,
        *bench.MANDATORY.values(),
    ]
    for phrase in phrases:
        assert phrase in template, phrase


# The core suite covers all four branch combinations, so coupling the branches cannot pass.
def test_drafts_span_all_branch_combinations():
    core = [bench.DRAFTS[name] for name in bench.suite_drafts("core")]
    combos = {(d.plan, d.independent) for d in core}
    assert combos == {(True, True), (True, False), (False, True), (False, False)}


# core stays the 8 model-choice drafts (and the default); edge adds the probing drafts.
def test_suites():
    core, edge, every = (bench.suite_drafts(s) for s in ("core", "edge", "all"))
    assert len(core) == 8
    assert len(edge) == 28
    assert every == core + edge


# A bench draft the data-protection gate blocks would fail on every run for a reason
# unrelated to the model.
@pytest.mark.parametrize("name", list(bench.DRAFTS))
def test_draft_passes_gate(name):
    assert scan(bench.DRAFTS[name].text) == []


def test_draft_languages_are_known():
    for d in bench.DRAFTS.values():
        assert d.language is None or d.language in bench.LANGUAGE_LETTERS


# A None branch expectation accepts either variant but still requires exactly one.
def test_check_unscored_branches():
    assert bench.check(GOOD, wants_plan=None, wants_independent=None) == []
    failed = bench.check(GOOD, wants_plan=False, wants_independent=None)
    assert failed == ["wrong planning branch"]
    both = GOOD.replace("3/ Analyse", "3/ Execute, but state assumptions up front. Analyse")
    assert "planning branch absent or both emitted" in bench.check(both, None, None)


def test_scaffold_tags_of_default_profile():
    tags = bench.scaffold_tags(system_prompt("default"))
    assert {"draft_handling", "section_rules", "step", "variant", "rewrite"} <= tags
    assert "CONTEXT" not in tags


@pytest.mark.parametrize(
    ("mutate", "failure"),
    [
        (lambda t: t.replace("<GOAL>", "<step>\n<GOAL>", 1), "scaffolding tag step"),
        (lambda t: t.replace("credit risk domain", "[domain] domain"), "unreplaced placeholder"),
    ],
)
def test_check_catches_leftovers(mutate, failure):
    assert failure in bench.check(mutate(GOOD), wants_plan=True, wants_independent=True)


def _draft(**kwargs):
    return bench.Draft("draft", None, None, "edge", **kwargs)


def test_check_draft_outputs_format():
    assert bench.check_draft(GOOD, _draft(outputs="doc")) == []
    assert bench.check_draft(GOOD, _draft(outputs="message")) == [
        "message given the .md OUTPUTS line"
    ]
    email = GOOD.replace(bench.DEFAULT_OUTPUTS, "plain-text email, ready to paste")
    assert bench.check_draft(email, _draft(outputs="message")) == []
    assert bench.check_draft(email, _draft(outputs="doc")) == [
        "document without the .md OUTPUTS line"
    ]


# A non-English draft is rewritten in English with a Language bullet; the original draft
# quoted in INPUTS does not count against it.
def test_check_draft_language():
    czech = _draft(language="Czech")
    quoted = GOOD.replace("NPL data.", "My request: potřebuju rychlý mail, že to pošleme v pátek")
    assert bench.check_draft(quoted, czech) == ["no language constraint"]
    ok = quoted.replace("Model rebuild.", "- Language: write the result in Czech.")
    assert bench.check_draft(ok, czech) == []
    untranslated = ok.replace("A board-ready paper", "Potřebuji stručný e-mail, přečíst v pátek")
    assert bench.check_draft(untranslated, czech) == ["not rewritten in English (Czech)"]


# A role stated in the draft opens CONTEXT, in place of the configured persona.
def test_check_draft_role():
    pm = _draft(role="product manager")
    assert bench.check_draft(GOOD, pm) == ["draft's role not in CONTEXT"]
    own = GOOD.replace("I am working as a Head of Data.", "As a product manager, I")
    assert bench.check_draft(own, pm, persona="I am working as a Head of Data.") == []
    both = GOOD.replace("I want", "As a product manager I want")
    assert bench.check_draft(both, pm, persona="I am working as a Head of Data.") == [
        "configured persona despite the draft's role"
    ]


def test_retention():
    d = _draft(keys=(("board",), ("NPL spike", "NPL"), ("Q3",)))
    assert bench.retention(GOOD, d) == pytest.approx(2 / 3)
    assert bench.retention(GOOD, _draft()) == 1.0


# A key matches at the start of a word, and an acronym only in capitals.
def test_key_found():
    assert bench.key_found("NPL", "the NPL spike")
    assert bench.key_found("phase", "in three Phases")
    assert not bench.key_found("ID", "validate all inputs")
    assert not bench.key_found("MAD", "fixes made")
    assert not bench.key_found("MAD", "a mad rush")
    assert bench.key_found("MAD", "median and MAD")


def _skeleton() -> str:
    """A rewrite holding only what every output contains whatever the draft: the persona,
    the tags, both variants of each branching step, the fixed steps and the .md line."""
    profile = PROFILES["default"]
    fixed = [ln for ln in profile.splitlines() if ln.startswith(tuple(bench.MANDATORY.values()))]
    return "\n".join(
        [
            bench.EXAMPLE_PERSONA,
            *(f"<{t}>\n</{t}>" for t in bench.TAGS),
            *re.findall(r'<variant id="[ab]">(.*?)</variant>', profile, re.DOTALL),
            *fixed,
            bench.DEFAULT_OUTPUTS,
            "- Language: write the result in",
            "- Out of scope:",
        ]
    )


def test_skeleton_holds_the_fixed_wordings():
    skeleton = _skeleton()
    phrases = [
        bench.PLAN_FIRST,
        bench.SELF_REVIEW,
        "fixes made",
        "before drafting",
        "let me decide",
    ]
    for phrase in phrases:
        assert phrase in skeleton


# No retention key is satisfied by the fixed wording alone, or retention would score a
# rewrite that dropped every specific of the draft.
@pytest.mark.parametrize("name", list(bench.DRAFTS))
def test_retention_keys_miss_the_fixed_wording(name):
    skeleton = _skeleton()
    hits = [k for alts in bench.DRAFTS[name].keys for k in alts if bench.key_found(k, skeleton)]
    assert hits == []


# Pasted material must reach INPUTS word for word; re-wrapped lines still count.
def test_check_draft_material():
    pasted = _draft(material=("from 1 January our API price rises by 8%",))
    copied = GOOD.replace("NPL data.", "Dear customer, from 1 January our API price rises by 8%.")
    assert bench.check_draft(copied, pasted) == []
    wrapped = copied.replace("our API price", "our API\nprice")
    assert bench.check_draft(wrapped, pasted) == []
    described = GOOD.replace("NPL data.", "Orbis Data's price update email.")
    assert bench.check_draft(described, pasted) == ["pasted material not copied"]
    # A copy outside INPUTS (e.g. in a work step) does not count.
    elsewhere = described.replace(
        "3/ Analyse", "3/ Note: from 1 January our API price rises by 8%."
    )
    assert bench.check_draft(elsewhere, pasted) == ["pasted material not copied"]
    # Every probe must be there: copying only one message of a thread is not a copy.
    two = _draft(material=("from 1 January our API price rises by 8%", "Regards, Orbis"))
    assert bench.check_draft(copied, two) == ["pasted material not copied"]


# Each probe is copied from its draft and sits on one line of it.
def test_material_is_in_its_draft():
    for name, d in bench.DRAFTS.items():
        for probe in d.material:
            assert any(probe in line for line in d.text.splitlines()), (name, probe)


def test_split_spec():
    assert bench.split_spec("a/b") == ("a/b", "", "")
    assert bench.split_spec("a/b@c/d") == ("a/b", "c/d", "")
    assert bench.split_spec("a/b~low") == ("a/b", "", "low")
    assert bench.split_spec("a/b@c/d~none") == ("a/b", "c/d", "none")
    # @auto means OpenRouter's own routing, exactly as in the CLI's --model.
    assert bench.split_spec("a/b@auto~low") == ("a/b", "", "low")


# Reasoning effort is sent to OpenRouter with the trace excluded from the returned text.
def test_call_sends_reasoning_effort(fake_http, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    fake_http.reply({"choices": [{"message": {"content": "ok"}}]})
    bench._call(Settings(), "a/b", "", "draft", "sys", "low")
    sent = fake_http.calls[0]["json"]
    assert sent["reasoning"] == {"effort": "low", "exclude": True}
    assert sent["max_tokens"] == bench.BENCH_MAX_TOKENS
    fake_http.reply({"choices": [{"message": {"content": "ok"}}]})
    bench._call(Settings(), "a/b", "", "draft", "sys", max_tokens=2400)
    assert fake_http.calls[-1]["json"]["max_tokens"] == 2400
    fake_http.reply({"choices": [{"message": {"content": "ok"}}]})
    bench._call(Settings(), "a/b", "", "draft", "sys")
    assert "reasoning" not in fake_http.calls[-1]["json"]


# A response cut off by max_tokens is a failure, and reasoning tokens are recorded.
def test_run_one_flags_truncation(fake_http, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    fake_http.reply(
        {
            "choices": [{"message": {"content": GOOD}, "finish_reason": "length"}],
            "usage": {"completion_tokens_details": {"reasoning_tokens": 42}},
        }
    )
    result = bench.run_one(Settings(), "a/b@c/d~low", "board", 1, tmp_path, bench.Budget(1.0))
    assert result.failed == ["truncated"]
    assert (result.effort, result.reasoning_tokens) == ("low", 42)
    assert result.label == "a/b@c/d~low"
    assert (tmp_path / "a_b__at__c_d__effort__low__board__1.txt").exists()


# _call goes through the gated factory path: hard pin, usage accounting, bench title.
def test_call_request_shape(fake_http, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    fake_http.reply({"choices": [{"message": {"content": "ok"}}], "usage": {"cost": 0.01}})
    text, body = bench._call(Settings(), "a/b", "c/d", "draft", "sys")
    assert text == "ok"
    assert body["usage"] == {"cost": 0.01}
    call = fake_http.calls[0]
    assert fake_http.client_kwargs == [
        {"timeout": httpx.Timeout(120, connect=10), "transport": None}
    ]
    assert call["headers"]["X-Title"] == "espanso-prompt-rewriter-bench"
    assert call["json"]["model"] == "a/b"
    assert call["json"]["usage"] == {"include": True}
    assert call["json"]["provider"] == {"order": ["c/d"], "allow_fallbacks": False}


def test_call_unpinned_sends_no_routing(fake_http, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    fake_http.reply({"choices": [{"message": {"content": "ok"}}]})
    bench._call(Settings(), "a/b", "", "draft", "sys")
    assert "provider" not in fake_http.calls[0]["json"]


# The bench cannot bypass the data-protection gate.
def test_call_is_gated(fake_http, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    with pytest.raises(ProviderError, match="Blocked cloud call"):
        bench._call(Settings(), "a/b", "", "card 4111 1111 1111 1111", "sys")
    assert fake_http.calls == []


def test_run_one_scores_and_records(fake_http, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    fake_http.reply(
        {
            "choices": [{"message": {"content": GOOD}}],
            "usage": {"cost": 0.002, "prompt_tokens": 10, "completion_tokens": 20},
            "provider": "Host",
        }
    )
    budget = bench.Budget(1.0)
    result = bench.run_one(Settings(), "a/b@c/d", "board", 1, tmp_path, budget)
    assert result.ok, result.failed
    assert (result.in_tokens, result.out_tokens, result.backend) == (10, 20, "Host")
    assert result.label == "a/b@c/d"
    assert budget.spent == pytest.approx(0.002)
    assert (tmp_path / "a_b__at__c_d__board__1.txt").read_text() == GOOD


def _system(fake_http, call=-1):
    return fake_http.calls[call]["json"]["messages"][0]["content"]


# run_one renders the persona it is given, never the runner's PROMPT_PERSONA, and defaults
# to the fictitious example.
def test_run_one_renders_given_persona(fake_http, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("PROMPT_PERSONA", "I am a private person.")
    fake_http.reply({"choices": [{"message": {"content": GOOD}}]})
    bench.run_one(Settings(), "a/b", "board", 1, tmp_path, bench.Budget(1.0))
    assert f'open with "{bench.EXAMPLE_PERSONA}"' in _system(fake_http)
    bench.run_one(Settings(), "a/b", "board", 1, tmp_path, bench.Budget(1.0), persona="I test.")
    assert 'open with "I test."' in _system(fake_http)
    assert "I am a private person." not in _system(fake_http, 0) + _system(fake_http)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("example", bench.EXAMPLE_PERSONA), ("none", ""), ("env", "I am a tester.")],
)
def test_bench_persona_modes(monkeypatch, mode, expected):
    monkeypatch.setenv("PROMPT_PERSONA", "I am a tester.")
    assert bench.bench_persona(mode, Settings()) == expected


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _main(monkeypatch, outdir, *extra):
    argv = ["bench", "--models", "a/b", "--drafts", "board", "--runs", "1", "--outdir", str(outdir)]
    monkeypatch.setattr(sys, "argv", [*argv, *extra])
    bench.main()
    return json.loads((outdir / "meta.json").read_text(encoding="utf-8"))


# Two runners with different private personas send the same system prompt by default, and
# nothing written to the run directory contains either persona.
def test_default_run_is_reproducible_and_private(fake_http, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    fake_http.reply({"choices": [{"message": {"content": GOOD}}]})
    systems = []
    for i, private in enumerate(["I head the Secret Unit.", "I run Hidden Corp."]):
        monkeypatch.setenv("PROMPT_PERSONA", private)
        meta = _main(monkeypatch, tmp_path / str(i))
        systems.append(_system(fake_http))
        written = "".join(f.read_text(encoding="utf-8") for f in (tmp_path / str(i)).iterdir())
        assert private not in written + capsys.readouterr().out
        assert meta["persona"] == "example"
        assert meta["prompt"] == "default"
        assert meta["prompt_sha256"] == _sha(PROFILES["default"])
        assert meta["system_prompt_sha256"] == _sha(systems[-1])
    assert systems[0] == systems[1]
    assert bench.EXAMPLE_PERSONA in systems[0]


# --max-tokens reaches the request and meta.json.
def test_main_max_tokens(fake_http, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    fake_http.reply({"choices": [{"message": {"content": GOOD}}]})
    meta = _main(monkeypatch, tmp_path / "out", "--max-tokens", "2400")
    assert meta["max_tokens"] == 2400
    assert fake_http.calls[-1]["json"]["max_tokens"] == 2400
    with pytest.raises(SystemExit):
        _main(monkeypatch, tmp_path / "zero", "--max-tokens", "0")


# --persona env renders the runner's own persona and records only the mode; a candidate
# file is recorded by name, never by its local path.
def test_env_persona_records_mode_only(fake_http, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("PROMPT_PERSONA", "I head the Secret Unit.")
    fake_http.reply({"choices": [{"message": {"content": GOOD}}]})
    candidate = tmp_path / "candidate.md"
    candidate.write_text("{{PERSONA_RULE}} <CONTEXT>\n", encoding="utf-8")
    out = tmp_path / "out"
    meta = _main(monkeypatch, out, "--persona", "env", "--system-prompt-file", str(candidate))
    assert 'open with "I head the Secret Unit."' in _system(fake_http)
    assert meta["persona"] == "env"
    assert meta["prompt"] == "candidate.md"
    assert meta["prompt_sha256"] == _sha(candidate.read_text(encoding="utf-8").strip())
    assert meta["system_prompt_sha256"] is None  # a hash of a private sentence can be guessed
    raw = (out / "meta.json").read_text(encoding="utf-8")
    assert "Secret" not in raw
    assert str(tmp_path) not in raw


# meta.json describes one run, so a directory holding another run's outputs is refused.
def test_outdir_must_be_empty(fake_http, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "x__board__1.txt").write_text("old run", encoding="utf-8")
    with pytest.raises(SystemExit, match="not empty"):
        _main(monkeypatch, tmp_path / "old")
    assert fake_http.calls == []


def test_default_outdir_is_timestamped(fake_http, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    fake_http.reply({"choices": [{"message": {"content": GOOD}}]})
    monkeypatch.setattr(
        sys, "argv", ["bench", "--models", "a/b", "--drafts", "board", "--runs", "1"]
    )
    bench.main()
    (run,) = (tmp_path / bench.DEFAULT_OUTDIR).iterdir()
    assert re.fullmatch(r"\d{8}T\d{12}Z", run.name)
    assert (run / "meta.json").exists()


def test_git_state(monkeypatch):
    sha, dirty = bench.git_state()
    assert re.fullmatch(r"[0-9a-f]{40}", sha) or sha == "unknown"
    assert isinstance(dirty, bool) or sha == "unknown"
    monkeypatch.setattr(bench.shutil, "which", lambda _: None)
    assert bench.git_state() == ("unknown", None)


# A run with flash-lite's <CONTEXT>...</GOAL> slip is scored as the CLI would paste it, and
# counted as repaired in the report.
def test_run_one_scores_repaired_text(fake_http, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    slipped = GOOD.replace("board paper.\n</CONTEXT>", "board paper.\n</GOAL>", 1)
    assert "mismatched closing tag" in bench.check(slipped, True, True)
    fake_http.reply({"choices": [{"message": {"content": slipped}}]})
    result = bench.run_one(Settings(), "a/b", "board", 1, tmp_path, bench.Budget(1.0))
    assert result.ok, result.failed
    assert result.repaired
    assert (tmp_path / "a_b__board__1.txt").read_text() == GOOD
    assert (tmp_path / "a_b__board__1.raw").read_text() == slipped
    bench.report([result], bench.Budget(1.0))
    out = capsys.readouterr().out
    assert "a/b board#1" in out.split("repaired")[-1]
    row = next(ln for ln in out.splitlines() if ln.startswith("a/b "))
    assert row.split()[1:3] == ["1/1", "1"]  # pass, rep


def test_run_one_well_formed_is_not_repaired(fake_http, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    fake_http.reply({"choices": [{"message": {"content": GOOD}}]})
    result = bench.run_one(Settings(), "a/b", "board", 1, tmp_path, bench.Budget(1.0))
    assert not result.repaired
    assert not list(tmp_path.glob("*.raw"))


# A bare run scores what every OpenRouter trigger sends with the shipped settings: both tiers
# resolve to the bench's profile (#61).
@pytest.mark.parametrize("tier", ["standard", "pro"])
def test_bench_profile_is_what_each_tier_sends(tier):
    assert Settings().for_call(tier).profile == bench.PROFILE


# cap-thread stays just under the prompt's 60-line copy limit, and its last material probe
# sits near the end, so a copy truncated by the output cap fails.
def test_cap_thread_size():
    draft = bench.DRAFTS["cap-thread"]
    assert 50 <= len(draft.text.split("---\n", 1)[1].splitlines()) <= 60
    assert draft.text.index(draft.material[-1]) > 0.8 * len(draft.text)


def _run_one_with(fake_http, monkeypatch, outdir, *replies):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(bench.time, "sleep", lambda seconds: None)
    fake_http.queue(*replies)
    return bench.run_one(Settings(), "a/b@c/d", "board", 1, outdir, bench.Budget(1.0))


# The bench repeats a call whose error is transient (a 500 here, which post_json does not
# retry for an interactive call), keyed on the error, not on its message text.
def test_run_one_retries_a_transient_error(fake_http, monkeypatch, tmp_path):
    result = _run_one_with(
        fake_http,
        monkeypatch,
        tmp_path,
        {"status_code": 500},
        {"json_data": {"choices": [{"message": {"content": GOOD}}], "usage": {"cost": 0.0}}},
    )
    assert result.retried is True
    assert result.error is None
    assert len(fake_http.requests) == 2


# A permanent error is reported at once.
def test_run_one_does_not_retry_a_permanent_error(fake_http, monkeypatch, tmp_path):
    result = _run_one_with(fake_http, monkeypatch, tmp_path, {"status_code": 401})
    assert result.retried is False
    assert result.error.startswith("OpenRouter returned HTTP 401")
    assert len(fake_http.requests) == 1
