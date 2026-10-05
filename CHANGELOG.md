# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## Unreleased

### Added
- A full-screen interface for setting up and managing prompt-workflow (#93, #64): run
  `prompt-workflow` in a terminal, or `prompt-workflow ui`. Six tabs (Home, Providers & keys,
  Profiles, Triggers, History, Diagnostics) show and change what the headless commands do,
  through the same services: keys only as set or not set, each trigger with its fixed provider
  and deploy state, a diff preview, deploy (an edited file is kept unless you choose otherwise),
  detach, `.env` and profile migration, export, prune and reset of the usage history, config
  provenance, SQLite and lost-write state, and an import-time check. Every change asks first
  (Cancel has the focus; deploy and detach refuse a plan that changed since their preview) and
  reloads every tab; credentials in a base URL are never shown; no provider is called except by the Test call button (a stub on
  127.0.0.1). Keys are letters and digits, `t` switches to a high-contrast theme, and
  `NO_COLOR` is honoured. Without a terminal a bare `prompt-workflow` still prints the help and
  exits 2 (the help now lists `ui`), and `ui` exits 3. Textual is a new dependency
  (`textual>=8.2.8,<9`), loaded only when the interface opens: no trigger or other command
  imports it. The `doctor` service gains `import_check()`, `factory` gains `routes()` and
  `assets` gains `triggers()`, read-only helpers the interface shows.
- Headless management commands (#92), each a thin wrapper over a service and safe to script:
  `prompt-workflow --version`; `setup` (provider, default profile, API key, a deploy preview and
  a smoke test that runs `improve` against a stub on 127.0.0.1, never a paid call; it offers a
  `.env` migration and applies it only on a yes; `--non-interactive`, `--api-key-stdin`,
  `--deploy`); `config show [--raw]|get|set|unset|validate|migrate|rollback` (show gives each
  value's source and what it overrides; migrate and rollback need a confirmation, or
  `--yes --preview-token` from the preview); `secrets set [--stdin]|status|remove`;
  `profiles list|migrate`; `stats [--by trigger|provider|model|day] [--json]` with its caveats
  (local observations, not billing; rendered is not pasted; triggers with an explicit provider
  ignore `PROMPT_PROVIDER`); `history export|prune|reset`; and `doctor [--json]` (#25), which
  reports each deployed match file (`prompts-template.yml: stale` in the drift case), launcher
  drift, keys set or not, Espanso found and running, history health and the clipboard's length,
  never a key, persona or clipboard text. `espanso deploy` gains `--dry-run`. A key is never a
  command-line argument (stdin or a hidden prompt only; `--stdin` at a terminal asks hidden),
  `config set` refuses one, and neither a usage error nor `config show|get` repeats one. These
  commands use exit codes 0-4 (README, "CLI"), never prompt without a terminal (exit 3), run on
  a broken `.env`/TOML in repair mode, and honour `NO_COLOR`. `setup` and `stats` disclose the
  usage history and how to switch it off (`prompt-workflow config set PROMPT_HISTORY false`).
  The commands load lazily, so the triggers import none of them.
- The usage history records every `improve` and `persona` run (#89): one operation per run
  (origin, trigger, kind, profile, outcome, latency until the output was printed) and one
  attempt per HTTP attempt, a retry's two included, with its tokens and reported charge. The
  run is written after its output is printed and flushed, in one transaction, so history never
  changes what is pasted, the exit code or a retry, and the charge of a reply whose content was
  then rejected, or whose `--copy` failed, is kept. Outcomes: `ok`, `error_marker`,
  `gate_blocked` (the gate, `PROMPT_LOCAL_ONLY` or a non-https cloud URL), `validation_failed`, `clipboard_failed`, `concealed_refused`,
  `unexpected_error`. Settings that fail to load record nothing (whether history is
  off is then unknown). With `PROMPT_HISTORY=false`
  nothing is written and `sqlite3` is never imported. Each recorded run also prunes up to 100
  records past `PROMPT_HISTORY_RETENTION_DAYS`, in its own transaction after the record is
  committed, and the write that creates the database gets
  0.75 s more than the usual ~0.25 s budget (a slow disk dropped it).
- `improve` and `persona` take a hidden `--trigger-id` from a fixed allowlist (`i`, `iok`,
  `ip`, `if`, `il`, `ilm`, `ic`, `p`); every managed Espanso match now passes its own as a
  literal argument (the commented-out `-ic-` too, and `-p-` through its one `persona` call).
  Without it a run is recorded as `direct`; an unknown value is recorded unattributed (a managed run
  with no trigger) and never fails. What a trigger pastes is unchanged. Run `prompt-workflow espanso deploy` to update the
  deployed matches; older ones keep working and are recorded as `direct`.
- `scripts/update_match_history.py` never drops a digest `match_history.py` already lists, and
  also records the match files at every commit that changed them on the branch and on the
  default branch, so a file an editable install of an untagged commit deployed is recognised
  as ours (`stale`), not `foreign`.
- Managed Espanso deployment (#86): `prompt-workflow espanso deploy|status|detach`. `deploy`
  shows a plan and a diff and asks first (`--yes` skips); a second run with nothing to change
  does nothing. A manifest in the per-device data dir (`~/.local/share/prompt-workflow/`,
  `%LOCALAPPDATA%\prompt-workflow\` on Windows) records each file it wrote, so `status` reports
  `missing`, `in sync`, `stale` (an older deploy of ours, the #25 drift case), `modified` or
  `foreign`. Each deployed file opens with `# prompt-workflow <version> (managed; edit at your own
  risk)`; the rest is byte-identical to what the install scripts wrote. A file you edited is never
  overwritten silently: keep yours (the default with `--yes`), take ours with a
  `.bak-<timestamp>` backup (only our last 2 backups are kept), or write ours side by side
  (`--on-conflict keep|ours|side`); a deploy that kept a file ends with a `WARNING` naming it.
  A file any earlier release's installer wrote, unedited, is recognised as ours and updated
  (by digest of its source, `match_history.py`). `detach` keeps `-prompt-`/`-risk-` by default
  (`--keep-static`) or removes every deployed file (`--remove-all`), and changes only files
  still as we wrote them. The launcher is the install channel's stable entry point (uv's tool
  bin, Homebrew's `bin/`, Scoop's shim), never a versioned path an upgrade removes.
- The install scripts pin the tool to `uv.lock` (`uv export` constraints for `uv tool install`)
  and check the result with `scripts/check_tool_lock.py` (#34), then call
  `prompt-workflow espanso deploy --yes`.
- Per-attempt usage metadata for every provider (#87), the data source for the coming usage
  history. `make_provider(..., observer=...)` passes an observer to each provider, inside the
  gate, and every HTTP attempt (both attempts of a retry, a timeout, a non-2xx reply) reports
  one `AttemptUsage`: status, error kind, latency, tokens (uncached input, cache read and
  write, output, reasoning), the cost as a `Decimal` with its unit, and the cost state. A
  missing cost is `None` with state `unknown`, never 0; a reported 0 stays 0. OpenRouter's
  `usage.cost` is in credits, and a BYOK call's upstream cost is kept apart from it; local
  Ollama and LM Studio are `not_applicable`. Usage is recorded before the reply is
  finalised, so a reply that then fails still has its tokens on record. Records never hold the
  prompt, the response, a raw body or a key. No trigger output changes: nothing passes an
  observer yet.
- A release workflow (#94). Run by hand with *dry-run* unticked (or, with the repository
  variable `RELEASE_ON_PUSH` set to `true`, on a push to `main`) for the head of `main` whose
  version has no published Release yet, it builds the sdist and wheel,
  installs the wheel in a clean venv on macOS, Windows and Linux and checks its match files,
  profiles and `persona`, attests build provenance, and creates the annotated tag and a GitHub
  Release with the version's CHANGELOG notes. Each Release also carries `constraints.txt`, the
  runtime pins from `uv.lock`, so `uv tool install <wheel-url> -c <constraints-url>` installs
  exactly the locked dependencies. Release builds pin the build backend (hatchling 1.32.4).
  CHANGELOG headings now carry their release date (`## 0.15.0 - 2026-10-04`); a test checks that the newest one is the `pyproject.toml` version.
- Saved settings (#84). Once `config.toml` exists in the config folder
  (`~/.config/prompt-workflow/`, `%APPDATA%\prompt-workflow\` on Windows), it is the saved
  configuration and no `.env` is read, so an old `.env` never overrides a saved value. API keys
  live apart in `secrets.toml`, private to your user (mode 600 from creation on macOS/Linux, a
  user-only access list on Windows; if that cannot be set, nothing is written). Precedence:
  default < `config.toml` or `.env` < `secrets.toml` < real environment < a trigger's options.
  With `PROMPT_WORKFLOW_ENV` set, that `.env` is used alone, as before, and without
  `config.toml` nothing changes. Services (the commands follow in #92) save settings validated
  and atomically, refuse a write over an edit made since the file was read (naming the changed
  settings), never write a secret to `config.toml`, treat a newer `config_version` as
  read-only, and migrate a `.env` after a preview and explicit confirmation: back up, write,
  verify by reloading, then move the `.env` into `backups/` (never deleted); a rollback
  restores the exact files. New dependency: `tomli-w`, loaded only when saving.
- Your own profiles live in `~/.config/prompt-workflow/profiles/<name>.md`
  (`%APPDATA%\prompt-workflow\profiles\` on Windows), next to the user `.env`, where an
  upgrade cannot replace them (#85). A new name works with `--profile <name>`, `PROMPT_PROFILE`
  or `PROMPT_PRO_PROFILE`. A file named like a built-in (`default.md`) is ignored unless the new
  setting `PROMPT_PROFILE_OVERRIDES` lists that built-in (comma-separated, built-in names only),
  so a stray copy cannot silently change what every trigger sends. The package's own profiles
  are never written. Service functions for the coming `doctor`/`profiles` commands report each
  user file (added, overrides, shadowed, invalid name, missing) and copy profiles a checkout
  added or edited under `src/prompt_workflow/prompts/` into the new folder, without deleting or
  overwriting anything.
- The wheel ships the Espanso match files at `prompt_workflow/espanso/match/`, read through the
  new `prompt_workflow.assets` module, so an installed (non-editable) package can deploy its
  triggers (#85). `espanso/config/` is not shipped. A new CI job builds the sdist and wheel,
  installs the wheel into a clean venv outside the checkout on Linux, macOS and Windows, and
  checks that every match file and profile resolves and that `prompt-workflow persona` runs.
- `-iok-` (`--allow-flagged`) sends one draft that the gate blocked only for soft findings: a
  confidentiality label, a Vietnamese ID number, an email address or an IBAN (#21). The paste
  starts with `[prompt-workflow: sent despite: …]`, and the next draft is checked as usual. Keys,
  tokens, passwords, cards, private keys, a bare token and `PROMPT_EXTRA_PATTERNS` matches are
  never sent this way. The block message now offers `-iok-` when it applies, and no longer
  recommends `ALLOW_CLOUD_OVERRIDE`.
- Tests freeze the trigger contract (#82): the exact stdout bytes of `improve` and `persona`
  (success, each error marker, the `sent despite` note, the persona placeholder), and an import
  guard that runs both in a fresh interpreter and fails if they load `textual`, `rich.console`,
  `sqlite3`, `tomli_w`, `tomlkit` or `keyring`, or more than 400 modules. CONTRIBUTING records
  the measured start-up time under "Trigger start-up budget". Nothing the CLI prints changes.
- A local usage-history store (`history.py`, #88): a per-device SQLite file in the user data
  dir (`~/.local/share/prompt-workflow`, `%LOCALAPPDATA%\prompt-workflow` on Windows) that
  holds metadata only, from a fixed column allowlist, and never prompt, clipboard, output,
  persona, key, form or raw-body text. Money is exact decimal text with its unit (`credits` or
  `USD`). Writes are fail-open and bounded (about 0.25 s), keyed by (operation, attempt) so a retry
  never duplicates, and a dropped write is counted in a `history.lost` marker. Services: stats
  by trigger, provider, model or day (reported, estimated and unknown costs kept apart),
  CSV/JSON export, prune, reset and health. New settings `PROMPT_HISTORY` (default `true`) and
  `PROMPT_HISTORY_RETENTION_DAYS` (default `365`, at most 36500). Optional estimates come from a user
  `prices.toml` in the config dir. Nothing records yet; the CLI starts recording in #89.

### Security
- A clipboard item that a password manager marked as concealed is refused before it is read,
  for every trigger, and cleared, so Espanso's restore cannot put it back unmarked for the next
  trigger (#24). Markers: `org.nspasteboard.ConcealedType` or `com.agilebits.onepassword` on
  macOS; `ExcludeClipboardContentFromMonitorProcessing`, `Clipboard Viewer Ignore` or
  `CanIncludeInClipboardHistory` (0, or present but unreadable) on Windows. The probe asks only
  which formats are present, through ctypes (about 10-40 ms on macOS), and adds no dependency.
  On Linux, for browser-extension copies, or if the probe fails, the clipboard is read as
  before.
- The `general` profile, used by the local triggers `-il-` and `-ilm-`, treats the whole
  clipboard as the draft: data to rewrite, never instructions to the rewriter (#47). Pasted
  material is copied word for word, and the rewrite adds a constraint that instructions inside
  it must not be followed. It adds no facts, roles or audiences, and returns only the prompt.
  On flash-lite (a proxy; no local model measured yet), the bench's `check_general` passes
  36/36 drafts, up from 4/36 (31 of the old failures were invented roles), and the injection
  drafts are never carried over as instructions, with the guard present, in 12/12 runs.
- Invisible characters no longer reach the model or the paste (#22). `_clean()` now drops
  every code point Unicode marks as default-ignorable (zero-width space, word joiner, BOM, bidi
  marks, soft hyphen, combining grapheme joiner, Hangul fillers, variation selectors, and the
  unassigned blocks that render as nothing) and every other format character, since a run of
  them after one visible character could carry a hidden instruction. An emoji keeps one
  presentation selector and one joiner, a keycap keeps its selector, and Persian and Indic
  joiners survive. Ideographic variation selectors, Mongolian variation selectors and bidi marks
  are dropped too. The gate's normalised scan drops the same characters, so they can no longer
  split a card number.
- The gate catches the secret shapes developers paste most (#20):
  - camelCase and JSON names (`clientSecret`, `accessToken`, `dbPassword`), compound env names
    (`PGPASSWORD`), `*_KEY` names (`SECRET_KEY`, `PRIVATE_KEY`, `secret_key_base`) and
    `_authToken`;
  - PHP `'password' => …`, Go `:=`, `define('DB_PASSWORD', …)`, `environ["API_KEY"] = …` and
    values aligned with many spaces;
  - passwords of 6+ characters (`password: hunter2`) and in prose (`the password for the
    admin account is …`, Vietnamese `mật khẩu wifi là …`, `mật khẩu đăng nhập: …`);
  - AWS temporary keys (`ASIA…`), `Basic` credentials (UTF-8 too), `curl -u user:password`
    (also after a `\` line continuation), npm tokens, Azure `AccountKey=`/`SharedAccessKey=`
    and SAS `sig=`;
  - IBANs (any case, spaces or dashes; country length and mod-97 checked);
  - card numbers split by up to three spaces, tabs, slashes, dashes or minus signs, or one line
    break, or next to another number.
- A draft that is one password- or token-like word (no spaces, 8-200 characters, a digit and
  three of lower case, upper case, digit and symbol) is blocked as `bare_token`: the classic
  stale-clipboard slip. URLs, paths, emails, UUIDs, hashes, versions, dates and lower-case
  slugs and file names are not. A single word is never a prompt, so a mixed-case identifier
  with a digit is blocked too.
- An email address next to a password (`jane@example.com:…`) is a hard `credential_pair`
  finding, and `scheme://:password@host` (no user name) counts as `url_credentials` (#21).

### Changed
- `espanso deploy` and `detach` no longer read an answer from a non-terminal stdin: without
  `--yes` there they stop with exit code 3. The release workflow checks the installed wheel with
  `prompt-workflow --version`.
- `.env.example` sets only `OPENROUTER_API_KEY` and `PROMPT_PERSONA`; every other setting is
  shown commented out with its default, so a copied file no longer pins the model, endpoint or
  effort, and later default changes reach you (#28). The README now recommends keeping the file
  with your key in the config folder, outside the repository (#35).
- A `config.toml` or `secrets.toml` that cannot be parsed fails closed: `improve` pastes its
  error marker without calling a provider, and `persona` its placeholder.
- Loading settings no longer copies `.env` values into the process environment (#83). A pure,
  layered merge (`ConfigLayers`: built-in default < `.env` < real environment, then per-call
  overrides) builds `Settings`, so loading again in the same process sees an edited `.env`, child
  processes inherit nothing from it, and each setting records where its value came from,
  including a real environment variable that shadows a `.env` value. A repair mode, for
  management commands, returns problems as findings instead of raising: a line that runs into
  the next, an invalid value (the setting falls back to the next layer, which records the
  refused one), a line without `=` (by line number) and a `.env` that exists but cannot be read.
  The trigger path raises the same errors as before, word for word, and still skips the last
  two silently. Precedence, the setting allowlist, merged-line
  rejection and the no-current-directory rule are unchanged.
- The `general` profile writes in the language of the user's own request instead of
  translating (#47).
- A reply wrapped in one code fence is pasted without the fence, for every profile. Fences
  inside the reply, and an empty fenced reply, are kept (#47).
- The bench takes `--profile` and scores a profile without the golden template on its output
  contract (`check_general`): no answer instead of a rewrite, no preamble, role or carried-over
  injection, the draft's language and its pasted material. Fenced replies are counted (#47).
- The gate stops blocking ordinary drafts (#21). A confidentiality label counts only when
  written as one: upper case, alone on a line or opening one before `:` or a dash, in
  brackets, a `Classification:`/`Sensitivity:`/`Độ mật:` field, *highly/company/strictly
  confidential*, *internal only* or *do not distribute*. "Output restricted to 5 bullets",
  "confidential information" and "customer data" in prose pass. Vietnamese *mật* counts in
  upper case, alone or opening a line, or as *tài liệu/văn bản/thông tin mật*, *tối mật* or
  *tuyệt mật*, so *bảo mật* (security), *mật độ* (density) and *mật khẩu* (password) in prose
  pass. A 12-digit number counts as a CCCD only with a valid province and century code, and
  never inside an AWS ARN; a 9-digit number needs a document word next to it (CMND, CCCD, CMT,
  *chứng minh nhân dân*, *căn cước*, *hộ chiếu*, *passport*, *national ID*, *ID card*), not a
  bare "ID" or *số*.
- Carriage returns, vertical tabs, form feeds, NEL and the Unicode line and paragraph
  separators become newlines instead of disappearing.
- A draft over 50,000 characters is refused before it is cleaned, so a pasted multi-megabyte
  log no longer stalls the expansion.

- The built-in profiles are listed in name order on every OS (`default`, `general`). Linux
  listed them in the file system's order, so an unknown-profile error or `profiles list` could
  name `general` first.

### Removed
- `espanso/config/default.yml` and the installers' `--with-config` / `-WithConfig` option (#37).
  Nothing deploys to Espanso's `config/` folder any more, so your own `default.yml` (and any
  symlink to it) is never replaced; set `toggle_key` or `search_shortcut` there yourself if
  you want them. The scripts now refuse the old option with a message.

## 0.15.0 - 2026-10-04

Upgrading: `OPENROUTER_REASONING_EFFORT` and `OPENROUTER_PRO_REASONING_EFFORT` are now checked
when settings load. A value other than empty, `none`, `minimal`, `low`, `medium` or `high`
turns every trigger into an inline error naming the variable, so fix it in `.env`.

### Added
- A call that hits a rate limit (429, unless `Retry-After` asks for more than 3 s), an
  unavailable upstream (502, 503, 504, 529, or such an error inside a 200 reply) or a refused
  connection to another machine is tried once more, within the same time limit. A timeout,
  a 500 or a 4xx is never retried.

### Changed
- `PROMPT_TIMEOUT_SECONDS`, `PROMPT_PRO_TIMEOUT_SECONDS` and `--timeout` limit the whole call,
  retry included. Before, httpx's read timer restarted with every chunk, so a server sending a
  byte now and then could hold the call (and Espanso) far longer. Connecting is capped at 10 s.
- The bench repeats a call when its error is `transient` instead of matching the message text.
- Error markers say why a call failed. A non-2xx reply shows the status, a hint
  (`out of credits`, `rate limited, try again shortly`, `provider unavailable, try again`, …)
  and the provider's own reason, cut to 160 printable characters and dropped if it matches a
  sensitive pattern. `ProviderError` carries `status` and `transient`.
- The `fake_http` test fixture runs real httpx over `httpx.MockTransport`, so provider tests
  exercise httpx's own request building (header encoding, JSON body, timeouts) instead of a
  stand-in client.

### Fixed
- An error OpenRouter returns with HTTP 200 (raised after generation started) was reported as
  "response was malformed". It is now reported with its code and message, and the partial
  text is not pasted.
- An API key containing a non-ASCII character, such as a smart quote, was reported as
  "returned invalid JSON". The key is now refused by name before any request is made.
- A mistyped `OPENROUTER_REASONING_EFFORT` or `OPENROUTER_PRO_REASONING_EFFORT` was sent to
  OpenRouter and came back as a bare HTTP 400. It is now rejected when settings load.
- A reply that stopped on `error` was pasted as complete; it is now an inline marker. A reply
  stopped by `content_filter` or `refusal` gets a visible "stopped early" note, or says the
  request was declined when it has no text.
- `pytest -m live` runs again. Since 0.11.0 it failed with a `TypeError` before any network
  call. An offline twin now runs the same command and checks on a canned reply in every
  default test run.
- A rewrite that mentions `<think>` or `</think>` after its start, such as a prompt about
  reasoning tags, is no longer cut short or pasted with the tags removed. Only reasoning at the
  start of the reply (a closed or unclosed `<think>` block, or a stray `</think>`) is stripped.

## 0.14.0 - 2026-10-04

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

## 0.13.0 - 2026-10-04

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

## 0.12.0 - 2026-10-03

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

## 0.11.0 - 2026-09-30

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

## 0.10.0 - 2026-09-29

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

## 0.9.0 - 2026-09-29

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

## 0.8.0 - 2026-09-29

### Changed
- Bumped `typer` to 0.27.2.

### Fixed
- The `prompts-llm.yml` header comment no longer contains the CLI placeholder, so the
  installers stop writing your absolute CLI path into it.

## 0.7.0 - 2026-09-29

First public release.

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
