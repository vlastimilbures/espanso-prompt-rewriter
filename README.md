# Espanso Prompt Rewriter

**Type a trigger, get a well-structured LLM prompt.** Copy a rough draft, type `-i-`
anywhere, and [Espanso](https://espanso.org/) replaces it with a precise, sectioned prompt
rewritten by a local or cloud model.

[![tests](https://github.com/vlastimilbures/espanso-prompt-rewriter/actions/workflows/test.yml/badge.svg)](https://github.com/vlastimilbures/espanso-prompt-rewriter/actions/workflows/test.yml)
[![secret-scan](https://github.com/vlastimilbures/espanso-prompt-rewriter/actions/workflows/secret-scan.yml/badge.svg)](https://github.com/vlastimilbures/espanso-prompt-rewriter/actions/workflows/secret-scan.yml)
[![release](https://img.shields.io/github/v/release/vlastimilbures/espanso-prompt-rewriter)](https://github.com/vlastimilbures/espanso-prompt-rewriter/releases)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
<br>
[![python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-blue?logo=python&logoColor=white)](pyproject.toml)
[![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)](#requirements)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![mypy: checked](https://img.shields.io/badge/mypy-checked-2a6db2)](https://mypy-lang.org/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

One small Python CLI sits behind every trigger. It reads your clipboard, checks the draft for
sensitive content before anything leaves your machine, sends it with the rewrite instructions
(including your persona, if you set one, which is not checked) to the model, and pastes the
result back — or a readable `[prompt-workflow: …]` message if something went wrong. See
[What is sent, and to whom](#what-is-sent-and-to-whom). It works the same way on macOS and Windows, with
[OpenRouter](https://openrouter.ai), [Anthropic](https://www.anthropic.com),
[Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai).

<details>
<summary><b>Example:</b> a one-line draft and what <code>-i-</code> turns it into</summary>

Draft on the clipboard:

```text
write a short board update on why customer churn went up last quarter, use the attached churn dashboard export
```

Pasted in its place (real output of v0.7.0 on its default model, `google/gemini-3.5-flash-lite`,
no persona configured; today's prompt differs in detail, for example it asks the step that
writes the update, step 4 here, to name the update's sections):

```text
<CONTEXT>
I need to write a short board update explaining the increase in customer churn during the previous quarter, based on the provided churn dashboard export.
</CONTEXT>

<GOAL>
A clear, concise board update explaining the root causes of the rise in customer churn last quarter.
</GOAL>

<INSTRUCTIONS>
1/ Plan the task thoroughly, list any assumptions and open questions, and validate the plan with me before executing.
2/ Load and validate all inputs. If anything is missing, ambiguous, or contradictory, ask me up to 5 targeted questions before drafting.
3/ Analyze the churn dashboard export to identify key metrics, trends, and primary drivers behind the churn increase.
4/ Synthesize the findings into a concise narrative structure suitable for a board audience, highlighting key data points and context.
5/ Spin up an independent agent with SaaS churn and retention domain knowledge and perform a critical review, check for errors, and ensure the output is complete and accurate, review formatting and clarity, and ensure the output is well structured and easy to read; summarize all issues and improvement points, validate them with me before implementing any changes.
6/ Flag material judgment calls or trade-offs and let me decide.
</INSTRUCTIONS>

<CONSTRAINTS>
- Keep the update short and scannable for board members.
- Maintain an objective, professional tone.
- Base all claims strictly on the provided churn dashboard export.
- Out of scope: strategic recommendations or mitigation plans unless directly evidenced in the data.
</CONSTRAINTS>

<INPUTS>
Churn dashboard export [REVIEW: attach or paste the dashboard export data]
</INPUTS>

<OUTPUTS>
structured .md, well formatted with clear headings/subheadings
</OUTPUTS>
```

The two choices are made independently. Analysing an export and then writing it up is
multi-step work, so step 1 is "plan first"; the board audience selected the independent-review
step. A quick note to yourself would get "execute now" and a self-review checklist instead.

</details>

## Contents

- ✨ [Features](#features)
- ⚙️ [How it works](#how-it-works)
- 📋 [Requirements](#requirements)
- 🚀 [Install](#install)
- 🖥️ [First run](#first-run)
- 🔄 [Updating](#updating)
- 🗑️ [Uninstall](#uninstall)
- ⌨️ [Usage](#usage)
- 🔧 [Configuration](#configuration)
- 🧩 [Profiles and persona](#profiles-and-persona)
- 🔒 [Privacy and data protection](#privacy-and-data-protection)
- 📊 [Model benchmark](#model-benchmark)
- 🩺 [Troubleshooting](#troubleshooting)
- 🛠️ [Development](#development)
- 🤝 [Contributing, security, license](#contributing-security-license)

## Features

- 🧱 **Golden-template rewrite.** The `default` profile turns any draft into
  `CONTEXT / GOAL / INSTRUCTIONS / CONSTRAINTS / INPUTS / OUTPUTS`, and picks the planning and
  review steps from the task's complexity and audience.
- 🧠 **Two tiers.** `-i-` answers in about 2 seconds; `-ip-` hands hard, multi-part
  drafts to a reasoning model for a more rigorous rewrite in about 6 seconds (median; 95 % of
  calls within about 9 seconds, see the [model benchmark](#model-benchmark)).
- 🔌 **Four providers, one interface.** OpenRouter (default), Anthropic, Ollama and LM Studio.
  Each trigger names its provider; `PROMPT_LOCAL_ONLY=true` refuses every cloud call.
- 🛡️ **Data-protection gate.** Before the draft can leave your machine it is scanned for
  payment cards, national IDs, emails, API keys, tokens, passwords, private keys,
  confidentiality labels and your own patterns; matches are blocked unless you explicitly
  override.
- 🙋 **Your persona, once.** Set `PROMPT_PERSONA` and every rewrite (and the `-p-` snippet) opens
  with your role.
- 🧯 **Never a blank expansion.** Errors arrive inline as `[prompt-workflow: …]`, because Espanso
  cannot show stderr or exit codes.
- 🧹 **Clean output.** A leading `<think>…</think>` reasoning block, control characters and
  invisible Unicode never reach the app you are typing in.
- 📊 **Benchmarked model choice and prompt.** A bundled benchmark scores models on template
  fidelity, injection and language edge cases, latency and real cost.

## How it works

```mermaid
flowchart LR
    A["You type -i-"] --> B["Espanso runs<br/>prompt-workflow improve"]
    B --> C["Load settings,<br/>read clipboard"]
    C --> D{"Leaves this<br/>machine?"}
    D -- yes --> E{"Sensitive content?"}
    E -- "yes, no override" --> X["Pastes a<br/>Blocked cloud call message"]
    E -- no --> F["OpenRouter / Anthropic /<br/>remote or cloud model"]
    D -- no --> G["Ollama / LM Studio<br/>on localhost"]
    F --> H["Strip think blocks and unsafe<br/>characters, validate output"]
    G --> H
    H --> I["Espanso pastes the<br/>rewritten prompt"]
```

Espanso starts the CLI as a GUI subprocess without your shell's `PATH` or environment. So
`prompt-workflow espanso deploy` writes the CLI's absolute path into the match files, and the
CLI reads its settings from files rather than your shell: a `.env`, or the saved `config.toml`
and `secrets.toml` (see [Configuration](#configuration)).

## Requirements

- macOS or Windows with [Espanso](https://espanso.org/install/) installed and running. Espanso
  also runs on Linux, where the CLI is tested but the triggers are not used day to day. There
  the clipboard needs `xclip` or `xsel` (X11) or `wl-clipboard` (Wayland); without one, a
  trigger pastes `[prompt-workflow: Clipboard unavailable: Pyperclip could not find a copy/paste
  mechanism …]` instead of a rewrite.
- Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/)
- For the default `-i-` trigger: an [OpenRouter API key](https://openrouter.ai/keys).
  For fully local use instead: [Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai).

## Install

Each [GitHub Release](https://github.com/vlastimilbures/espanso-prompt-rewriter/releases) from
0.16 on carries the wheel and `constraints.txt`, the exact dependency versions from `uv.lock`
(a plain `uv tool install` would resolve the version ranges afresh). Install a release with:

```bash
uv tool install \
  https://github.com/vlastimilbures/espanso-prompt-rewriter/releases/download/v<version>/espanso_prompt_rewriter-<version>-py3-none-any.whl \
  -c https://github.com/vlastimilbures/espanso-prompt-rewriter/releases/download/v<version>/constraints.txt
```

To check the files first, download them (`gh release download v<version> -R
vlastimilbures/espanso-prompt-rewriter`), run `gh attestation verify <file> -R
vlastimilbures/espanso-prompt-rewriter` on each, and install the local wheel with
`-c constraints.txt`. This installs the CLI only; [First run](#first-run) sets it up and writes
the Espanso match files. Homebrew, Scoop and WinGet packages are planned (#95). To run from a
checkout instead, see [Development](#development).

## First run

Run `prompt-workflow` in a terminal (or `prompt-workflow ui`) for a full-screen interface with
six tabs: Home (the `doctor` summary), Providers & keys, Profiles, Triggers, History and
Diagnostics. It does what the [management commands](#management-commands) do, through the same
code. Keys are shown only as set or not set, removing a key, deploying, detaching, migrating or
deleting history asks first, and no provider is called except by the Test call button, which
runs `improve` against a stub on `127.0.0.1` with a placeholder key.

![The Home tab of the prompt-workflow interface](docs/interface.svg)

`1`-`6` switch tabs, `r` reloads, `t` switches to a high-contrast theme and `q` quits;
`NO_COLOR` turns colour off. Scripts and screen readers can use the headless commands instead.
Without a terminal, a bare `prompt-workflow` prints the help.

Or answer a few questions instead: `prompt-workflow setup` asks for the provider and default
profile (saved in `config.toml`), the API key (hidden input, saved in `secrets.toml`), shows the
match files it would deploy and writes them only if you agree, and ends with a smoke test that
runs `improve` against a stub on `127.0.0.1` (never a paid call). In a script, pass the key on
stdin and nothing is asked:

```bash
prompt-workflow setup --non-interactive --api-key-stdin --deploy < key.txt
```

Without `--deploy` the deploy stays a preview. Now copy a rough draft, type `-i-` in any text
field, and wait a couple of seconds without typing or switching windows: Espanso pastes the
rewrite wherever the focus is when the answer arrives.

### Managing the deployed match files

`prompt-workflow espanso` owns the match files it writes into Espanso's `match/` folder (found
with `espanso path config`, else Espanso's default folder) and keeps a manifest of them,
`espanso-manifest.json`, in `~/.local/share/prompt-workflow/` (`$XDG_DATA_HOME`;
`%LOCALAPPDATA%\prompt-workflow\` on Windows). Each deployed file starts with
`# prompt-workflow <version> (managed; edit at your own risk)`.

| Command | What it does |
|---------|--------------|
| `prompt-workflow espanso status [--diff]` | Each file: `missing`, `in sync`, `stale` (an older deploy of ours, such as after an upgrade), `modified` (you edited it) or `foreign` (not ours) |
| `prompt-workflow espanso deploy` | Shows the plan and a diff, asks, then writes and restarts Espanso. `--yes` skips the question, `--dry-run` stops after the diff; a second run with nothing to change does nothing |
| `prompt-workflow espanso detach` | Removes the matches that call the CLI and keeps `-prompt-`/`-risk-` as static snippets (`--keep-static`, the default); `--remove-all` removes every file we deployed |

A file you edited is never overwritten silently: `deploy` asks whether to keep yours, take ours
(yours is saved as `<file>.bak-<timestamp>`; only the last 2 of these backups are kept) or write
ours side by side as `<file>.prompt-workflow-new`, which Espanso does not load. With `--yes` it
keeps yours unless you pass `--on-conflict ours|side`, and ends with a `WARNING` naming each file
it kept. A file an older release's installer wrote, unedited, is recognised as ours and updated. `detach` likewise removes only files whose
content is still what we wrote, and reports any you edited. The launcher written into the
matches is the install channel's stable entry point (uv's tool bin, Homebrew's `bin/`, Scoop's
shim), never a versioned path an upgrade would remove; `--launcher PATH` overrides it. A manifest
entry for a file that no longer exists (its folder was deleted, or Espanso now uses another
config folder) is forgotten by the next `deploy`; until then `doctor` mentions it but does not
judge its launcher.

## Updating

**Coming from a checkout** (the install scripts, an editable install)? The wheel never reads the
checkout's `.env` or its profiles, but it can copy them. Leave the checkout and its `.env` where
they are, so the old triggers keep working until the match files are deployed again:

1. Install the wheel as shown below, then run `prompt-workflow setup` (or open the interface,
   `prompt-workflow`, which shows a "Previous install" checklist). It finds the checkout through
   the deployed match files, the deploy manifest or uv's receipt, and offers to copy the
   settings and key (only those still at their default here) and the profiles you edited, then
   to deploy the match files. If it finds nothing (a `--force` install overwrites uv's receipt,
   and a uv launcher does not point into the checkout), name the checkout:
   `prompt-workflow setup --migrate-from PATH`. Without setup: `prompt-workflow config migrate
   --from PATH` and `prompt-workflow profiles migrate --checkout PATH`, then
   `prompt-workflow espanso deploy`.
2. Once the match files no longer run the checkout's CLI, `prompt-workflow config retire --from
   PATH` moves the old `.env` into the backup. `prompt-workflow config rollback` undoes the copy
   and the retire.
3. If `doctor` says `prompt-workflow` on `PATH` is the checkout's, run `deactivate` (its `.venv`
   is active) or take it off `PATH`.

Or keep a `.env`: move it to the config folder (`~/.config/prompt-workflow/.env`,
`%APPDATA%\prompt-workflow\.env` on Windows), where the wheel reads it too, or point
`PROMPT_WORKFLOW_ENV` at it (set for GUI apps, since Espanso does not inherit your shell).

Install the new release's wheel with its own `constraints.txt`. `--force` makes uv install over
the tool already there (the install scripts pass it too). Then check the result:

```bash
uv tool install --force \
  https://github.com/vlastimilbures/espanso-prompt-rewriter/releases/download/v<version>/espanso_prompt_rewriter-<version>-py3-none-any.whl \
  -c https://github.com/vlastimilbures/espanso-prompt-rewriter/releases/download/v<version>/constraints.txt
prompt-workflow doctor
```

Upgrading the CLI does not touch the match files Espanso holds. If `doctor` reports one as
`stale` (for example `prompts-template.yml: stale`), run `prompt-workflow espanso deploy` to
bring it up to date.

## Uninstall

Detach before you uninstall: once the CLI is gone, every trigger that calls it fails with
Espanso's rendering error.

1. `prompt-workflow espanso detach` removes the matches that call the CLI
   (`prompts-llm.yml`, `prompts-template.yml`) and keeps `-prompt-` and `-risk-` as static
   snippets (`--keep-static`, the default); `--remove-all` removes every file it deployed. The
   `.bak-…` backups are never deleted.
2. Check with `prompt-workflow espanso status` that neither `prompts-llm.yml` nor
   `prompts-template.yml` is left (both should be `missing`). Detach removes only files on
   record and unedited, so:
   - if it said `Nothing to do` (the files came from an install script before 0.16, so there is
     no record), run `prompt-workflow espanso deploy` first: it adopts unedited files any
     release wrote, after which `detach` removes them;
   - a file you edited (`modified` or `foreign`) is kept: delete it from Espanso's `match/`
     folder by hand, or remove its CLI-calling matches.

   Do not uninstall while one of these files is left.
3. Optionally, and only if you want them gone: `prompt-workflow history reset` deletes the
   usage history, and `prompt-workflow secrets remove OPENROUTER_API_KEY` (or
   `ANTHROPIC_API_KEY`) deletes a saved key. The config folder (`config.toml`, `profiles/`,
   `backups/`) and the data folder stay until you delete them.
4. `uv tool uninstall espanso-prompt-rewriter`.

If the CLI is already broken or gone, install it again (see [Install](#install)), then detach.
Or clean up by hand: `espanso-manifest.json` in the data folder lists each deployed file as
`target` and its `backups`; delete the targets in Espanso's `match/` folder, restore a backup
if you want your earlier version back, and restart Espanso.

## Usage

### Triggers

| Trigger           | What it does                                                 | Provider   | Profile   |
|-------------------|--------------------------------------------------------------|------------|-----------|
| `-i-`             | Rewrites the clipboard into the golden template              | OpenRouter | `default` |
| `-ip-`            | Same rewrite on the pro tier (reasoning model, slower)       | OpenRouter | `default` |
| `-if-`            | Same rewrite; a popup picks model, effort, tokens, timeout   | OpenRouter | `default` |
| `-iok-`           | `-i-`, sent once despite a flagged label, ID, email or IBAN  | OpenRouter | `default` |
| `-il-`            | General prompt improvement, fully local                      | Ollama     | `general` |
| `-ilm-`           | General prompt improvement, fully local                      | LM Studio  | `general` |
| `-ic-`            | General improvement via Claude (commented out by default)    | Anthropic  | `general` |
| `-p-`             | Empty golden template to fill in, opening with your persona  | —          | —         |
| `-prompt-`        | Form: role, objective, context, constraints, output          | —          | —         |
| `-risk-`          | Enterprise-risk analysis prompt scaffold                     | —          | —         |

The Profile column shows the defaults. `-i-` uses `PROMPT_PROFILE`. `-ip-` and `-if-` use
`PROMPT_PRO_PROFILE` when it is set (it is empty by default, so they use `PROMPT_PROFILE` too),
except that a model other than `OPENROUTER_PRO_MODEL` picked in `-if-` always gets
`PROMPT_PROFILE`. The local triggers always use `general`.

Each match has a label starting with `prompt-workflow:`, which Espanso's search bar
(Alt+Space / Option+Space by default) shows instead of the `{{output}}` placeholder.

Triggers expand only at the start of a word: after a space, tab, newline, punctuation
(`. , ? ! : ; ' "`) or a bracket, or as the first thing typed after clicking into a field. Text
such as `a[n-i-1]` or `only-if-cached` does not fire them, but `s[-i-1]` or `x = -i-1` still
does. If a trigger follows anything else (a letter, digit, `-`, `=`, `/` …), type a space first.

The improve triggers (`-i-`, `-ip-`, `-if-`, `-iok-`, `-il-`, `-ilm-`) send your current clipboard
as-is, whatever it holds, so copy the draft first. On macOS and Windows an item a password
manager marked as concealed is refused and cleared from the clipboard
(`[prompt-workflow: The clipboard held a password-manager item …]`) when the check can tell. Cloud triggers pass through the
[data-protection gate](#privacy-and-data-protection) first. To rewrite text in place, select
it, copy it (Cmd+C / Ctrl+C) and type `-i-`: as in any editor, the first character you type
replaces the selection, and Espanso then replaces the trigger with the rewrite.

While a rewrite runs (about 2 s for `-i-`, about 6 s median for `-ip-`, at most the call's time
limit, see `PROMPT_TIMEOUT_SECONDS`), do not type or switch windows: Espanso pastes the result
wherever the focus is when the answer arrives, and other triggers do not expand until it
finishes. Writing the [usage history](#usage-history) adds up to 0.25 s after the output (1 s
once, for the write that creates the file).

In a checkout ([Development](#development)), you enable `-ic-` by uncommenting it in
[`espanso/match/prompts-llm.yml`](espanso/match/prompts-llm.yml) and running
`prompt-workflow espanso deploy` (or the installer) again.

`-if-` opens an Espanso form with four dropdowns before the rewrite runs: the model
(each entry is `model@endpoint`, the OpenRouter slug plus its endpoint pin; `@auto` leaves
routing to OpenRouter), reasoning effort, max output tokens and timeout (at most 120 s).
Every list starts with, and defaults to, `default`, which keeps the pro-tier setting
(`OPENROUTER_PRO_*`; for the model, `OPENROUTER_PRO_MODEL` with its
`OPENROUTER_PRO_PROVIDER` pin). In a checkout, edit the lists in
[`espanso/match/prompts-llm.yml`](espanso/match/prompts-llm.yml) and deploy again; the
tests reject any value the CLI would not accept. Two things to know:

- Many reasoning models count thinking tokens against the max-tokens cap, so pair `high` effort
  with `8000` or more, or the rewrite can come back cut short.
- Espanso waits for the command, and other triggers do not expand until it finishes. A `high`
  effort rewrite on `openai/gpt-6-luna` took about 23 s.

With a release wheel there is no checkout to edit: put your own variants (`-ic-`, an `-if-`
with other lists) in a file of your own in Espanso's `match/` folder, such as
`my-prompts.yml`. Copy the match from the deployed `prompts-llm.yml`, which already holds the
CLI's absolute path, give it a trigger no other match uses, and keep its `--trigger-id`
only if its runs should count as that trigger in the usage history. `prompt-workflow espanso
deploy`, `status` and `detach` handle only the files they deployed (`prompts-core.yml`,
`prompts-llm.yml`, `prompts-template.yml`), so they never list or change yours. Editing a
deployed file instead marks it `modified`: deploy then keeps your copy (or replaces it, with a
backup, if you choose ours), so it no longer receives updates.

### CLI

The same CLI works on its own, which is handy for trying profiles and models:

```bash
echo "summarise the Q3 incident log for the ops team" | prompt-workflow improve --source stdin
prompt-workflow improve --provider ollama --profile general --source clipboard
prompt-workflow improve --model openai/gpt-4.1-nano --source argument --text "your draft"
prompt-workflow improve --tier pro --source clipboard   # the reasoning model behind -ip-
prompt-workflow persona          # prints PROMPT_PERSONA (used by -p-)
```

| Option       | Default                     | Meaning                                         |
|--------------|-----------------------------|-------------------------------------------------|
| `--provider` | `PROMPT_PROVIDER`           | `ollama`, `lmstudio`, `openrouter`, `anthropic` |
| `--profile`  | `PROMPT_PROFILE` (`PROMPT_PRO_PROFILE`, if set, with `--tier pro` on `OPENROUTER_PRO_MODEL`) | `default`, `general`, or one of [your own profiles](#profiles-and-persona) |
| `--model`    | provider's configured model | Override the model of whichever provider runs; `model@endpoint` also pins the OpenRouter endpoint (`@auto` unpins) |
| `--tier`     | `standard`                  | `pro` uses the `OPENROUTER_PRO_*` settings      |
| `--effort`   | tier's setting              | `none`, `minimal`, `low`, `medium`, `high`      |
| `--max-tokens` | tier's setting            | Output cap for this call                        |
| `--timeout`  | tier's setting              | Time limit in seconds for this call, retry included |
| `--source`   | `clipboard`                 | `clipboard`, `stdin` or `argument`              |
| `--text`     | —                           | The draft, with `--source argument`             |
| `--copy`     | off                         | Also copy the result to the clipboard           |

Output is UTF-8 with no trailing newline, and every failure is printed as `[prompt-workflow: …]`
with exit code 0, so Espanso always has something to paste. Drafts over 50,000 characters are
refused (an accidental copy of a log or document should not go to the cloud). If the model stops
at its output limit, the partial rewrite is pasted with
`[prompt-workflow: the reply hit the model's output limit and is cut off]` at the end.

#### Management commands

| Command | What it does |
|---------|--------------|
| `prompt-workflow --version` | Prints the installed version |
| `prompt-workflow` / `prompt-workflow ui` | Opens the [full-screen interface](#first-run) when stdin and stdout are a terminal. Otherwise a bare `prompt-workflow` prints the help and exits 2, and `ui` exits 3 |
| `prompt-workflow setup` | First run: provider and default profile (saved in `config.toml`), the API key (hidden prompt), a deploy preview it applies only if you agree, and a smoke test that runs `improve` against a stub on `127.0.0.1` (never a paid call, never your real key). If your settings are in a `.env`, it offers to migrate them and changes nothing unless you say yes; the same goes for an earlier checkout install it finds (or the one `--migrate-from PATH` names): it offers to copy its settings and edited profiles, and to retire its `.env` once the match files no longer run it. `--non-interactive` asks nothing (key with `--api-key-stdin`; the deploy stays a preview unless `--deploy`) |
| `prompt-workflow config show [--raw]` | Every setting, its value and where it comes from (default, a file or the environment), and which lower files it overrides. Keys and the persona are shown only as set or not set |
| `prompt-workflow config get NAME` / `set NAME VALUE` / `unset NAME` | Read one setting; save it in `config.toml` after checking it as the CLI reads it; remove it so the default applies. A key is refused here |
| `prompt-workflow config validate` | Checks the settings as `improve` reads them, and the profiles they name |
| `prompt-workflow config migrate` / `rollback` | Moves the `.env` in use to `config.toml` and its keys to the secret store, with a backup, or undoes that. Shows a preview first and applies only once you confirm it; in a script, pass `--yes --preview-token <token>` from that preview. `--from PATH` also copies an earlier checkout's `.env` into every setting still at its default; that `.env` stays in place, so the old triggers keep working until you deploy again |
| `prompt-workflow config retire --from PATH` | Moves that checkout's `.env` into the backup once its settings were copied. Refused while a match file still runs the checkout's CLI (`prompt-workflow espanso deploy` first); same preview and `--yes --preview-token` as migrate, and `rollback` puts it back |
| `prompt-workflow secrets set NAME [--stdin]` / `status` / `remove NAME` | Saves a key from a hidden prompt or stdin (never an argument); shows whether each key is set and where from, never the value; deletes one |
| `prompt-workflow profiles list` / `migrate` | Lists built-in and your own profiles and their state; copies profiles you added or edited in a checkout to your profile folder (copies only, never overwrites) |
| `prompt-workflow espanso deploy [--dry-run]` / `status` / `detach` | See [Managing the deployed match files](#managing-the-deployed-match-files) |
| `prompt-workflow stats [--by trigger\|provider\|model\|day] [--json]` | Calls, latency, tokens and costs from the [usage history](#usage-history) |
| `prompt-workflow history export [--format json\|csv] [-o FILE]` / `prune [--older-than DAYS]` / `reset` | Exports (metadata only), deletes old records (asking first when the age is shorter than `PROMPT_HISTORY_RETENTION_DAYS`), or deletes them all |
| `prompt-workflow doctor [--json]` | Version, CLI path and install channel, config validity, keys set or not, Espanso found and running, each deployed match file (`in sync`, `stale`, `modified`, `missing`), launcher drift, history health, SQLite version, and a clipboard read test that reports only the length. Safe to paste into an issue: it never shows a key, your persona or clipboard text |

`stats` reports local observations on this device, not provider billing: check your provider's
dashboard for what you were charged. Read it with these caveats:

- Costs are summed per unit as the provider reported them. OpenRouter reports credits, which are
  never converted to USD; a BYOK call's upstream cost (USD) is kept in the history and its
  export but not added to the totals. Estimates from `prices.toml` are shown apart.
- An unknown cost is counted as unknown (`N attempt(s) with an unknown cost`), never as 0.
- A call counts once the CLI rendered its output, which does not mean it was pasted.
- Triggers that name a provider (`-i-`, `-ip-`, `-if-`, `-iok-`, `-il-`, `-ilm-`) ignore
  `PROMPT_PROVIDER`, so changing it does not move their calls to another provider.

Unlike `improve` and `persona`, these commands print errors to stderr and use ordinary exit
codes. None of them asks a question without a terminal (pass `--yes`, `--stdin` or
`--non-interactive` instead), each runs on a broken `.env` or `config.toml` and reports what is
wrong, and colour is off when `NO_COLOR` is set or output is not a terminal. An option or a
mistyped argument that looks like a key is refused and never repeated in an error.

| Exit code | Meaning |
|-----------|---------|
| 0 | Done |
| 1 | Failed or refused, or you declined a confirmation |
| 2 | Usage error: an unknown option, setting or value |
| 3 | An answer was needed but stdin is not a terminal, or `ui` ran without a terminal |
| 4 | `doctor` or `config validate` found a problem |

## Configuration

All settings are named like environment variables and usually set in a `.env` (see
[`.env.example`](.env.example): it sets only the key and the persona, and shows every other
setting commented out with its default, so later default changes still reach you). Real
environment variables take precedence over any file.

The CLI reads its settings from the first of these that exists:

1. the `.env` named by `PROMPT_WORKFLOW_ENV`, if set. That file alone is used, as before.
2. `config.toml` in the config folder: `~/.config/prompt-workflow/`, or
   `$XDG_CONFIG_HOME/prompt-workflow/` when `XDG_CONFIG_HOME` is set (macOS and Linux);
   `%APPDATA%\prompt-workflow\` on Windows, which ignores `XDG_CONFIG_HOME`. Once it exists, it is the saved configuration and no `.env` is read, so an old
   `.env` can never override a saved value.
3. the `.env` in the repository the CLI was installed from, for an editable install only
   (the [Development](#development) scripts); a wheel install never reads a checkout;
4. the `.env` in the config folder.

Unless `PROMPT_WORKFLOW_ENV` is set, API keys are also read from `secrets.toml` in the config
folder, which wins over a key in a `.env`. Order of precedence: built-in default <
`config.toml` or `.env` < `secrets.toml` < real environment variable < a trigger's own options.

`config.toml` holds plain TOML with the same names (`OPENROUTER_MODEL = "…"`,
`OLLAMA_THINK = true`) and a `config_version`; it never holds a key. `secrets.toml` holds only
`OPENROUTER_API_KEY` and `ANTHROPIC_API_KEY` and is private to your user (mode 600 on
macOS/Linux, an access list for your account alone on Windows). An existing `.env` can be
migrated to both after a preview and your confirmation: values equal to their default are
left out, the `.env` is moved into `backups/` in the config folder rather than deleted, and a
rollback restores it exactly: `prompt-workflow config migrate` and `prompt-workflow config
rollback` (both show a preview first). With `PROMPT_WORKFLOW_ENV` set (legacy mode), that `.env` stays
the only settings file: nothing is migrated, and `config set` and `secrets` refuse to write, so
edit the file itself. A keychain is
not supported yet. [Your own profiles](#profiles-and-persona) live in `profiles/` in the same
config folder.

It never reads a settings file from the current directory, so running the CLI inside some
other project cannot change its endpoint or switch off the gate. Only the settings in the table
below are read; anything else (such as `HTTPS_PROXY` or `SSL_CERT_FILE`) is ignored.

Cloud calls use the system proxy (macOS System Settings, Windows Internet Options) or the
`HTTP_PROXY`, `HTTPS_PROXY` and `ALL_PROXY` environment variables. Since Espanso starts the CLI
without your shell's environment, set such variables for GUI apps (`launchctl setenv` on macOS,
user environment variables on Windows) rather than in a shell profile. A base URL on this
machine (`localhost`, `127.0.0.0/8`, `::1`) is always reached directly, never through a proxy.

Values may be quoted, and an unquoted value may be followed by a ` # comment`. Quote a value
that itself contains ` #`: for `PROMPT_EXTRA_PATTERNS` and `PROMPT_PERSONA`, where `#` may be
part of the text, `config validate`, `doctor` and `config migrate` report a value cut at ` #`
(migrate refuses until it is quoted), and a cut `PROMPT_EXTRA_PATTERNS` also stops every
trigger with a marker, so the gate never runs on part of your patterns. A `.env` must be UTF-8 (a byte order mark is fine); any other encoding is
an error. Booleans are
`true` or `false`, timeouts and token caps are numbers above 0 (temperature may be 0); anything
else is reported inline rather than silently ignored. An error repeats the bad value only when it is
short, does not look like a key and matches none of your `PROMPT_EXTRA_PATTERNS`.

| Variable                     | Default                        | Purpose                                                   |
|------------------------------|--------------------------------|-----------------------------------------------------------|
| `PROMPT_PROVIDER`            | `openrouter`                   | Provider when `--provider` is not given (the bare CLI; every trigger passes its own) |
| `PROMPT_PROFILE`             | `default`                      | Profile when `--profile` is not given (`-i-`, and `-if-` on a non-pro model) |
| `PROMPT_PERSONA`             | *(empty)*                      | Your first-person role, see [persona](#profiles-and-persona) |
| `PROMPT_TIMEOUT_SECONDS`     | `30`                           | Time limit for one call, retry included                   |
| `PROMPT_TEMPERATURE`         | `0.2`                          | Kept low so fixed template wording survives               |
| `OPENROUTER_API_KEY`         | —                              | Required for OpenRouter                                   |
| `OPENROUTER_MODEL`           | `google/gemini-3.5-flash-lite` | See [benchmark](#model-benchmark)                         |
| `OPENROUTER_PROVIDER`        | `google-ai-studio/flex`        | Pin a serving endpoint; empty = OpenRouter's own routing  |
| `OPENROUTER_REASONING_EFFORT` | `minimal`                     | `none`…`high`; empty omits it (models without the control) |
| `OPENROUTER_ALLOW_FALLBACKS` | `true`                         | `false` makes the pin binding                             |
| `OPENROUTER_MAX_TOKENS`      | `2400`                         | Output cap (cost control)                                 |
| `OPENROUTER_BASE_URL`        | `https://openrouter.ai/api/v1` |                                                           |
| `OPENROUTER_PRO_MODEL`       | `openai/gpt-6-luna`            | Model for `--tier pro` / `-ip-`                   |
| `OPENROUTER_PRO_PROVIDER`    | `openai`                       | Endpoint pin for the pro tier                             |
| `OPENROUTER_PRO_REASONING_EFFORT` | `low`                     | Reasoning effort for the pro tier                         |
| `PROMPT_PRO_TIMEOUT_SECONDS` | `60`                           | Time limit for one pro-tier call, retry included          |
| `PROMPT_PRO_PROFILE`         | (empty)                        | Profile for the pro tier (`-ip-`, `-if-`) when it runs `OPENROUTER_PRO_MODEL`; empty = `PROMPT_PROFILE` |
| `PROMPT_PROFILE_OVERRIDES`   | *(empty)*                      | Comma-separated built-in profiles (`default`, `general`) your own same-named file replaces, see [profiles](#profiles-and-persona) |
| `ANTHROPIC_API_KEY`          | —                              | Required for Anthropic                                    |
| `ANTHROPIC_MODEL`            | `claude-sonnet-5`              |                                                           |
| `ANTHROPIC_MAX_TOKENS`       | `2400`                         |                                                           |
| `ANTHROPIC_BASE_URL`         | `https://api.anthropic.com`    |                                                           |
| `OLLAMA_BASE_URL`            | `http://localhost:11434`       |                                                           |
| `OLLAMA_MODEL`               | `qwen3:8b`                     |                                                           |
| `OLLAMA_THINK`               | `false`                        | Keep reasoning off for thinking models                    |
| `LMSTUDIO_BASE_URL`          | `http://localhost:1234/v1`     |                                                           |
| `LMSTUDIO_MODEL`             | `local-model`                  |                                                           |
| `ALLOW_CLOUD_OVERRIDE`       | `false`                        | `true` lets flagged drafts reach cloud providers          |
| `PROMPT_LOCAL_ONLY`          | `false`                        | `true` refuses every provider that can send the draft off this machine |
| `PROMPT_GATE_LOCAL`          | `false`                        | `true` runs the gate for Ollama / LM Studio on `localhost` too (a local relay to a cloud API) |
| `PROMPT_EXTRA_PATTERNS`      | *(empty)*                      | Your own `;`-separated regexes for the gate               |
| `PROMPT_HISTORY`             | `true`                         | Keep a local [usage history](#usage-history) (metadata only); `false` keeps none |
| `PROMPT_HISTORY_RETENTION_DAYS` | `365`                       | Days a usage-history record is kept before pruning (1 to 36500) |
| `PROMPT_WORKFLOW_ENV`        | *(unset)*                      | Path of the `.env` to load, alone: no `config.toml` or `secrets.toml` (real environment only) |

## Profiles and persona

A profile is a system prompt in [`src/prompt_workflow/prompts/`](src/prompt_workflow/prompts/):

- **`default`** rewrites the draft into the golden template (the same one the static `-p-`
  snippet gives you). Step 1 is "plan first" for multi-step or ambiguous work, otherwise
  "execute, but state assumptions". The review step is decided by who reads the result: a
  self-review checklist when only the user, a colleague, their manager or their team will, and
  an independent reviewer for anyone else (an executive, a board or committee, a regulator,
  anyone outside the organisation, or published text), however short. `CONSTRAINTS` lists the
  rules the result must respect (length, tone, deadline, format, standards, data limits), with
  an "Out of scope:" line when the draft implies one.
  The whole draft is treated as material to rewrite, never as instructions: a question becomes a
  prompt that asks for the answer, pasted emails or notes up to about 60 lines are copied into
  `INPUTS` in full (longer material is described there and flagged `[REVIEW: …]`), and text such
  as "ignore previous instructions" is dropped. The rewrite is always in English; a draft in
  another language gets a `- Language: …` constraint so the result comes back in that language.
  `OUTPUTS` is the `.md` line for documents and a matching format (plain-text email, code block,
  slides…) for everything else. Specifics from the draft (numbers, names, dates, deliverables)
  are kept; anything missing is flagged `[REVIEW: …]` rather than invented. The prompt itself is
  organised in lowercase XML sections (`<section_rules>`, `<step>`, `<decision_rule>`,
  `<example>`…), which keeps its own scaffolding visibly apart from the uppercase sections the
  model must write.
  Both tiers send it. `default-pro`, the pro tier's variant in 0.12.0 and 0.13.0, is now an alias
  of `default`.
- **`general`** is a short (~200 words) rewrite for the local triggers, small enough for an 8B
  model: it treats the whole clipboard as the draft (data, never instructions to the rewriter),
  copies pasted material word for word with a constraint that instructions inside it must not
  be followed, adds no facts, roles or audiences, writes in the language of your own request,
  and returns only the prompt, without a preamble or a code fence. A reply wrapped in one code
  fence anyway is pasted without it, for every profile.

**Your own profiles** live outside the package, so an upgrade never replaces them:
`~/.config/prompt-workflow/profiles/<name>.md` (`%APPDATA%\prompt-workflow\profiles\<name>.md`
on Windows; `$XDG_CONFIG_HOME/prompt-workflow/profiles/` when that is set on macOS or Linux),
in the config folder next to `config.toml` and the user `.env`. The file holds the system prompt as plain text, may use `{{PERSONA_RULE}}` like the
built-ins, and is selected by its name: `--profile <name>`, `PROMPT_PROFILE` or
`PROMPT_PRO_PROFILE`. A name is lower-case letters, digits, `-` and `_` (no dots or spaces, not a Windows device name such as `con`), and the file must be named exactly `<name>.md`.

A file named like a built-in (`default.md`, `general.md`) is ignored, so a stray copy cannot
silently change what every trigger sends. To replace a built-in on purpose, list it in
`PROMPT_PROFILE_OVERRIDES`, for example `PROMPT_PROFILE_OVERRIDES=default`; delete the line to
go back. The package's own files are never changed. A profile you added under
`src/prompt_workflow/prompts/` in a checkout keeps working there, but belongs in this folder.
To add a built-in profile to the project, see [CONTRIBUTING.md](CONTRIBUTING.md#add-a-profile).

**Persona.** Set `PROMPT_PERSONA` to a first-person sentence, for example
`PROMPT_PERSONA="I am working as a Head of Data at Example Corp."`. The `default` rewrite then
opens `CONTEXT` with it, unless the draft names a different role, and `-p-` inserts it for you
(even while another setting is invalid; only a settings file it cannot read gives the
`[role]` placeholder). Left empty, the rewrite uses only a role the draft itself states and never guesses one.

## Privacy and data protection

> [!IMPORTANT]
> Cloud triggers send your clipboard to a third-party API. Use them only where your organisation's
> policy allows. For sensitive work, use `-il-` or `-ilm-` with a model that runs on your machine,
> and nothing leaves it. Each trigger names its own provider, so `PROMPT_PROVIDER` does not make
> `-i-` local. To rule out cloud calls, set `PROMPT_LOCAL_ONLY=true`: every trigger or command
> that would send the draft off your machine then pastes
> `[prompt-workflow: PROMPT_LOCAL_ONLY=true: … would send the draft off this machine]` instead.
> It judges by the base URL and the Ollama model tag, so a relay on `localhost` that forwards to
> a cloud API (LiteLLM, an SSH tunnel) still counts as local. If your `localhost` server is such
> a relay, set `PROMPT_GATE_LOCAL=true`: the gate below then scans `-il-` and `-ilm-` drafts too,
> with the same override rules (it does not make `PROMPT_LOCAL_ONLY` refuse them).

The trigger sends whatever is on the clipboard, unseen: if you forgot to copy the draft, the last
thing you copied goes instead. On macOS and Windows the CLI first asks the clipboard which formats
it holds, without reading the item, and refuses one that a password manager marked as concealed
or as not for clipboard history (`org.nspasteboard.ConcealedType` or 1Password's own type on
macOS; `ExcludeClipboardContentFromMonitorProcessing`, `Clipboard Viewer Ignore` or
`CanIncludeInClipboardHistory` = 0 on Windows), for every trigger, local ones included. The
refused item is then cleared from the clipboard: Espanso pastes the refusal through the clipboard
and restores the previous content as plain text, without the marker, so the next trigger would
otherwise send it. Not covered: browser extensions of password managers (they copy through the
web clipboard and set no marker), apps that set none of these markers, a probe that fails, and
Linux; there the item is sent like any other text.

Every call that can send the draft off your machine first runs through a regex gate
([`redaction.py`](src/prompt_workflow/redaction.py)): OpenRouter and Anthropic always, and
Ollama or LM Studio when their base URL is not `localhost` (or `127.0.0.1`, `::1`) or the Ollama
model is a cloud model (a `:cloud` or `-cloud` tag in any letter case, also with an
`@sha256:…` digest, which the local daemon forwards to ollama.com; a model copied under another
name with `ollama cp` is not detected). With `PROMPT_GATE_LOCAL=true` the gate also covers
Ollama and LM Studio on `localhost`. The gate blocks drafts containing:

- payment card numbers (Luhn-checked, also when split by double spaces, tabs, dashes or one line
  break), IBANs (checksum-validated) and email addresses
- API keys (OpenRouter, Anthropic, OpenAI, Stripe, GitHub, GitLab, Hugging Face, Slack, Google,
  xAI, npm, AWS including temporary `ASIA…` keys), Azure storage keys and SAS signatures, JWTs,
  bearer and `Basic` credentials, `curl -u user:password`, PEM/OpenSSH/PGP private keys and
  `user:password@` URLs
- secrets assigned to a name: `DB_PASSWORD=…`, JSON `"client_secret": "…"`, camelCase
  `clientSecret`/`dbPassword`, `PGPASSWORD`, `SECRET_KEY`, `PRIVATE_KEY`, `_authToken`, PHP
  `=>` and Go `:=` (the value must contain a digit), and passwords in prose (`the password is
  …`, `mật khẩu là …`)
- a draft that is a single password- or token-like word, such as a vault password left on the
  clipboard
- an email address next to a password (`jane@example.com:…`, `login jane@… / …`)
- confidentiality labels written as labels: upper case (`CONFIDENTIAL`, `RESTRICTED`, `MẬT`,
  `NỘI BỘ`), alone on a line or opening one (`# Confidential`, `**Confidential**:`,
  `Restricted - …`), in brackets (`[restricted]`), a classification field (`Classification:
  Restricted`, `Độ mật: Mật`), *highly/company/strictly confidential*, *internal only*, *do not
  distribute*, and Vietnamese *tài liệu/văn bản/thông tin mật*, *tối mật*, *lưu hành nội bộ*.
  The words in prose ("output restricted to 5 bullets", "confidential information", *bảo mật*,
  *mật độ*, *mật khẩu*) are not flagged.
- Vietnamese national IDs: a 12-digit CCCD with a valid province and century code (not digits
  inside an AWS ARN), and a 9-digit number next to CMND, CCCD, CMT, *chứng minh nhân dân/thư*,
  *căn cước*, *hộ chiếu*, *passport*, *national ID* or *ID card*
- your own patterns from `PROMPT_EXTRA_PATTERNS`, for example
  `PROMPT_EXTRA_PATTERNS="project[- ]falcon;CUST-\d{6}"` (case-insensitive; reported as
  `custom_1`, `custom_2`, … so the pattern itself never appears in the message). An entry
  that is not a valid regex is rejected when settings load: `config validate`, `config set`
  and `doctor` report it by position, and every trigger prints a marker until it is fixed.
  Keep each pattern simple: it runs on every draft and on each value written to the usage
  history, and Python's regex engine can take exponential time on a pattern with nested
  quantifiers such as `(\w+\s?)+` or `(a|aa)+`. Prefer a literal word, a character class with
  a fixed count (`CUST-\d{6}`) or a bounded repeat (`\w{1,20}`).

> [!WARNING]
> The gate is a heuristic safety net, not a compliance control. It misses things (names,
> addresses, phone numbers, IP addresses, most countries' ID formats, a password in a
> sentence that does not call it one, look-alike letters from other alphabets) and sometimes
> flags harmless text.

The draft is also scanned in a normalised form, so no-break or zero-width spaces, soft hyphens,
variation selectors, Hangul fillers and fullwidth digits cannot split a card number or key.

A blocked draft pastes `[prompt-workflow: Blocked cloud call. Sensitive content detected: …]`
instead of calling the API. When every finding is a label, a Vietnamese ID, an email address or
an IBAN, you can send that one draft with `-iok-` (`--allow-flagged`): the paste then starts with
`[prompt-workflow: sent despite: …]`, and the next draft is checked as usual. Keys, tokens,
passwords, cards, private keys, a bare token and your own `PROMPT_EXTRA_PATTERNS` are never sent
this way. `ALLOW_CLOUD_OVERRIDE=true` turns the gate off for every finding and every later call;
prefer `-iok-` for a one-off. No code path builds a
provider that can reach another machine without the gate. Cloud base URLs must be `https://`
(plain `http` only to `localhost`), so a key is never sent in clear text. Ollama and LM Studio
send no key and accept any scheme: an `http://` base URL on another machine sends the draft and
the rewrite in clear text across your network, so use `https://` (or an SSH tunnel) for a server
you do not reach over `localhost`. Keys stay in your
`.env` or `secrets.toml` (never `config.toml`), are never logged and never shown in a traceback.

The rewrite comes from a model that read your clipboard, so text copied from a web page can steer
it. Before anything is pasted, the CLI removes control characters (an escape sequence could end a
terminal's bracketed paste and run the lines after it) and every character Unicode marks as
default-ignorable, which renders as nothing and can carry hidden instructions for the next AI:
zero-width spaces, bidi marks and overrides, Unicode tag characters, Hangul fillers and variation
selectors. Other line breaks become newlines. An emoji keeps the one presentation selector and
joiner it needs, a keycap (`1️⃣`) keeps its selector, and the joiners Persian and Indic scripts use
survive. One selector or joiner can still follow each non-ASCII character, so a hidden channel of
a few bits per character remains in non-Latin text, but none in English prose. The same
characters are removed from the draft before it is sent. Still read a rewrite before running
anything it contains.

### What is sent, and to whom

Each call is one HTTP request (two when a rate limit or an unavailable upstream is retried)
carrying:

- **the draft**, after the gate above and the clean-up of invisible characters;
- **the system prompt**: the profile's instructions, and with the `default` profile (or your
  own profile using `{{PERSONA_RULE}}`) your `PROMPT_PERSONA` sentence, word for word. It goes
  with every call and is **not** scanned by the gate: an email, a company name or one of your
  `PROMPT_EXTRA_PATTERNS` in the persona is sent even where the same text in the draft would be
  blocked. Keep the persona to what you are happy to send to every recipient below;
- the model name and request settings, such as the temperature and maximum tokens (for
  OpenRouter also the reasoning effort and the endpoint preference; for Ollama its `think`
  setting; the Anthropic API version header);
- the API key, only as the authentication header (`Authorization: Bearer …` for OpenRouter,
  `x-api-key` for Anthropic; none for Ollama or LM Studio).

Who receives it:

| Provider | Recipients |
|---|---|
| OpenRouter (`-i-`, `-iok-`, `-ip-`, `-if-`) | OpenRouter (`OPENROUTER_BASE_URL`), and the upstream endpoint that serves the model: the one pinned by `OPENROUTER_PROVIDER` (`OPENROUTER_PRO_PROVIDER` for `-ip-`, the form's pick for `-if-`), or another endpoint serving the same model when OpenRouter falls back, which `OPENROUTER_ALLOW_FALLBACKS` allows by default (`false` makes the pin binding). An empty pin leaves the choice to OpenRouter. The request also carries an `X-Title: espanso-prompt-rewriter` header, which attributes the calls to this app in OpenRouter's dashboard (the benchmark script sends `espanso-prompt-rewriter-bench`). |
| Anthropic (`-ic-`, commented out) | Anthropic (`ANTHROPIC_BASE_URL`). |
| Ollama / LM Studio on `localhost` (`-il-`, `-ilm-`) | Nobody else, as long as the server on this machine runs the model itself (a `localhost` relay that forwards to a cloud API is not detected, see above; a `cloud`-tagged Ollama model is the next row). These triggers use the `general` profile, which has no persona. |
| Ollama / LM Studio at another address, or an Ollama `cloud` model | The configured server (`OLLAMA_BASE_URL`/`api/chat` or `LMSTUDIO_BASE_URL`); for a cloud model that Ollama server also forwards the request to ollama.com (this tool sends no key there). With an `http://` URL anyone on the network path can read the draft too. The gate applies as for the cloud providers. |

A request to a server that is not on this machine also passes through any proxy in between
(the system proxy, or `HTTPS_PROXY`/`ALL_PROXY`, see [Configuration](#configuration)); a proxy
that inspects TLS sees the whole request, the key header included.

How long each recipient keeps the request and whether it may train on it is set by them, not by
this tool: see OpenRouter's privacy and data settings for your account (they cover which
upstream endpoints it may route to) and the privacy terms of the upstream provider or of
Anthropic.

What never leaves this machine: the usage history (below), your settings files and keys (a key
only as the authentication header of its own provider), and anything else on the clipboard
before or after the trigger.

### Usage history

The CLI keeps a local usage history, so you can see what your triggers cost and how fast
they are. Every `improve` and `persona` run is recorded: one entry per run, plus one per HTTP
request it made (a retried request counts twice, in the same run). It is on by default
(`PROMPT_HISTORY=true`), and stays on this device:

- **What is stored:** metadata only, from a fixed list of columns: when a trigger ran, which
  trigger, profile and outcome (`ok`, `error_marker`, `gate_blocked` for any data-protection
  refusal (a flagged draft, `PROMPT_LOCAL_ONLY`, a cloud URL that is not https), `validation_failed` for
  a reply whose content was rejected, `clipboard_failed`, `concealed_refused` or
  `unexpected_error`), how long it took until the output was printed,
  and per request the provider, model, HTTP status, token counts and the cost the provider
  reported, with its unit (OpenRouter credits are never converted to USD).
- **What is never stored:** your draft or clipboard, the rewrite, your persona, API keys, form
  picks or any raw response; no column is meant for them, and a record's other fields are
  never read. Each text column also has a fixed shape: the outcome, endpoint, cost state and
  error kind are words from fixed lists; a trigger looks like `-i-`; a profile, provider or
  version is a short lower-case or slug name; a response id must start with `gen-`, `msg_` or
  `chatcmpl-`. Only the two model columns take `/` and `:` (`openai/gpt-x:free`). No column
  takes a space, `@` or `\`, and a value the data-protection gate or your
  `PROMPT_EXTRA_PATTERNS` would flag, or that holds a token-like word, is not stored. So a
  sentence, a path, an email, a `user:password@host` or a key cannot get in; a single
  harmless-looking word in a name column (say, a model called `hunter2`) can, which is why only
  your settings and the provider's reply fill those columns, never the draft.
- **Where:** an SQLite file, `history.sqlite3`, in `~/.local/share/prompt-workflow/` (or
  `$XDG_DATA_HOME/prompt-workflow/`) on macOS and Linux, `%LOCALAPPDATA%\prompt-workflow\` on
  Windows. It is never synced and never sent anywhere.
- **Which trigger:** each managed match passes its own fixed `--trigger-id` (`i`, `iok`,
  `ip`, `if`, `il`, `ilm`, `ic`, `p`), stored as the trigger (`-i-`). A run from the terminal,
  or from a match of your own without `--trigger-id`, is recorded as `direct`; a match passing
  an id that is not on that list is recorded as a managed run with no trigger (unattributed).
  The trigger is never guessed from the other options. `-p-` counts once, through its `persona` call.
- **How long:** 365 days by default (`PROMPT_HISTORY_RETENTION_DAYS`, at most 36500). Each
  recorded run also deletes up to 100 of the oldest records past that age, so the history
  stays within it without a separate clean-up (after lowering the setting, a large backlog goes
  over the next few runs).
- **Export, prune, reset:** `prompt-workflow history export [--format json|csv] [-o FILE]`
  writes every record (the same metadata, nothing more), `history prune [--older-than DAYS]`
  deletes older records now, and `history reset` deletes them all. Uninstalling does not delete
  the history (see [Uninstall](#uninstall)).
- **Off:** `prompt-workflow config set PROMPT_HISTORY false` (or `PROMPT_HISTORY=false` in your
  `.env`), then `prompt-workflow history reset` to delete what is there. `setup` and `stats`
  say that the history is on, what it stores and where.
  Nothing is recorded either when the settings fail to load, since whether you turned it off
  is then unknown.

The run is written after its output is printed, so it never changes what is pasted, and the
usage of a reply that then failed (rejected content, a clipboard error) is still kept. Writing
never breaks a trigger and delays it by about 0.25 s at most (1 s once, for the write that
creates the file): a write that cannot finish in that time (a locked, read-only, full or
corrupt file) is dropped, and a small `history.lost` file next to it counts the dropped writes. Only a disk the operating
system itself stalls (a hung network drive) can hold it up longer. Costs are reported only as the provider reported them; an
unknown cost is shown as unknown, never as 0. If you want an estimate for providers that report
no cost (Anthropic), create `prices.toml` in the config directory
(`~/.config/prompt-workflow/`, `%APPDATA%\prompt-workflow\` on Windows) with your prices per
million tokens:

```toml
version = "2026-10-01"
unit = "USD"
[models."claude-sonnet-5"]
input_uncached = 3.00
cache_read = 0.30
cache_write = 3.75
output = 15.00
```

An estimate is stored with the table's `version` and always shown apart from reported costs.

## Model benchmark

The default models and prompt were chosen with a benchmark
([`scripts/bench_models.py`](scripts/bench_models.py)) that scores every rewrite mechanically
for template fidelity, prompt injection and language edge cases, latency and real cost. With the
prompt of v0.14.0 (unchanged since):

- `-i-` (standard tier): `google/gemini-3.5-flash-lite` on `google-ai-studio/flex`, effort
  `minimal`: 30/30 core and 102/120 edge runs passed, 2.0 s median and 3.2 s p95.
- `-ip-` (pro tier): `openai/gpt-6-luna` on `openai`, effort `low`: 23/24 core and 98/99 edge
  runs passed, 5.6 s median and 9.2 s p95.

Prices change quickly, so re-run it before relying on these numbers. The method, all result
tables (with cost per rewrite), the older prompts' results and how to run it are in
[docs/benchmark.md](docs/benchmark.md).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Trigger does not expand right after a letter, digit, `-` or `=`, or in a field you emptied with the keyboard | Triggers only fire at the start of a word, judged by what you last typed. Type a space first, or click into the field. |
| Trigger does not expand | Run `espanso status`, then `prompt-workflow espanso status`, then `prompt-workflow espanso deploy`. |
| `[Espanso]: An error occurred during rendering` | The CLI the matches call is gone or broken: the checkout of an editable install was moved or deleted, or the tool was uninstalled without `prompt-workflow espanso detach`. `prompt-workflow doctor` (once a CLI runs again) reports `launcher: the deployed matches call …, which is gone`. Install it again (see [Install](#install)), then run `prompt-workflow espanso deploy`, or `prompt-workflow espanso detach` to remove the triggers. |
| `doctor` reports `launcher: the deployed matches call …, this install is …` | Launcher drift: the matches call another install of the CLI than the one you just ran (say, an old checkout after switching to a release wheel). Run `prompt-workflow espanso deploy` from the install you want the triggers to use. |
| `doctor` reports `prompts-template.yml: stale` (or another match file) | The deployed match files are from an older version, so `-p-` and the `-if-` lists differ from the CLI. Run `prompt-workflow espanso deploy`. |
| Not sure what is wrong | `prompt-workflow doctor` checks the install, settings, keys, Espanso, the match files and the history; `prompt-workflow config validate` checks the settings alone. |
| `[prompt-workflow: OPENROUTER_API_KEY is not configured]` | The key is missing from `.env` (or `secrets.toml`), or the file is not in one of the [places the CLI looks](#configuration). Once `config.toml` exists, a `.env` is no longer read. |
| `[prompt-workflow: config.toml is not valid TOML (at line …)]` | Fix that line of `config.toml` in the config folder (`secrets.toml` likewise). |
| `[prompt-workflow: OpenRouter returned HTTP 401: check the API key…]` | Wrong key. Replace it in `.env`. |
| `[prompt-workflow: OpenRouter returned HTTP 402: out of credits…]` | Add credits to your OpenRouter account. |
| `[prompt-workflow: … returned HTTP 429: rate limited…]`, `… HTTP 5xx: provider unavailable…` or `… returned an error (code …)` | The provider is busy or down. A rate limit (unless it asks to wait more than 3 s), a 502/503/504/529 or a refused connection to another machine was already retried once within the time limit. Trigger again in a moment, or pick another endpoint in `-if-`. The text after the hint is the provider's own reason. |
| `[prompt-workflow: … HTTP 400: bad request…]` or `… HTTP 404: not found…` | Check the model slug, the endpoint pin and the base URL; the provider's reason follows the hint. |
| `[prompt-workflow: …_API_KEY contains a non-ASCII or invisible character…]` | The key was pasted with a smart quote or an invisible character. Paste it again as plain text. |
| `[prompt-workflow: OPENROUTER_REASONING_EFFORT must be empty or one of …]` | Fix the value in `.env` (`OPENROUTER_PRO_REASONING_EFFORT` likewise). |
| `… the model stopped early (content_filter)]` at the end, or `… declined the request (refusal)` | A content filter or the model's safety policy stopped the rewrite. Rephrase the draft or use another model. |
| `[prompt-workflow: Ollama request failed: …]` | Start Ollama (`ollama serve`) and pull the model (`ollama pull qwen3:8b`). |
| `[prompt-workflow: Blocked cloud call. …]` | The [gate](#privacy-and-data-protection) matched. Remove the content or use a local trigger. If the message offers `-iok-` and the content may leave your machine, use `-iok-` for this draft. |
| `[prompt-workflow: Blocked call to the local server …]` | `PROMPT_GATE_LOCAL=true` and the gate matched a `-il-`/`-ilm-` draft. Remove the content, or set `PROMPT_GATE_LOCAL=false` if your `localhost` server runs the model itself (not a relay to a cloud API). |
| `[prompt-workflow: sent despite: …]` at the top of a rewrite | You used `-iok-`; the draft was sent despite those findings. Delete the line. |
| `[prompt-workflow: Input is too long …]` | The clipboard holds more than 50,000 characters. Copy just the draft. |
| `… the reply hit the model's output limit and is cut off]` at the end, or `… used its whole output limit before writing any text` | The model hit its output cap, often by spending it on reasoning. Raise `OPENROUTER_MAX_TOKENS` (`ANTHROPIC_MAX_TOKENS` for Anthropic), or pick a larger max tokens (or lower effort) in `-if-`. For Ollama the cap is the model's own setting. |
| A `base.yml.bak-…` file appeared in Espanso's `match` folder | Versions before 0.9 deployed `-p-` as `match/base.yml`, the file Espanso creates for your own snippets. The installer backed up that copy and replaced it with `prompts-template.yml`. Older installers overwrote `base.yml` without a backup, so snippets you kept there before first installing this project can only come from your own backups. |
| Expansion is slow | Use a faster model or endpoint (see [benchmark](#model-benchmark)); Espanso waits for the CLI. |
| `[prompt-workflow: … must be an https:// URL]` | A cloud `*_BASE_URL` uses `http`. Switch it to `https`. |
| `[prompt-workflow: OLLAMA_THINK must be true or false, got …]` (or `must be a number above 0`) | Fix that value in `.env`. A long value, or one that looks like a key, is shown as `<redacted, N chars>`. |
| `[prompt-workflow: … in .env runs into the next line; add the missing newline]` | Two lines of `.env` were saved as one. Split them. |
| `[prompt-workflow: … request failed: invalid header value (check the API key)]` | The API key in `.env` contains a line break or another character a key never has. Paste it again. |
| `-il-` or `-ilm-` says `Blocked cloud call` | `OLLAMA_BASE_URL` / `LMSTUDIO_BASE_URL` points at another machine, or the Ollama model is a cloud model, so the gate applies. Use a model on `localhost` for sensitive drafts. |
| Reasoning text appears in the output | Set `OLLAMA_THINK=false`. A `<think>` block at the start of the reply is stripped (a later one is kept as answer text); extend `strip_thinking` in `providers/base.py` for other tag formats. |

For fully local use, pull a model (`ollama pull qwen3:8b`) or load one in LM Studio and enable
its local server, then check it answers: `curl http://localhost:11434/api/tags` or
`curl http://localhost:1234/v1/models`.

## Development

```bash
uv sync                  # installs the dev tools too
uv run pytest            # unit tests, no network
```

To use a checkout with Espanso, install it as an editable tool instead of a release wheel:

```bash
./scripts/install_macos.sh      # macOS
.\scripts\install_windows.ps1   # Windows (PowerShell)
```

The script runs `uv tool install --editable` pinned to `uv.lock` (and checks the tool venv
against it), then `prompt-workflow espanso deploy --yes`, which writes the match files with the
CLI's absolute path and restarts Espanso. It never touches Espanso's `config/` folder (the old
`--with-config` / `-WithConfig` option is gone). The Windows script runs on Windows PowerShell
5.1 and PowerShell 7. If script execution is disabled, run it as
`powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1` (`pwsh` for PowerShell
7), which allows this one run without changing your execution policy.
On Linux, run `uv tool install --editable .` and `prompt-workflow espanso deploy`. An editable
install runs the code and profiles straight from the checkout, so a `git pull` or a branch
switch changes `-i-` at once, while the match files change only on the next deploy. It also
reads a `.env` in the checkout; keep the one holding your key in the config folder instead
(`chmod 600` it), where a careless `git add` cannot pick it up.

[CONTRIBUTING.md](CONTRIBUTING.md) has the full checks, the project layout and how to add a
profile, trigger, setting or provider. CI runs lint and type checks once, and the tests on macOS,
Windows and Linux with Python 3.12 to 3.14. Gitleaks scans every push for secrets.

## Contributing, security, license

- 🤝 Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first.
- 🔐 Found a way around the data-protection gate, or another vulnerability? Report it privately
  as described in [SECURITY.md](SECURITY.md).
- 📝 Release notes are in [CHANGELOG.md](CHANGELOG.md).
- 📄 Licensed under the [MIT License](LICENSE).

Built on [Espanso](https://espanso.org/), [Typer](https://typer.tiangolo.com/),
[HTTPX](https://www.python-httpx.org/) and [uv](https://docs.astral.sh/uv/), with models served by
[OpenRouter](https://openrouter.ai/), [Anthropic](https://www.anthropic.com/),
[Ollama](https://ollama.com/) and [LM Studio](https://lmstudio.ai/).
