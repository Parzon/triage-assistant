"""Credentials out of the model's context, before it sees them.

Systems print secrets into alerts ("login failed, password=..."), and
anyone who can send an alert can plant one ("the admin password is ...").
The prompt tells the model never to repeat them: measured with
gpt-oss:20b, that rule fails about 1 run in 100
(injection-fake-conversation, 2 in 200). Redacting them first is a
deterministic floor under it: the model cannot repeat what it never saw,
and the secret never leaves for the model provider (OWASP LLM02).

Only what goes to a model changes: the prompt (alerts, runbook sections,
the question) and the text sent for embedding. The alert list and the
stored runbooks keep the text as it was sent. Patterns, not a guarantee -
an unknown secret format passes through, and the prompt's rule remains.

Three kinds of pattern, because a secret is recognisable three ways:
- by its name: DB_PASSWORD=..., "client_secret": "...", X-Api-Key: ...;
- by its format: a GitHub token, an AWS key id, a private key;
- by where it sits: a URL's user:password@, an Authorization header, a
  password on a command line.
Measured on 54 secrets in the shapes alerts and logs carry them (the AI
security chapter): the first version - names as whole words, six formats,
URL passwords - caught 17. Most secrets in logs are named by an identifier
that ENDS with the word (PGPASSWORD, AWS_SECRET_ACCESS_KEY), or quoted.
This one catches all 54, and 21 of 24 held-out shapes it was not written
against (the three it missed are covered since: redis-cli -a, cookies, a
Kubernetes env entry).
"""

import re

REDACTED = "[redacted]"

# An identifier names a secret when it ends with one of these. Ending
# matters: max_tokens, token_count and PASSWORD_MIN_LENGTH name no secret.
# pass and sig must be the whole name or follow a separator (DB_PASS, and
# &sig=, an Azure SAS signature; not bypass), and pwd must follow one
# (DB_PWD): the shell's own PWD=/home/... is a directory.
_NAME = (
    r"(?:password|passwd|passphrase|secret|token"
    r"|api[_-]?key|(?:access|secret|private|account|master|signing|encryption)[_-]?key"
    r"|(?<![a-z0-9])(?:pass|sig)|(?<=[_.-])pwd)"
)
_LABELLED = re.compile(
    # The name starts where an identifier starts, and ends at the separator.
    rf"(?i)(?<![\w.-])[\w.-]*?{_NAME}"
    # "is" for prose; = : => for env files, JSON, YAML, headers, code.
    r"(?:[\"']?\s*(?:=>|[:=])\s*|\s+is\s+)"
    # A bare value runs to a space, a comma or a semicolon; & can be part of
    # a password, so a query string's next parameter goes with it.
    r"(?:\"(?P<dq>[^\"\n]*)\"|'(?P<sq>[^'\n]*)'|(?P<bare>[^\s,;\"'`]+))"
)

# Recognisable by format alone.
_FORMATS = (
    re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}\b"),  # AWS access key id
    # OpenAI, Anthropic: sk-...; long, with an upper-case letter and a digit,
    # so a host named sk-prod-eu-west-1 is left alone.
    re.compile(r"\bsk-(?=[\w-]{0,256}\d)(?=[\w-]{0,256}[A-Z])[\w-]{32,}"),
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"),  # Stripe key
    re.compile(r"\bwhsec_[A-Za-z0-9+/=]{24,}"),  # Stripe webhook secret
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),  # GitHub token
    re.compile(r"\bgithub_pat_\w{50,}"),  # GitHub fine-grained token
    re.compile(r"\bglpat-[\w-]{20,}"),  # GitLab token
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack token
    re.compile(r"\bAIza[\w-]{35}"),  # Google API key
    re.compile(r"\bGOCSPX-[\w-]{20,}"),  # Google OAuth client secret
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),  # npm token
    re.compile(r"\bpypi-AgEIcHlwaS5vcmc[\w-]{50,}"),  # PyPI token
    re.compile(r"\bhf_[A-Za-z]{30,}\b"),  # Hugging Face token
    re.compile(r"\bSG\.[\w-]{16,}\.[\w-]{16,}"),  # SendGrid key
    re.compile(r"\bdckr_pat_[\w-]{20,}"),  # Docker Hub token
    re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"),  # a JWT
    # A private key: its header and base64 body (with literal \n, as in
    # JSON), with its END line when there is one - a log line can cut it off.
    re.compile(
        r"-----BEGIN ([A-Z ]*)PRIVATE KEY-----(?:[A-Za-z0-9+/=\s]|\\n)*"
        r"(?:-----END \1PRIVATE KEY-----)?"
    ),
)

