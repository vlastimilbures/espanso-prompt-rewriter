# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

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
