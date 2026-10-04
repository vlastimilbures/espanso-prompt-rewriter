import base64
import time

import pytest

from prompt_workflow.redaction import compile_extra, is_bare_token, safe_repr, scan, scan_draft


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


# scan() flags a JWT (assembled at runtime, like the fake keys below).
def test_detects_jwt():
    token = ".".join(
        [
            "eyJhbGciOiJIUzI1NiJ9",
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0",
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        ]
    )
    assert "jwt" in scan(f"auth header {token}")


# scan() flags PEM, OpenSSH and PGP private key headers.
@pytest.mark.parametrize(
    "kind", ["RSA PRIVATE KEY", "OPENSSH PRIVATE KEY", "PGP PRIVATE KEY BLOCK"]
)
def test_detects_pem_private_key(kind):
    assert "pem_private_key" in scan(f"-----BEGIN {kind}-----\nMIIB...")


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
        ("glpat-" + _BODY[:20], "gitlab_token"),
        ("hf_" + _BODY[:34], "huggingface_token"),
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


# scan() flags a password or token assignment whose value looks like a secret, including
# env-style names with a prefix, JSON keys and the AWS secret key. Values are assembled at
# runtime so no secret-shaped literal sits in the repo.
_VALUE = "a1b2c3" * 2


@pytest.mark.parametrize(
    "name",
    [
        "password=",
        "API_KEY: ",
        "DB_PASSWORD=",
        "client_secret: ",
        "access_token=",
        '"password": ',
        "AWS_SECRET_ACCESS_KEY=",
        "export GITHUB_TOKEN=",
    ],
)
def test_detects_secret_assignment(name):
    assert "secret_assignment" in scan(f"{name}'{_VALUE}'")


# Prose after "token:", or a longer word that merely contains a key name, is not a secret.
@pytest.mark.parametrize(
    "text",
    [
        "token: explanation of the design",
        "tokenizer: bert-base-2",
        "token_count=12345678",
        "max_tokens=24000000",
        "passwords: see policy doc",
    ],
)
def test_ignores_secret_assignment_near_misses(text):
    assert scan(text) == []


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
# zero-width space, soft hyphen, variation selectors, Hangul fillers and fullwidth digits are
# folded before scanning.
@pytest.mark.parametrize(
    "text",
    [
        "card 4111\u00a01111\u00a01111\u00a01111",
        "card 4111\u200b1111\u200b1111\u200b1111",
        "card 4111\u00ad1111\u00ad1111\u00ad1111",
        "card 4111\ufe0f1111\ufe0f1111\ufe0f1111",
        "card 4111\U000e01011111\U000e01011111\U000e01011111",
        "card 4111\u31641111\u31641111\u31641111",
        "card 4111\uffa01111\uffa01111\uffa01111",
        "card 4111\u034f1111\u034f1111\u034f1111",
        "card 4111\u180b1111\u180b1111\u180b1111",
        "card 4111\u17b41111\u17b41111\u17b41111",
        "card 4111\ufff01111\ufff01111\ufff01111",
        "card 4111\U000e02001111\U000e02001111\U000e02001111",
        "card 4111\u28001111\u28001111\u28001111",
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
# quadratic (80k chars took 15 s and froze Espanso). CPU time, not wall time, so a busy
# machine cannot fail it; a quadratic pattern still takes many times the budget.
# Explicit ids: a 200k-char node id breaks Windows, where pytest puts it in an env var.
@pytest.mark.parametrize(
    "text",
    [
        "a." * 100_000,
        "a-" * 100_000,
        "Bearer " + "a." * 100_000,
        "token=" * 30_000,
        "_token='" * 30_000,
        "-----BEGIN " + "A " * 100_000,
        "4\ufe0f" * 100_000,
        "curl " * 40_000,
        "curl -u " * 25_000,
        "password is " * 16_000,
        "m\u1eadt kh\u1ea9u " * 20_000,
        "Basic " + "A" * 200_000,
        "1\n" * 100_000,
        "1  " * 66_000,
        "1\u2013" * 100_000,
        "CZ65" + " 0" * 100_000,
        "aToken" * 30_000,
        "&sig=" * 40_000,
        "AccountKey=" * 20_000,
        "password a " * 20_000,
        "m\u1eadt kh\u1ea9u a " * 20_000,
        "password" + " " * 200_000,
        "'password' => " * 15_000,
        "curl \\\n" * 40_000,
        "CZ65-0-" * 30_000,
        "1/" * 100_000,
        "aPASSWORD" * 20_000,
        "-u a:b " * 30_000,
        "curl\n-u a:b\n" * 17_000,
        "curlx -u a:b " * 16_000,
    ],
    ids=[
        "dots",
        "dashes",
        "bearer",
        "assignments",
        "prefixed-assignments",
        "pem",
        "selectors",
        "curl",
        "curl-u",
        "password-is",
        "mat-khau",
        "basic",
        "newline-digits",
        "double-spaced-digits",
        "en-dashed-digits",
        "iban",
        "camel-case",
        "sas",
        "azure",
        "password-words",
        "mat-khau-words",
        "assignment-spaces",
        "php-arrows",
        "curl-continuations",
        "iban-dashes",
        "slashed-digits",
        "compound-names",
        "user-options",
        "curl-then-options",
        "not-curl-options",
    ],
)
def test_scan_is_fast_on_adversarial_input(text):
    started = time.process_time()
    scan_draft(text)
    assert time.process_time() - started < 2


# Error messages repeat a short, harmless value; anything that could be a secret is described.
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("abc", "'abc'"),
        ("", "''"),
        ("x" * 40, repr("x" * 40)),
        ("x" * 41, "<redacted, 41 chars>"),
        ("jane.doe@example.com", "<redacted, 20 chars>"),
        ("4111 1111 1111 1111", "<redacted, 19 chars>"),
        ("a=b", "<redacted, 3 chars; two .env lines may have run together>"),
    ],
)
def test_safe_repr(raw, expected):
    assert safe_repr(raw) == expected


