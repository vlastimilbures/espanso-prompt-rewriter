from prompt_workflow.providers.base import strip_thinking


# strip_thinking() removes a single-line <think> block.
def test_removes_think_block():
    text = "<think>reasoning here</think>Final answer"
    assert strip_thinking(text) == "Final answer"


# strip_thinking() removes a multiline <think> block.
def test_removes_multiline_think_block():
    text = "<think>\nline1\nline2\n</think>\nResult"
    assert strip_thinking(text) == "Result"


# strip_thinking() removes an orphaned closing </think> tag with no matching open tag.
def test_removes_orphan_tags():
    text = "Partial output</think>"
    assert strip_thinking(text) == "Partial output"


# Text without think tags is unchanged besides trimming whitespace.
def test_plain_text_untouched():
    assert strip_thinking("  hello  ") == "hello"
