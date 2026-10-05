# Repository instructions

`CLAUDE.md` at the repository root is the source of truth for this repository's architecture,
commands and rules; read it before changing code. `CONTRIBUTING.md` has the checks and ground
rules. The invariants below must never be broken:

- The CLI prints only through `cli._emit()` (no trailing newline); every failure is a
  `[prompt-workflow: ...]` marker on stdout with exit code 0, never a traceback.
- Build every provider with `factory.make_provider()`, which wraps any call that can leave the
  machine in the data-protection gate. The only overrides are `ALLOW_CLOUD_OVERRIDE=true` and
  `--allow-flagged` (one call, soft findings only); never add a code path around the gate.
- Every Espanso match that runs the CLI sets `force_mode: clipboard`, and every match sets
  `left_word: true`.
- Match commands start with the quoted `"__PROMPT_WORKFLOW__"` placeholder and quote nothing
  else; nothing is ever deployed to Espanso's `config/` folder.
- Never put API keys, customer data, credentials or confidential prompts in source, tests,
  logs or commits.
- Tests stay offline: never call external APIs from unit tests; mock the HTTP client.
- Update README.md and the CHANGELOG `## Unreleased` section with every user-visible change.
- Do not run the paid bench (`scripts/bench_models.py`), `pytest -m live`, the installers or
  `espanso` commands unless asked.