# Gate corpus (#20, #21). Every value is fake; anything key-shaped is assembled at runtime.
_B64_KEY = base64.b64encode(b"not-a-real-storage-account-key").decode()
_BASIC = base64.b64encode(b"admin:" + b"Winter2026").decode()
_PW = "Winter" + "2026!"
_FAKE = "Zq81" + "Lm0Pw9x2"
# Kept apart so a secret scanner does not read the test commands as real credentials.
_HTTP_CLI = "curl"

# Secret shapes that the gate must flag, with the finding each must produce.
MUST_DETECT = [
    pytest.param(f"SECRET_KEY = 'django-insecure-{_FAKE}'", "secret_assignment", id="django"),
    pytest.param(f"secret_key_base: {_FAKE}", "secret_assignment", id="rails"),
    pytest.param(f'{{"clientSecret": "Xy7~{_FAKE}"}}', "secret_assignment", id="json-camel"),
    pytest.param(f'{{"accessToken": "{_FAKE}"}}', "secret_assignment", id="access-token"),
    pytest.param(f"refreshToken={_FAKE}", "secret_assignment", id="refresh-token-camel"),
    pytest.param(f"const dbPassword = '{_FAKE}';", "secret_assignment", id="js-camel"),
    pytest.param(f"PRIVATE_KEY=0x{_FAKE}", "secret_assignment", id="private-key"),
    pytest.param(f"ENCRYPTION_KEY={_FAKE}", "secret_assignment", id="encryption-key"),
    pytest.param(f"signingKey: '{_FAKE}'", "secret_assignment", id="signing-key"),
    pytest.param("password: hunter2", "secret_assignment", id="short-password"),
    pytest.param('{"pwd": "abc123x"}', "secret_assignment", id="json-pwd"),
    pytest.param("//registry.npmjs.org/:_authToken=" + "npm_" + _BODY, "npm_token", id="npmrc"),
    pytest.param(f"//registry.npmjs.org/:_authToken={_FAKE}", "secret_assignment", id="auth"),
    pytest.param(f"the admin password is {_PW}", "password_value", id="prose-password"),
    pytest.param("password was abc123def", "password_value", id="prose-password-was"),
    pytest.param("mật khẩu: Hanoi2026x", "password_value", id="vn-password"),
    pytest.param("mật khẩu là Saigon2026", "password_value", id="vn-password-la"),
    pytest.param("aws_access_key_id = " + "ASIA" + "Z7EXAMPLE1234567", "aws_access_key", id="asia"),
    pytest.param(f"Authorization: Basic {_BASIC}", "basic_auth", id="basic-auth"),
    pytest.param(
        f"{_HTTP_CLI} -u admin:{_PW} https://api.example.com",
        "curl_user",
        id="dash-u-user-password",
    ),
    pytest.param(
        f"{_HTTP_CLI} -X POST --user=deploy:{_FAKE} https://x.example.com", "curl_user", id="curl"
    ),
    pytest.param(
        f"DefaultEndpointsProtocol=https;AccountName=x;AccountKey={_B64_KEY};",
        "azure_key",
        id="azure-connection-string",
    ),
    pytest.param(f"Endpoint=sb://x/;SharedAccessKey={_B64_KEY}", "azure_key", id="service-bus"),
    pytest.param("https://x.blob.core.windows.net/c?sv=2022&sig=" + _BODY, "azure_key", id="sas"),
    pytest.param("4111  1111  1111  1111", "payment_card", id="card-double-space"),
    pytest.param("4111\u20131111\u20131111\u20131111", "payment_card", id="card-en-dash"),
    pytest.param("4111\t1111\t1111\t1111", "payment_card", id="card-tab"),
    pytest.param("4111 1111\n1111 1111", "payment_card", id="card-wrapped"),
    pytest.param("card 4111 1111 1111 1111\n2. next item", "payment_card", id="card-then-list"),
    pytest.param("ref 12 4111 1111 1111 1111", "payment_card", id="card-after-number"),
    pytest.param("CZ65 0800 0000 1920 0014 5399", "iban", id="iban-cz"),
    pytest.param("DE89 3704 0044 0532 0130 00", "iban", id="iban-de"),
    pytest.param("pay to GB29NWBK60161331926819 today", "iban", id="iban-gb-compact"),
    pytest.param(_PW + "x", "bare_token", id="bare-password"),
    pytest.param("xK9mQ2vL8pR4tW7nB3cF6hJ1", "bare_token", id="bare-token"),
    pytest.param("P@ss" + "w0rd", "bare_token", id="bare-short-password"),
    pytest.param("  " + _PW + "x\n", "bare_token", id="bare-password-padded"),
    pytest.param("Winter" + "2026", "bare_token", id="bare-word-and-year"),
    pytest.param("Passw" + "0rd", "bare_token", id="bare-leet"),
    pytest.param("Tiger-Lily-" + "2026", "bare_token", id="bare-dashed-words"),
    pytest.param("Blue_Moon_" + "77", "bare_token", id="bare-underscored-words"),
    pytest.param(
        "wJalrXUtnFEMI/K7MDENG/" + "bPxRfiCYEXAMPLEKEY", "bare_token", id="bare-aws-secret"
    ),
    pytest.param("/Summer" + "2026x!", "bare_token", id="bare-leading-slash"),
    pytest.param("xK9mQ2vL8pR4." + "tW", "bare_token", id="bare-dotted"),
    pytest.param("P@ss!." + "word1", "bare_token", id="bare-at-sign"),
    # Config shapes: PHP, Go, WordPress, Python, compound env names, aligned values.
    pytest.param(f"'password' => '{_FAKE}',", "secret_assignment", id="php-arrow"),
    pytest.param(f"'api_key' => '{_FAKE}',", "secret_assignment", id="php-arrow-key"),
    pytest.param(f'password := "{_FAKE}"', "secret_assignment", id="go-walrus"),
    pytest.param(f"define( 'DB_PASSWORD', '{_FAKE}' );", "secret_assignment", id="wp-define"),
    pytest.param(f'os.environ["API_KEY"] = "{_FAKE}"', "secret_assignment", id="py-environ"),
    pytest.param(f"PGPASSWORD={_FAKE} psql -h db", "secret_assignment", id="pgpassword"),
    pytest.param(f"set dbpassword={_FAKE} in the file", "secret_assignment", id="lower-compound"),
    pytest.param(f"password:{' ' * 20}{_FAKE}", "secret_assignment", id="aligned-value"),
    pytest.param(f"password is: {_FAKE}", "password_value", id="prose-is-colon"),
    pytest.param(
        f"the password for the admin account is {_FAKE}", "password_value", id="prose-words"
    ),
    pytest.param("mật khẩu đăng nhập: Hanoi2026", "password_value", id="vn-login-password"),
    pytest.param("mật khẩu wifi là Hanoi2026", "password_value", id="vn-wifi-password"),
    pytest.param("mật khẩu của tôi là Hanoi2026", "password_value", id="vn-my-password"),
    pytest.param(
        f"{_HTTP_CLI} https://x.example.com \\\n  -u admin:{_FAKE}", "curl_user", id="continued"
    ),
    pytest.param("cz65 0800 0000 1920 0014 5399", "iban", id="iban-lower"),
    pytest.param("CZ65-0800-0000-1920-0014-5399", "iban", id="iban-dashes"),
    pytest.param(
        "CZ65 0800 0000 1920 0014 5398 DE89 3704 0044 0532 0130 00", "iban", id="iban-after-bad"
    ),
    pytest.param("RU02 0445 2560 0407 0281 0412 3456 7890 1", "iban", id="iban-ru"),
    pytest.param("4111/1111/1111/1111", "payment_card", id="card-slash"),
    pytest.param("4111\u22121111\u22121111\u22121111", "payment_card", id="card-minus"),
    pytest.param("3782 822463 10005", "payment_card", id="card-amex"),
    pytest.param(
        "Basic " + base64.b64encode("jos\u00e9:contrase\u00f1a1".encode()).decode(),
        "basic_auth",
        id="basic-utf8",
    ),
    pytest.param("Basic " + base64.b64encode(b"a:b").decode(), "basic_auth", id="basic-short"),
]

