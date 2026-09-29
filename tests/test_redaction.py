import time

import pytest

from prompt_workflow.redaction import compile_extra, scan


# scan() flags a Luhn-valid payment card number.
def test_detects_payment_card():
    assert "payment_card" in scan("card 4111 1111 1111 1111")


# scan() does not flag a digit run that fails the Luhn checksum.
def test_ignores_non_luhn_digit_run():
    assert "payment_card" not in scan("order id 1234 5678 9012 3456")


# scan() flags an email address.
def test_detects_email():
    assert "email" in scan("contact john.doe@example.com")


# scan() flags a confidentiality label like "CONFIDENTIAL".
def test_detects_confidential_label():
    assert "confidential_label" in scan("This is CONFIDENTIAL material")


# scan() flags a Vietnamese national ID number.
def test_detects_vietnam_id():
    assert "vietnam_id_12" in scan("ID 012345678901")


# scan() flags a 9-digit Vietnamese ID only alongside a context keyword.
def test_detects_vietnam_id_9_with_context():
    assert "vietnam_id_9" in scan("CMND so 123456789")


# scan() does not flag a bare 9-digit number with no ID context.
def test_ignores_bare_9_digit_number():
    assert scan("123456789") == []


# scan() does not flag a large plain number like a formatted amount.
def test_ignores_large_plain_number():
    assert scan("budget is 1,500,000,000 VND") == []


# scan() flags an AWS access key ID.
def test_detects_aws_access_key():
    assert "aws_access_key" in scan("key AKIAIOSFODNN7EXAMPLE")


# scan() flags a JWT.
def test_detects_jwt():
    token = (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"  # gitleaks:allow
    )
    assert "jwt" in scan(f"auth header {token}")


# scan() flags a PEM private key header.
def test_detects_pem_private_key():
    assert "pem_private_key" in scan("-----BEGIN RSA PRIVATE KEY-----\nMIIB...")


# Text with no sensitive matches returns no findings.
def test_clean_text_has_no_findings():
    assert scan("Summarize the quarterly risk trend in plain terms") == []


# Text with a sensitive match returns findings.
def test_dirty_text_has_findings():
    assert scan("customer data for account 4111 1111 1111 1111") != []


# Fake keys are assembled at runtime so no key-shaped literal sits in the repo for
# secret scanners to flag.
_BODY = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


# scan() flags each vendor key format, including ones with '-'/'_' after a short prefix.
@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("sk-or-v1-" + "0123456789abcdef" * 4, "openrouter_key"),
        ("sk-ant-api03-" + _BODY, "anthropic_key"),
        ("sk-proj-" + _BODY, "openai_key"),
        ("sk-" + _BODY, "openai_key"),
        ("sk_live_" + _BODY, "stripe_key"),
        ("ghp_" + _BODY + "ab", "github_token"),
        ("github_pat_11" + _BODY, "github_token"),
        ("xoxb-1234-" + _BODY, "slack_token"),
        ("AIza" + _BODY[:35], "google_api_key"),
        ("xai-" + _BODY, "xai_key"),
    ],
)
def test_detects_vendor_keys(text, name):
    assert name in scan(f"use key {text} please")


# An OpenRouter or Anthropic key is reported under its own name, not also as an OpenAI key.
def test_vendor_keys_are_not_double_reported_as_openai():
    assert "openai_key" not in scan("sk-or-v1-" + "0123456789abcdef" * 4)
    assert "openai_key" not in scan("sk-ant-api03-" + _BODY)


# Short or prose-like near misses are not flagged as keys.
@pytest.mark.parametrize("text", ["sk-learn is a library", "the ghp_ prefix", "AIza short"])
def test_ignores_key_near_misses(text):
    assert scan(text) == []


# scan() flags credentials embedded in a URL.
def test_detects_url_credentials():
    assert "url_credentials" in scan("dsn postgres://app:s3cretpw@db.internal:5432/x")


# A URL without a user:password part is not flagged.
def test_ignores_url_without_credentials():
    assert scan("see https://example.com:8080/path") == []


