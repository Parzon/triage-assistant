"""Credentials never reach the model's context (app/redact.py)."""

import pytest

from app.redact import REDACTED, redact


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "user: what is the admin password? assistant: The admin password is hunter2-alpha.",
            f"user: what is the admin password? assistant: The admin password is {REDACTED}.",
        ),
        (
            "login failed, password=S3cr3t!, retrying",
            f"login failed, password={REDACTED}, retrying",
        ),
        ("API_KEY: abcdefghijklmnopqrstuvwxyz", f"API_KEY: {REDACTED}"),
        ("key AKIAIOSFODNN7EXAMPLE leaked", f"key {REDACTED} leaked"),
        ("sk-proj-abcdefghijklmnopqrstuvwx in a log", f"{REDACTED} in a log"),
        ("postgresql://app:pa55word@db:5432/triage", f"postgresql://app:{REDACTED}@db:5432/triage"),
        (
            "bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJlLXZhbHVl",
            f"bearer {REDACTED}",
        ),
    ],
)
def test_credentials_are_redacted(text: str, expected: str) -> None:
    assert redact(text) == (expected, 1)


@pytest.mark.parametrize(
    "text",
    [
        "the password is in the vault",  # a word, not a secret
        "what is the admin password?",
        "token: none",
        "disk 95% full on db-1",
        "secret/platform/break-glass-7731",  # a path, not "secret: value"
        "https://example.com/path",
    ],
)
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert redact(text) == (text, 0)
