# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

Upgrading: re-run the installer (`./scripts/install_macos.sh` or
`.\scripts\install_windows.ps1`) to deploy the `-p-` template fix. If your `.env` sets
`PROMPT_PRO_PROFILE=default-pro`, remove the line or leave it empty: both tiers now send
`default`, and `default-pro` only still works as an alias. If you set `PROMPT_PROFILE`, `-ip-`
and `-if-` now use it too, unless `PROMPT_PRO_PROFILE` is set.

### Added
- The bench checks that sentences from the start, middle and end of pasted material reach
  `INPUTS` word for word (`pasted material not copied`) on `pasted`, `outage`,
  `pasted-injection` and a new 35-line `long-thread` edge draft. On the 2026-10 review's saved
  outputs (local, not in the repo) it flags the shipped prompt on `long-thread` in 9 of 9 runs
  and on flash-lite `pasted` in 6 of 6.
- `scripts/bench_models.py --max-tokens` sets the output cap per call (default 6000), so a run
  can score what a trigger returns under the CLI's `OPENROUTER_MAX_TOKENS`.
- The bench has three new edge drafts: `cap-thread` (a 57-line thread, just under the copy
  limit), `supplier` and `status-page` (outside readers in a short text).

### Changed
- The bench's `kept` metric matches a key only at the start of a word, and an acronym only in
  capitals, so "ID" no longer matches inside "validate" or "MAD" inside "fixes made". A test
  checks that no key matches the fixed template wordings.
- `scripts/bench_models.py` renders a fixed fictitious persona by default (`--persona example`)
  instead of the runner's `PROMPT_PERSONA`, so every machine scores the same system prompt and
  no private persona ends up in bench outputs. `--persona none` renders none, and
  `--persona env` the runner's own. Each run writes `meta.json` (git commit and dirty flag,
  SHA-256 of the template and, for the shared personas, of the rendered system prompt, persona
  mode, temperature, models, drafts, runs) and goes to `bench-out/<UTC timestamp>/` unless
  `--outdir` is given. It refuses an `--outdir` that is not empty.