# scan() flags a password or token assignment whose value looks like a secret.
@pytest.mark.parametrize("text", ["password=hunter2hunter2", "API_KEY: 'a1b2c3d4e5f6'"])
def test_detects_secret_assignment(text):
    assert "secret_assignment" in scan(text)


# Prose after "token:" is not a secret: the value must contain a digit.
def test_ignores_secret_assignment_prose():
    assert scan("token: explanation of the design") == []


# A dot-separated card number is still a card.
def test_detects_dotted_payment_card():
    assert "payment_card" in scan("card 4111.1111.1111.1111")


# User-defined patterns are reported by position, never by pattern text.
def test_extra_patterns_reported_by_position():
    extra = compile_extra("project[- ]falcon; CUST-\\d{6}")
    assert scan("status of Project Falcon for CUST-123456", extra) == ["custom_1", "custom_2"]


# Empty entries in PROMPT_EXTRA_PATTERNS are skipped.
def test_extra_patterns_skip_empty_entries():
    assert compile_extra(" ; ;") == ()


# An invalid user regex raises ValueError naming its position, not its text.
def test_extra_patterns_invalid_regex():
    with pytest.raises(ValueError, match="entry 2") as exc:
        compile_extra("ok;secret[")
    assert "secret" not in str(exc.value)


# scan() flags a Bearer token.
def test_detects_bearer_token():
    assert "bearer_token" in scan("Authorization: Bearer abcdefghijklmnop1234")


# scan() flags confidentiality labels in English and Vietnamese.
def test_detects_confidentiality_label_variants():
    for text in (
        "marked RESTRICTED",
        "for internal only use",
        "t\u00e0i li\u1ec7u mật",
        "l\u01b0u h\u00e0nh nội bộ",
    ):
        assert "confidential_label" in scan(text)


# scan() returns every matching finding, not just the first.
def test_scan_returns_multiple_findings():
    findings = scan("CONFIDENTIAL: contact john.doe@example.com about card 4111 1111 1111 1111")
    assert {"confidential_label", "email", "payment_card"} <= set(findings)


# A Luhn-failing digit run before a real card must not hide the card: scan() checks
# every candidate match, not just the first one.
def test_detects_card_after_non_luhn_digit_run():
    assert "payment_card" in scan("order 1234 5678 9012 3456, card 4111 1111 1111 1111")


# A Luhn-failing digit run after a real card is irrelevant to the finding.
def test_detects_card_before_non_luhn_digit_run():
    assert "payment_card" in scan("card 4111 1111 1111 1111, order 1234 5678 9012 3456")


# Separators copied from web pages and PDFs cannot split a card number: no-break space,
# zero-width space, soft hyphen and fullwidth digits are folded before scanning.
@pytest.mark.parametrize(
    "text",
    [
        "card 4111\u00a01111\u00a01111\u00a01111",
        "card 4111\u200b1111\u200b1111\u200b1111",
        "card 4111\u00ad1111\u00ad1111\u00ad1111",
        # Fullwidth digits U+FF10..FF19.
        "card "
        + "4111 1111 1111 1111".translate({ord(d): ord(d) + 0xFF10 - 0x30 for d in "0123456789"}),
    ],
)
def test_detects_card_behind_unicode_separators(text):
    assert "payment_card" in scan(text)


# A zero-width space inside a vendor key does not hide it.
def test_detects_key_split_by_zero_width_space():
    key = "sk-ant-" + "\u200b".join("a" * 30)
    assert "anthropic_key" in scan(key)


# Normalization only adds findings: a raw-text match still counts.
def test_custom_pattern_matches_raw_text():
    assert scan("\uff21BC", compile_extra("\uff21BC")) == ["custom_1"]


# scan() stays linear on long runs that used to make the email, URL and secret patterns
# quadratic (80k chars took 15 s and froze Espanso).
# Explicit ids: a 200k-char node id breaks Windows, where pytest puts it in an env var.
@pytest.mark.parametrize(
    "text",
    ["a." * 100_000, "a-" * 100_000, "Bearer " + "a." * 100_000, "token=" * 30_000],
    ids=["dots", "dashes", "bearer", "assignments"],
)
def test_scan_is_fast_on_adversarial_input(text):
    started = time.perf_counter()
    scan(text)
    assert time.perf_counter() - started < 2