# Ordinary drafts the gate must let through. The xfail rows are #21's false positives.
_FP_21 = pytest.mark.xfail(strict=True, reason="#21: over-broad label and ID patterns")
MUST_PASS = [
    pytest.param("Output restricted to 5 bullets.", id="restricted", marks=_FP_21),
    pytest.param(
        "Draft an NDA template that defines what counts as confidential information.",
        id="nda",
        marks=_FP_21,
    ),
    pytest.param(
        "Write a script that deduplicates customer data from two CRMs.",
        id="customer-data",
        marks=_FP_21,
    ),
    pytest.param(
        "Viết hướng dẫn bảo mật cho nhân viên mới.",
        id="vn-security",
        marks=_FP_21,
    ),
    pytest.param(
        "Phân tích mật độ dân số Hà Nội.",
        id="vn-density",
        marks=_FP_21,
    ),
    pytest.param("Công thức trà mật ong gừng.", id="vn-honey", marks=_FP_21),
    pytest.param(
        "Viết email nhắc nhân viên đổi mật khẩu mỗi 90 ngày.",
        id="vn-password-prose",
        marks=_FP_21,
    ),
    pytest.param(
        "Fix this policy for arn:aws:iam::123456789012:role/Deploy", id="arn", marks=_FP_21
    ),
    pytest.param(
        "Customer asks about order 202410041234, draft a reply.", id="order-12", marks=_FP_21
    ),
    pytest.param("Bug ID: ticket 123456789 crashes on login", id="bug-id-9", marks=_FP_21),
    pytest.param(
        "Tóm tắt hợp đồng số 123456789 cho sếp.",
        id="vn-contract-9",
        marks=_FP_21,
    ),
    pytest.param("123e4567-e89b-12d3-a456-426614174000", id="uuid", marks=_FP_21),
    pytest.param("the model has a token count of 128000 tokens", id="token-count"),
    pytest.param("Write a policy explaining why passwords must be 12 characters.", id="pw-policy"),
    pytest.param("Explain how password: hashing with bcrypt works", id="pw-colon-prose"),
    pytest.param("password is at least 12 characters long", id="pw-rule"),
    pytest.param("Basic understanding of Kubernetes is enough", id="basic-prose"),
    pytest.param("Basic authentication should be replaced by OAuth2", id="basic-auth-prose"),
    pytest.param("curl -u admin https://api.example.com", id="curl-prompts-for-password"),
    pytest.param('curl -u "$USER:$PASS" https://api.example.com', id="curl-env-vars"),
    pytest.param("set maxTokens=24000000 in the config", id="max-tokens-camel"),
    pytest.param("tokenCount: 12345678", id="token-count-camel"),
    pytest.param("api_key_v2_rotation", id="snake-case"),
    pytest.param("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", id="hash"),
    pytest.param("https://example.com/path?q=1", id="url"),
    pytest.param("~/Projects/app/src/main.py", id="path"),
    pytest.param("C:\\Users\\me\\report2026.docx", id="windows-path"),
    pytest.param("openai/gpt-4o-mini", id="slug"),
    pytest.param("v2.10.3-rc1", id="semver"),
    pytest.param("2026-10-04T12:00:00Z", id="iso-date"),
    pytest.param("report2026.xlsx", id="file-name"),
    pytest.param("db01.internal:5432", id="host-port"),
    pytest.param("Summarize!", id="one-word"),
    pytest.param("call +420 777 123 456 tomorrow", id="phone"),
    pytest.param("2021\n2022\n2023\n2024", id="years-on-lines"),
    # Luhn-valid only when read across three line breaks: a card wraps over one at most.
    pytest.param("4111\n1111\n1111\n1111", id="digit-groups-on-lines"),
    pytest.param("ISBN 978-3-16-148410-0", id="isbn"),
    pytest.param("PL2024Q3 budget review", id="code-like-word"),
    pytest.param("Contacts:\n0927 138 188\n0955 528 171", id="phone-list"),
    pytest.param("Gọi:\n0912 345 678\n0987 654 321\n0903 111 222", id="vn-phone-list"),
    pytest.param("Deadlines: 4.10.2024 5.11.2024 6.12.2025", id="dates"),
    pytest.param("Sprints: 1.10.2025 15.10.2025 29.10.2025", id="sprint-dates"),
    pytest.param("Explain why the password is base64-encoded in the Basic header.", id="pw-b64"),
    pytest.param("Write docs: the password is SHA256-hashed with a salt.", id="pw-hashed"),
    pytest.param("The new password is 6-digit, numeric only.", id="pw-6-digit"),
    pytest.param("The password was bcrypt2-hashed before 2020.", id="pw-bcrypt"),
    pytest.param(
        "Hướng dẫn đổi mật khẩu Office365 cho nhân viên mới.", id="vn-pw-product", marks=_FP_21
    ),
    pytest.param("maxToken: 16384000", id="max-token-count"),
    pytest.param("Rename variable masterKeyId: 12345678", id="key-id"),
    pytest.param("jwks signing_key_id: kid-2024-10-04", id="signing-key-id"),
    pytest.param("meta-llama/llama-3.1-8b-instruct", id="model-slug"),
    pytest.param("notes2026.md", id="file-name-lower"),
]


