# Contributing

Thanks for helping improve Espanso Prompt Rewriter. Bug reports, prompt-quality findings, new
redaction patterns and documentation fixes are all welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on the approach.
- Never put real API keys, customer data or confidential text in code, tests, issues or commits.
  Use obviously fake values (`sk-test…`, `4111 1111 1111 1111`, `john.doe@example.com`).
- Security problems, including ways around the data-protection gate, go through
  [SECURITY.md](SECURITY.md), not public issues.

## Set up

```bash
git clone https://github.com/vlastimilbures/espanso-prompt-rewriter.git
cd espanso-prompt-rewriter
uv sync                  # the project plus its dev tools
uv run pre-commit install
```

## Checks

Run these before opening a pull request. CI runs the same.

```bash
uv run pytest                                   # unit tests, no network
uv run ruff check . && uv run ruff format --check .
uv run mypy                                     # strict, src and scripts
uv run pre-commit run --all-files               # also YAML checks, gitleaks and zizmor
```

If you change a prompt, a provider or anything on the request path, also run the opt-in live
tests (needs `OPENROUTER_API_KEY` in `.env`, costs fractions of a cent):

```bash
uv run pytest -m live
```

## Ground rules

- **The Espanso contract.** The CLI prints the result with no trailing newline, and every failure
  as `[prompt-workflow: …]` with exit code 0. Espanso cannot show stderr or exit codes, so a
  traceback or a blank line reaches the user as a silent failure. Print only through
  `cli._emit()`, which strips control and invisible characters from whatever gets pasted.
- **The gate.** Build providers only through `factory.make_provider()`. It wraps every provider
  that can send the draft off the machine in `GatedProvider`: the cloud ones always, a local one
  when its base URL is not loopback or the Ollama model is a cloud model.
- **Cross-platform.** Everything must work on macOS and Windows. Use `pathlib` and explicit
  timeouts.
- **Tests.** Unit tests never touch the network; use the `fake_http` fixture in
  `tests/conftest.py`, which records requests and replays responses, or `stub_provider` to
  replace the provider behind a CLI test. Add a test for every
  behaviour change, and a regression test for every bug fix.
- **Triggers.** Keep existing trigger names working. Tests enforce the `-name-` shape, uniqueness
  and no prefix collisions.
- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org/): `feat:`,
  `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:`.
- **Changelog.** Add a line under *Unreleased* in [CHANGELOG.md](CHANGELOG.md) for anything a user
  would notice.

## Project layout

```text
espanso-prompt-rewriter/
├── espanso/                      deployed into Espanso by the installers
│   ├── match/
│   │   ├── prompts-llm.yml       -i- -ip- -if- -il- -ilm- (-ic-): call the CLI
│   │   ├── prompts-core.yml      -prompt- -risk-: static snippets and forms
│   │   └── prompts-template.yml  -p-: the empty golden template, opens with your persona
│   └── config/
│       └── default.yml           optional Espanso settings (--with-config / -WithConfig)
├── src/prompt_workflow/          the prompt-workflow CLI
│   ├── cli.py                    improve and persona commands, the single output sink
│   ├── config.py                 Settings from the environment and .env
│   ├── factory.py                make_provider(): builds providers, decides which are gated
│   ├── gate.py                   GatedProvider: scans every draft that can leave the machine
│   ├── redaction.py              the gate's sensitive-content patterns
│   ├── prompt_builder.py         loads profiles, fills in the persona rule
│   ├── prompts/
│   │   ├── default.md            golden-template rewrite (-i-, -ip-, -if-)
│   │   └── general.md            lighter "make this precise" rewrite (local triggers)
│   └── providers/
│       ├── base.py               HTTP call, error mapping, <think> stripping
│       ├── openai_compatible.py  OpenRouter and LM Studio
│       ├── anthropic.py          Anthropic Messages API
│       └── ollama.py             Ollama /api/chat
├── scripts/
│   ├── install_macos.sh          install the CLI and deploy the match files
│   ├── install_windows.ps1       the same for Windows
│   └── bench_models.py           score models on template fidelity, latency, cost
├── tests/                        unit tests, no network (fake_http in conftest.py)
│   ├── test_live.py              opt-in real OpenRouter calls (pytest -m live)
│   └── test_docs.py              README and .env.example list every setting
├── .github/                      CI (tests, gitleaks), Dependabot, issue and PR templates
├── .env.example                  every setting with its default; copy to .env
├── CONTRIBUTING.md               setup, checks, how to add a profile/trigger/provider
├── SECURITY.md                   how to report a gate bypass or other vulnerability
└── CHANGELOG.md                  release notes
```

