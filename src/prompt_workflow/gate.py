from __future__ import annotations

import re

from .providers.base import Provider, ProviderError
from .redaction import SOFT_FINDINGS, scan_draft


def _block_message(findings: list[str], hard: list[str], allow_flagged: bool, name: str) -> str:
    """The hint names what applies to this provider: -iok- is the OpenRouter trigger, and a
    remote Ollama or LM Studio has no local trigger to fall back to."""
    once = "-iok-" if name == "openrouter" else "--allow-flagged"
    local = "a local trigger (-il-)" if name in ("openrouter", "anthropic") else "a local model"
    message = "Blocked cloud call. Sensitive content detected: " + ", ".join(findings) + ". "
    if not hard:
        return message + f"If it may leave this machine, send it once with {once}, or use {local}."
    if allow_flagged:
        message += f"{once} never sends " + ", ".join(hard) + ". "
    return message + f"Remove it, or use {local}."


class GateBlocked(ProviderError):
    """The gate refused to send a draft; the usage history records it as ``gate_blocked``."""


class GatedProvider:
    """Wraps a provider that can send the draft off this machine and refuses to transmit
    sensitive drafts. factory.make_provider() applies it, so no code path can skip it.

    ``allow_flagged`` sends a draft once despite soft findings only (SOFT_FINDINGS); the
    caller shows ``sent_despite`` with the result. ``allow_override`` (ALLOW_CLOUD_OVERRIDE)
    turns the gate off for every finding."""

    def __init__(
        self,
        inner: Provider,
        allow_override: bool,
        extra_patterns: tuple[re.Pattern[str], ...] = (),
        allow_flagged: bool = False,
        name: str = "openrouter",
    ) -> None:
        self._inner = inner
        self._name = name
        self._allow_override = allow_override
        self._extra_patterns = extra_patterns
        self._allow_flagged = allow_flagged
        self.sent_despite: tuple[str, ...] = ()

    def generate(self, prompt: str, system_prompt: str) -> str:
        self.sent_despite = ()
        findings = scan_draft(prompt, self._extra_patterns)
        if findings and not self._allow_override:
            hard = [f for f in findings if f not in SOFT_FINDINGS]
            if hard or not self._allow_flagged:
                message = _block_message(findings, hard, self._allow_flagged, self._name)
                raise GateBlocked(message)
            self.sent_despite = tuple(findings)
        return self._inner.generate(prompt, system_prompt)
