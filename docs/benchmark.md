# Model benchmark

The default models and the default prompt were chosen with this benchmark, which scores every
rewrite mechanically for template fidelity, prompt injection and language edge cases, latency
and real cost. This page holds the method, every result table and how to run it; it is the
only place the numbers live. The README's [Model benchmark](../README.md#model-benchmark)
section links here.

## Contents

- [At a glance](#at-a-glance)
- [Method](#method)
- [Results](#results)
- [Running it](#running-it)

## At a glance

The shipped prompt of v0.19.0 on each tier's default model, from the first table under
[Results](#results) (`--suite all` plus `--suite holdout`, 3 runs per draft, 2026-10-06):

| Tier | Trigger | Model, endpoint, effort | core | edge | holdout | p50 | p95 | $ per rewrite |
|------|---------|-------------------------|------|------|---------|-----|-----|---------------|
| Standard | `-i-` | `google/gemini-3.5-flash-lite` @ `google-ai-studio/flex`, `minimal` | 23/24 | 76/84 | 23/24 | 2.5 s | 3.5 s | 0.0012 |
| Pro | `-ip-` | `openai/gpt-6-luna` @ `openai`, `low` | 24/24 | 82/84 | 22/24 | 7.5 s | 12.2 s | 0.0004 |

The holdout drafts were frozen before this prompt was benched, and no profile quotes them.
Both models opened `CONTEXT` with the configured persona in every run scored on it. Prices
and endpoints change quickly, so re-run the benchmark before relying on these numbers.

## Method

The rewrite is only useful if the template comes back intact, so the default model and prompt
were chosen with [`scripts/bench_models.py`](../scripts/bench_models.py) rather than by taste. The
`core` suite (the default) sends 8 drafts that cross two independent decisions — *plan first or
execute now* (task complexity) and *independent or self review* (who reads the result). The
`edge` suite (`--suite edge` or `--suite all`) adds 28 drafts that probe the rest: prompt
injection, questions, pasted emails, Czech, German and Spanish drafts, a draft stating its own
role, code, and outside readers that are only implied. The `holdout` suite (`--suite holdout`,
not part of `all`) is 8 drafts frozen on 2026-10-06 that no prompt was tuned on; it estimates
how a prompt does on drafts it was not shaped on, and a test keeps every profile from quoting
it or any other bench draft (#48). Every response is scored mechanically:
all six sections present and correctly closed, mandatory steps verbatim, `1/ 2/ 3/` numbering,
both branch choices right, no leaked scaffolding or `[domain]` placeholder, the draft's language
and role respected, the configured persona in `CONTEXT` (unless the draft states its own role;
not scored on a `--persona none` run), `OUTPUTS` matching the deliverable, sentences from the
start, middle and end of pasted material copied word for word into `INPUTS`, no `INPUTS` or
`OUTPUTS` that opens with "None" and then asks for something with `[REVIEW: …]`, no
third-person context, no degeneration. Each check's name starts with its kind: `struct:` (the
template's form and fixed wordings, the same for every draft), `branch:` (the plan and review
choice, scored only on drafts with a label, none where either variant is defensible (the
contested `quick-ceo`, `memo` and `outliers` review choices among them), and
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
and a key found only there was not carried into the rewrite. A `[REVIEW] flags per rewrite by
draft` table gives the mean number of `[REVIEW: …]` flags per rewrite, a metric to read for
too few or too many flags rather than a check. `rep` counts how many rewrites had a `<CONTEXT>…</GOAL>` slip (seen on flash-lite), which the CLI repairs
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

The prompt's examples and wordings no longer quote the bench's drafts (#48): the examples are
new situations, and the decision rules name other readers and lengths than the drafts do
("a brief letter to a tax office" instead of "a quick email to a regulator", no "Herr
Maier", landlord, help-center, "two-line" or "one-paragraph"). Run on 2026-10-06 with
`--suite all` plus `--suite holdout`, `--runs 3 --persona example --max-tokens 2400`, the
decontaminated prompt against the first v0.19.0 wording (#163, below) on the same bench, with
the contested `quick-ceo`, `memo` and `outliers` review labels accepting either answer:

| Setup | Prompt | core | edge | holdout | persona in `CONTEXT` | `[REVIEW]` per rewrite | `rep` | p50 | p95 | $ per rewrite |
|-------|--------|------|------|---------|----------------------|------------------------|-------|-----|-----|---------------|
| `google/gemini-3.5-flash-lite` @ `google-ai-studio/flex`, effort `minimal` (standard) | decontaminated | 23/24 | 76/84 | 23/24 | 128/128 | 0.62 | 3 | 2.5 s | 3.5 s | 0.0012 |
| | first decontaminated draft | 23/24 | 75/84 | 18/24 | 129/129 | 0.61 | 33 | 2.3 s | 3.3 s | 0.0012 |
| | #163 | 24/24 | 73/84 | 18/24 | 129/129 | 0.66 | 3 | 2.3 s | 3.3 s | 0.0012 |
| `openai/gpt-6-luna` @ `openai`, effort `low` (pro) | decontaminated | 24/24 | 82/84 | 22/24 | 129/129 | 0.91 | 0 | 7.5 s | 12.2 s | 0.0004 |
| | first decontaminated draft | 24/24 | 83/84 | 23/24 | 129/129 | 0.80 | 0 | 6.8 s | 11.6 s | 0.0004 |
| | #163 | 24/24 | 81/84 | 24/24 | 129/129 | 0.85 | 0 | 5.9 s | 10.2 s | 0.0004 |

The first decontaminated draft made flash-lite close `CONTEXT` with `</GOAL>` in 33 of 132
rewrites (every run of `quick-external`, `quick-ceo`, `memo`, `slack` and `supplier`), against
3 with the #163 prompt. The CLI repairs that slip before pasting, but it was a regression. Its
examples differed from the shipped ones only on the surface ("quick recap … only I will read
it", "email Ms Lopez …, keep it brief: …", "Brief: a few sentences."); bringing their wording
back to the #163 shape ("quick summary of my own notes …, only for me", "brief email to Ms
Lopez … asking her to move …", "Keep it brief: a few sentences.") while still quoting no draft
brought it back to 3, and that is the shipped prompt. A gpt-6-luna re-run on the shipped
wording (2026-10-06, $0.047) is within noise of the first draft: its failures are the known
`cap-thread` `.md` line (2 of 3) and `ho-wiki` given the other review branch (2 of 3), with no
slips.

By kind (132 runs per row), the shipped prompt against #163 on flash-lite: `struct` 131 of 131
and 132, `branch` 116 of 122 and 118 of 123, `draft` 128 of 131 and 119 of 132 (one run of the
shipped prompt ended in an HTTP 400 from the provider and is counted as a failure, not scored
by kind). On gpt-6-luna, the first draft against #163: `struct` 132 and 130, `branch` 122 and
123 of 123, `draft` 131 and 131. Every interval overlaps. The new `draft:` checks catch
flash-lite on pasted material: with the #163 prompt it summarised `pasted` instead of copying
it in 3 of 3 runs, and `ho-signing` (the holdout's pasted email) in 3 of 3; with the shipped
prompt it copied both in 3 of 3. The other failures are the known gaps: flash-lite gives
`long-thread` the self-review (3 of 3) and `cap-thread` the `.md` line (3 of 3).

The first v0.19.0 wording (#163) was run earlier on 2026-10-06 with `--suite all --runs 3
--persona example --max-tokens 2400`, against the v0.14.0 prompt (shipped until v0.18.0) on
the same bench, before the contested labels were relaxed. It opens both examples' `CONTEXT`
with the configured persona (#49), tells the inputs step to state an assumption instead of
asking when step 1 says execute (#46), and words the independent review so it can run
without a separate agent (#46):

| Setup | Tier | Prompt | core | edge | persona in `CONTEXT` | `rep` | kept | p50 | p95 | $ per rewrite |
|-------|------|--------|------|------|----------------------|-------|------|-----|-----|---------------|
| `google/gemini-3.5-flash-lite` @ `google-ai-studio/flex`, effort `minimal` | standard (default) | #163 | 24/24 | 75/84 | 105/105 | 2 | 0.99 | 2.0 s | 2.9 s | 0.0012 |
| | | v0.14.0 | 23/24 | 69/84 | 100/105 | 7 | 1.00 | 2.0 s | 3.1 s | 0.0012 |
| `openai/gpt-6-luna` @ `openai`, effort `low` | pro (default) | #163 | 23/24 | 81/84 | 105/105 | 0 | 1.00 | 5.8 s | 10.8 s | 0.0004 |
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

#### Per-draft findings of the 2026-10 round

The 2026-10 prompt round (with `--persona example`) behind CONTRIBUTING's
[known gaps](../CONTRIBUTING.md#known-gaps-in-the-default-prompt), on the v0.14.0 prompt:

- **Review branch on flash-lite.** Over 6 runs each, flash-lite gave `faq` and `quick-ceo` the
  independent review 6/6 (the 0.13.0 prompt: 0/6 and 1/6), and `light`, `teams-jana`, `memo`,
  `slack`, `sql`, `outliers` and `code` the self-review 6/6.
- **Review branch on gpt-6-luna.** With a fixed fictitious persona, `outliers`, `sql` and
  `memo` got the self-review in every run. Over 123 calls, gpt-6-luna chose the wrong review
  branch once (`light`, 1 of 3).
- **Injection meta-commentary.** With a fixed fictitious persona, flash-lite wrote no CONTEXT
  about an override attempt in 9 runs of `injection` and `pasted-injection`.
- **Persona bleed.** It did not reproduce with a neutral persona (`vague` and a near-empty
  draft, 12 gpt-6-luna and 3 flash-lite runs).
- **Prompt length.** The 2026-09 and 2026-10-02 reworks made the system prompt about 40%
  longer. Against the shorter prompt, p50 latency stayed at 2.1 s on flash-lite and fell from
  7.6 s to 6.6 s on gpt-6-luna, and the cost per 1,000 calls rose from $0.96 to $1.13 on
  flash-lite and from $0.34 to $0.35 on gpt-6-luna: length does not drive latency, and cost rose
  about 18% on flash-lite and is flat on gpt-6-luna.
- **Pasted material (#42).** gpt-6-luna copied it in every run (`pasted`, `outage`,
  `pasted-injection`, the 35-line `long-thread` and the 57-line `cap-thread`, 6/6 each).
  flash-lite copied `outage`, `pasted-injection` (6/6), `long-thread` and `cap-thread` (5/6),
  but summarised the short email in `pasted` (5 of 6 runs). It also gave `long-thread` the
  self-review in 6 of 6 runs, and `cap-thread` the `.md` line in 6 of 6, where the bench
  expects a message. The 0.13.0 prompt did the same (`long-thread` 2 of 3, `cap-thread` 3 of
  3). The v0.19.0 prompt copies `pasted` (see [Current](#current-prompt-of-v0190)).
- **Noise.** At 3 runs, a single draft flipped between 0/3 and 2/3 from noise alone (seen on
  `big-personal` and `memo`).

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

## See also

- [Profiles](profiles.md): what the `default` and `general` profiles do
- [Configuration](configuration.md#openrouter): the model and endpoint settings
- [CONTRIBUTING.md, "Change the default prompt"](../CONTRIBUTING.md#change-the-default-prompt): when a prompt change needs a re-run
