# PromptMend

```text
 .-------.    ___                    _   __  __             _
 |  [+]  |   | _ \_ _ ___ _ __  _ __| |_|  \/  |___ _ _  __| |
 '--. .--'   |  _/ '_/ _ \ '  \| '_ \  _| |\/| / -_) ' \/ _` |
    |/       |_| |_| \___/_|_|_| .__/\__|_|  |_\___|_||_\__,_|
                               |_|

A rough draft in, a precise prompt out: type -i- in any text field.
```

**Type a trigger, get a well-structured LLM prompt.** Copy a rough draft, type `-i-` anywhere,
and [Espanso](https://espanso.org/) replaces it with a precise, sectioned prompt rewritten by a
local or cloud model.

[![tests](https://github.com/vlastimilbures/promptmend/actions/workflows/test.yml/badge.svg)](https://github.com/vlastimilbures/promptmend/actions/workflows/test.yml)
[![release](https://img.shields.io/github/v/release/vlastimilbures/promptmend)](https://github.com/vlastimilbures/promptmend/releases)
[![PyPI](https://img.shields.io/pypi/v/promptmend)](https://pypi.org/project/promptmend/)
[![python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-blue?logo=python&logoColor=white)](https://github.com/vlastimilbures/promptmend/blob/main/pyproject.toml)
[![platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)](https://github.com/vlastimilbures/promptmend/blob/main/docs/install.md)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/vlastimilbures/promptmend/blob/main/LICENSE)

One small Python CLI, `promptmend`, sits behind every trigger. Espanso runs it when you type
the trigger; it reads your clipboard, checks the draft for sensitive content before anything
leaves your machine, and sends it with the rewrite instructions to the model. Espanso then
pastes the result in place of the trigger, or a readable `[promptmend: …]` message if
something went wrong, never a blank.

It works the same way on macOS and Windows, with [OpenRouter](https://openrouter.ai),
[Anthropic](https://www.anthropic.com), [Ollama](https://ollama.com) or
[LM Studio](https://lmstudio.ai). A full-screen terminal interface and plain commands set it up
and manage it.

```text
copy a draft --> type -i- --> data-protection gate --> model (cloud or local) --> rewrite pasted
                                       |
                                       +--> sensitive content: a [promptmend: ...] marker instead
```

## Highlights

- **Golden-template rewrite.** The `default` profile turns any draft into
  `CONTEXT / GOAL / INSTRUCTIONS / CONSTRAINTS / INPUTS / OUTPUTS`, and picks the planning and
  review steps from the task's complexity and audience.
- **Two tiers.** `-i-` answers in a few seconds; `-ip-` hands hard, multi-part drafts to a
  reasoning model for a more rigorous rewrite.
- **Four providers, fully local if you like.** OpenRouter (default), Anthropic, Ollama and LM
  Studio. Each trigger names its provider; `PROMPT_LOCAL_ONLY=true` refuses every cloud call.
- **Data-protection gate.** Before a draft can leave your machine it is scanned for payment
  cards, national IDs, emails, API keys, tokens, passwords, private keys, confidentiality
  labels and your own patterns, and blocked unless you explicitly override.
- **Your persona, once.** Set `PROMPT_PERSONA` and every rewrite (and the `-p-` snippet) opens
  with your role.
- **Never a blank paste.** Errors arrive inline as `[promptmend: …]`, because Espanso cannot
  show stderr or exit codes.
- **Clean output.** Reasoning blocks, control characters and invisible Unicode never reach the
  app you are typing in.
- **Interface and commands.** A full-screen interface with a Try tab, and headless commands
  for scripts, with `promptmend doctor` to check everything.
- **Benchmarked defaults.** The default models and prompt were chosen by a bundled benchmark
  that scores template fidelity, injection, language edge cases, latency and cost.

## Screenshots

![The Home tab of the PromptMend interface](https://raw.githubusercontent.com/vlastimilbures/promptmend/main/docs/interface.svg)

Home: whether you are ready, what each trigger runs, the match files, the history and the
`doctor` checks, with a command line below.

![The Try tab of the PromptMend interface](https://raw.githubusercontent.com/vlastimilbures/promptmend/main/docs/try.svg)

Try: type a draft and rewrite it the way `-i-` would, against a local stub or, once you
confirm, a real provider; the clipboard is never touched.

## Example

Draft on the clipboard:

```text
write a short board update on why customer churn went up last quarter, use the attached churn dashboard export
```

Pasted in its place (real output of the v0.19.0 prompt on its default model,
`google/gemini-3.5-flash-lite` on `google-ai-studio/flex`, effort `minimal`, no persona
configured, generated 2026-10-06):

```text
<CONTEXT>
I need to write a short board update explaining why customer churn went up last quarter, using the attached churn dashboard export.
</CONTEXT>

