# CLAUDE.md

## What this is

A Python CLI (`prompt-workflow`) invoked by Espanso text-expansion triggers to rewrite a
clipboard/stdin draft into a more precise prompt via a local or cloud LLM. Espanso match files
in `espanso/match/` call the CLI as a shell command and paste back stdout.

## Commands

Deploy Espanso configs (substitutes the absolute CLI path into match files, since GUI-launched
Espanso does not inherit shell PATH):

```bash
./scripts/install_macos.sh      # macOS
.\scripts\install_windows.ps1   # Windows
```

## Architecture

- `cli.py` — main Typer command, `improve`. Reads a draft (`clipboard`/`stdin`/`argument`),
  calls a provider built by `make_provider()` (which applies the gate), and prints the result
  with no trailing newline (Espanso inserts stdout verbatim). All failures are caught and
  converted to a `[prompt-workflow: ...]` marker printed to stdout with exit code 0, rather than a
  stack trace or blank expansion, since Espanso has no good way to surface a nonzero exit /
  stderr to the user. Everything printed goes through `_emit()`, which strips control, bidi and
  Unicode tag characters (the draft gets the same `_clean()`); stdin/stdout are reconfigured to
  UTF-8 because Windows pipes default to the ANSI code page. `--tier pro` swaps in the
  `OPENROUTER_PRO_*` settings via `Settings.for_tier()`; `--model`/`--effort`/`--max-tokens`/
  `--timeout` are applied by `Settings.with_overrides()` (`--model` sets every provider's model),
  so `make_provider()` always sees the effective settings.
- `factory.py` — `make_provider(name, cfg)` builds a provider from `Settings`; `PROVIDER_NAMES`
  lists the valid names. `cli.py` and `scripts/bench_models.py` both build through it. For
  OpenRouter it adds the endpoint pin (`OPENROUTER_PROVIDER`) and
  `reasoning: {effort, exclude: true}` (`OPENROUTER_REASONING_EFFORT`; empty omits it).
- `config.py` — `Settings` is a frozen dataclass read from env vars, with defaults for each
  provider. Values are parsed strictly (`_bool`, `_positive_int`, ...) and a bad one raises a
  `ValueError` naming the variable; API key fields are `secret` (kept out of `repr()`). `_load_dotenv()` loads the first of `$PROMPT_WORKFLOW_ENV`, the editable-install
  repo root's `.env` (derived from `__file__`), or the user config dir `.env`, with a
  dependency-free `setdefault` (never overrides real env vars). It deliberately never reads the
  cwd, so a planted `.env` cannot redirect the base URL or enable the override. This matters
  because Espanso runs the CLI as a GUI-spawned subprocess without an inherited login-shell
  environment. `tests/conftest.py` points `PROMPT_WORKFLOW_ENV` at a temp file per test.
- `prompt_builder.py` — `PROFILES` maps a profile name (`default`, `general`) to a system
  prompt used to instruct the rewrite. `render()` fills the `{{PERSONA_RULE}}` token from
  `PROMPT_PERSONA` (also printed by the `persona` subcommand for the `-p-` snippet). `default` (the
  `PROMPT_PROFILE` fallback) rewrites the draft into the golden template kept in
  `espanso/match/prompts-template.yml` (`CONTEXT / GOAL / INSTRUCTIONS / CONSTRAINTS / INPUTS / OUTPUTS`),
  selecting the plan-first vs execute-now and the independent-review vs self-review instruction
  variant from signals in the draft. It treats the whole user message as the draft (data, not
  instructions), always rewrites in English (adding a `- Language:` constraint for other
  languages) and matches `OUTPUTS` to the deliverable. The prompt uses lowercase XML tags for its
  own structure so they are not confused with the uppercase output sections. Its exact variant
  wordings are
  matched by `scripts/bench_models.py` and `tests/test_bench.py` (which also checks the `-p-`
  template in `prompts-template.yml`); change them together, and
  re-run the benchmark (`--suite all --system-prompt-file`) on both default models before
  shipping a prompt change. Open weaknesses are listed in CONTRIBUTING.md ("Known gaps in the
  default prompt").

### Data-protection gate

`factory.py`'s `make_provider()` (the only place providers are built) wraps, via `_gate()`,
everything that can send the draft off the machine in `gate.GatedProvider`: `openrouter` and
`anthropic` always, `ollama`/`lmstudio` when the base URL is not loopback (`_is_loopback()`) or
the Ollama model is a `cloud`-tagged one. The gate runs `redaction.scan()` (built-in patterns
plus the user's `PROMPT_EXTRA_PATTERNS`) and blocks the call unless `ALLOW_CLOUD_OVERRIDE=true`.
It also requires `https` cloud base URLs (plain `http` only to loopback).
`scripts/bench_models.py` builds through it too. A new provider that can leave the machine must
return through `_gate()`.

### Espanso integration contract

- `espanso/match/prompts-llm.yml` triggers call `"__PROMPT_WORKFLOW__" improve ...`; the install
  scripts substitute `__PROMPT_WORKFLOW__` with the resolved absolute path to the installed CLI.
  Only that path is quoted (`cmd.exe` mangles more than one quoted part); there is no `cd`.
  The installers deploy `espanso/config/` only with `--with-config` / `-WithConfig`.
- `espanso/match/prompts-core.yml` holds static, non-LLM form-based snippets (no CLI call).
- `-i-` (OpenRouter, `default` profile) is the live cloud trigger, and `-ip-` is
  the same rewrite with `--tier pro` (reasoning model). `-if-` puts an Espanso form
  (choice dropdowns) in front of the pro tier and passes the picks as `--model model@endpoint
  --effort --max-tokens --timeout` via `{{form1.*}}`, which `Settings.with_overrides()` applies.
  Its fields must stay fixed choices (no free text reaches the shell); `tests/test_yaml.py`
  checks each value against the CLI. `-ic-`
  (Anthropic) stays commented out in `prompts-llm.yml`. The data-protection gate is a heuristic,
  not a guarantee — cloud triggers should only be used where company policy permits.
