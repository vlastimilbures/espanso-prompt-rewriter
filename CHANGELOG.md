# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

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
