from pathlib import Path

import pytest
from bench_module import bench

from prompt_workflow.config import Settings
from prompt_workflow.prompt_builder import system_prompt
from prompt_workflow.providers.base import ProviderError

# A rewrite that satisfies every check for a plan-first, independent-review draft.
GOOD = """<CONTEXT>
I am working as a Head of Data. I want a board paper.
</CONTEXT>

<GOAL>
A board-ready paper on collections model risk.
</GOAL>

<INSTRUCTIONS>
1/ Plan the task thoroughly, list any assumptions and open questions, and validate the plan \
with me before executing.
2/ Load and validate all inputs. If anything is missing, ambiguous, or contradictory, ask me \
up to 5 targeted questions before drafting.
3/ Analyse the NPL spike.
4/ Spin up an independent agent with credit risk domain knowledge and perform a critical review.
5/ Flag material judgment calls or trade-offs and let me decide.
</INSTRUCTIONS>

<CONSTRAINTS>
Model rebuild.
</CONSTRAINTS>

<INPUTS>
NPL data.
</INPUTS>

<OUTPUTS>
structured .md, well formatted with clear headings/subheadings
</OUTPUTS>"""


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


# Both expected branch combinations are covered by the draft set.
def test_drafts_span_all_branch_combinations():
    combos = {(plan, independent) for _, plan, independent in bench.DRAFTS.values()}
    assert combos == {(True, True), (True, False), (False, True), (False, False)}


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
    bench._call(Settings(), "a/b", "", "draft", "sys")
    assert "reasoning" not in fake_http.calls[1]["json"]


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
    assert fake_http.client_kwargs == [{"timeout": 120}]
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


# The bench scores the same system prompt the CLI would send, persona included.
def test_run_one_uses_configured_persona(fake_http, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("PROMPT_PERSONA", "I am a tester.")
    fake_http.reply({"choices": [{"message": {"content": GOOD}}]})
    bench.run_one(Settings(), "a/b", "board", 1, tmp_path, bench.Budget(1.0))
    system = fake_http.calls[0]["json"]["messages"][0]["content"]
    assert 'open with "I am a tester."' in system