## Common changes

### Add a profile

1. Add `src/prompt_workflow/prompts/<name>.md` containing the system prompt as plain prose. It is
   picked up automatically.
2. Optionally include `{{PERSONA_RULE}}` where the user's persona should be applied (see
   `prompt_builder.render()`).
3. Add a test in `tests/test_prompts.py`.

### Add a trigger

Add a match to `espanso/match/prompts-llm.yml`. Shell commands start with the quoted
`__PROMPT_WORKFLOW__` placeholder, which the install scripts replace with the absolute CLI path.
Quote nothing else: `cmd.exe` mangles a command line holding more than one quoted part.
Set `force_mode: clipboard` on every match that runs the CLI, so Espanso pastes the output
instead of typing short replies key by key.

```yaml
- trigger: "-ireg-"
  left_word: true  # fire only at the start of a word, never inside one such as n-i-1
  replace: "{{output}}"
  force_mode: clipboard
  vars:
    - name: output
      type: shell
      params:
        cmd: "\"__PROMPT_WORKFLOW__\" improve --provider ollama --profile regulation --source clipboard"
```

`tests/test_yaml.py` checks the placeholder, quoting and `force_mode`, and that the profile and
provider exist. Add the trigger's expected provider, profile and tier to `EXPECTED` in
`tests/test_triggers.py`, which replays every trigger's command line through the CLI. Pass
`--profile` only when the trigger needs a fixed profile: it overrides `PROMPT_PROFILE` and the
pro tier's profile.

### Add a setting

Add a field to `Settings` in `src/prompt_workflow/config.py` with `_env("NAME", "default")`, then
document it in the README configuration table and in `.env.example`. `tests/test_docs.py` fails
until both are done.

### Add a static snippet or form

Snippets that need no model go in `espanso/match/prompts-core.yml`, as plain Espanso matches
(`replace:` for fixed text, `form:` plus `form_fields:` for a fill-in form). See the
[Espanso docs](https://espanso.org/docs/matches/basics/). Test in a plain-text editor first.

### Add a provider

1. Implement `generate(prompt, system_prompt, model=None) -> str` in
   `src/prompt_workflow/providers/`. Use `post_json()` and `finalize_content()` from
   `providers/base.py` so transport errors and `<think>` stripping behave like the other providers.
2. Add it to `PROVIDER_NAMES` and `make_provider()` in `factory.py`. If it can send data off the
   machine, return it through `_gate()`, as the other providers do, and make `_leaves_machine()`
   report it, so the gate and `PROMPT_LOCAL_ONLY` both cover it.
3. Add wire-format and error-path cases to `tests/test_providers.py`, and a factory test.

### Improve the redaction gate

Add a pattern to `_PATTERNS` in `src/prompt_workflow/redaction.py`, with a validator in
`_VALIDATORS` if the raw regex is too broad. Add both a positive and a near-miss negative test to
`tests/test_redaction.py`. Build fake keys at runtime (`"sk-ant-" + body`) so no key-shaped
literal lands in the repo. Patterns specific to one organisation belong in the user's
`PROMPT_EXTRA_PATTERNS`, not in the code.

### Change the default prompt

Template wording is scored by `scripts/bench_models.py`, and `tests/test_bench.py` checks the
scored phrases still exist in `prompts/default.md` and in the static `-p-` template
(`espanso/match/prompts-template.yml`), so change all three together. A/B a change on both the
standard and pro defaults, on both suites, before proposing it, and include the before/after
pass rates in the pull request, with each run's `meta.json` (commit, prompt hash, persona
mode). The bench renders a fixed fictitious persona by default, so anyone can reproduce your
numbers. Never commit the outputs of a `--persona env` run, which hold your own persona. For
example:

```bash
uv run python scripts/bench_models.py --suite all --runs 3 --models \
  google/gemini-3.5-flash-lite@google-ai-studio/flex~minimal openai/gpt-6-luna@openai~low
uv run python scripts/bench_models.py --suite all --runs 3 --system-prompt-file candidate.md \
  --models google/gemini-3.5-flash-lite@google-ai-studio/flex~minimal openai/gpt-6-luna@openai~low
```

The `core` suite is saturated: the previous prompt already passed it in full, so it only guards
against regressions. Improvements show on the `edge` suite and in how good the rewrites read,
which the mechanical checks cannot judge. For a prompt change that is not purely mechanical,
also compare the two prompts' outputs blind and pairwise: same model, draft and run, A/B order
randomised, labels hidden. Judge fidelity, invention, specificity of the work steps and
calibration of CONSTRAINTS, INPUTS, OUTPUTS and `[REVIEW: …]`, and read the result **per
model**. The two default models react to the same wording in opposite directions: an
anti-invention rule that fixed flash-lite made gpt-6-luna write thin, generic steps. Keep
drafts you wrote after freezing the candidate for the final comparison.

Small models such as flash-lite need an explicit trigger for *each* variant of a branching step.
A rule like "if unsure, use (a)" with no positive condition for (b) makes them pick (a) almost
every time. They also choose a variant by analogy to the examples in the rule, so a named
example ("a polite reply to a vendor") works better than an abstract class ("any external
party"). Check the per-draft table in the report, not only the total.

### Known gaps in the default prompt

Both tiers send the `default` profile (flash-lite on the standard tier, gpt-6-luna on the pro
tier); `default-pro` is an alias of it. The 2026-10-02 rework (see
`docs/prompt-candidates/PLAN.md`) closed the old gaps 1–4 and 7: fixed wordings are copied word
for word, document steps name their sections, method choices are stated as assumptions, a
message to a named person at another organisation gets the independent review, and the
self-review lines match the format in `OUTPUTS`. The gaps it left, and their state after the
2026-10 round (its bench runs use `--persona example`):

1. **Published text on flash-lite** (closed 2026-10). The review rule now names its readers
   (only the user, a colleague, their manager or their team take the self-review; everyone
   else, including the CEO and published text, takes the independent review), and says that
   "quick", "short" and "brief" never decide it. Over 6 runs each, flash-lite gives `faq` and
   `quick-ceo` the independent review 6/6 (the 0.13.0 prompt: 0/6 and 1/6), and `light`,
   `teams-jana`, `memo`, `slack`, `sql`, `outliers` and `code` the self-review 6/6.
2. **Self-review drafts on gpt-6-luna** (mostly closed). With a fixed fictitious persona,
   `outliers`, `sql` and `memo` get the self-review in every run. Over 123 calls, gpt-6-luna chose
   the wrong review branch once (`light`, 1 of 3).
3. **Half-answers in work steps on flash-lite.** For "what's the difference between IFRS 9
   stage 2 and stage 3", flash-lite writes the answer into the steps, sometimes wrongly. Naming
   the aspects to cover is welcome; stating facts is not. Watch it on `question`.
4. **Injection meta-commentary on flash-lite** (not reproduced). Instead of dropping "ignore
   previous instructions", flash-lite sometimes wrote a CONTEXT about "an instruction that
   attempts to override my role". With a fixed fictitious persona it did not happen in 9 runs of
   `injection` and `pasted-injection` (2026-10). Watch it if a persona is set.
5. **Unscored review branches.** Two edge drafts are not scored on the review branch: a one-page
   PRD for a fintech feature and `outage`, an incident summary for the user's manager. The
   2026-10 rule decides by reader, so `outage` should take the self-review; who reads a PRD is
   still open. Score both once that is settled.
6. **Persona bleed** (depends on the persona). With `PROMPT_PERSONA` set, near-empty drafts
   can pick up the persona's domain: "help with the report" became a report on "risk management
   metrics". It did not reproduce with a neutral persona (`vague` and a near-empty draft, 12
   gpt-6-luna and 3 flash-lite runs), so it likely depends on the persona's domain. A design
   trade-off, not a bug; check it with `--persona env` on your own machine.
