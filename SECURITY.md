# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please do **not** open a public issue. Report privately through
[GitHub private vulnerability reporting](https://github.com/vlastimilbures/espanso-prompt-rewriter/security/advisories/new)
(repository **Security** tab, then **Report a vulnerability**).

Include what you found, how to reproduce it, and the impact you expect. You can expect an
acknowledgement within a week. Once a fix is released, you are welcome to be credited in the
advisory.

## Scope

This project sends clipboard text to LLM providers, so the most important security property is
that nothing reaches a cloud provider without passing the data-protection gate.

Report privately:

- any way to make a cloud provider call **without** the gate running (a gate bypass)
- anything that exposes API keys, for example in output, logs or error messages
- command injection through the install scripts or the Espanso match files

Report as a normal issue:

- a sensitive pattern the gate does not detect, or harmless text it blocks, or input that makes
  the scan noticeably slow. The gate is a
  documented heuristic, so these are improvements rather than vulnerabilities. Use fake sample
  data in the issue.

Out of scope: the behaviour of third-party model providers, and what a model writes in its
output.
