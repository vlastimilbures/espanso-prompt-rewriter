# Default prompt: A/B round 2026-10-02 and plan for the OpenRouter run

Continues "Known gaps in the default prompt" in CONTRIBUTING.md. The candidates here are
**not shipped**: `src/prompt_workflow/prompts/default.md` is unchanged. Each candidate is a
complete prompt file for `scripts/bench_models.py --system-prompt-file`.

## What was run here (cloud screen, no OpenRouter)

OpenRouter is not reachable from the cloud session, so this round used Opus subagents as the
rewrite model, with the bench's own checks and two blind Opus judges on top. Opus is much
stronger than flash-lite and gpt-6-luna, so this round **screens** candidates. It cannot
confirm them: gaps 4–6 are failures specific to flash-lite and do not show on Opus.

- 14 drafts, 1 run per prompt: `analysis board code vendor pasted german light big-personal
  question client investor outage quick-external memo`.
- **Mechanical checks** (`check_draft`, `retention`): A and B both pass all 14, retention 1.00.
  On a strong model the checks cannot separate the prompts.
- **Blind pairwise judging**: two independent judges, A/B order randomised, labels hidden.

| Draft | Judge 1 | Judge 2 | Note |
|---|---|---|---|
| analysis | B | B | B asks for a change log and splits the rise into mix vs rate |
| board | B | B | B: named sections, costed options, existing validation findings as input |
| code | B | B | B: threshold stated as an assumption, not a `[REVIEW]` (gap 3 works) |
| question | B | B | A chose `.md` with headings for "keep it short"; B chose plain text |
| big-personal | B | A | |
| outage | B | A | |
| vendor, pasted, german, client, investor | A | B | split |
| light | A | A | B asked for an extra time `[REVIEW]` on a 3-line reply |
| quick-external | A | A | |
| memo | A | A | B chose `.md`; A chose a one-page plain-text memo (run variance) |

**Total: B 15, A 13 of 28.** Both judges agreed on 7 drafts. B won all 4 document or analysis
drafts it was agreed on (gaps 2 and 3 work). A won all 3 short messages it was agreed on: B's
per-source `INPUTS` rule added `[REVIEW]` noise to short replies. Candidate **C** keeps B's
gains and targets that noise.

### Recurring weaknesses both judges named in both prompts

1. The self-review asks for "clear headings" and "show calculation steps" on 3-line replies
   with no headings (= gap 7). Both judges raised it independently.
2. The same `[REVIEW]` item is asked for twice (in INSTRUCTIONS and INPUTS), or a flag
   contradicts a constraint, e.g. "may I share the rate card?" next to "Out of scope: sharing
   the rate card".
3. `[REVIEW]` asks for material the task doesn't need (Sam's original message, the Q2 return,
   an agreement reference) or that the draft says is already attached.
4. The independent domain-agent review plus "validate with me" on trivial outside emails is
   heavy. This is a **design decision**, not a bug: CONTRIBUTING fixes "a quick email to a
   regulator is (a)". Revisit only if you want it to change (see Decision 2).
5. Fixed step 2 ("Load and validate all inputs…") appears even when there are no inputs. It is
   golden-template wording, so it is left as is.

## Candidates

All candidates keep every bench-scored phrase and `{{PERSONA_RULE}}`. Each builds on the one
before it.

| File | Changes vs. current `default.md` | Gaps |
|---|---|---|
| `B.md` | planning and review variants "word for word" (bans "Plan the analysis…"); yes/no outside-reader question for the review step, naming Kunde/cliente/zákazník; document-producing step names its sections (board paper example); `INPUTS` one line + `[REVIEW]` per source; method choices become stated assumptions, not `[REVIEW]` | 1, 2, 3, 4 |
| `C.md` | B, plus: per-source `INPUTS` only for multi-source documents; never ask for material already pasted, attached, or not needed; each `[REVIEW]` asked once and never against a constraint; "short answer / keep it short" is not a document for `OUTPUTS` | 1–4 + judge findings 2, 3 |
| `D.md` | C, plus self-review lines changed to "show **any** calculation steps" and "**matches the format in OUTPUTS**, scannable, no filler" (example updated too) | + 7 |

D changes fixed wording. If D wins, update `espanso/match/prompts-template.yml` and check
`tests/test_bench.py` in the same PR. The bench matches only the first line of the self-review,
so scoring works unchanged.

## Plan for the local OpenRouter run

Models from CONTRIBUTING:
`google/gemini-3.5-flash-lite@google-ai-studio/flex~minimal openai/gpt-6-luna@openai~low`.

1. **Baseline and candidates, mechanical.** Run each with a separate `--outdir`:
   ```bash
   M="google/gemini-3.5-flash-lite@google-ai-studio/flex~minimal openai/gpt-6-luna@openai~low"
   uv run python scripts/bench_models.py --suite all --runs 3 --models $M --outdir bench-A
   for c in B C D; do
     uv run python scripts/bench_models.py --suite all --runs 3 --models $M \
       --system-prompt-file docs/prompt-candidates/$c.md --outdir bench-$c
   done
   ```
   Read the per-draft table **per model**. Reject a candidate that loses any core-suite pass.
   Watch these drafts:
   - `analysis`, gpt-6-luna, planning check: was 2/3 failing (gap 1)
   - `vendor`, `pasted`, `german`, flash-lite: independent review expected (gap 4)
   - `light`, `big-personal`, flash-lite: **must stay** self-review; v3's regression showed here
   - `question`: half-answers in steps (gap 5, observe only)
   - `injection`, `pasted-injection`: no meta-commentary, tags intact (gap 6, observe only)

2. **Count `[REVIEW]` flags** per draft and model across outdirs. The `.txt` files are named
   `<model>__<draft>__<run>.txt`. Expect C/D ≤ A on the short-message drafts (`light`, `slack`,
   `client`, `quick-external`, `short-external`, `czech`) and ≥ A on `board`, `analysis`,
   `investor`.

3. **Blind pairwise judging** of the best mechanical candidate against A: same model, draft
   and run; A/B order randomised; labels hidden. Use two judges with different framings
   ("better result for the user" and "skeptical receiving assistant"). Ask for `winner` plus a
   ≤20-word reason per pair, and up to 5 weaknesses common to both. Unblind afterwards and
   report **per model**: gpt-6-luna and flash-lite moved in opposite directions last time.
   Focus on `board`, `analysis`, `code`, `investor`, `memo`, `question` and the short
   messages.

4. **Fresh drafts.** Before the final comparison, write 4–5 drafts after freezing the
   candidate: one multi-source document, one short outside email, one internal quick note,
   one code task with an open threshold, and one "keep it short" question.

5. **Ship gate.** No core regressions on either model; edge pass rate ≥ A on both; blind
   preference ≥ A per model; and flag counts on short messages not worse. Then copy the winner
   to `src/prompt_workflow/prompts/default.md`, update `prompts-template.yml` if D, close the
   gaps you fixed in CONTRIBUTING, and put the before/after numbers in the PR.

## Decisions for you (not settled by testing)

1. **Gap 8**: does an internal incident summary for your manager count as (b)? B/C/D say a
   manager is not outside, so (b). Does a fintech PRD carry a "money or compliance
   consequence"? It is still unscored.
2. **Heavy review on short outside emails** (weakness 4): keep the independent review for a
   2-line note to an auditor, or allow a lighter variant? A lighter variant needs a new fixed
   wording in the golden template.