@pytest.mark.parametrize(("text", "name"), MUST_DETECT)
def test_gate_corpus_detects(text, name):
    assert name in scan_draft(text)


@pytest.mark.parametrize("text", MUST_PASS)
def test_gate_corpus_passes(text):
    assert scan_draft(text) == []


# The camelCase name boundary is case-sensitive: MYAPIKEY is one word, not MY + APIKEY, even
# though the pattern ignores case. Names that end a compound env name (PASSWORD, TOKEN,
# SECRET) match anywhere, but not as the start of a longer word.
def test_secret_name_boundaries():
    assert "secret_assignment" not in scan("rename MYAPIKEY=abc12345678 to X")
    assert "secret_assignment" in scan("rename myApiKey=abc12345678 to X")
    assert "secret_assignment" in scan("rename MYTOKEN=abc12345678 to X")
    assert "secret_assignment" not in scan("rename MYTOKENIZER=abc12345678 to X")


# bare_token is a whole-draft rule: a password-like word inside a sentence is left to the
# other patterns, and only the gate applies it (safe_repr still quotes a short setting value).
def test_bare_token_is_whole_draft_only():
    assert not is_bare_token(f"my vault entry {_PW}x is old")
    assert is_bare_token(_PW + "x")
    assert scan(_PW + "x") == []
    assert safe_repr(_PW + "x") == repr(_PW + "x")


