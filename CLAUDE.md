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
  applies the data-protection gate, calls a provider, and prints the result to stdout with no
  trailing newline (Espanso inserts stdout verbatim). All failures are caught and converted to a
  `[prompt-workflow: ...]` marker printed to stdout with exit code 0, rather than a stack trace or
  blank expansion, since Espanso has no good way to surface a nonzero exit / stderr to the user.
  `--tier pro` swaps in the `OPENROUTER_PRO_*` model, endpoint, reasoning effort and timeout
  via `Settings.for_tier()`, then builds through `make_provider()` as usual (so it stays gated).
- `factory.py` — `make_provider(name, cfg)` builds a provider from `Settings`; `PROVIDER_NAMES`
  lists the valid names. `cli.py` and `scripts/bench_models.py` both build through it. For
  OpenRouter it adds the endpoint pin (`OPENROUTER_PROVIDER`) and
  `reasoning: {effort, exclude: true}` (`OPENROUTER_REASONING_EFFORT`; empty omits it).
- `config.py` — `Settings` is a frozen dataclass read from env vars, with defaults for each
  provider. `_load_dotenv()` loads the first of `$PROMPT_WORKFLOW_ENV`, the editable-install
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
  variant from signals in the draft. The prompt uses lowercase XML tags for its own structure so
  they are not confused with the uppercase output sections. Its exact variant wordings are
  matched by `scripts/bench_models.py` and `tests/test_bench.py`; change them together, and
  re-run the benchmark (`--system-prompt-file`) before shipping a prompt change.

### Data-protection gate

`factory.py`'s `make_provider()` (the only place providers are built) wraps `openrouter` and `anthropic` in `gate.GatedProvider`, which
runs `redaction.scan()` (built-in patterns plus the user's `PROMPT_EXTRA_PATTERNS`) on every
prompt and blocks the call unless `ALLOW_CLOUD_OVERRIDE=true` is set. It also requires `https`
cloud base URLs (plain `http` only to loopback). Ollama and LM Studio are local and always allowed. `scripts/bench_models.py` applies it too,
since building a provider directly would otherwise bypass the gate. Keep this gate in mind when adding a new
cloud provider — wrap it in `GatedProvider` in `make_provider()` to be covered by the gate.

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
