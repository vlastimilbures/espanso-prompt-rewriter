# Model benchmark

The default models and the default prompt were chosen with this benchmark. The README's
[Model benchmark](../README.md#model-benchmark) section has the summary.

## Method

The rewrite is only useful if the template comes back intact, so the default model and prompt
were chosen with [`scripts/bench_models.py`](../scripts/bench_models.py) rather than by taste. The
`core` suite (the default) sends 8 drafts that cross two independent decisions — *plan first or
execute now* (task complexity) and *independent or self review* (who reads the result). The
`edge` suite (`--suite edge` or `--suite all`) adds 28 drafts that probe the rest: prompt
injection, questions, pasted emails, Czech, German and Spanish drafts, a draft stating its own
role, code, and outside readers that are only implied. Every response is scored mechanically:
all six sections present and correctly closed, mandatory steps verbatim, `1/ 2/ 3/` numbering,
both branch choices right, no leaked scaffolding or `[domain]` placeholder, the draft's language
and role respected, the configured persona in `CONTEXT` (unless the draft states its own role;
not scored on a `--persona none` run), `OUTPUTS` matching the deliverable, sentences from the
start, middle and end of pasted material copied word for word into `INPUTS`, no `INPUTS` or
`OUTPUTS` that opens with "None" and then asks for something with `[REVIEW: …]`, no
third-person context, no degeneration. Each check's name starts with its kind: `struct:` (the
template's form and fixed wordings, the same for every draft), `branch:` (the plan and review
choice, scored only on drafts with a label, none where either variant is defensible, and
only on rewrites that chose exactly one variant of that step; a missing or doubled variant is
a `struct:` failure, never a branch pass) or `draft:` (what the draft's role, language,
deliverable and pasted material call for, the configured persona, and how `INPUTS` and
`OUTPUTS` are filled). Besides the overall `pass` column, the report gives each kind's
pass rate over the runs scored on it, and the overall one again, with a Wilson 95% interval: at
3 to 6 runs per draft the interval spans tens of points, and most failures are one branch
choice on a few borderline drafts, so compare two runs per kind and against the intervals. A
`kept` column reports the share of the draft's specifics carried over, found at the start of a
word (an all-caps key only in capitals). It leaves `INPUTS` out unless the draft pastes
material: otherwise `INPUTS` is often the draft quoted as is (a non-English draft's original),
and a key found only there was not carried into the rewrite. `rep` counts how many rewrites had a `<CONTEXT>…</GOAL>` slip (seen on flash-lite), which the CLI repairs
before pasting; those are scored on the repaired text. Cost and token counts come from
OpenRouter's own usage data. Every HTTP attempt's reported cost counts against `--budget`,
failed and retried ones included; an attempt that reports no cost is counted apart and never
as 0, so the total spend is a lower bound and the report says how many attempts it leaves out.
`p50` and `p95` time only the attempt that answered (a failed attempt and the wait before a
retry are left out; retries are kept in `results.json`), and `p95` is the nearest-rank 95th
percentile, shown only from 20 runs on (`-` below). A run skipped once the budget is spent is
counted under `skip`, not in `pass`. A run that fails in an unexpected way is recorded as that
run's error, and `results.json` is rewritten after every finished run, so a crash keeps what
finished. Each model is given as
`model@endpoint~effort`: the endpoint is pinned with fallbacks off, because the same model on
another host can differ several-fold in latency and cost, and `~effort` sets the reasoning
effort. The report splits passes by draft, so a draft every model fails shows up as a prompt
problem rather than a model one. By default a run renders the same fictitious persona
(`--persona example`), so two machines score the same system prompt whatever their own
`PROMPT_PERSONA`. Every run writes `meta.json` (git commit, prompt hashes, persona mode) next to
its outputs in `bench-out/<UTC timestamp>/`. `--persona env` renders your own persona instead;
keep those outputs to yourself.

Every result below the first table was scored before the persona and "None"/`[REVIEW` checks
and the `INPUTS`-free `kept` were added (#48, after 0.17.0), so it is not directly comparable
with a later run: the new checks can only lower a pass count, and `kept` can only drop. Re-run
both prompts with the same bench before comparing them, as the first table does.

## Results

Each heading names the release whose `default` prompt was scored. The prompt is unchanged
between releases a heading spans; a release that changes it gets new results.

### Current: prompt of v0.19.0

Run on 2026-10-06 with `--suite all --runs 3 --persona example --max-tokens 2400`, the new
prompt and the v0.14.0 prompt (shipped until v0.18.0) side by side on the same bench. The
prompt now opens both examples' `CONTEXT` with the configured persona (#49), tells the inputs
step to state an assumption instead of asking when step 1 says execute (#46), and words the
independent review so it can run without a separate agent (#46):

| Setup | Tier | Prompt | core | edge | persona in `CONTEXT` | `rep` | kept | p50 | p95 | $ per rewrite |
|-------|------|--------|------|------|----------------------|-------|------|-----|-----|---------------|
| `google/gemini-3.5-flash-lite` @ `google-ai-studio/flex`, effort `minimal` | standard (default) | v0.19.0 | 24/24 | 75/84 | 105/105 | 2 | 0.99 | 2.0 s | 2.9 s | 0.0012 |
| | | v0.14.0 | 23/24 | 69/84 | 100/105 | 7 | 1.00 | 2.0 s | 3.1 s | 0.0012 |
| `openai/gpt-6-luna` @ `openai`, effort `low` | pro (default) | v0.19.0 | 23/24 | 81/84 | 105/105 | 0 | 1.00 | 5.8 s | 10.8 s | 0.0004 |
| | | v0.14.0 | 22/24 | 74/84 | 96/105 | 0 | 1.00 | 6.5 s | 10.7 s | 0.0003 |

By kind (all 108 runs per row; Wilson 95% intervals in the report), the new prompt against
the old: flash-lite `struct` 108 and 108, `branch` 101 and 97 of 102, `draft` 100 and 97;
gpt-6-luna `struct` 107 and 108, `branch` 101 and 100 of 102, `draft` 106 and 98. Every
kind's intervals overlap; the clear difference is the persona rate. The old prompt ran on the
bench of v0.18.0, which differs only in the review phrase it looks for. The persona is scored on the 35 drafts that state no
role of their own. The new inputs-step sentence was copied word for word in 215 of 216
rewrites. gpt-6-luna's one `struct` failure is a paraphrase of the new review wording ("Use a
separate supplier communications domain expert if you can run one"), and one more rewrite put
the domain in place of "that domain"; the old wording was copied exactly in all 53 of its
independent reviews. The remaining failures are the known gaps: flash-lite summarises `pasted`
and `outage` instead of copying them, and both models give `cap-thread` the `.md` line.

A blind pairwise comparison by Opus judges (run 1 of each draft and model, 72 pairs, A/B order
randomised, the changed fixed wordings masked in both) preferred the new prompt in 24 pairs and
the old in 30, with 18 ties (flash-lite 12, 14 and 10; gpt-6-luna 12, 16 and 8). Five of the
new prompt's wins were the persona; without the six pairs judged on it, the old prompt led 29
to 19, a difference a sign test does not separate from chance (p = 0.19, one run per pair).
The judges named run-to-run differences (an invented detail, a sharper work step) rather than
anything the change touches.

### Prompt of v0.14.0 (to v0.18.0)

Run on 2026-10-04 with `--persona example` and `--max-tokens 2400`, before the persona and
"None"/`[REVIEW` checks:

| Setup | Tier | core | edge | kept | p50 | p95 | $ per rewrite |
|-------|------|------|------|------|-----|-----|---------------|
| `google/gemini-3.5-flash-lite` @ `google-ai-studio/flex`, effort `minimal` | standard (default) | 30/30 | 102/120 | 1.00 | 2.0 s | 3.2 s | 0.0012 |
| `openai/gpt-6-luna` @ `openai`, effort `low` | pro (default) | 23/24 | 98/99 | 1.00 | 5.6 s | 9.2 s | 0.0004 |

Every draft ran 3 times, and the drafts that check pasted material and the review step ran 6
times (14 on flash-lite, 5 on gpt-6-luna). Most of flash-lite's edge failures are on drafts with
pasted material: `pasted` is summarised instead of copied, `long-thread` gets the self-review,
and `cap-thread` gets the `.md` line where the bench expects a message (see
[CONTRIBUTING.md](../CONTRIBUTING.md#known-gaps-in-the-default-prompt)). The 0.13.0 prompts
(`default` on flash-lite, `default-pro` on gpt-6-luna), scored with the same checks, reached
22/27 and 66/90 on flash-lite and 24/24 and 75/84 on gpt-6-luna. Those numbers cover a different
mix of drafts and runs, and mix fresh `--persona example` runs with outputs saved before the
bench had a fixed persona.

The tables below come from the bench before it had a fixed persona: it rendered the runner's
own `PROMPT_PERSONA` (today's `--persona env`), so they cannot be reproduced exactly.

### Prompt of v0.11.0 (older checks)

Run on 2026-09-30:

| Setup | Tier | core | edge | kept | p50 | p95 | $ per rewrite |
|-------|------|------|------|------|-----|-----|---------------|
| `google/gemini-3.5-flash-lite` @ `google-ai-studio/flex`, effort `minimal` | standard (default) | 23/24 | 55/60 | 1.00 | 1.9 s | 2.9 s | 0.0010 |
| `openai/gpt-6-luna` @ `openai`, effort `low` | pro (default) | 24/24 | 57/60 | 1.00 | 5.0 s | 10.0 s | 0.0004 |

The prompt before 0.11.0, scored with the same checks, reached 12/24 and 15/60 on flash-lite and
21/24 and 43/60 on gpt-6-luna. Most of the gap is emails and code given the `.md` line, drafts
in other languages answered in that language (gpt-6-luna even translated the fixed steps), and
flash-lite not spotting a vendor, partner or customer as an outside reader. It had scored 24/24
on the older `core` checks. In a one-off blind pairwise comparison of 168 rewrite pairs (Opus
judges, A/B order randomised), judged by hand: no judge prompt, pairing script or verdicts
are in the repository, so it cannot be reproduced. The 0.11.0 prompt was preferred 117 to 32 with 19 ties: 70 to 12 on
flash-lite, 47 to 20 on gpt-6-luna, and 28 to 3 on 6 drafts written after the prompt was frozen.

### Model choice: prompt of v0.10.0 (older `core` checks)

Run on 2026-09-29, 24 calls per setup, with the prompt shipped from v0.7.0 to v0.10.0:

| Setup | Tier | Pass | p50 | p95 | $ per rewrite |
|-------|------|------|-----|-----|---------------|
| `google/gemini-3.5-flash-lite` @ `google-ai-studio/flex`, effort `minimal` | standard (default) | 24/24 | 2.0 s | 2.6 s | 0.0009 |
| `google/gemini-3.1-flash-lite` @ `google-ai-studio/flex`, effort `minimal` | standard | 24/24 | 2.2 s | 2.8 s | 0.0006 |
| `google/gemini-3.5-flash-lite` @ `google-vertex/global`, effort `minimal` | standard | 24/24 | 2.4 s | 2.9 s | 0.0018 |
| `google/gemini-2.5-flash-lite` @ `google-ai-studio/flex` (previous default) | standard | 14/24 | 2.1 s | 3.0 s | 0.0002 |
| `openai/gpt-6-luna` @ `openai`, effort `low` | pro (default) | 24/24 | 5.4 s | 8.3 s | 0.0004 |
| `google/gemini-3.8-flash` @ `google-ai-studio`, effort `low` | pro | 24/24 | 3.1 s | 5.4 s | 0.0036 |

Pass rate alone did not pick the pro model. Reading the outputs side by side, `gpt-6-luna`
wrote the most rigorous prompts: claims tied to sources, targeted `[REVIEW: …]` flags, and it
never invented anything. `gemini-3.8-flash` was faster but cited regulatory circulars the draft
never mentioned in 3 of 24 outputs. `inception/mercury-2.5` scored 10/24 and was dropped.

## Running it

> [!NOTE]
> OpenRouter's models, endpoints and prices change quickly, so re-run the benchmark before
> relying on these results.

```bash
uv run python scripts/bench_models.py --models google/gemini-3.5-flash-lite@google-ai-studio/flex~minimal --runs 3
uv run python scripts/bench_models.py --suite all --runs 3                 # core + edge drafts
uv run python scripts/bench_models.py --system-prompt-file candidate.md   # A/B a prompt change
uv run python scripts/bench_models.py --profile general --suite all       # the local triggers' profile
```