# Single words that are too short, too long or of one character class are not tokens.
@pytest.mark.parametrize(
    "text", ["Ab1!x", "A" * 201 + "b1!", "abcdefgh1", "ABCD-1234", "Abcdefghij", "abc_def!x"]
)
def test_bare_token_needs_length_and_classes(text):
    assert not is_bare_token(text)


# An IBAN needs its country's length and a valid checksum; a broken one or an unknown
# country is not flagged.
@pytest.mark.parametrize(
    "text", ["CZ65 0800 0000 1920 0014 5398", "QQ65 0800 0000 1920 0014 5399", "DE89 3704 0044"]
)
def test_iban_needs_checksum_and_length(text):
    assert "iban" not in scan(text)


# Basic credentials that are not valid base64 or do not decode to user:password are ignored.
@pytest.mark.parametrize(
    "text",
    [
        "Basic " + base64.b64encode(b"no colon here").decode(),
        "Basic " + base64.b64encode(b"\xff\xfe:binary").decode(),
        "Basic abc=defgh",
    ],
)
def test_basic_auth_needs_user_password(text):
    assert "basic_auth" not in scan(text)


# -u belongs to curl only on the same logical line; a later line or another command is not curl.
@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        (f"{_HTTP_CLI} https://x \\\n  -H 'a: b' \\\n  -u admin:{_FAKE}", True),
        (f"{_HTTP_CLI} https://x\nssh -u admin:{_FAKE}", False),
        (f"curly -u admin:{_FAKE}", False),
        (f"tar -u admin:{_FAKE}", False),
    ],
    ids=["continued-twice", "next-line", "other-word", "other-command"],
)
def test_curl_user_needs_the_curl_command(text, flagged):
    assert ("curl_user" in scan(text)) == flagged