<GOAL>
A concise, executive-level board update explaining the drivers of increased customer churn last quarter and the actions being taken.
</GOAL>

<INSTRUCTIONS>
1/ Execute, but state assumptions up front.
2/ Load and validate all inputs. If anything is missing, ambiguous, or contradictory, ask me up to 5 targeted questions before drafting; if step 1 says execute, state your assumption instead and ask only about a gap that blocks the task.
3/ Analyse the churn dashboard export to identify the trends, segments, and main drivers behind the churn increase last quarter.
4/ Draft the board update containing: an executive summary of the churn metrics, the primary causes identified in the data, and the mitigation steps.
5/ Use a separate agent with corporate governance and investor reporting domain knowledge if you can run one, otherwise review as an independent expert in that domain would: perform a critical review, check for errors, and ensure the output is complete and accurate, review formatting and clarity, and ensure the output is well structured and easy to read; summarize all issues and improvement points, validate them with me before implementing any changes.
6/ Flag material judgment calls or trade-offs and let me decide.
</INSTRUCTIONS>

<CONSTRAINTS>
- Keep it short: a brief update suitable for a board of directors.
- Tone: professional, objective, and analytical.
- Use only the data provided in the churn dashboard export and general business knowledge labelled as such.
- Out of scope: detailed tactical execution plans or historical data prior to last quarter.
</CONSTRAINTS>

<INPUTS>
Churn dashboard export [REVIEW: attach or paste the churn dashboard export data]
</INPUTS>

<OUTPUTS>
structured .md, well formatted with clear headings/subheadings
</OUTPUTS>
```

The two choices are made independently. Here the model read "short" as a single small task, so
step 1 is "execute now" (analysing an export first could also justify "plan first"); the board
audience selected the independent-review step. A quick note to yourself would get a
self-review checklist instead.

## Requirements

- [Espanso](https://espanso.org/install/), installed and running.
- macOS or Windows. Linux works too, with a clipboard tool (`xclip`, `xsel` or `wl-clipboard`).
- An [OpenRouter API key](https://openrouter.ai/keys) for the default `-i-` trigger, or
  [Ollama](https://ollama.com) or [LM Studio](https://lmstudio.ai) for fully local use.
- Python 3.12 or later, which Homebrew or uv installs for you.

## Install

| Channel | macOS | Windows | Linux |
|---------|-------|---------|-------|
| Homebrew tap | Recommended | — | Yes |
| One-command install | — | Recommended | — |
| uv (from PyPI) | Yes | Yes | Recommended |

### macOS

With [Homebrew](https://brew.sh) (recommended):

```bash
brew install vlastimilbures/tap/promptmend
```

The formula lives in the project's own tap,
[vlastimilbures/homebrew-tap](https://github.com/vlastimilbures/homebrew-tap), not in
homebrew/core. Or with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv tool install promptmend -c https://github.com/vlastimilbures/promptmend/releases/latest/download/constraints.txt
```

### Windows

