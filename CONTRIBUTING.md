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

```yaml
- trigger: "-ireg-"
  replace: "{{output}}"
  vars:
    - name: output
      type: shell
      params:
        cmd: "\"__PROMPT_WORKFLOW__\" improve --provider ollama --profile regulation --source clipboard"
```

`tests/test_yaml.py` checks the placeholder and quoting, and that the profile and provider exist.

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
   machine, return it through `_gate()`, as the other providers do.
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
standard and pro defaults before proposing it, and include the before/after pass rates in the
pull request:

```bash
uv run python scripts/bench_models.py --runs 3 --models \
  google/gemini-3.5-flash-lite@google-ai-studio/flex~minimal openai/gpt-6-luna@openai~low
uv run python scripts/bench_models.py --runs 3 --system-prompt-file candidate.md --models \
  google/gemini-3.5-flash-lite@google-ai-studio/flex~minimal openai/gpt-6-luna@openai~low
```

Small models such as flash-lite need an explicit trigger for *each* variant of a branching step.
A rule like "if unsure, use (a)" with no positive condition for (b) makes them pick (a) almost
every time. Check the per-draft table in the report, not only the total.
