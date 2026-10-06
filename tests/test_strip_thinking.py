import pytest

from prompt_workflow.providers.base import strip_thinking


# strip_thinking() removes a single-line <think> block.
def test_removes_think_block() -> None:
    text = "<think>reasoning here</think>Final answer"
    assert strip_thinking(text) == "Final answer"


# strip_thinking() removes a multiline <think> block.
def test_removes_multiline_think_block() -> None:
    text = "<think>\nline1\nline2\n</think>\nResult"
    assert strip_thinking(text) == "Result"


# Consecutive leading blocks are all reasoning.
def test_removes_consecutive_leading_blocks() -> None:
    assert strip_thinking("<think>a</think>\n<THINK>b</THINK>\nAnswer") == "Answer"


# A stray closing tag at the start (the opening tag sat in the chat template) is dropped.
def test_removes_leading_orphan_close() -> None:
    assert strip_thinking("</think>Partial output") == "Partial output"


# Text without think tags is unchanged besides trimming whitespace.
def test_plain_text_untouched() -> None:
    assert strip_thinking("  hello  ") == "hello"


# A <think> block cut off before its closing tag is reasoning to the end.
@pytest.mark.parametrize("text", ["<think>half a thought", "  <think>a</think><think>cut off"])
def test_removes_unclosed_leading_block(text: str) -> None:
    assert strip_thinking(text) == ""


# Think tags after the start are answer text, e.g. a rewrite of a prompt about reasoning tags,
# and are pasted verbatim.
@pytest.mark.parametrize(
    "text",
    [
        "<INSTRUCTIONS>\n1/ Reason inside <think> tags, then answer in <answer> tags.\n"
        "2/ Keep it short.\n</INSTRUCTIONS>",
        "1/ Put your reasoning between <think></think> and the answer after it.",
        "Use <THINK> as a section marker",
        "Partial output</think>",
        "Answer<think>half a thought",
    ],
)
def test_keeps_think_tags_after_the_start(text: str) -> None:
    assert strip_thinking(text) == text


# Reasoning is stripped, but a later mention in the answer survives.
def test_strips_leading_block_but_keeps_later_mention() -> None:
    text = "<think>plan</think>Answer<think>cut off"
    assert strip_thinking(text) == "Answer<think>cut off"
