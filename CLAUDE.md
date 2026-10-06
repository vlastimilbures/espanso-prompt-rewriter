# CLAUDE.md

## What this is

A Python CLI (`prompt-workflow`) invoked by Espanso text-expansion triggers to rewrite a
clipboard/stdin draft into a more precise prompt via a local or cloud LLM. Espanso match files
in `espanso/match/` call the CLI as a shell command and paste back stdout.

## Commands

Contributor install (`uv tool install --editable` constrained to `uv.lock`, checked by
`scripts/check_tool_lock.py`, then `prompt-workflow espanso deploy --yes`, which substitutes the
absolute CLI path into the match files, since GUI-launched Espanso does not inherit shell PATH):

```bash
./scripts/install_macos.sh      # macOS
.\scripts\install_windows.ps1   # Windows
prompt-workflow espanso status  # missing / in sync / stale / modified / foreign per match file
```

Users install the release wheel instead (`uv tool install <wheel> -c constraints.txt`, README
"Install"), which reads no checkout `.env`; README "Updating"/"Uninstall" document `--force`
and detach-before-uninstall. `tests/test_docs.py` checks every `prompt-workflow …` invocation
in README and in the CHANGELOG's Unreleased and newest release notes, and every standalone
`--flag` span in README, against the Click tree.

Checks (CONTRIBUTING "Checks"; CI runs `uv sync --locked`, then these, with `pytest --cov`
held to `fail_under = 95`):

```bash
uv run pytest --cov                             # unit tests, offline
uv run ruff check . && uv run ruff format --check .
uv run mypy                                     # strict, src and scripts
uv run pre-commit run --all-files               # also YAML checks, gitleaks and zizmor
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_tui_snapshots.py  # after a tui/ screen change
```

Rules for agents:

- `scripts/bench_models.py` and `uv run pytest -m live` (`tests/test_live.py`) call the paid
  OpenRouter API: run them only when asked.
- Never run the installers, `prompt-workflow espanso …` (`deploy`, `status`, `detach`),
  `setup --deploy` or `espanso` itself: they read or rewrite the user's live Espanso match
  files, and a deploy restarts Espanso.