Paste this into PowerShell or a Command Prompt; it needs no Python or uv first:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://github.com/vlastimilbures/promptmend/releases/latest/download/install.ps1 | iex"
```

It installs uv if it is missing, then the attested wheel from the latest GitHub Release, and
runs `promptmend doctor`. It never deploys the triggers: that is the next step. Or with uv:

```powershell
winget install --id astral-sh.uv -e
uv tool install promptmend -c https://github.com/vlastimilbures/promptmend/releases/latest/download/constraints.txt
```

### Linux

Use the uv line above, plus `xclip` or `xsel` (X11) or `wl-clipboard` (Wayland) for the
clipboard. Homebrew works too.

The `-c` file holds the exact dependency versions the release was tested with; without it, uv
would resolve them afresh.

### Update and uninstall

Update through the channel you installed with, then run `promptmend doctor`; if it reports a
match file as `stale`, run `promptmend espanso deploy`.

| Channel | Update | Uninstall |
|---------|--------|-----------|
| Homebrew | `brew upgrade promptmend` | `brew uninstall promptmend` |
| One-command install | Run the same command again | `uv tool uninstall promptmend` |
| uv | The `uv tool install` line above with `--force` | `uv tool uninstall promptmend` |

Before you uninstall, run `promptmend espanso detach`: once the CLI is gone, every trigger
that calls it fails with Espanso's rendering error.

More: [docs/install.md](https://github.com/vlastimilbures/promptmend/blob/main/docs/install.md)
(verify a download, update, uninstall, upgrade from 0.18 or a checkout install).

## Quick start

1. Set it up in a terminal, or open the interface instead:

   ```bash
   promptmend setup
   promptmend                 # or the full-screen interface
   ```

   `setup` asks for the provider and default profile, then the API key (hidden input, saved
   in `secrets.toml`). It shows the Espanso match files it would deploy and writes them only
   if you agree, and ends with a test call against a stub on `127.0.0.1`: never a paid call,
   never your real key. An existing `.env` or an earlier checkout install is offered for
   migration, and nothing changes unless you say yes.
2. Copy a rough draft.
3. Type `-i-` in any text field and wait a few seconds without typing or switching windows:
   Espanso pastes the rewrite wherever the focus is when the answer arrives.
4. If anything is off, ask the doctor:

   ```bash
   promptmend doctor
   ```

### Fully local

No API key is needed for the local triggers. Pull a model in [Ollama](https://ollama.com), or
load one in [LM Studio](https://lmstudio.ai) and enable its local server, then use `-il-`
(Ollama) or `-ilm-` (LM Studio):

```bash
ollama pull qwen3:8b                            # the default OLLAMA_MODEL
promptmend config set PROMPT_LOCAL_ONLY true    # optional: refuse every cloud call
```

## Triggers

| Trigger | What it does | Provider |
|---------|--------------|----------|
| `-i-` | Rewrites the clipboard into the golden template | OpenRouter |
| `-ip-` | The same rewrite on the pro tier (a reasoning model, slower) | OpenRouter |
| `-if-` | The same rewrite; a form picks model, effort, tokens and timeout | OpenRouter |
| `-iok-` | `-i-`, sent once despite a flagged label, ID, email or IBAN | OpenRouter |
| `-il-` | General prompt improvement, fully local | Ollama |
| `-ilm-` | General prompt improvement, fully local | LM Studio |
| `-p-` | An empty golden template to fill in, opening with your persona | — |
| `-risk-` | An enterprise-risk analysis prompt scaffold | — |

- A trigger fires only at the start of a word: after a space, punctuation or a bracket, never
  inside `a[n-i-1]`.
- It sends whatever is on the clipboard, so copy the draft first.
- Do not type or switch windows while it runs, or set `PROMPT_OUTPUT=clipboard` to get the
  rewrite on the clipboard instead of pasted.

More: [docs/usage.md](https://github.com/vlastimilbures/promptmend/blob/main/docs/usage.md)
(profiles per trigger, clipboard output, the `-if-` form, your own triggers).

## Key commands

| Command | What it does |
|---------|--------------|
| `promptmend` / `promptmend ui` | Opens the full-screen interface |
| `promptmend setup` | First run: provider, profile, key, match files and a smoke test |
| `promptmend doctor` | Checks the install, settings, keys, Espanso, match files and history |
| `promptmend config show` | Every setting, its value and where it comes from |
| `promptmend config set NAME VALUE` | Saves a setting after checking it |
| `promptmend secrets set NAME` | Saves an API key from a hidden prompt |
| `promptmend profiles list` | Lists the built-in and your own profiles |
| `promptmend espanso status` | Shows whether the deployed match files are up to date |
| `promptmend espanso deploy` | Writes or updates the match files, after a preview |
| `promptmend stats` | Calls, latency, tokens and costs from the local usage history |
| `promptmend improve --source stdin` | Rewrites a draft piped in, in a terminal |
| `promptmend --version` | Prints the installed version |

More: [docs/commands.md](https://github.com/vlastimilbures/promptmend/blob/main/docs/commands.md)
(every option, the scripting contract, exit codes).

## Configuration

Settings live in `config.toml` and API keys in `secrets.toml`, both in the config folder. Each
setting is named like an environment variable, and a real environment variable wins over the
files.

| OS | Config folder (settings, keys, profiles) | Data folder (usage history) |
|----|------------------------------------------|-----------------------------|
| macOS | `~/.config/promptmend/` | `~/.local/share/promptmend/` |
| Windows | `%APPDATA%\promptmend\` | `%LOCALAPPDATA%\promptmend\` |
| Linux | `~/.config/promptmend/` (`$XDG_CONFIG_HOME`) | `~/.local/share/promptmend/` (`$XDG_DATA_HOME`) |

Change them with `promptmend config set` or in the interface's Providers tab. The common
changes:

```bash
promptmend config set OPENROUTER_MODEL google/gemini-3.5-flash-lite      # the -i- model
promptmend config set PROMPT_PERSONA "I am working as a Head of Data at Example Corp."
promptmend config set PROMPT_OUTPUT clipboard   # copy the rewrite instead of pasting it
promptmend config set PROMPT_LOCAL_ONLY true    # refuse every cloud call
promptmend config set PROMPT_HISTORY false      # keep no usage history
```

A change applies to the next trigger, with no redeploy. Keys go in with
`promptmend secrets set OPENROUTER_API_KEY`, never as an argument. Settings still in a `.env`
move over with `promptmend config migrate`, after a preview.

More: [docs/configuration.md](https://github.com/vlastimilbures/promptmend/blob/main/docs/configuration.md)
(all settings, files and folders) and
[docs/profiles.md](https://github.com/vlastimilbures/promptmend/blob/main/docs/profiles.md)
(persona, your own profiles).

## Privacy

Cloud triggers send your clipboard to OpenRouter (and the endpoint that serves the model) or
Anthropic. Each call carries:

- the draft, after the data-protection gate and the removal of invisible characters;
- the system prompt: the profile's instructions and your persona, which the gate does not scan;
- the model name and request settings;
- the API key, only as the authentication header of its own provider.

Before that, the gate blocks keys, tokens, passwords, payment cards, national IDs,
confidentiality labels and your own patterns; `-iok-` sends one flagged draft on purpose when
every finding is a label, ID, email or IBAN. `PROMPT_LOCAL_ONLY=true` refuses every cloud call,
and the local triggers keep everything on your machine. On macOS and Windows, clipboard items
a password manager marks as concealed are refused. The usage history is metadata only and never leaves this
device.

**The gate is a heuristic safety net, not a compliance control: use cloud triggers only where
your organisation's policy allows.**

More: [docs/privacy.md](https://github.com/vlastimilbures/promptmend/blob/main/docs/privacy.md)
(what is sent and to whom, what the gate blocks, clipboard safety, usage history).

## Model benchmark

The defaults were chosen with a bundled benchmark that scores every rewrite mechanically for
template fidelity, prompt injection and language edge cases, latency and real cost. `-i-`
runs `google/gemini-3.5-flash-lite` on `google-ai-studio/flex` (effort `minimal`); `-ip-` runs
`openai/gpt-6-luna` on `openai` (effort `low`). The scores, timings and costs, and how to run
it: [docs/benchmark.md](https://github.com/vlastimilbures/promptmend/blob/main/docs/benchmark.md).

## Documentation

| Page | What you find there |
|------|---------------------|
| [Install](https://github.com/vlastimilbures/promptmend/blob/main/docs/install.md) | Every channel on macOS, Windows and Linux, verifying, updating, uninstalling, upgrading |
| [Usage](https://github.com/vlastimilbures/promptmend/blob/main/docs/usage.md) | The triggers, what they send, paste or clipboard output, your own triggers, the match files |
| [Commands](https://github.com/vlastimilbures/promptmend/blob/main/docs/commands.md) | `improve` and its options, the scripting contract, management commands, exit codes |
| [Configuration](https://github.com/vlastimilbures/promptmend/blob/main/docs/configuration.md) | Where settings and keys live, how to change them, the complete settings reference |
| [Profiles](https://github.com/vlastimilbures/promptmend/blob/main/docs/profiles.md) | The built-in profiles, your own profiles and the persona |
| [Privacy](https://github.com/vlastimilbures/promptmend/blob/main/docs/privacy.md) | What is sent and to whom, the gate, local-only mode, the usage history |
| [Interface](https://github.com/vlastimilbures/promptmend/blob/main/docs/interface.md) | The tabs, the command line, the Try tab and the keys |
| [Troubleshooting](https://github.com/vlastimilbures/promptmend/blob/main/docs/troubleshooting.md) | `doctor` first, then each marker and symptom with its fix |
| [Benchmark](https://github.com/vlastimilbures/promptmend/blob/main/docs/benchmark.md) | How the default models and prompt were chosen, and every result |

## Contributing

Contributions are welcome. Read
[CONTRIBUTING.md](https://github.com/vlastimilbures/promptmend/blob/main/CONTRIBUTING.md)
first: it covers setting up a checkout, the checks CI runs, and how to add a profile, trigger,
setting or provider. Release notes are in
[CHANGELOG.md](https://github.com/vlastimilbures/promptmend/blob/main/CHANGELOG.md).

## Security

Found a way around the data-protection gate, or another vulnerability? Report it privately as
described in [SECURITY.md](https://github.com/vlastimilbures/promptmend/blob/main/SECURITY.md).

## License

Licensed under the
[MIT License](https://github.com/vlastimilbures/promptmend/blob/main/LICENSE).

Built on [Espanso](https://espanso.org/), [Typer](https://typer.tiangolo.com/),
[Textual](https://textual.textualize.io/), [HTTPX](https://www.python-httpx.org/) and
[uv](https://docs.astral.sh/uv/), with models served by [OpenRouter](https://openrouter.ai/),
[Anthropic](https://www.anthropic.com/), [Ollama](https://ollama.com/) and
[LM Studio](https://lmstudio.ai/).
