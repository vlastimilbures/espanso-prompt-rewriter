# Default prompt: A/B rounds 1–2 (2026-10-02) and plan for the OpenRouter run

Continues "Known gaps in the default prompt" in CONTRIBUTING.md. The candidates here are
**not shipped**: `src/prompt_workflow/prompts/default.md` is unchanged. Each candidate is a
complete prompt file for `scripts/bench_models.py --system-prompt-file`.

## Round 1: gap fixes (screened here, no OpenRouter)

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

## Round 1 candidates

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

## Round 2: best-practice candidates (screened here)

All round-2 candidates build on C. Each change applies a prompt-engineering best practice.

| File | Adds | Status |
|---|---|---|
| `E.md` | **Decide first** (`<procedure>`: deliverable → planning → review → language, then write); a **reason** on key rules (first-person CONTEXT, no invented facts); **done-criteria in GOAL** (deliverable, reader, what makes it done); **one** consolidated fixed-wording rule in place of four repeats; tone and form of address for outside messages; shorter `final_check` | screened |
| `F.md` | E + a **second, contrasting example** (plan-first, independent review, multi-source document with named sections and one `[REVIEW]` per source), set outside finance to limit domain bleed | screened |
| `G.md` | E + round-2 judge findings: one or two work steps for short messages, with no duplicate "produce" step; no added offers, commitments or content; never `None` followed by a `[REVIEW]`; a missing outside recipient's name is a fair flag; a question gets no prompt to cite paragraphs or sources the draft doesn't name | **not screened** |
| `H.md` | G + D's self-review wording (gap 7) | **not screened** |

**Setup:** 18 drafts, including 4 fresh ones written before the run and kept out of every
prompt (multi-source vendor review for a procurement committee, a short email to a landlord, a
quick Teams message to Jana, and a pandas outlier script). The rewrite model was Opus, 1 run per
prompt, with A, C, E and F compared. Two blind judges ranked all four rewrites per draft, with
labels shuffled per draft.

- **Mechanical checks:** all 4 prompts passed every bench check, and all 4 picked the expected
  variants on the fresh drafts. Total `[REVIEW]` flags: A 22, C 13, E 19, F 17.
- **Rankings** (1 = best). Judge 1 did not rank `investor` or `outage`.

| | A | C | E | F |
|---|---|---|---|---|
| Judge 1, mean rank (16 drafts) | 2.62 | 3.00 | **2.00** | 2.38 |
| Judge 2, mean rank (18 drafts) | **2.22** | 2.72 | 2.50 | 2.56 |
| First places, judge 1 / judge 2 | 3 / 6 | 2 / 3 | **8 / 7** | 3 / 2 |
| Short and outside messages (judge 1 / judge 2) | 2.50 / 3.00 | 3.25 / 2.56 | **1.75 / 2.00** | 2.50 / 2.44 |
| Documents (judge 1 / judge 2) | 3.20 / **1.50** | 2.40 / 2.67 | **2.00** / 3.17 | 2.40 / 2.67 |
| Fresh drafts (judge 1 / judge 2) | 2.75 / 2.50 | 2.75 / 2.75 | 2.50 / 2.75 | **2.00 / 2.00** |

**Reading the results:**
- Per draft, the two judges agreed closely; on 7 drafts their orders were identical. The quality
  differences between individual rewrites are therefore real.
- With one run per prompt, though, a single good or bad rewrite moves a prompt's mean a lot.
  Across prompts the results are a lean, not a verdict.
- **E** is the most consistent winner. It won `analysis`, `code`, `pasted`, `client` and
  `fresh-landlord` for both judges, so its gains are on outside messages and on fidelity.
- E lost `light` and `question` for both judges. On `light` it added an unneeded time flag; on
  `question` it invited citations. G targets both.
- **A** stays strong on documents for judge 2 (`board`, `memo`, `investor`). E's procedure did
  not hurt short tasks, but it has not shown a document gain.
- **F's** second example helped the fresh drafts most (best mean for both judges) and did not
  cause copying. Whether it causes domain bleed on flash-lite is unknown.
- **C** came last: with flags trimmed and no other change, the rewrites were thinner.

