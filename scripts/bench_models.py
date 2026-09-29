#!/usr/bin/env python
"""Benchmark OpenRouter models on the `default` (golden template) profile.

Scores every run mechanically: tag balance, mandatory steps, branch selection,
degeneration. Token counts and cost come from OpenRouter's own `usage` block, not
from an estimate. Run manually; nothing in the package imports this.

    uv run python scripts/bench_models.py
    uv run python scripts/bench_models.py --models openai/gpt-4.1-nano --runs 1
    uv run python scripts/bench_models.py --models openai/gpt-6-luna@openai~low

A spec is `model`, optionally `@provider-tag` to pin one endpoint and `~effort` to set
OpenRouter's reasoning effort (none/minimal/low/medium/high).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from prompt_workflow.config import Settings
from prompt_workflow.factory import make_provider
from prompt_workflow.prompt_builder import PROFILES, render

MODELS: list[str] = [
    # Standard tier (-i-). The first is the shipped default.
    "google/gemini-3.5-flash-lite@google-ai-studio/flex~minimal",
    "google/gemini-3.1-flash-lite@google-ai-studio/flex~minimal",
    "google/gemini-3.5-flash-lite@google-vertex/global~minimal",
    "google/gemini-2.5-flash-lite@google-ai-studio/flex",
    # Pro tier (-ip-). The first is the shipped default.
    "openai/gpt-6-luna@openai~low",
    "openai/gpt-6-luna@openai/flex~low",
    "google/gemini-3.8-flash@google-ai-studio~low",
]

# Room for a reasoning budget on top of the ~1k-token rewrite, so a thinking model is not
# scored on an answer it never got to finish. Truncation is still reported as a failure.
BENCH_MAX_TOKENS = 6000

ICAAP = (
    "I need a full ICAAP-style risk appetite refresh for the consumer finance book: "
    "revisit credit risk, operational risk, liquidity, model risk and conduct risk appetite "
    "statements; rebuild the KRI thresholds off 2024-2025 actuals; run stress scenarios for a "
    "200bp funding shock and a 30% NPL uplift; benchmark against regulator circulars and the "
    "group risk appetite framework; produce a board risk committee pack plus an appendix of "
    "methodology, data lineage and limitations; also draft the talking points for the CRO and a "
    "one-page summary for the CEO, and flag every place where our data quality is too weak to "
    "support a threshold"
)

PERSONAL = (
    "I want to redo how I keep my own reading notes and follow-ups: right now they are scattered "
    "across Obsidian, a notebook and browser bookmarks. Work out how to consolidate them, in "
    "phases, and how I should tag things going forward. I have not decided what the end state "
    "should look like yet, and nobody else will ever see this."
)

QUICK_EXTERNAL = (
    "quick three-sentence email to the regulator's inspector confirming the 4.2% NPL figure we "
    "reported in the Q2 return is the one they should use"
)

SHORT_EXTERNAL = (
    "two-line note to our external auditor confirming the provisioning policy version we applied "
    "in June"
)

QUICK_CEO = "quick one-paragraph note to the CEO on why collections cost is up this month"

BIG_PERSONAL = (
    "map out, over several phases, how I want to restructure my own weekly risk review routine; "
    "just for me"
)

# The two axes are chosen independently in prompts/default.md: the planning branch on
# complexity/ambiguity, the review branch on audience/consequence. These five drafts span
# all four combinations, so a model that merely couples the branches cannot score 100%.
# name -> (draft, expects_plan_first, expects_independent_review)
DRAFTS: dict[str, tuple[str, bool, bool]] = {
    "light": (
        "quick 3-line reply to Sam confirming the meeting moved to Thursday",
        False,
        False,
    ),
    "board": (
        "board paper on collections model risk after the new NPL spike, needs numbers",
        True,
        True,
    ),
    "icaap": (ICAAP, True, True),
    "personal": (PERSONAL, True, False),
    "quick-external": (QUICK_EXTERNAL, False, True),
    "short-external": (SHORT_EXTERNAL, False, True),
    "quick-ceo": (QUICK_CEO, False, True),
    "big-personal": (BIG_PERSONAL, True, False),
}

TAGS = ["CONTEXT", "GOAL", "INSTRUCTIONS", "CONSTRAINTS", "INPUTS", "OUTPUTS"]
MANDATORY = {
    "validate-inputs": "Load and validate all inputs",
    "flag-judgment": "Flag material judgment calls",
}
PLAN_FIRST = "Plan the task thoroughly"
EXECUTE_NOW = "Execute, but state assumptions up front"
INDEPENDENT = "Spin up an independent agent"
SELF_REVIEW = "Review your own output against these checks"

TRANSIENT = ("timed out", "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504")


def check(text: str, wants_plan: bool, wants_independent: bool) -> list[str]:
    """Return the names of the checks this output failed."""
    failed = []

    for tag in TAGS:
        if f"<{tag}>" not in text:
            failed.append(f"no <{tag}>")
        if f"</{tag}>" not in text:
            failed.append(f"no </{tag}>")
    if not text.rstrip().endswith("</OUTPUTS>"):
        failed.append("trailing content after </OUTPUTS>")

    # A section must be closed by its own tag, not by whichever tag comes to mind.
    opened = re.findall(r"<(/?)(" + "|".join(TAGS) + r")>", text)
    stack = [t for slash, t in opened if not slash]
    closed = [t for slash, t in opened if slash]
    if stack != closed:
        failed.append("mismatched closing tag")

    for name, needle in MANDATORY.items():
        if needle not in text:
            failed.append(f"missing {name} step")

    steps = re.findall(r"^\s*(\d+)/", text, re.M)
    if [int(s) for s in steps] != list(range(1, len(steps) + 1)):
        failed.append("step numbering")
    if len(steps) < 5:
        failed.append("fewer than 5 steps")
    if re.search(r"^\s*\d+\.\s", text, re.M):
        failed.append("used 1. instead of 1/")

    got_plan = PLAN_FIRST in text
    got_execute = EXECUTE_NOW in text
    if got_plan == got_execute:
        failed.append("planning branch absent or both emitted")
    elif got_plan != wants_plan:
        failed.append("wrong planning branch")

    got_independent = INDEPENDENT in text
    got_self = SELF_REVIEW in text
    if got_independent == got_self:
        failed.append("review branch absent or both emitted")
    elif got_independent != wants_independent:
        failed.append("wrong review branch")

    context = text.split("</CONTEXT>")[0]
    if "the user" in context.lower():
        failed.append("third-person CONTEXT")

    if re.search(r"[一-鿿]", text):
        failed.append("CJK degeneration")
    lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) > 10]
    if lines and max(lines.count(ln) for ln in set(lines)) > 3:
        failed.append("repetition loop")

    return failed


@dataclass
class Result:
    model: str
    draft: str
    run: int
    seconds: float
    out_tokens: int
    in_tokens: int
    cost: float = 0.0
    backend: str = ""
    pin: str = ""
    effort: str = ""
    reasoning_tokens: int = 0
    finish_reason: str = ""
    retried: bool = False
    failed: list[str] = field(default_factory=list)
    error: str | None = None
    skipped: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and not self.skipped and not self.failed

    @property
    def label(self) -> str:
        label = f"{self.model}@{self.pin}" if self.pin else self.model
        return f"{label}~{self.effort}" if self.effort else label


class Budget:
    """Running total of real spend, shared across bench threads."""

    def __init__(self, limit: float) -> None:
        self.limit = limit
        self.spent = 0.0
        self._lock = threading.Lock()

    def exhausted(self) -> bool:
        with self._lock:
            return self.spent >= self.limit

    def add(self, cost: float) -> None:
        with self._lock:
            self.spent += cost


def split_spec(spec: str) -> tuple[str, str, str]:
    """`model[@provider-tag][~effort]` -> (model, pin, effort); absent parts are ""."""
    rest, _, effort = spec.partition("~")
    model, _, pin = rest.partition("@")
    return model, pin, effort


def _call(
    cfg: Settings, model: str, pin: str, draft: str, sys_prompt: str, effort: str = ""
) -> tuple[str, dict[str, object]]:
    """One gated OpenRouter call; returns the text and the raw response body."""
    body: dict[str, object] = {}
    # A pin routes to one specific endpoint (e.g. `deepinfra/fp4`) and fails rather than
    # silently falling back, so latency and quality are attributable to that backend.
    bench_cfg = replace(
        cfg,
        openrouter_model=model,
        openrouter_provider=pin,
        openrouter_allow_fallbacks=False,
        # The spec, not OPENROUTER_REASONING_EFFORT, decides: no ~effort means no field.
        openrouter_reasoning_effort=effort,
        openrouter_max_tokens=BENCH_MAX_TOKENS,
        timeout=120,
    )
    provider = make_provider(
        "openrouter",
        bench_cfg,
        extra_body={"usage": {"include": True}},
        on_response=body.update,
        title="espanso-prompt-rewriter-bench",
    )
    return provider.generate(draft, sys_prompt, model=model), body


def run_one(
    cfg: Settings,
    spec: str,
    draft_name: str,
    run: int,
    outdir: Path,
    budget: Budget,
    sys_prompt: str | None = None,
) -> Result:
    model, pin, effort = split_spec(spec)
    draft, wants_plan, wants_independent = DRAFTS[draft_name]
    # A --system-prompt-file candidate gets the same token filling as a shipped profile.
    sys_prompt = render(PROFILES["default"] if sys_prompt is None else sys_prompt, cfg.persona)

    if budget.exhausted():
        return Result(
            model,
            draft_name,
            run,
            0.0,
            0,
            0,
            pin=pin,
            effort=effort,
            skipped=True,
            error="budget exhausted",
        )

    started = time.monotonic()
    retried = False
    for attempt in (1, 2):
        try:
            text, body = _call(cfg, model, pin, draft, sys_prompt, effort)
            break
        except Exception as exc:
            message = str(exc)
            if attempt == 1 and any(t in message for t in TRANSIENT):
                retried = True
                time.sleep(3)
                continue
            elapsed = time.monotonic() - started
            return Result(
                model,
                draft_name,
                run,
                elapsed,
                0,
                0,
                pin=pin,
                effort=effort,
                retried=retried,
                error=message,
            )
    elapsed = time.monotonic() - started

    usage = body.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    cost = float(usage.get("cost", 0.0))
    budget.add(cost)
    details = usage.get("completion_tokens_details")
    reasoning_tokens = int(details.get("reasoning_tokens") or 0) if isinstance(details, dict) else 0
    try:
        finish_reason = str(body["choices"][0].get("finish_reason") or "")  # type: ignore[index]
    except (KeyError, IndexError, TypeError, AttributeError):
        finish_reason = ""

    failed = check(text, wants_plan, wants_independent)
    if finish_reason == "length":
        failed.insert(0, "truncated")

    slug = spec.replace("/", "_").replace("@", "__at__").replace("~", "__effort__")
    (outdir / f"{slug}__{draft_name}__{run}.txt").write_text(text, encoding="utf-8")
    return Result(
        model=model,
        draft=draft_name,
        run=run,
        seconds=elapsed,
        out_tokens=int(usage.get("completion_tokens", 0)),
        in_tokens=int(usage.get("prompt_tokens", 0)),
        cost=cost,
        backend=str(body.get("provider", "")),
        pin=pin,
        effort=effort,
        reasoning_tokens=reasoning_tokens,
        finish_reason=finish_reason,
        retried=retried,
        failed=failed,
    )


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


def report(results: list[Result], budget: Budget) -> None:
    by_model: dict[str, list[Result]] = {}
    for r in results:
        by_model.setdefault(r.label, []).append(r)

    header = (
        f"{'model':62s} {'pass':>7s} {'p50 s':>7s} {'p95 s':>7s} "
        f"{'in':>6s} {'out':>6s} {'reas':>6s} {'$/1k':>7s} backend"
    )
    print(f"\n{header}")
    print("-" * len(header))
    rows = []
    for model, rs in by_model.items():
        done = [r for r in rs if r.error is None]
        n_passed = sum(1 for r in rs if r.ok)
        times = [r.seconds for r in done]
        costs = [r.cost for r in done if r.cost]
        rows.append(
            (
                -n_passed / len(rs),
                statistics.median(times) if times else 999.0,
                model,
                f"{n_passed}/{len(rs)}",
                statistics.median(times) if times else float("nan"),
                _p95(times) if times else float("nan"),
                statistics.mean([r.in_tokens for r in done]) if done else 0.0,
                statistics.mean([r.out_tokens for r in done]) if done else 0.0,
                statistics.mean([r.reasoning_tokens for r in done]) if done else 0.0,
                statistics.mean(costs) * 1000 if costs else float("nan"),
                next((r.backend for r in done if r.backend), "?"),
            )
        )
    for _, _, model, passed, p50, p95, tin, tout, reas, per_k, backend in sorted(rows):
        print(
            f"{model:62s} {passed:>7s} {p50:7.1f} {p95:7.1f} "
            f"{tin:6.0f} {tout:6.0f} {reas:6.0f} {per_k:7.2f} {backend}"
        )

    # Split by draft before blaming a backend: a draft every model fails is a prompt problem.
    drafts = list(dict.fromkeys(r.draft for r in results))
    print(f"\n{'passes by draft':62s} " + " ".join(f"{d[:14]:>14s}" for d in drafts))
    for model, rs in by_model.items():
        cells = []
        for d in drafts:
            ds = [r for r in rs if r.draft == d]
            cells.append(f"{sum(r.ok for r in ds)}/{len(ds)}")
        print(f"{model:62s} " + " ".join(f"{c:>14s}" for c in cells))

    print("\nfailures:")
    any_failure = False
    for r in results:
        if r.error:
            any_failure = True
            print(f"  {r.label} {r.draft}#{r.run}: ERROR {r.error[:90]}")
        elif r.failed:
            any_failure = True
            print(f"  {r.label} {r.draft}#{r.run}: {', '.join(r.failed)}")
    if not any_failure:
        print("  none")

    print(f"\ntotal spend: ${budget.spent:.4f} of ${budget.limit:.2f} budget")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models",
        nargs="*",
        default=MODELS,
        help="model[@provider-tag][~effort]: pin one endpoint and/or set reasoning effort "
        "(e.g. openai/gpt-6-luna@openai~low)",
    )
    parser.add_argument("--drafts", nargs="*", default=list(DRAFTS))
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--budget", type=float, default=1.0, help="hard spend ceiling in USD")
    parser.add_argument("--outdir", default="bench-out")
    # A/B a candidate template without adding a throwaway file to
    # src/prompt_workflow/prompts/, where _load_profiles() would pick it up as a profile.
    parser.add_argument(
        "--system-prompt-file", help="score this file instead of the 'default' profile"
    )
    args = parser.parse_args()

    cfg = Settings.load()
    if not cfg.openrouter_api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    budget = Budget(args.budget)
    sys_prompt = (
        # .strip() to match how _load_profiles() reads the shipped profiles.
        Path(args.system_prompt_file).read_text(encoding="utf-8").strip()
        if args.system_prompt_file
        else None
    )

    jobs = [
        (m, d, run) for m in args.models for d in args.drafts for run in range(1, args.runs + 1)
    ]
    print(
        f"{len(jobs)} calls, temperature {cfg.temperature}, "
        f"budget ${args.budget:.2f}, output in {outdir}/"
    )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(run_one, cfg, m, d, run, outdir, budget, sys_prompt) for m, d, run in jobs
        ]
        results = []
        for i, fut in enumerate(futures, 1):
            r = fut.result()
            results.append(r)
            mark = "skip" if r.skipped else "ok  " if r.ok else "FAIL"
            print(f"[{i}/{len(jobs)}] {mark} {r.label} {r.draft}#{r.run} {r.seconds:.1f}s")

    (outdir / "results.json").write_text(
        json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8"
    )
    report(results, budget)


if __name__ == "__main__":
    main()
