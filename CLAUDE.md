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
  stderr to the user. A golden-template rewrite (its profile contains `<output_template>`, which
  `tests/test_prompts.py` ties to `<CONTEXT>`) first goes through
  `prompt_builder.repair_template_tags()`, which fixes only the `<CONTEXT>…</GOAL>` slip. Everything printed goes through `_emit()`, which strips control characters, every
  default-ignorable code point (`redaction.DEFAULT_IGNORABLE`) and every other format character
  except the visible prepended concatenation marks, maps other line breaks to `\n`, and keeps a
  selector/joiner run only as one selector plus one joiner after a visible character (after
  ASCII, only a keycap's selector) (the draft gets the same `_clean()`; the length limit is
  checked before cleaning); stdin/stdout are reconfigured to
  UTF-8 because Windows pipes default to the ANSI code page. `--tier pro` swaps in the
  `OPENROUTER_PRO_*` settings and `PROMPT_PRO_PROFILE` (if set) via `Settings.for_tier()`; `--model`/`--effort`/`--max-tokens`/
  `--timeout` are applied by `Settings.with_overrides()` (`--model` sets every provider's model),
  so `make_provider()` always sees the effective settings. `Settings.for_call()` chains the two
  and drops back to `PROMPT_PROFILE` when a pro call runs a model other than
  `OPENROUTER_PRO_MODEL`. An explicit `--profile` beats both, so the OpenRouter triggers pass none.
- `clipboard_guard.py` — `is_concealed()` asks the clipboard (ctypes: NSPasteboard types on
  macOS, user32 formats on Windows) whether a password manager marked the item, without reading
  it. `cli._read_input()` refuses such an item before `pyperclip.paste` and clears the clipboard
  (Espanso's restore would put it back unmarked); `None` (Linux, a probe error) reads as before. `tests/conftest.py` stubs it so tests never probe the real clipboard.
- `factory.py` — `make_provider(name, cfg)` builds a provider from `Settings`; `PROVIDER_NAMES`
  lists the valid names. `cli.py` and `scripts/bench_models.py` both build through it. For
  OpenRouter it adds the endpoint pin (`OPENROUTER_PROVIDER`) and
  `reasoning: {effort, exclude: true}` (`OPENROUTER_REASONING_EFFORT`; empty omits it).
  An optional `observer=` is passed to every provider, inside the gate (no request, no record).
- `providers/usage.py` — `AttemptUsage` (frozen) and the `UsageObserver` protocol. With an
  observer, `post_json(..., meter=Meter(...))` reports one record per HTTP attempt (both
  attempts of a retry, timeouts and non-2xx included) before it returns or raises, so usage
  survives a `finalize_content()` failure. Each provider adds what its body reports
  (`openrouter_usage`, `openai_usage`, `anthropic_usage`, `ollama_usage`). Records hold
  metadata only, never prompt, response, body or key text. A missing cost is `None`/`unknown`,
  never 0; the factory's `local=True` (loopback, non-cloud Ollama/LM Studio) makes it
  `not_applicable`. Observer and parser exceptions are swallowed (no retry, output unchanged);
  `decimal` is imported only when a cost is parsed. Nothing stores the records yet (#88, #89).
- `config.py` — `Settings` is a frozen dataclass read from env vars, with defaults for each
  provider. Values are parsed strictly (`_bool`, `_positive_int`, ...) and a bad one raises a
  `ValueError` naming the variable; API key fields are `secret` (kept out of `repr()`). Any error
  that quotes a rejected value goes through `redaction.safe_repr()`, since markers are pasted
  into the focused app. `Settings.load()` builds from `ConfigLayers.resolve()`, a pure merge
  of layers (lowest first): built-in default < the first readable of `$PROMPT_WORKFLOW_ENV`, the
  editable-install repo root's `.env` (derived from `__file__`), or the user config dir `.env` <
  the real environment; per-call overrides come after, in `Settings.with_overrides()`. It never
  writes `os.environ` (a second load sees an edited file; child processes inherit nothing) and
  records each key's source (`default`, `file:<path>`, `env`) and the layers it shadows. Only
  `env_names()` keys are taken, so a `.env` cannot set `HTTPS_PROXY`, `SSL_CERT_FILE` or any
  other variable, and a value holding another setting's `NAME=` (a merged line) is refused.
  `resolve(strict=False)` (repair mode) returns those errors as `Finding`s and falls back to the
  next lower layer (recorded in `Entry.rejected`) instead of raising; it also notes an unreadable
  `.env` and lines without `=`, which strict mode skips silently. It deliberately never reads the
  cwd, so a planted `.env` cannot redirect the base URL or enable the override. This matters
  because Espanso runs the CLI as a GUI-spawned subprocess without an inherited login-shell
  environment. `tests/conftest.py` points `PROMPT_WORKFLOW_ENV` at a temp file per test.
- `history.py` — the local usage history (#88): `HistoryStore` over a per-device SQLite file,
  `config.user_data_dir()/history.sqlite3` (`$XDG_DATA_HOME` or `~/.local/share`,
  `%LOCALAPPDATA%` on Windows; `tests/conftest.py` points both at a temp dir). Metadata only:
  the tables' columns are `OPERATION_COLUMNS` and `ATTEMPT_COLUMNS` (the `AttemptUsage` fields
  of #87, keyed by `(operation_id, seq)`), only those keys are read from a record mapping, and a
  text value must be an enum or a short identifier without spaces that `redaction.scan()` does
  not flag. `tests/test_history.py` holds the allowlist and fails on any other column; add a
  column only through a new `_MIGRATIONS` step (forward only, `PRAGMA user_version`). Money is
  exact decimal TEXT plus a unit, never REAL. `record()` never raises: one `BEGIN IMMEDIATE`
  transaction within `_WRITE_BUDGET`, and a dropped write bumps the `history.lost` sidecar
  (temp file + `os.replace` under a lock file). The services (`stats`, `export`, `prune`,
  `reset`) raise `HistoryError`; `health()` never raises. `PROMPT_HISTORY` (default `true`) and
  `PROMPT_HISTORY_RETENTION_DAYS` configure it; estimates come only from a user `prices.toml`
  in the config dir and are kept apart from reported costs. `sqlite3`, `tomllib`, `csv` and
  `decimal` are imported inside functions, and `cli.py` does not import the module yet (#89).
- `prompt_builder.py` — `PROFILES` maps a profile name (`default`, `general`) to a system
  prompt used to instruct the rewrite; `ALIASES` keeps the retired `default-pro` resolving to `default`. `render()` fills the `{{PERSONA_RULE}}` token from
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
  shipping a prompt change. Both tiers send `default` (`PROMPT_PRO_PROFILE` is empty by
  default), so one prompt serves flash-lite and gpt-6-luna; the bench scores it on both. Open
  weaknesses are listed in CONTRIBUTING.md ("Known gaps in the default prompt"). `general`
  (the local triggers) is a short draft-as-data rewrite in the language of the user's request; the bench scores
  it with `--profile general` via `check_general()`, and `test_general_profile_contract` pins its
  phrases. `strip_outer_fence()` removes a fence around a whole reply; the CLI and the bench
  apply it to every profile before the tag repair.
- User profiles: `system_prompt()` also reads `<user config dir>/profiles/<name>.md`
  (`user_profiles_dir()`, the dir `config._user_config_dir()` gives `.env`), but only for a name
  that is not built in, or a built-in listed in `PROMPT_PROFILE_OVERRIDES` (strictly parsed;
  built-in names only). A user `default.md` without that opt-in is ignored, never a silent swap,
  and a built-in that is not opted in touches no file. Names must match `PROFILE_NAME` (lower case,
  no dots, separators or Windows device names, so `--profile` cannot leave the dir and `Default`
  never opens `default.md` on a case-insensitive FS) and the listed file name exactly. `user_profiles()` reports each file as
  added/overrides/shadowed/invalid name, plus opted-in built-ins without a file (missing), for
  the doctor/profiles commands (#92). `profiles.migrate_profiles()` copies a checkout's added
  or modified `src/prompt_workflow/prompts/*.md` (against `git_pristine_profiles()` or the
  packaged `PROFILES`) into that dir: copy only, exclusive create, never deletes or overwrites.
- `assets.py` — the Espanso match files as package data. Hatch `force-include` copies the repo's
  `espanso/match/` (the source of truth) to `prompt_workflow/espanso/match/` in the wheel;
  `espanso/config/` is not shipped. An editable install falls back to the checkout's folder.
  CI's `wheel` job builds sdist + wheel, installs the wheel in a clean venv outside the checkout
  and runs `scripts/check_wheel.py` (match files, profiles, `persona`) on all three OSes.

### Data-protection gate

`factory.py`'s `make_provider()` (the only place providers are built) wraps, via `_gate()`,
everything that can send the draft off the machine (`_leaves_machine()`) in `gate.GatedProvider`: `openrouter` and
`anthropic` always, `ollama`/`lmstudio` when the base URL is not loopback (`providers.base.is_loopback()`) or
the Ollama model is a `cloud`-tagged one. The gate runs `redaction.scan_draft()` (built-in patterns,
the user's `PROMPT_EXTRA_PATTERNS`, and the whole-draft `bare_token` rule, which `safe_repr()`'s
`scan()` leaves out) and blocks the call unless `ALLOW_CLOUD_OVERRIDE=true`.
`make_provider(..., allow_flagged=True)` (`--allow-flagged`, the `-iok-` trigger) lets one call
through when every finding is in `redaction.SOFT_FINDINGS` (labels, Vietnamese IDs, email, IBAN);
any other finding, including `custom_N`, stays blocked. The CLI then prefixes the output with
`[prompt-workflow: sent despite: …]` (finding names only) from `GatedProvider.sent_despite`.
It also requires `https` cloud base URLs (plain `http` only to loopback).
`providers.base.post_json()` (the only HTTP call) gives a loopback URL its own `HTTPTransport`,
which makes httpx skip env and system proxies for it; every other URL keeps the proxy.
`scripts/bench_models.py` builds through it too. A new provider that can leave the machine must
return through `_gate()` and be listed in `_leaves_machine()`. `PROMPT_LOCAL_ONLY=true` makes
`make_provider()` refuse such a provider before building it (and `_gate()` refuses too), so it
overrides any `--provider` a trigger passes; `PROMPT_PROVIDER` only sets the bare CLI's default.

### Espanso integration contract

- `espanso/match/prompts-llm.yml` triggers call `"__PROMPT_WORKFLOW__" improve ...`; the install
  scripts substitute `__PROMPT_WORKFLOW__` with the resolved absolute path to the installed CLI.
  Only that path is quoted (`cmd.exe` mangles more than one quoted part); there is no `cd`.
  The installers deploy `espanso/config/` only with `--with-config` / `-WithConfig`.
- Every match that runs the CLI (including commented-out ones and `-p-`) sets
  `force_mode: clipboard`, so output is always pasted: Espanso's default backend would type output
  shorter than 100 characters key by key. `tests/test_yaml.py` enforces it.
- `espanso/match/prompts-core.yml` holds static, non-LLM form-based snippets (no CLI call).
- Every match (commented-out ones too) sets `left_word: true`, so a trigger fires only after a
  word separator (space, punctuation, bracket, newline), never inside a word such as
  `a[n-i-1]`; `tests/test_yaml.py` enforces it.
- `-i-` (OpenRouter, `PROMPT_PROFILE`) is the live cloud trigger, `-iok-` is the same call
  with `--allow-flagged`, and `-ip-` is
  the same rewrite with `--tier pro` (reasoning model, `PROMPT_PRO_PROFILE` if set). Only `-il-`,
  `-ilm-` and the commented `-ic-` pass `--profile general`; `tests/test_triggers.py` replays every trigger's real
  command and checks its provider, profile and tier. `-if-` puts an Espanso form
  (choice dropdowns) in front of the pro tier and passes the picks as `--model model@endpoint
  --effort --max-tokens --timeout` via `{{form1.*}}`, which `Settings.with_overrides()` applies.
  Its fields must stay fixed choices (no free text reaches the shell); `tests/test_yaml.py`
  checks each value against the CLI. `-ic-`
  (Anthropic) stays commented out in `prompts-llm.yml`. The data-protection gate is a heuristic,
  not a guarantee — cloud triggers should only be used where company policy permits.
