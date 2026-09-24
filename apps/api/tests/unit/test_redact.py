"""Credentials never reach the model's context (app/redact.py).

Token-shaped fakes are assembled at runtime, so this public repository holds
no string a secret scanner would flag."""

import time

import pytest

from app.redact import REDACTED, redact

# Mixed case and digits, as real keys are: sk- keys must be, to be told
# from a host named sk-prod-eu-west-1.
RANDOM = "A1b2C3d4E5f6G7h8J9k0" * 3


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
        (f"sk-proj-{RANDOM} in a log", f"{REDACTED} in a log"),
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
    ("text", "secret"),
    [
        # Named by an identifier that ENDS with the word: the commonest shape
        # in logs, and what the first version missed (its names were whole
        # words: \bpassword never matched PGPASSWORD).
        ("DB_PASSWORD=Tr0ub4dor&3", "Tr0ub4dor&3"),
        ("PGPASSWORD=s3cret-pw psql -h db", "s3cret-pw"),
        ("aws_secret_access_key = " + RANDOM[:40], RANDOM[:40]),
        ("OIDC_CLIENT_SECRET=" + RANDOM[:32], RANDOM[:32]),
        ("+ export API_TOKEN=" + RANDOM[:32], RANDOM[:32]),
        ("DB_PASS=hunter2!", "hunter2!"),
        ("spring.datasource.password=hunter2!", "hunter2!"),
        # Quoted: JSON, YAML, Python, Terraform - a quoted value may hold spaces.
        ('{"user": "svc", "password": "hunter2!"}', "hunter2!"),
        ('{"apiKey": "' + RANDOM[:32] + '"}', RANDOM[:32]),
        ("{'host': 'db', 'password': 'hunter2!'}", "hunter2!"),
        ('db_password = "correct horse battery staple"', "correct horse battery staple"),
        ('"Env": ["POSTGRES_PASSWORD=hunter2!", "LANG=C.UTF-8"]', "hunter2!"),
        ("Server=db;User Id=sa;Password=hunter2!;", "hunter2!"),
        ("AccountName=logs;AccountKey=" + RANDOM + "==;EndpointSuffix=core", RANDOM),
        ("blob.core.windows.net/c/f?sv=2022-11-02&sig=" + RANDOM[:43] + "%3D", RANDOM[:43]),
        ("- name: DB_PASS\n  value: hunter2!", "hunter2!"),
        # By format.
        ("assumed-role session ASIA" + "2" * 16 + " expired", "ASIA" + "2" * 16),
        ("token " + "gh" + "p_" + RANDOM[:36], "gh" + "p_" + RANDOM[:36]),
        ("github_pat_" + RANDOM + "_" + RANDOM, RANDOM),
        ("sk-ant-api03-" + RANDOM + "AA", RANDOM),
        ("sk_" + "live_" + RANDOM[:32], RANDOM[:32]),
        ("AIza" + RANDOM[:35], RANDOM[:35]),
        ("hf_" + "abcdefghijklmnopqrstuvwxyzABCDEFGH", "abcdefghijklmnopqrstuvwxyzABCDEFGH"),
        ("SG." + RANDOM[:22] + "." + RANDOM[:43], RANDOM[:22]),
        (
            '"private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEv'
            + RANDOM
            + '\\n-----END PRIVATE KEY-----\\n"',
            RANDOM,
        ),
        ("dump: -----BEGIN RSA PRIVATE KEY----- MIIE" + RANDOM, RANDOM),  # cut short
        # By where it sits.
        ("REDIS_URL=redis://:hunter2!@redis:6379/0", "hunter2!"),
        ("upstream 401, Authorization: Bearer " + RANDOM[:40], RANDOM[:40]),
        ("Authorization: Basic ZGVwbG95Omh1bnRlcjIh", "ZGVwbG95Omh1bnRlcjIh"),
        ("Set-Cookie: session=" + RANDOM[:43] + "; HttpOnly", RANDOM[:43]),
        ("posting to https://hooks.slack.com/services/T0/B1/" + RANDOM[:24], RANDOM[:24]),
        ("curl -u admin:hunter2! https://grafana.internal/api/health", "hunter2!"),
        ("mysql -u root -phunter2! -h db", "hunter2!"),
        ("sshpass -p 'hunter2!' ssh deploy@10.0.0.5", "hunter2!"),
        ("docker login -u ci -p " + RANDOM[:27], RANDOM[:27]),
        ("redis-cli -a hunter2! ping", "hunter2!"),
    ],
)
def test_secrets_in_the_shapes_logs_carry_them_are_redacted(text: str, secret: str) -> None:
    redacted, count = redact(text)
    assert secret not in redacted
    assert count >= 1


@pytest.mark.parametrize(
    "text",
    [
        "the password is in the vault",  # a word, not a secret
        "what is the admin password?",
        "token: none",
        "disk 95% full on db-1",
        "secret/platform/break-glass-7731",  # a path, not "secret: value"
        "https://example.com/path",
        # The name must END with the word.
        "LLM request failed: max_tokens: 800 reached",
        "token_count=1200 over budget",
        "PASSWORD_MIN_LENGTH=12",
        "SECRET_NAME=prod/db/credentials",
        "api_key_rotation=enabled",
        '{"token_type": "Bearer", "expires_in": 3600}',
        '{"password_changed_at": "2026-09-01T10:00:00Z"}',
        "PWD=/home/deploy OLDPWD=/srv",  # the shell's working directory
        "bypass=1",
        # References, masks and placeholders are not secrets.
        "kubectl create secret generic db --from-literal=password=$DB_PASSWORD",
        "PGPASSWORD=${DB_PASSWORD} psql",
        "docker login -u ci -p ****",
        "DB_PASSWORD=<your password here>",
        "+ password = (sensitive value)",
        '{"password": {"type": "string", "minLength": 12}}',
        # Identifiers that look random but are public.
        "deployed commit 3f2b9c1d8e7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c",
        "trace_id=4bf92f3577b34da6a3ce929d0e0e4736",
        "sk-prod-eu-west-1-cluster-a unreachable",  # a host, not a key
        "Bearer token expired for user 42",
        "Authorization header missing on /api/alerts",
        "postgresql://app@db:5432/triage",
        "-----BEGIN CERTIFICATE-----\nMIIDdzCCAl+gAwIBAgIE\n-----END CERTIFICATE-----",
    ],
)
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert redact(text) == (text, 0)


@pytest.mark.parametrize(
    "unit",
    ["mysql ", "curl ", "docker ", "redis-cli ", "x.", "a-", "sk-", "SG.", "password=", "bearer "],
)
def test_hostile_text_is_redacted_in_linear_time(unit: str) -> None:
    """Alert and runbook text is written by others. A scan restarted at every
    "mysql" or "x." was quadratic: 100,000 characters of "mysql " took
    6.4 s, on the event loop. Linear, the worst of these takes ~0.2 s here."""
    text = unit * (100_000 // len(unit))
    start = time.perf_counter()
    redact(text)
    assert time.perf_counter() - start < 2