**Weaknesses both judges still saw in most rewrites** (G covers 1–3; 4 is gap 7 and covered by H;
5 is by design):
1. INPUTS says "None needed" and then adds a `[REVIEW]`.
2. Added content the draft never asked for: offers of help, handover plans, an "outlook/asks"
   slide, example drivers.
3. Short messages split into trivial sub-steps, plus a duplicate "write the X" step.
4. The self-review demands headings and calculation steps on Teams messages and 3-line replies.
5. The fixed "ask up to 5 questions" step and the independent review add overhead to trivial
   outside emails.

## Plan for the local OpenRouter run (next)

Models:
`google/gemini-3.5-flash-lite@google-ai-studio/flex~minimal openai/gpt-6-luna@openai~low`.
Candidates for the run: **A (baseline), E, F, G, H**. Drop B, C and D: C lost in round 2, and H
supersedes D.

1. **Add the 4 fresh drafts to the bench first** as an `edge` group in
   `scripts/bench_models.py` `DRAFTS`, with expectations:

   | Draft | Planning | Review | `outputs` |
   |---|---|---|---|
   | `vendor-review` | plan-first | independent (committee) | `doc` |
   | `landlord` | execute | independent (outside) | `message` |
   | `teams-jana` | execute | self | `message` |
   | `outliers` | either | self | `code` |

   Freeze them before running.

2. **Mechanical run.** Run each candidate with its own `--outdir`:
   ```bash
   M="google/gemini-3.5-flash-lite@google-ai-studio/flex~minimal openai/gpt-6-luna@openai~low"
   uv run python scripts/bench_models.py --suite all --runs 3 --models $M --outdir bench-A
   for c in E F G H; do
     uv run python scripts/bench_models.py --suite all --runs 3 --models $M \
       --system-prompt-file docs/prompt-candidates/$c.md --outdir bench-$c
   done
   ```
   Reject any candidate that loses a core-suite pass on either model. Drafts to watch:
   - `analysis`, gpt-6-luna: planning wording (gap 1). E–H keep one consolidated
     "word for word" rule; check it still holds on the small model.
   - `vendor`, `pasted`, `german`, `landlord`, flash-lite: independent review expected (gap 4)
   - `light`, `big-personal`, `teams-jana`, flash-lite: must stay self-review (the v3 regression)
   - `injection`, `pasted-injection`, flash-lite: the longer prompt and F's second example must
     not bring back meta-commentary or broken tags (gap 6)
   - F only: check `vague` and the persona run (`PROMPT_PERSONA` set) for bleed from the
     bike-sharing example
   - Latency and cost per model, since E–H are 20–40% longer than A

3. **Flag count.** Count `[REVIEW` per draft, model and outdir from the `.txt` files
   (`<model>__<draft>__<run>.txt`). Expect fewer flags than A on short messages, no
   "None + [REVIEW]" pairs (grep `None` near `[REVIEW`), and a recipient-name flag on outside
   messages.

4. **Blind ranking** of the top 2–3 mechanical survivors against A:
   - Use the round-2 setup: ranking 3–4 rewrites per draft, labels shuffled per draft, two
     judges with different framings, compact JSON output. Take **run 1 of each model**, so
     there are two sets.
   - Report mean rank and first places **per model** and per group (documents, short messages,
     fresh drafts).
   - Read the documents group closely: it is where A still led for judge 2.

5. **Ship gate.**
   - Pass rate on the edge suite: no core regressions and ≥ A on both models.
   - Blind mean rank better than A on both models, and not worse on documents.
   - `[REVIEW]` flags on short messages ≤ A.
   - If H wins, update `espanso/match/prompts-template.yml` (self-review lines) and confirm
     `tests/test_bench.py` passes.
   - Then copy the winner to `src/prompt_workflow/prompts/default.md`, add the 4 drafts'
     expectations (already in step 1), update "Known gaps" in CONTRIBUTING (close gaps 1–4 and
     7 if fixed; record what is still open), and put before/after numbers per model in the PR.

## Decisions for you (not settled by testing)

1. **Gap 8**: does an internal incident summary for your manager count as (b)? B/C/D say a
   manager is not outside, so (b). Does a fintech PRD carry a "money or compliance
   consequence"? It is still unscored.
2. **Heavy review on short outside emails** (weakness 4): keep the independent review for a
   2-line note to an auditor, or allow a lighter variant? A lighter variant needs a new fixed
   wording in the golden template.
