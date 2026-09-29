from __future__ import annotations

import re

from .providers.base import Provider, ProviderError
from .redaction import scan


class GatedProvider:
    """Wraps a cloud provider and refuses to transmit sensitive drafts.

    The redaction gate previously lived only at the CLI call site, so any
    other code that builds a cloud provider directly (e.g. scripts/bench_models.py)
    bypassed it entirely. Wrapping the provider itself makes the gate
    impossible to route around.
    """

    def __init__(
        self,
        inner: Provider,
        allow_override: bool,
        extra_patterns: tuple[re.Pattern[str], ...] = (),
    ) -> None:
        self._inner = inner
        self._allow_override = allow_override
        self._extra_patterns = extra_patterns

    def generate(self, prompt: str, system_prompt: str, model: str | None = None) -> str:
        findings = scan(prompt, self._extra_patterns)
        if findings and not self._allow_override:
            raise ProviderError(
                "Blocked cloud call. Sensitive content detected: "
                + ", ".join(findings)
                + ". Use a local provider or set ALLOW_CLOUD_OVERRIDE=true."
            )
        return self._inner.generate(prompt, system_prompt, model)