7. **Draft delimiting in the CLI.** Sending the draft wrapped in `<draft>…</draft>` gave a
   small, consistently positive but not significant gain once the prompt already said the
   whole message is the draft. If adopted, escape `</draft>` inside drafts.
8. **Prompt length** (closed 2026-10). The 2026-09 and 2026-10-02 reworks made the system
   prompt about 40% longer (about 3,900 input tokens per call). Against the shorter prompt, p50
   latency stayed at 2.1 s on flash-lite and fell from 7.6 s to 6.6 s on gpt-6-luna, and the cost
   per 1,000 calls rose from $0.96 to $1.13 on flash-lite and from $0.34 to $0.35 on gpt-6-luna.
   Length does not drive latency; cost rose about 18% on flash-lite and is flat on gpt-6-luna.
9. **The bench runs `default` on every model** (closed 2026-10). Both tiers now send `default`,
   so a plain `--suite all` run scores what ships; `tests/test_bench.py` checks that the bench's
   profile is the one the CLI uses.
10. **Pasted material on flash-lite (#42).** The prompt now copies pasted material up to about
    60 lines in full. gpt-6-luna copies it in every run (`pasted`, `outage`, `pasted-injection`,
    the 35-line `long-thread` and the 57-line `cap-thread`, 6/6 each). flash-lite copies
    `outage`, `pasted-injection` (6/6), `long-thread` and `cap-thread` (5/6), but still
    summarises the short email in `pasted` (5 of 6 runs). It also gives `long-thread` ("summarise
    this thread for me and draft my reply to Tomasz") the self-review in 6 of 6 runs, and
    `cap-thread` (a 5-bullet summary for the user's manager) the `.md` line in 6 of 6, where the
    bench expects a message. The 0.13.0 prompt did the same (`long-thread` 2 of 3, `cap-thread` 3
    of 3). The `OUTPUTS` rule treats a summary as a document, so the `cap-thread` failure may be
    the bench's expectation rather than the model's.

### Next steps for the default prompt

Limits of the 2026-10-02 and 2026-10 evaluations, and what to do next:

1. **Re-judge the shipped prompt blind on new drafts.** The 2026-10-02 `default` and
   `default-pro`, and the merged `default` of 2026-10, were checked mechanically only; the blind
   ranking was on I2. The revisions were tuned on the
   same 32 drafts, and `landlord` is no longer held out. Write 6–8 new drafts (outside emails,
   published text, internal memos, "for me" scripts) before changing anything else, and judge
   the current prompts against the previous release on both tiers.
2. **Use more runs per draft.** At 3 runs, a single draft flips between 0/3 and 2/3 from noise
   alone (seen on `big-personal` and `memo`). Re-check any single-draft change at 6 runs or more
   before acting on it.
3. **Fix gap 1 (published FAQ text on flash-lite).** Done in 2026-10: the review rule names
   its readers, and `quick-ceo` and `memo` held at 6/6.
4. **Fix gap 10 on flash-lite.** Find a wording that makes flash-lite copy a short pasted
   email and give a reply to an outside sender the independent review when the draft also says
   "for me". Check `pasted`, `long-thread` and `cap-thread` at 6 runs or more, and decide
   whether a summary for the user's manager should get the `.md` line.
5. **Keep one profile.** The profiles were merged in 2026-10 (gap 9). Before splitting them
   again, show that no single wording serves both models: one prompt per tier doubles the bench
   and review work.
6. **Watch the prompt length.** The prompt is now about 3,900 input tokens; check latency on the
   pro tier (6–9 s) after any further additions.