- Tests stay offline: no external API calls; mock HTTP (`fake_http`).
- Every user-visible change gets a CHANGELOG `## Unreleased` entry and a README update.

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
  `OPENROUTER_PRO_*` settings (`OPENROUTER_PRO_MAX_TOKENS` only if set; empty inherits
  `OPENROUTER_MAX_TOKENS`) and `PROMPT_PRO_PROFILE` (if set) via `Settings.for_tier()`; `--model`/`--effort`/`--max-tokens`/
  `--timeout` are applied by `Settings.with_overrides()` (`--model` sets every provider's model),
  so `make_provider()` always sees the effective settings (`--max-tokens` for Ollama/LM Studio
  goes in `call_max_tokens`, a per-call field that is not a setting: `setting_fields()` skips
  it). `Settings.for_call()` chains the two, applies the tier only for `provider="openrouter"`,
  and drops back to `PROMPT_PROFILE` when a pro call runs a model other than
  `OPENROUTER_PRO_MODEL`. `config.openrouter_only()` then refuses `--tier pro` and a non-`default`
  `--effort` for any other provider (a ValueError marker, `error_marker` in the history). An
  explicit `--profile` beats both, so the OpenRouter triggers pass none.
- `clipboard_guard.py` — `is_concealed()` asks the clipboard (ctypes: NSPasteboard types on
  macOS, user32 formats on Windows) whether a password manager marked the item, without reading
  it. `cli._read_input()` refuses such an item before `pyperclip.paste` and clears the clipboard
  (Espanso's restore would put it back unmarked); `None` (Linux, a probe error) reads as before. `tests/conftest.py` stubs it so tests never probe the real clipboard.
- `factory.py` — `make_provider(name, cfg)` builds a provider from `Settings`; `PROVIDER_NAMES`
  lists the valid names. `cli.py` and `scripts/bench_models.py` both build through it. For
  OpenRouter it adds the endpoint pin (`OPENROUTER_PROVIDER`), `provider.data_collection`
  when `OPENROUTER_DATA_COLLECTION` is set (both tiers; empty sends no field) and
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
  into the focused app; it also hides a value matching PROMPT_EXTRA_PATTERNS once settings
  resolve (`ConfigLayers.resolve()` resolves that setting first and calls
  `redaction.set_user_patterns()` before parsing the rest; conftest resets it). Errors raised
  before that point, and Click usage errors before any load, see only the built-in patterns. `Settings.load()` builds from `ConfigLayers.resolve()`, a pure merge
  of layers (lowest first): built-in default < saved settings < the secret store < the real
  environment; per-call overrides come after, in `Settings.with_overrides()`. Saved settings
  are `$PROMPT_WORKFLOW_ENV` alone if set (legacy mode: no TOML, no secret store, exactly as
  before); else the user config dir's `config.toml` once it exists (then no `.env` is read, so
  one can never shadow a saved value; repair mode reports a lingering one); else the first
  readable of the editable-install repo root's `.env` (derived from `__file__`) or the user
  config dir `.env` (read as UTF-8, a byte order mark dropped; one that is not UTF-8 is an error,
  never skipped for the next one; an unquoted ` #` starts a comment, and repair mode and
  `config migrate` flag only a `_HASH_IN_VALUE` value cut that way: the extra patterns, which
  also fail strict mode, so the gate never runs on part of them, and the persona, where `#`
  may be data). The secret store (`config_files.secret_store()`, today `secrets.toml`; a
  keyring seam is left as a TODO) holds only `secret_names()`. A broken `config.toml` or store
  fails closed: `improve` prints the marker. `persona` then reads PROMPT_PERSONA alone
  (`ConfigLayers.resolve(only=...)`: another setting's bad value or a broken secret store does
  not hide it) and prints its placeholder only when the persona itself cannot be read. It never
  writes `os.environ` (a second load sees an edited file; child processes inherit nothing) and
  records each key's source (`default`, `file:<path>`, `env`) and the layers it shadows. Only
  `env_names()` keys are taken, so a `.env` cannot set `HTTPS_PROXY`, `SSL_CERT_FILE` or any
  other variable, and a value holding another setting's `NAME=` (a merged line) is refused.
  `resolve(strict=False)` (repair mode) returns those errors as `Finding`s and falls back to the
  next lower layer (recorded in `Entry.rejected`) instead of raising; it also notes an unreadable
  `.env` and lines without `=`, which strict mode skips silently. It deliberately never reads the
  cwd, so a planted `.env` cannot redirect the base URL or enable the override. This matters
  because Espanso runs the CLI as a GUI-spawned subprocess without an inherited login-shell
  environment. `tests/conftest.py` points `PROMPT_WORKFLOW_ENV` at a temp file per test, and
  `HOME`, `XDG_CONFIG_HOME`, `APPDATA` and `_PROJECT_ROOT` at temp dirs.
- `config_files.py` — the light read side (on the trigger path: `tomllib` only once a file
  exists) and `write_atomic()` (temp file in the same dir + `os.replace`; mode 600 from creation
  on POSIX; on Windows a protected single-ACE DACL for the current user's SID, set via Win32
  while the file is empty and read back, else `SecretStoreError`; no plaintext fallback).
  A config dir that is a file or unreadable, or a `config.toml`/`secrets.toml` that is not a
  regular file, counts as absent; only a real but unreadable file fails closed. The
  migration marker is written before any change and names every place a `.env` may go.
- `config_store.py` — services for the management commands (#92), never on the trigger path:
  `save_settings()` (validates with the field parsers, refuses secrets, a newer
  `config_version`, and a file changed since its `read_settings()` snapshot, naming the changed
  keys), `save_secret()`, and the `.env` migration: `plan_migration()` previews (names only),
  `apply_migration(consent=plan.token)` backs up, writes, verifies by reloading that every
  effective value is unchanged, then moves each `.env` into `backups/` (rename, never delete)
  and writes `migration.json`; `plan_rollback()`/`apply_rollback(consent=...)` restore exact
  bytes. Copy mode (#110, `source=<checkout root>`): the earlier checkout's `.env` fills only
  keys whose effective source is the default (the rest are listed as `kept`), the active
  `.env` still migrates as above, the check is `after == before` overlaid with the plan, the
  copied `.env` is never moved, and the marker gets `"mode": "copy"` and a `copied` entry.
  `plan_retire()`/`apply_retire()` later rename that `.env` into the backup, refused while a
  deployed match file or manifest entry still runs a launcher inside the checkout (or Espanso
  cannot say where its files are); a rollback moves a retired one back. `tomli_w` is imported
  lazily (forbidden on the trigger path).
- `history.py` — the local usage history (#88): `HistoryStore` over a per-device SQLite file,
  `config.user_data_dir()/history.sqlite3` (`$XDG_DATA_HOME` or `~/.local/share`,
  `%LOCALAPPDATA%` on Windows; `tests/conftest.py` points both at a temp dir). Metadata only:
  the tables' columns are `OPERATION_COLUMNS` and `ATTEMPT_COLUMNS` (the `AttemptUsage` fields
  of #87, keyed by `(operation_id, seq)`), and only those keys are read from a record mapping.
  Each text column has its own shape (`_TRIGGER`, `_PROFILE`, `_PROVIDER`, `_MODEL`,
  `_GENERATION_ID`, `_VERSION`, or an enum such as `ERROR_KINDS`, which a test ties to
  `providers/base.py`); only the model columns take `/` and `:`, none takes a space, `@` or
  `\`, and `_looks_secret()` (the gate's patterns plus `PROMPT_EXTRA_PATTERNS`, and the
  bare-token check on each part) rejects the rest. A bad optional value becomes NULL (a bad
  cost leaves it `unknown`); a bad required one drops the record. `tests/test_history.py` holds the allowlist and fails on any other column; add a
  column only through a new `_MIGRATIONS` step (forward only, `PRAGMA user_version`). Money is
  exact decimal TEXT plus a unit, never REAL. `record()` never raises and returns within `_BUDGET`
  (0.25 s) from its start: one `BEGIN IMMEDIATE` transaction within `_WRITE_BUDGET`, and a
  dropped write bumps the `history.lost` sidecar (temp file + `os.replace` under a lock file
  holding a nonce; a stale one is broken by renaming it first). The services (`stats`, `export`, `prune`,
  `reset`) raise `HistoryError`; `reset()` deletes the file only on `UnusableHistory` (corrupt
  or newer schema), never when it is merely locked. `health()` never raises and sets
  `tracking_incomplete` when writes were lost or the files cannot be written. The write that creates the
  database gets `_CREATE_EXTRA` (0.75 s) on top of both budgets. `record(prune=True)` (the
  recorder's call) then deletes up to `_PRUNE_BATCH` operations past the retention in a second
  short transaction, only after the record committed and only within the budget (an interrupt
  rolls back a whole SQLite transaction, so the two are never one), so a failed prune never
  costs the record. `prompt-workflow history prune` prunes on demand. `PROMPT_HISTORY` (default `true`) and
  `PROMPT_HISTORY_RETENTION_DAYS` configure it; estimates come only from a user `prices.toml`
  in the config dir and are kept apart from reported costs. `sqlite3`, `tomllib`, `csv` and
  `decimal` are imported inside functions, and `recorder.py` imports the module only when a run is recorded.
- `recorder.py` — records each `improve`/`persona` run (#89). `Recorder.track(cfg)` turns it
  on when `PROMPT_HISTORY` is true (settings that fail to load record nothing); its `observer`
  goes to `make_provider(observer=...)` and only collects `AttemptUsage` in memory; `finish()`
  flushes stdout, then writes the operation and every attempt in one `history.record(...,
  prune=True)` after `_emit()`, so history never changes stdout, the exit code or a retry, and
  a rejected reply's charge is kept. `cli._failure()` maps the marker's exception to the
  outcome: `ConcealedClipboard` -> `concealed_refused`, `ClipboardUnavailable` ->
  `clipboard_failed`, `gate.GateBlocked` (the gate's block, and factory.py's `PROMPT_LOCAL_ONLY` and non-https
  URL refusals) -> `gate_blocked`, a non-ProviderError/ValueError ->
  `unexpected_error`, a ProviderError after a 2xx last attempt (`Recorder.answered`) ->
  `validation_failed`, else `error_marker`. Off, it never imports `history`/`sqlite3`
  (`tests/test_trigger_contract.py` runs both ways).
- `prompt_builder.py` — `PROFILES` maps a profile name (`default`, `general`) to a system
  prompt used to instruct the rewrite; `ALIASES` keeps the retired `default-pro` resolving to `default`. `render()` fills the `{{PERSONA_RULE}}` token (and
  `{{PERSONA_OPENING}}`, which opens both examples' CONTEXT, #49) in one pass from
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
  `espanso/match/` (the source of truth) to `prompt_workflow/espanso/match/` in the wheel. An editable install falls back to the checkout's folder.
  CI's `wheel` job builds sdist + wheel, installs the wheel in a clean venv outside the checkout
  and runs `scripts/check_wheel.py` (match files, profiles, `persona`) on all three OSes.
- `deploy.py` — managed Espanso deployment behind `prompt-workflow espanso deploy|status|detach`
  (cli.py imports it lazily, and `tests/test_trigger_contract.py` keeps it off the trigger path).
  `plan()` renders each packaged match file (`render()`: the stamp line
  `# prompt-workflow <version> (managed; edit at your own risk)` + a literal replace of
  `__PROMPT_WORKFLOW__`, byte-identical to the old scripts otherwise, see `tests/test_deploy.py`)
  and compares it with disk and the manifest (`user_data_dir()/espanso-manifest.json`: target,
  asset version, digest, launcher, backups) into `missing`/`in sync`/`stale`/`modified`/`foreign`.
  A file that is exactly what any release wrote counts as ours (`stale`): stamp dropped, the
  quoted launcher (`\"<path>\"`, so Windows forward slashes too) put back as the placeholder,
  and its SHA-256 found in `match_history.KNOWN_SOURCES` (generated by `scripts/update_match_history.py` as a
  union that never drops a listed digest: the `v*` tags, the file at every commit that changed it
  on this branch and on `origin/HEAD` (an editable install runs untagged commits), and the
  current sources; rerun after editing `espanso/match/`
  and at each release, `tests/test_deploy.py` checks it). A symlinked target is `foreign`.
  `Manifest.load()` type-checks every field (`DeployError` otherwise), writes go through
  `mkstemp` in the same folder + `os.replace`, and backups use `"xb"` after an `is_symlink()`
  check.
  `apply()` writes only into `<espanso>/match/` (never `config/`, #37), keeps a modified/foreign
  file unless the caller chose `ours` (timestamped backup; pruning to 2 deletes only paths in the
  target's folder named exactly `<name>.bak-<14 digits>[-n]`) or `side`
  (`<name>.prompt-workflow-new`), and retires the pre-0.9 `match/base.yml` with a backup. The
  CLI ends a deploy that kept a file with a stderr `WARNING:` naming it (exit 0).
  `detach()` (default `--keep-static`, D-UNI-1) removes only owned files whose digest still
  matches, and only entries naming one of `assets.match_names()` in the current (resolved)
  Espanso match folder; `--espanso-dir` is resolved to an absolute path first. `resolve_launcher()` picks the channel's stable entry point (uv tool bin, Homebrew
  `<prefix>/bin` or `opt`, Scoop shim, else a running console script outside any versioned,
  `Cellar` or `.venv` dir); `launcher_text()` keeps the old path guards (POSIX refuses
  a quote, `$`, backtick or backslash; Windows converts to `/` and refuses `" % ^ & | < >`). Every external command
  (`espanso path config`, `espanso restart`/`start`, `uv tool dir`, `brew --prefix`) goes
  through `run_command`, which `tests/conftest.py` replaces with a refusal.
- `commands/` — the management commands (#92): `setup`, `config show|get|set|unset|validate|
  migrate|rollback`, `secrets set|status|remove`, `profiles list|migrate`, `stats`,
  `history export|prune|reset`, `doctor` (plus `espanso` in `cli.py`, which gained
  `deploy --dry-run`). `cli.py`'s `_LazyGroup` imports a command's module only when it runs or
  `--help` lists it (`_LAZY_COMMANDS`), so improve/persona never load them; it also passes
  every Click usage error through `redaction.redact_words()`, since Click quotes a stray argument;
  `tests/test_trigger_contract.py` forbids `commands`, `doctor`, `smoke` and `config_store` on
  the trigger path. `--version` is an eager callback on `_main`. Unlike the triggers they use
  ordinary exit codes (`commands/common.py`: 0 ok, 1 failed, 2 usage, 3 needs a terminal, 4
  doctor/validate found problems; README "CLI"), print errors to stderr via `@guard` (never a
  traceback), load settings in repair mode (`load_layers()`), never prompt without a TTY
  (`stdin_is_tty()`, which tests patch), colour only on a TTY without `NO_COLOR`, and take a key
  only from stdin or `getpass` (`read_secret()`); `config set` refuses secret names and any
  value `scan()` flags as a credential. `config set`/`secrets set` refuse in legacy
  `PROMPT_WORKFLOW_ENV` mode, and `config set` refuses while a `.env` is in use without
  `config.toml` (it would be orphaned): migrate first. `config migrate|rollback` apply only with
  an interactive yes or `--yes --preview-token <token printed by the preview>`.
  `tests/test_commands.py` walks the whole command tree: no option named like a secret, a key
  given to any value is never saved, and every command on a broken `.env`/TOML/secrets exits
  0-4 without a traceback (add a new command to `_commands()` there).
- `tui/` — the full-screen Textual interface (#93): `prompt-workflow ui`, and a bare
  `prompt-workflow` when stdin and stdout are both TTYs (D-UI-1: `_LazyGroup.parse_args()` turns
  `[]` into `["ui"]`; anywhere else `no_args_is_help` prints the help and exits 2 exactly as
  before, pinned by `tests/golden/no-args-help.txt`; `ui` without a TTY exits 3). `textual` is a
  required dependency (D-UI-2) imported only by `tui/`, which only `commands/ui.py` imports, once
  the interface opens; `tests/test_trigger_contract.py` checks neither the triggers nor `--help`,
  `doctor`, `config show` and the other headless commands load it. Six tabs (Home, Providers &
  keys, Profiles, Triggers, History, Diagnostics) show one `tui/state.State`, which `gather()`
  reads again in a worker thread after every change (a generation number drops an older load
  that finishes late); each action calls the same services as the
  headless command (`commands/settings.save_setting()` is shared with `config set`) and adds no
  logic. Keys appear only as set/not set; destructive actions (key removal, deploy, detach,
  migrate, history prune/reset) go through a dialog whose Cancel has the focus, and deploy keeps
  an edited file unless a conflict choice says otherwise. Only one deploy/detach dialog opens at a
  time, and each re-reads the plan or manifest before writing and refuses if it changed since the
  preview. Workers run through `Pane.background()`, which reports any failure instead of
  crashing; base URLs and models are shown through `common.shown_value()`. No provider call except the explicit
  "Test call" (`smoke.run()`); doctor runs without the clipboard. Bindings are letters and digits
  only (on `MainScreen`, so none fires under a dialog); `t` switches to the high-contrast theme;
  Textual honours `NO_COLOR`; exit is `sys.exit(app.return_code or 0)`. `tui/previous.py`'s
  `PreviousInstallScreen` (#110) opens by itself once per session when `State.previous`
  (`previous_install.detect()`) has a candidate or an unskipped pending copy, and from Home's
  "Previous install…": four steps (copy via `plan_migration(source=)`, profiles via
  `panes.copy_profiles()`, deploy via `TriggersPane.start_deploy()`, retire via
  `plan_retire()` in a worker, enabled only once no launcher points into the checkout and
  refused without `espanso path config`'s answer), none opening while `TriggersPane.busy`;
  digits 1-4 as keys,
  "Enter a path…" (detected again with `entered=`) and "Skip" (the skip marker, pending root
  too). `tests/test_tui.py`
  drives each tab with Pilot (`asyncio.run`, no pytest-asyncio); `tests/test_tui_snapshots.py`
  compares SVG exports with `tests/snapshots/` (Linux and macOS; `UPDATE_SNAPSHOTS=1`
  regenerates them, and the Home one is `docs/interface.svg`). The header shows
  `prompt-workflow <version>` (#112); the snapshots pin the version, so a release changes none.
- `doctor.py` — `run()` returns a `Report` of the fixed `CHECK_IDS` (JSON `schema_version` 1:
  only add ids/keys). Read-only: `espanso path config`/`espanso status` and the launcher lookup
  via `run_command`; keys as set/not set; the clipboard only as a length (never read when
  concealed). Match files `stale`/`missing` or a deployed launcher that is gone fail (exit 4).
  The `persona` check (shared with `config validate` via `persona_problem()`) runs
  `redaction.scan()` (no `bare_token`) with `PROMPT_EXTRA_PATTERNS` over `PROMPT_PERSONA`, which
  the gate never scans, and warns by finding name only (#29), only when a configured profile's
  template (`prompt_builder.template()`) holds `{{PERSONA_RULE}}`, and not with
  `PROMPT_LOCAL_ONLY=true` unless `PROMPT_GATE_LOCAL=true` (`_persona_stays_local()`); shown on
  the interface's Home and Diagnostics tabs; never on the trigger path.
  `import_check()` (the interface's Diagnostics) imports `prompt_workflow.cli` in a fresh
  interpreter (`-P`, so a module planted in the working directory never runs; the smoke test's
  child uses `-P` too) and reports its time, module count and any `HEAVY_MODULES` it loaded.
- `previous_install.py` — finds an earlier checkout install (#110) from the launcher in the
  deployed match files (`deploy.deployed_launchers()`) and manifest, the uv tool receipt and a
  path the user entered; never a disk scan, and a checkout's `.env` is only checked for
  existence. A root counts only as `<root>/.venv/bin/prompt-workflow` (or
  `.venv\Scripts\prompt-workflow.exe`) whose `pyproject.toml` names this project and that is
  not the running `config._PROJECT_ROOT`. Gated (D-MIG-4): nothing in legacy mode or once
  `config.toml` or the secret store exists; a manifest does not gate. Skipped roots live in
  `user_data_dir()/previous-install.json` (damaged = empty). `shadow()` reports a different
  `prompt-workflow` first on PATH. `Detection.pending` (ungated, since the copy itself closes
  the gate) is a copy from `migration.json` whose `.env` is not retired yet: doctor WARNs
  (`retire_pending`) once no launcher points into it. `setup` offers the copy at the start of
  its Settings step (`--migrate-from PATH`), then the profiles, and the retire after the
  deploy step; a broken `.env` in a checkout it found by itself (not entered) is a to-do and
  setup then writes nothing, since config.toml or a key would close the gate. `best()` picks the
  candidate setup and the interface offer. Doctor's `previous_install` check; off the trigger
  path.
- `smoke.py` — `setup`'s smoke test: a `ThreadingHTTPServer` on 127.0.0.1:0 answering the
  OpenAI-compatible, Anthropic and Ollama shapes, and a child `python -m prompt_workflow.cli
  improve --provider <p>` whose env points every `*_BASE_URL` at it with a placeholder key (the
  real key is never sent). `setup` offers a `.env` migration (applies only on an interactive
  yes), previews the deploy (`--deploy` applies, keeping edited files), and discloses the usage
  history (D-HIST-0).

### Data-protection gate

`factory.py`'s `make_provider()` (the only place providers are built) wraps, via `_gate()`,
everything that can send the draft off the machine (`_leaves_machine()`) in `gate.GatedProvider`: `openrouter` and
`anthropic` always, `ollama`/`lmstudio` when the base URL is not loopback (`providers.base.is_loopback()`) or
the Ollama model is a `cloud`-tagged one (`_is_ollama_cloud()`: the tag only, any case, an
`@digest` stripped). `PROMPT_GATE_LOCAL=true` gates a loopback Ollama/LM Studio too
(`_gate(..., remote=False)`), without changing `_leaves_machine()`, so `PROMPT_LOCAL_ONLY`,
`routes()` and the `not_applicable` cost still treat it as local. The gate runs `redaction.scan_draft()` (built-in patterns,
the user's `PROMPT_EXTRA_PATTERNS`, and the whole-draft `bare_token` rule, which `safe_repr()`'s
`scan()` leaves out) and blocks the call unless `ALLOW_CLOUD_OVERRIDE=true`.
`make_provider(..., allow_flagged=True)` (`--allow-flagged`, the `-iok-` trigger) lets one call
through when every finding is in `redaction.SOFT_FINDINGS` (labels, Vietnamese IDs, email, IBAN);
any other finding, including `custom_N`, stays blocked. `Settings` rejects an invalid
`PROMPT_EXTRA_PATTERNS` regex when it loads (`config._regexes`, naming the entry, never the
pattern); every repeat in a built-in pattern is bounded (a test walks each one). The CLI then prefixes the output with
`[prompt-workflow: sent despite: …]` (finding names only) from `GatedProvider.sent_despite`.
It also requires `https` cloud base URLs (plain `http` only to loopback).
`providers.base.post_json()` (the only HTTP call) gives a loopback URL its own `HTTPTransport`,
which makes httpx skip env and system proxies for it; every other URL keeps the proxy.
`scripts/bench_models.py` builds through it too. A new provider that can leave the machine must
return through `_gate()` and be listed in `_leaves_machine()`. `PROMPT_LOCAL_ONLY=true` makes
`make_provider()` refuse such a provider before building it (and `_gate()` refuses too), so it
overrides any `--provider` a trigger passes; `PROMPT_PROVIDER` only sets the bare CLI's default.

### Espanso integration contract

- `espanso/match/prompts-llm.yml` triggers call `"__PROMPT_WORKFLOW__" improve ...`;
  `prompt-workflow espanso deploy` (which the install scripts call) substitutes
  `__PROMPT_WORKFLOW__` with the stable absolute path to the installed CLI.
  Only that path is quoted (`cmd.exe` mangles more than one quoted part); there is no `cd`.
  Nothing is ever deployed to Espanso's `config/` (`espanso/config/` and `--with-config` were
  removed, #37).
- Every match that runs the CLI (including commented-out ones and `-p-`) sets
  `force_mode: clipboard`, so output is always pasted: Espanso's default backend would type output
  shorter than 100 characters key by key. `tests/test_yaml.py` enforces it.
- Every match (commented-out ones too) has its own `label:` (`prompt-workflow: …`), shown in
  Espanso's search bar; `tests/test_yaml.py` enforces it.
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
  Its fields must stay fixed choices (no free text reaches the shell), each list starting with
  and defaulting to `default` (keep the pro-tier setting), timeouts at most 120 s;
  `tests/test_yaml.py` checks each value against the CLI. `-ic-`
  (Anthropic) stays commented out in `prompts-llm.yml`. The data-protection gate is a heuristic,
  not a guarantee — cloud triggers should only be used where company policy permits.
- Every CLI match (commented-out `-ic-` and `-p-`'s `persona` call too) passes its own literal
  `--trigger-id <id>` right after the subcommand: the trigger without dashes, from
  `recorder.TRIGGER_IDS` (`i iok ip if il ilm ic p`), stored in the usage history as `-i-`.
  Never a `{{form}}` value. No option means `origin=direct`; an unknown id is recorded
  unattributed (`espanso_managed`, trigger NULL) and never fails the run; the option is hidden from `--help` and is never inferred from other flags.
  `tests/test_yaml.py` checks each match's id; `tests/test_triggers.py` replays each and
  checks what is recorded. Changing a match file needs `scripts/update_match_history.py`.

### Releases

`.github/workflows/release.yml` is the only way a version is tagged and released (steps in
CONTRIBUTING's "Releasing"; the one exception is the documented v0.10.0 backfill). It runs on a
manual dispatch with *dry-run* unticked, or on a push to `main` only while the repository
variable `RELEASE_ON_PUSH` is `true`; it releases only the current head of `main` and does
nothing for a version whose Release is already published. A half-failed run can be re-run (it
reuses an annotated tag on the same commit and a draft Release). Only its `release` job can
write. `scripts/release_notes.py` gives the workflow the
version and its notes and refuses a CHANGELOG whose newest `## X.Y.Z - YYYY-MM-DD` heading is not
the `pyproject.toml` version (`## Unreleased` may come first); `tests/test_release.py` runs the
same check. Each Release carries `constraints.txt` (uv.lock's runtime pins), and the artifact
test installs the wheel with it and runs `scripts/check_wheel.py --constraints`.
