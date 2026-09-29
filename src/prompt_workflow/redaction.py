from __future__ import annotations

import re
from collections.abc import Callable

# Patterns that suggest content should not leave the device for a cloud model.
# These are heuristics, not a guarantee: false positives are handled via
# ALLOW_CLOUD_OVERRIDE, but false negatives are expected and should not be
# treated as a compliance control on their own.
_PATTERNS: dict[str, re.Pattern[str]] = {
    "payment_card": re.compile(r"\b(?:\d[ .-]?){13,19}\b"),
    "vietnam_id_12": re.compile(r"\b\d{12}\b"),
    "vietnam_id_9": re.compile(r"\b(?:CMND|CCCD|ID|passport|số)\D{0,20}\d{9}\b", re.IGNORECASE),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    # Vendor keys contain '-'/'_' after a short prefix (sk-or-v1-, sk-ant-api03-, sk-proj-),
    # so the key bodies allow both.
    "openrouter_key": re.compile(r"\bsk-or-v1-[0-9a-f]{32,}"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "openai_key": re.compile(r"\bsk-(?!or-|ant-)[A-Za-z0-9_-]{20,}"),
    "stripe_key": re.compile(r"\b[spr]k_(?:live|test)_[A-Za-z0-9]{16,}"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    "xai_key": re.compile(r"\bxai-[A-Za-z0-9]{20,}"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "pem_private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}\b", re.IGNORECASE),
    # scheme://user:password@host
    "url_credentials": re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
    # password=..., api_key: ...; the value must contain a digit so prose such as
    # "token: explanation" does not trip it.
    "secret_assignment": re.compile(
        r"\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*[\"']?"
        r"(?=[^\s\"']*\d)[^\s\"']{8,}",
        re.IGNORECASE,
    ),
    "confidential_label": re.compile(
        r"\b(confidential|restricted|internal only|customer data|mật|nội bộ)\b",
        re.IGNORECASE,
    ),
}


def _luhn(digits: str) -> bool:
    """Luhn checksum, used to confirm a payment-card-length digit run is a real card number."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _is_card(match: re.Match[str]) -> bool:
    return _luhn(re.sub(r"\D", "", match.group()))


# Patterns whose raw regex match is too broad: a finding needs one match that also
# passes the validator. Every match is checked, so a failing candidate cannot hide a
# later valid one.
_VALIDATORS: dict[str, Callable[[re.Match[str]], bool]] = {"payment_card": _is_card}


# Separator between user-defined regexes in PROMPT_EXTRA_PATTERNS. A literal ';' inside a
# pattern can be written as \x3b.
EXTRA_SEPARATOR = ";"


def compile_extra(spec: str) -> tuple[re.Pattern[str], ...]:
    """Compile PROMPT_EXTRA_PATTERNS (`;`-separated regexes, case-insensitive)."""
    patterns = []
    for i, raw in enumerate((p.strip() for p in spec.split(EXTRA_SEPARATOR) if p.strip()), 1):
        try:
            patterns.append(re.compile(raw, re.IGNORECASE))
        except re.error as exc:
            # Name the position, not the pattern: it may itself describe sensitive data.
            raise ValueError(f"PROMPT_EXTRA_PATTERNS entry {i} is not a valid regex") from exc
    return tuple(patterns)


def scan(text: str, extra: tuple[re.Pattern[str], ...] = ()) -> list[str]:
    """Return the names of any sensitive patterns found in text.

    User-defined ``extra`` patterns are reported as custom_1, custom_2, ... so the
    pattern text never appears in the output.
    """
    findings = []
    for name, pattern in _PATTERNS.items():
        validate = _VALIDATORS.get(name)
        if validate is None:
            found = pattern.search(text) is not None
        else:
            found = any(validate(m) for m in pattern.finditer(text))
        if found:
            findings.append(name)
    findings += [f"custom_{i}" for i, p in enumerate(extra, 1) if p.search(text)]
    return findings