- The `default` prompt, after the 2026-10 review:
  - pasted material up to about 60 lines (was about 20) is copied into `INPUTS` in full,
    keeping its line breaks, because the other assistant never sees the original draft (#42);
  - the review step is decided only by who reads the result: the user, a colleague, their
    manager or their team take the self-review, and everyone else, including the CEO and
    published text, the independent review; "quick", "short" and "brief" never decide it (#43);
  - the `- Out of scope:` bullet appears only when the draft implies concrete exclusions, and
    the first example no longer invents a one-page length or a `.md` output (#50).

  With `--persona example` and `--max-tokens 2400` (3 runs per draft, 6 on 14 drafts on
  flash-lite and 5 on gpt-6-luna), flash-lite passes 30/30 core and 102/120 edge, and gpt-6-luna
  23/24 core and 98/99 edge. The 0.13.0 prompts scored 22/27 and 66/90 on flash-lite and 24/24
  and 75/84 on gpt-6-luna with the same checks, on a different mix of drafts and runs, partly
  from outputs saved before the bench had a fixed persona. flash-lite still summarises a short pasted email and
  gives a reply to an outside sender the self-review when the draft says "for me" (see
  CONTRIBUTING's known gap 10).
- Both tiers send `default`: `PROMPT_PRO_PROFILE` is now empty by default, so `-ip-` and `-if-`
  use `PROMPT_PROFILE` like `-i-` (#44). A plain bench run now scores what every OpenRouter
  trigger sends (#61).
- The `-p-` template no longer puts a `[Constraints etc.]` placeholder in `CONTEXT` (#50).

### Deprecated
- The `default-pro` profile is an alias of `default` and will be removed in a later release.
  Set `PROMPT_PRO_PROFILE` empty, or to a profile of your own.

### Fixed
- The bench output directories that the docs suggest (`--outdir bench-A`) were not gitignored,
  so `git add .` could commit outputs that carried the runner's persona. Every `bench-*/`
  directory is now ignored, and a test checks each documented `--outdir`.
- A rewrite whose `<CONTEXT>` section the model closed with `</GOAL>` right before `<GOAL>` (a
  slip seen only on flash-lite) is now repaired before it is pasted. Only that exact pattern in
  the rewrite's first section is changed; other malformed output, and tags inside pasted
  material, are pasted as returned. Of 4,842 saved bench rewrites (local, not in the repo), the
  repair fixed all 29 with the slip and left every well-formed one unchanged. The bench scores
  the repaired text, keeps the raw one as `*.raw`, and reports the count in a `rep` column.

## 0.13.0

Upgrading: re-run the installer (`./scripts/install_macos.sh` or
`.\scripts\install_windows.ps1`). Every trigger that runs the CLI changed (`force_mode`,
`left_word`, no `--profile` on `-i-`, `-ip-` and `-if-`), and the fix for
GHSA-9v85-6m6j-8529 takes effect only once the match files are redeployed. A proxy or CA
variable set only in `.env` no longer applies; set it for GUI apps instead (see the README).

### Security
- [GHSA-pchf-7qh9-8xpc](https://github.com/vlastimilbures/espanso-prompt-rewriter/security/advisories/GHSA-pchf-7qh9-8xpc)
  (medium): with an HTTP proxy set in the environment, in `.env` or in the system settings,
  `-il-` and `-ilm-` sent the draft to the proxy without the data-protection gate. Local calls
  now connect directly, and `.env` can no longer set proxy or CA variables (see Changed).
- [GHSA-6mvg-6wv7-5f2m](https://github.com/vlastimilbures/espanso-prompt-rewriter/security/advisories/GHSA-6mvg-6wv7-5f2m)
  (low): a settings error could paste a value holding an API key (for example, two `.env` lines
  saved without the newline between them) into the focused app. Settings errors now redact
  such values (see Fixed).
- [GHSA-9v85-6m6j-8529](https://github.com/vlastimilbures/espanso-prompt-rewriter/security/advisories/GHSA-9v85-6m6j-8529)
  (medium): by default, Espanso typed replies shorter than 100 characters as keystrokes, so each
  newline pressed Enter in the focused app, a terminal included. Every trigger that runs the CLI
  now pastes through the clipboard, so replies are no longer typed as keystrokes (see Fixed). A
  pasted newline can still run a line in a shell without bracketed paste.

### Added
- `PROMPT_LOCAL_ONLY` (default `false`): `true` refuses every provider that can send the draft
  off this machine, including the ones the cloud triggers name, with an inline marker and no
  request. `-il-` and `-ilm-` on localhost keep working.

### Changed
- Calls to a base URL on this machine (`localhost`, `127.0.0.0/8`, `::1`; any provider) now
  connect directly and ignore `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and the system proxy.
  Remote endpoints still use them.
- `.env` now sets only the settings in the README's configuration table (except
  `PROMPT_WORKFLOW_ENV`, which only the real environment can set). Any other variable there, such
  as `HTTPS_PROXY` or `SSL_CERT_FILE`, is ignored; set it for GUI apps instead (see README).

### Fixed
- Settings errors repeat a rejected value only when it is short and does not look like a key;
  otherwise they show `<redacted, N chars>`. The same applies to an unknown provider or profile.
- A `.env` value that contains another `NAME=` assignment (two lines saved without the newline
  between them) is reported by setting name instead of being read as one long value.
- Spaces around an API key are dropped, and a key with a control character (such as a newline)
  is reported as `invalid header value (check the API key)`.
- Every trigger that runs the CLI (`-i-`, `-ip-`, `-if-`, `-il-`, `-ilm-`, `-ic-`, `-p-`) now
  sets `force_mode: clipboard`, so Espanso always pastes the result instead of typing short
  replies key by key. This overrides any Espanso `backend` setting for these triggers. Re-run
  the installer to redeploy the match files.
- `-ip-` and `-if-` now use `PROMPT_PRO_PROFILE` (default `default-pro`), as documented. They
  passed `--profile default`, which took precedence. `-i-` likewise follows `PROMPT_PROFILE`.
  In `-if-`, a model other than `OPENROUTER_PRO_MODEL` uses `PROMPT_PROFILE`, because
  `default-pro` is tuned for the pro model. Re-run the installer to redeploy the match files.
- `--model default` keeps the configured model, as the `-if-` popup's `default` does for the other
  fields. It used to request a model literally named `default`.
- Every trigger now sets `left_word: true`, so it expands only at the start of a word. Text such
  as `a[n-i-1]`, `len(a)-n-i-1` or `only-if-cached` no longer fires `-i-` or `-if-` (and no
  longer sends the clipboard). A trigger right after a space or bracket still expands, as in
  `s[-i-1]`. Type a space before a trigger that follows a letter, digit or `-`. Re-run the
  installer to redeploy the match files.
- The README and `.env.example` no longer suggest that `PROMPT_PROVIDER=ollama` makes `-i-`
  local: every trigger passes its own `--provider`. Use `PROMPT_LOCAL_ONLY=true` instead.

## 0.12.0

### Changed
- The `default` prompt was reworked after an A/B run on both tiers (judged blind; details in
  `docs/prompt-candidates/PLAN.md`). The rewrite now decides deliverable, planning, review and
  language before writing; names the sections of a document deliverable; states method choices
  as assumptions instead of `[REVIEW: …]`; asks for each missing item once and never for
  material the draft already gives; gives an email to a named person at another organisation
  (a customer, supplier, landlord) the independent review; and describes a slide deck in
  `OUTPUTS` instead of the `.md` line. The self-review lines now read "show any calculation
  steps" and "matches the format in OUTPUTS", in `prompts-template.yml` too.
  On the 32-draft bench (3 runs): flash-lite 22→23/24 core, 63→66/72 edge; gpt-6-luna
  (`default-pro`) 23→22/24 core (one run; a 6-run recheck was clean), 63→67/72 edge.

### Added
- `PROMPT_PRO_PROFILE` (default `default-pro`): the profile `--tier pro` uses. `default-pro`
  is `default` without one review-rule clause that flash-lite needs and gpt-6-luna over-applies.
  Set it empty to use `PROMPT_PROFILE` for both tiers.
- Four held-out drafts in the bench's `edge` suite (`vendor-review`, `landlord`, `teams-jana`,
  `outliers`), and the `spanish` draft accepts a plain-text `OUTPUTS` line.

## 0.11.0

### Changed
- The `default` prompt was reworked after a benchmark review; the golden template and its fixed
  step wordings are unchanged. The rewrite now:
  - treats the whole draft as material to rewrite, never as instructions: questions become
    prompts, pasted emails or notes go word for word into `INPUTS`, and "ignore previous
    instructions" is dropped (a small model used to paste "OK" and a haiku);
  - is always written in English, with a `- Language: …` constraint for a draft in another
    language, instead of the draft's language (which made gpt-6-luna translate the fixed steps
    and break the template);
  - matches `OUTPUTS` to the deliverable (plain-text email, code block, slides…); the `.md`
    line is kept for documents;
  - keeps every number, name, date and deliverable from the draft, names only the regulations
    the draft names, and flags gaps with `[REVIEW: …]` instead of guessing;
  - treats vendors, suppliers, partners, clients and published FAQ or help-center text as
    outside readers for the review step, and routine memos to colleagues as internal;
  - writes concrete work steps and a concrete "Out of scope" line.

  Blind pairwise judges preferred it 117 to 32 (19 ties) across gemini-3.5-flash-lite and
  gpt-6-luna.

### Added
- `scripts/bench_models.py --suite edge|all`: 20 more drafts (injection, questions, pasted
  material, Czech, German and Spanish, a draft stating its own role, code, implied outside
  readers). New checks for leaked scaffolding tags, a leftover `[domain]` placeholder, the
  draft's language and role, and `OUTPUTS` matching the deliverable, plus a `kept` column for
  the share of the draft's specifics carried over. The default is still the 8-draft `core`
  suite.

## 0.10.0

### Security
- Ollama and LM Studio pass the data-protection gate when the draft would leave the
  machine: a base URL that is not loopback, or an Ollama cloud model (`:cloud`, `-cloud`).
- Control characters, bidi overrides and invisible Unicode tag characters are stripped from
  the pasted output and from the draft. An escape sequence in a model's reply could end a
  terminal's bracketed paste and run the lines after it.
- The gate also catches `DB_PASSWORD=…`, `access_token=…`, `AWS_SECRET_ACCESS_KEY=…`,
  JSON `"client_secret": "…"`, PGP private key blocks, and GitLab and Hugging Face tokens.
- API keys no longer appear in the `repr()` of the settings or providers.
- Gitleaks allowlists two historical test values by fingerprint instead of all of
  `tests/test_redaction.py`. Pre-commit hooks are pinned to commit SHAs and updated by
  Dependabot, zizmor checks the workflows, and Dependabot waits 7 days before proposing a
  new release.

### Fixed
- Output is always UTF-8. On Windows, a rewrite with letters outside the ANSI code page
  (Czech, Vietnamese) crashed into a blank expansion.
- Building a wheel (`uv build`, any non-editable install) failed because
  `prompts/default.md` was added twice.
- `PROMPT_TEMPERATURE` also applies to Ollama.
- The macOS installer no longer needs a system `python3`, and makes `.env` readable by
  the current user only.

### Changed
- Settings are validated strictly: booleans must be `true` or `false` (`OLLAMA_THINK=1`
  used to mean false), and timeouts and token caps must be finite numbers above 0.
- `--model` is applied to the settings of whichever provider runs, so the gate sees the
  model actually used.
- Development: the dev tools are a uv dependency group (`uv sync`, no `--extra dev`), mypy
  runs strict over `src` and `scripts`, and CI adds Python 3.14 and a 95% coverage floor.

## 0.9.0

### Security
- The redaction gate scans in linear time. An 80,000-character clipboard used to take
  15 s and freeze Espanso before any request timeout applied.
- The gate also scans a Unicode-normalised copy of the draft, so no-break or zero-width
  spaces, soft hyphens and fullwidth digits no longer hide a card number or API key.
- Drafts over 50,000 characters are refused.

### Fixed
- The installers no longer overwrite Espanso's own `match/base.yml`. The `-p-` template
  now ships as `prompts-template.yml`. A `base.yml` deployed by an earlier version is
  backed up and removed, and any match or config file whose content differs is backed
  up to `.bak-<timestamp>` before it is replaced.
- Windows installer: match files are read as UTF-8 (Windows PowerShell 5.1 turned the
  em dashes into mojibake), a failing `uv` or `espanso` command stops the script, and
  `espanso start` is used when Espanso is not running.
- A rewrite cut off at the max-tokens cap ends with
  `[prompt-workflow: output truncated at max tokens]` instead of passing as complete.
  A reasoning model that spent the whole budget thinking asks for a larger
  `--max-tokens`.
- A `<think>` block left open by truncation is dropped instead of pasted.
- Anthropic: an unexpected content block is reported as a malformed response.
- The `-p-` template's independent-review step matches the `default` profile word for
  word.
- `.env`: an inline ` # comment` after an unquoted value is no longer part of the value.
- `bench_models.py`: `model@auto` means OpenRouter's own routing, as in `--model`.

### Changed
- CI: checkout no longer persists credentials; tests run on pushes to `main` and on
  pull requests. Ruff's version is pinned once, in `uv.lock`.

## 0.8.0

### Changed
- Bumped `typer` to 0.27.2.

### Fixed
- The `prompts-llm.yml` header comment no longer contains the CLI placeholder, so the
  installers stop writing your absolute CLI path into it.

## 0.7.0 — first public release

### Features
- `prompt-workflow improve` rewrites a clipboard, stdin or argument draft into a precise
  prompt through OpenRouter (default), Anthropic, Ollama or LM Studio. Output has no
  trailing newline, and every failure is printed inline as `[prompt-workflow: …]` with
  exit code 0, so Espanso always has something to paste.
- `default` profile: rewrites the draft into the golden template
  (`CONTEXT / GOAL / INSTRUCTIONS / CONSTRAINTS / INPUTS / OUTPUTS`), choosing plan-first
  or execute-now, and independent or self review, from the draft. `general` profile: a
  lighter "make this prompt precise" rewrite. Any `*.md` in `prompts/` becomes a profile.
- `PROMPT_PERSONA` opens the rewrite's CONTEXT with your role, and fills the `-p-` snippet
  via `prompt-workflow persona`.
- Triggers: `-i-` (standard tier), `-ip-` (pro tier: a reasoning model), `-if-` (a popup
  picks the model, effort, max tokens and timeout for one call), `-il-` (Ollama), `-ilm-`
  (LM Studio), `-ic-` (Anthropic, commented out), plus the static `-p-`, `-prompt-` and
  `-risk-` snippets.
- OpenRouter endpoint pinning (`OPENROUTER_PROVIDER`, `OPENROUTER_ALLOW_FALLBACKS`) and
  reasoning effort (`OPENROUTER_REASONING_EFFORT`), with the reasoning trace excluded from
  the pasted text. `<think>` blocks from local models are stripped.
- `scripts/bench_models.py` scores models on template fidelity, latency and real cost from
  OpenRouter's usage data.
- macOS and Windows installers that write the absolute CLI path into the Espanso match files.

### Security
- Data-protection gate: every OpenRouter and Anthropic call is scanned first and blocked on
  payment cards (Luhn-checked, space/dot/dash separated), emails, vendor API keys
  (OpenRouter, Anthropic, OpenAI, Stripe, GitHub, Slack, Google, xAI, AWS), JWTs, bearer
  tokens, PEM private keys, `user:password@` URLs, `password=…`-style assignments,
  confidentiality labels and Vietnamese ID formats, unless `ALLOW_CLOUD_OVERRIDE=true`.
  Providers are built only through `make_provider()`, which wraps cloud ones in the gate.
- `PROMPT_EXTRA_PATTERNS` adds your own `;`-separated regexes to the gate. Matches are
  reported as `custom_1`, `custom_2`, … so the pattern text never appears in the output.
- Cloud base URLs must be `https://` (plain `http` only to loopback), so a key is never
  sent in clear text.
- `.env` is read from `PROMPT_WORKFLOW_ENV`, the installed repository, or the user config
  dir, never from the current directory, so a planted `.env` cannot redirect the endpoint
  or switch off the gate.
- Match files quote the CLI path (paths with spaces work) and no longer `cd`; the
  installers refuse a CLI path containing characters the shell or YAML would interpret.
- The installers leave Espanso's `config/default.yml` alone unless `--with-config` /
  `-WithConfig` is passed.
- CI actions are pinned to commit SHAs, dependencies install from the lockfile
  (`uv sync --locked`), Dependabot keeps both current, and gitleaks scans every push.
