from __future__ import annotations

import re

from .providers.base import Provider, ProviderError
from .redaction import scan_draft


class GatedProvider:
    """Wraps a provider that can send the draft off this machine and refuses to transmit
    sensitive drafts. factory.make_provider() applies it, so no code path can skip it."""

    def __init__(
        self,
        inner: Provider,
        allow_override: bool,
        extra_patterns: tuple[re.Pattern[str], ...] = (),
    ) -> None:
        self._inner = inner
        self._allow_override = allow_override
        self._extra_patterns = extra_patterns

    def generate(self, prompt: str, system_prompt: str) -> str:
        findings = scan_draft(prompt, self._extra_patterns)
        if findings and not self._allow_override:
            raise ProviderError(
                "Blocked cloud call. Sensitive content detected: "
                + ", ".join(findings)
                + ". Use a local provider or set ALLOW_CLOUD_OVERRIDE=true."
            )
        return self._inner.generate(prompt, system_prompt)
