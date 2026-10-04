"""Imports scripts/bench_models.py, which is a script rather than a package module."""

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_models.py"
_spec = importlib.util.spec_from_file_location("bench_models", _SCRIPT)
assert _spec
assert _spec.loader
bench = importlib.util.module_from_spec(_spec)
# @dataclass resolves annotations through sys.modules, so register before executing.
sys.modules["bench_models"] = bench
_spec.loader.exec_module(bench)

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