# Recognisable by where they sit: group 1 stays, the rest is the secret.
# Every scan is bounded ({0,200}, {0,31}): alert and runbook text is written
# by others, and an unbounded scan that restarts at each "mysql" or "x." is
# quadratic - measured, 100,000 characters of "mysql " took 6.4 s before
# the bound, on the event loop.
_CONTEXTS = (
    # user:password@ in a URL; the user may be empty (redis://:pw@host).
    re.compile(r"(?i)\b([a-z][a-z0-9+.-]{0,31}://[^\s:@/]*:)[^\s@/]+(?=@)"),
    re.compile(r"(?i)\b(hooks\.slack\.com/services/)[A-Za-z0-9/]+"),  # Slack webhook
    re.compile(r"(?i)\b(authorization\s*[:=]\s*[\"']?(?:bearer|basic|token)?\s*)[^\s\"',;]{8,}"),
    re.compile(r"(?i)\b(bearer\s+)(?=[\w.~+/-]*\d)[\w.~+/-]{20,}=*"),
    # A cookie header is a session: the rest of its line goes.
    re.compile(r"(?i)\b((?:set-)?cookie:\s*)[^\n]+"),
    # A Kubernetes env entry: the name on one line, the value on the next.
    re.compile(rf"(?i)(\bname:\s*[\"']?[\w.-]*?{_NAME}[\"']?[ \t]*\n\s*value:\s*[\"']?)[^\s\"']+"),
    # Passwords on command lines.
    re.compile(r"\b(curl\b[^\n]{0,200}?\s(?:-u|--user)\s+[\"']?[^\s:\"']+:)[^\s\"']+"),
    re.compile(r"(?i)(--password[=\s]\s*[\"']?)[^\s\"']+"),
    re.compile(r"\b(sshpass\s+-p\s*[\"']?)[^\s\"']+"),
    re.compile(r"\b(redis-cli\b[^\n]{0,200}?\s-a\s+[\"']?)[^\s\"']+"),
    re.compile(r"\b((?:mysql|mysqldump|mysqladmin|mariadb)\b[^\n]{0,200}?\s-p)(?=\S)[^\s\"']+"),
    re.compile(r"\b((?:docker|podman|helm|twine)\b[^\n]{0,200}?\s-p\s+[\"']?)[^\s\"']+"),
)


def _looks_secret(value: str) -> bool:
    """A value after a secret's name is one when it looks like one: a digit
    or a symbol, or long - so "the password is in the vault" keeps its
    words. Masks (****) and references ($DB_PASSWORD, ${...}, {{ ... }},
    <your password>, (sensitive value)) are not secrets."""
    if not value or value[0] in "$%{<([" or set(value) <= {"*", "x", "X", "."}:
        return False
    return len(value) >= 12 or not value.isalpha()


def redact(text: str) -> tuple[str, int]:
    """The text with credentials replaced, and how many were."""
    count = 0

    def keep_prefix(match: re.Match[str]) -> str:
        nonlocal count
        if not _looks_secret(match[0][len(match[1]) :]):
            return match[0]
        count += 1
        return match[1] + REDACTED

    def whole(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return REDACTED

    def labelled(match: re.Match[str]) -> str:
        nonlocal count
        group = next(g for g in ("dq", "sq", "bare") if match[g] is not None)
        value = match[group]
        if group == "bare":
            value = value.rstrip(".")  # a sentence's full stop is not the secret's
        if not _looks_secret(value):
            return match[0]
        count += 1
        start = match.start(group) - match.start()
        return match[0][:start] + REDACTED + match[0][start + len(value) :]

    for pattern in _CONTEXTS:
        text = pattern.sub(keep_prefix, text)
    for pattern in _FORMATS:
        text = pattern.sub(whole, text)
    return _LABELLED.sub(labelled, text), count
