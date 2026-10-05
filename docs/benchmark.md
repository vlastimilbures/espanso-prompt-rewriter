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
and role respected, `OUTPUTS` matching the deliverable, sentences from the start, middle
and end of pasted material copied word for word into `INPUTS`, no third-person context, no
degeneration. A `kept` column reports the share of the draft's specifics carried over, and `rep`
how many rewrites had a `<CONTEXT>…</GOAL>` slip (seen on flash-lite), which the CLI repairs
before pasting; those are scored on the repaired text. Cost and token counts come from
OpenRouter's own usage data. Each model is given as
`model@endpoint~effort`: the endpoint is pinned with fallbacks off, because the same model on
another host can differ several-fold in latency and cost, and `~effort` sets the reasoning
effort. The report splits passes by draft, so a draft every model fails shows up as a prompt
problem rather than a model one. By default a run renders the same fictitious persona
(`--persona example`), so two machines score the same system prompt whatever their own
`PROMPT_PERSONA`. Every run writes `meta.json` (git commit, prompt hashes, persona mode) next to
its outputs in `bench-out/<UTC timestamp>/`. `--persona env` renders your own persona instead;
keep those outputs to yourself.

## Results

Each heading names the release whose `default` prompt was scored. The prompt is unchanged
between releases a heading spans; a release that changes it gets new results.

### Current: prompt of v0.14.0 (unchanged through 0.17.0)

Run on 2026-10-04 with `--persona example` and `--max-tokens 2400`:

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
on the older `core` checks. In a blind pairwise comparison of 168 rewrite pairs (Opus judges,
A/B order randomised), the 0.11.0 prompt was preferred 117 to 32 with 19 ties: 70 to 12 on
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
