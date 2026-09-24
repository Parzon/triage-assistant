"""Credentials out of the model's context, before it sees them.

Systems print secrets into alerts ("login failed, password=..."), and
anyone who can send an alert can plant one ("the admin password is ...").
The prompt tells the model never to repeat them: measured with
gpt-oss:20b, that rule fails about 1 run in 100
(injection-fake-conversation, 2 in 200). Redacting them first is a
deterministic floor under it: the model cannot repeat what it never saw,
and the secret never leaves for the model provider (OWASP LLM02).

Only the prompt's copy changes: the alert list shows alerts as they were
sent. Patterns, not a guarantee - an unknown secret format passes through,
and the prompt's rule remains.
"""

import re

REDACTED = "[redacted]"

# A value after "password is", "token:", "api_key=" counts as a secret when it
# looks like one: a digit or a symbol, or long - so "the password is in the
# vault" keeps its words.
_LABELLED = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_ -]?key)(\s*(?:is|=|:)\s*)"
    # The lookahead stays inside the value: a symbol of its own, not the
    # space after it. The value never ends with a sentence's full stop.
    r"(?=[^\s,;]*[0-9_\-!@#$%^&*+=/\\]|[^\s,;]{12,})([^\s,;]*[^\s,;.])"
)
_FORMATS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),  # OpenAI-style secret key
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),  # GitHub token
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack token
    re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"),  # a JWT
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)
# user:password@ in a connection URL: the password goes, the rest stays.
_URL_PASSWORD = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:@/]+:)[^\s@/]+@")


def redact(text: str) -> tuple[str, int]:
    """The text with credentials replaced, and how many were."""
    count = 0

    def labelled(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match[1]}{match[2]}{REDACTED}"

    def whole(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return REDACTED

    def url(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match[1]}{REDACTED}@"

    text = _LABELLED.sub(labelled, text)
    for pattern in _FORMATS:
        text = pattern.sub(whole, text)
    return _URL_PASSWORD.sub(url, text), count
