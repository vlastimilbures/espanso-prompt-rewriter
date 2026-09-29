## What and why

<!-- What does this change, and what problem does it solve? Link the issue if there is one. -->

## How it was tested

<!-- Commands you ran. For prompt or provider changes, include `pytest -m live` and benchmark
     before/after pass rates. -->

## Checklist

- [ ] `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy` pass
- [ ] Tests added or updated for the behaviour change
- [ ] Cloud providers are built only through `factory.make_provider()` (gate intact)
- [ ] CLI output keeps the Espanso contract: no trailing newline, errors as `[prompt-workflow: …]`
- [ ] CHANGELOG.md updated under *Unreleased* if users would notice
- [ ] No real keys, customer data or confidential text anywhere in the diff
