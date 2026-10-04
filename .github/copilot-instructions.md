# Repository instructions

- Keep all functionality cross-platform for macOS and Windows.
- Use Python 3.12, uv, type hints, pathlib, and explicit timeouts.
- Never place API keys, customer data, credentials, or confidential prompts in source, tests, logs, or commits.
- Keep Espanso matches in espanso/match and behavior settings in espanso/config.
- Espanso match commands must start with the quoted "__PROMPT_WORKFLOW__" placeholder and quote nothing else; install scripts substitute the absolute CLI path.
- Every Espanso match that runs the CLI sets `force_mode: clipboard`, so output is pasted, never typed.
- Preserve existing triggers unless a migration note and test are added.
- Every Espanso match sets `left_word: true`, so triggers never fire inside a word.
- Route every call that can leave the machine through the redaction gate: build providers with `factory.make_provider()`, never bypass it without an explicit override flag.
- Strip model reasoning (<think> blocks) before returning text to Espanso.
- CLI output goes through `cli._emit()` (strips unsafe characters, no trailing newline), and errors must be surfaced inline as [prompt-workflow: ...].
- Add tests for YAML parsing, prompt profiles, provider success and failure paths, redaction, config binding, and CLI behavior.
- Do not call external APIs from unit tests; mock the HTTP client.
- Update README.md and CHANGELOG.md when commands, paths, dependencies, or configuration change.
