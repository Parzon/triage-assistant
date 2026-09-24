"""How much does the redactor catch? (app/redact.py; the AI security lab.)

Run by `labs/ai-security/lab redaction`, inside the dev api container.

Two sets of fake credentials, in the shapes alerts and logs carry them, and
ordinary ops text that must come through unchanged:
- the TUNING set: what the patterns were written against;
- the HELD-OUT set: written before the patterns changed, and not looked at
  while changing them. A score on the tuning set shows the patterns fit it;
  only the held-out score says how they do on shapes nobody tuned them to.

Every fake is assembled at runtime from random characters, so this file
holds no string a secret scanner would flag. A secret counts as caught when
no 8-character piece of it survives.
"""

import base64
import random
import string
import sys
import uuid

from app.redact import redact

rng = random.Random(20260924)  # noqa: S311 - seeded on purpose: the same fakes every run
UP, LOW, DIG = string.ascii_uppercase, string.ascii_lowercase, string.digits
ALNUM = UP + LOW + DIG
B64 = ALNUM + "+/"
URLSAFE = ALNUM + "-_"
HEX = "0123456789abcdef"
B32 = UP + "234567"


def r(alphabet: str, n: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(n))


def pw() -> str:
    return r(ALNUM, 9) + rng.choice("!#%&*") + r(DIG, 2)


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def positives() -> list[tuple[str, str, str]]:
    """(name, text, the secret that must not survive)."""
    cases = []

    def add(name: str, template: str, secret: str) -> None:
        cases.append((name, template.replace("{s}", secret), secret))

    add("aws key id (AKIA)", "boto3: InvalidClientTokenId for key {s}", "AKIA" + r(B32, 16))
    add("aws key id (ASIA, temporary)", "assumed-role session {s} expired", "ASIA" + r(B32, 16))
    add("aws secret, credentials file", "aws_secret_access_key = {s}", r(B64, 40))
    add("aws secret, env", "AWS_SECRET_ACCESS_KEY={s}", r(B64, 40))
    add(
        "aws secret, STS json",
        '{"SecretAccessKey": "{s}", "Expiration": "2026-09-24T12:00:00Z"}',
        r(B64, 40),
    )
    add("aws session token, env", "AWS_SESSION_TOKEN={s}", "IQoJb3JpZ2luX2Vj" + r(B64, 280))
    add(
        "github classic token",
        "git push rejected: token {s} lacks workflow scope",
        "ghp_" + r(ALNUM, 36),
    )
    add(
        "github fine-grained token",
        "using token {s} for the release",
        "github_pat_" + r(ALNUM, 22) + "_" + r(ALNUM, 59),
    )
    add("github app token", "installation token {s} issued", "ghs_" + r(ALNUM, 36))
    add(
        "gitlab token",
        "git clone https://oauth2:{s}@gitlab.example.com/ops/infra.git",
        "glpat-" + r(URLSAFE, 20),
    )
    add(
        "slack bot token",
        "slack notify failed with {s}",
        "xoxb-" + r(DIG, 11) + "-" + r(DIG, 13) + "-" + r(ALNUM, 24),
    )
    add(
        "slack webhook url",
        "posting to https://hooks.slack.com/services/T{s}",
        r(UP + DIG, 8) + "/B" + r(UP + DIG, 10) + "/" + r(ALNUM, 24),
    )
    add("openai key", "OpenAI 401 for {s}", "sk-proj-" + r(URLSAFE, 80))
    add(
        "anthropic key",
        "anthropic client configured with {s}",
        "sk-ant-api03-" + r(URLSAFE, 93) + "AA",
    )
    add("stripe live key", "stripe charge failed, key {s}", "sk_live_" + r(ALNUM, 32))
    add("stripe webhook secret", "verifying signatures with {s}", "whsec_" + r(ALNUM, 32))
    add("google api key", "maps quota exceeded for {s}", "AIza" + r(URLSAFE, 35))
    add("google oauth client secret", "client secret {s} rejected", "GOCSPX-" + r(URLSAFE, 28))
    add(
        "gcp service account json",
        '"private_key": "-----BEGIN PRIVATE KEY-----\\n{s}\\n-----END PRIVATE KEY-----\\n"',
        "MIIEv" + r(B64, 200),
    )
    add(
        "pem rsa key",
        "-----BEGIN RSA PRIVATE KEY-----\n{s}\n-----END RSA PRIVATE KEY-----",
        "MIIE" + r(B64, 60) + "\n" + r(B64, 64),
    )
    add(
        "pem openssh key",
        "-----BEGIN OPENSSH PRIVATE KEY-----\n{s}\n-----END OPENSSH PRIVATE KEY-----",
        "b3BlbnNzaC1rZXktdjEAAAAA" + r(B64, 40) + "\n" + r(B64, 64),
    )
    add(
        "pem key, log line cut short",
        "config dump: -----BEGIN RSA PRIVATE KEY----- {s}",
        "MIIE" + r(B64, 120),
    )
    add(
        "jwt bearer",
        "Authorization: Bearer {s}",
        "eyJhbGciOiJSUzI1NiJ9." + r(URLSAFE, 40) + "." + r(URLSAFE, 43),
    )
    add("opaque bearer token", "upstream 401, sent Authorization: Bearer {s}", r(URLSAFE, 40))
    add("basic auth header", "Authorization: Basic {s}", b64("deploy:" + pw()))
    add("curl -u user:password", "curl -u admin:{s} https://grafana.internal/api/health", pw())
    add("postgres url password", "could not connect to postgresql://app:{s}@db:5432/triage", pw())
    add("redis url, empty user", "REDIS_URL=redis://:{s}@redis:6379/0", pw())
    add("amqp url password", "amqp://svc:{s}@rabbitmq:5672/ unreachable", pw())
    add("password in a query string", "GET /login?user=bob&password={s} 302", pw())
    add(
        "azure storage connection string",
        "DefaultEndpointsProtocol=https;AccountName=prodlogs;AccountKey={s};EndpointSuffix=core.windows.net",
        r(B64, 86) + "==",
    )
    add(
        "azure sas signature",
        "403 on https://prodlogs.blob.core.windows.net/c/f.log?sv=2022-11-02&sp=r&se=2026-10-01&sig={s}",
        r(ALNUM, 43) + "%3D",
    )
    add("env DB_PASSWORD", "DB_PASSWORD={s}", pw())
    add("env PGPASSWORD", "PGPASSWORD={s} psql -h db", pw())
    add("env MYSQL_ROOT_PASSWORD", "MYSQL_ROOT_PASSWORD={s}", pw())
    add("env OIDC_CLIENT_SECRET", "OIDC_CLIENT_SECRET={s}", r(ALNUM, 32))
    add("env SECRET_KEY (django)", "SECRET_KEY={s}", r(ALNUM, 40) + "!@#$%")
    add("env DD_API_KEY", "DD_API_KEY={s}", r(HEX, 32))
    add("env GITHUB_TOKEN", "GITHUB_TOKEN={s}", r(ALNUM, 40))
    add("json password", '{"user": "svc", "password": "{s}"}', pw())
    add("json apiKey", '{"apiKey": "{s}", "region": "eu"}', r(ALNUM, 32))
    add("json client_secret", '{"client_id": "web", "client_secret": "{s}"}', r(ALNUM, 32))
    add("yaml db_password", '  db_password: "{s}"', pw())
    add("yaml password", "  password: {s}", b64(pw()))
    add("labelled in prose", "the admin password is {s}", pw())
    add("x-api-key header", "X-Api-Key: {s}", r(ALNUM, 32))
    add("npm token", "npm publish failed for {s}", "npm_" + r(ALNUM, 36))
    add(
        "npmrc auth token",
        "//registry.npmjs.org/:_authToken={s}",
        str(uuid.UUID(int=rng.getrandbits(128), version=4)),
    )
    add("pypi token", "twine upload -p {s}", "pypi-AgEIcHlwaS5vcmc" + r(URLSAFE, 60))
    add("hugging face token", "HF hub 401 with {s}", "hf_" + r(LOW + UP, 34))
    add("sendgrid key", "sendgrid rejected {s}", "SG." + r(URLSAFE, 22) + "." + r(URLSAFE, 43))
    add("docker hub token", "docker login -u ci -p {s}", "dckr_pat_" + r(URLSAFE, 27))
    add("sshpass -p", "sshpass -p '{s}' ssh deploy@10.0.0.5", pw())
    add("mysql -p", "mysql -u root -p{s} -h db", pw())
    return cases


def negatives() -> list[tuple[str, str]]:
    """(name, text that must come through unchanged)."""
    return [
        ("git sha", "deployed commit " + r(HEX, 40)),
        ("image digest", "pulled ghcr.io/parzon/api@sha256:" + r(HEX, 64)),
        ("uuid", "request " + str(uuid.UUID(int=rng.getrandbits(128), version=4)) + " failed"),
        ("trace id", "trace_id=" + r(HEX, 32) + " span_id=" + r(HEX, 16)),
        ("etag", 'etag: "' + r(HEX, 32) + '"'),
        ("pod name", "pod api-7d9f8b6c4-x2k9p OOMKilled"),
        ("arn", "arn:aws:iam::123456789012:role/deploy denied s3:GetObject"),
        ("max_tokens", "LLM request failed: max_tokens: 800 reached"),
        ("token_count", "token_count=1200 over budget"),
        ("tokens per second", "tokens_per_second=42.5"),
        ("metric name", "password_reset_requests_total 17"),
        ("rotation setting", "secret_rotation_days=90"),
        ("prose, vault", "the password is in the vault"),
        ("token: none", "token: none"),
        ("passwordless", "passwordless sign-in enabled for 3 users"),
        ("reference to a secret", "SECRET_NAME=prod/db/credentials"),
        ("vault path", "read secret/platform/break-glass-7731"),
        ("header missing", "Authorization header missing on /api/alerts"),
        ("bearer prose", "Bearer token expired for user 42"),
        (
            "ssh public key",
            "authorized ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQ" + r(B64, 300) + " deploy@ci",
        ),
        (
            "certificate",
            "-----BEGIN CERTIFICATE-----\nMIID" + r(B64, 60) + "\n-----END CERTIFICATE-----",
        ),
        ("url without password", "postgresql://app@db:5432/triage"),
        ("redis url, no auth", "redis://redis:6379/0 connection refused"),
        ("email", "alert routed to oncall@example.com"),
        ("ip and port", "connection from 10.0.3.17:52344 refused"),
        ("base64 payload", "payload=eyJldmVudCI6ImRpc2tfZnVsbCJ9"),
        ("api_key_rotation", "api_key_rotation=enabled"),
        ("json timestamps", '{"password_changed_at": "2026-09-01T10:00:00Z"}'),
        ("oauth response", '{"token_type": "Bearer", "expires_in": 3600}'),
        ("PASSWORD_MIN_LENGTH", "PASSWORD_MIN_LENGTH=12"),
        ("secret: true", "secret: true"),
        (
            "kubectl secret ref",
            "kubectl create secret generic db --from-literal=password=$DB_PASSWORD",
        ),
        ("prometheus alert", "p95 latency 1.2s on checkout"),
        ("disk alert", "disk 95% full on db-1"),
        ("sk- in a word", "task-scheduler-backlog-exceeded-threshold"),
        ("hostname", "sk-prod-eu-west-1-cluster-a unreachable"),
    ]


def heldout_positives() -> list[tuple[str, str, str]]:
    cases = []

    def add(name: str, template: str, secret: str) -> None:
        cases.append((name, template.replace("{s}", secret), secret))

    add("kubectl describe env", "      DATABASE_PASSWORD:  {s}", pw())
    add(
        "docker inspect Env",
        '"Env": ["PATH=/usr/bin", "POSTGRES_PASSWORD={s}", "LANG=C.UTF-8"]',
        pw(),
    )
    add("spring properties", "spring.datasource.password={s}", pw())
    add(".NET connection string", "Server=db;Database=app;User Id=sa;Password={s};", pw())
    add("jdbc url password", "jdbc:postgresql://db:5432/app?user=app&password={s}", pw())
    add(
        "git remote with token",
        "origin https://x-access-token:{s}@github.com/acme/api.git (fetch)",
        "ghs_" + r(ALNUM, 36),
    )
    add("bash -x trace", "+ export API_TOKEN={s}", r(ALNUM, 32))
    add("http_proxy with credentials", "http_proxy=http://proxyuser:{s}@proxy.corp:3128", pw())
    add("redis AUTH command", "redis-cli -a {s} ping", pw())
    add(
        "mongodb uri", "mongodb+srv://app:{s}@cluster0.abcd.mongodb.net/prod?retryWrites=true", pw()
    )
    add("smtp url", "smtp://alerts:{s}@smtp.example.com:587", pw())
    add("helm values", "  adminPassword: {s}", pw())
    add("terraform var", 'db_password = "{s}"', pw())
    add("python dict repr", "{'host': 'db', 'password': '{s}'}", pw())
    add("env lowercase", "export aws_secret_access_key={s}", r(B64, 40))
    add("github token in ci log", "Using GH_TOKEN {s}", "ghp_" + r(ALNUM, 36))
    add("stripe restricted key", "STRIPE_KEY=rk_live_{s}", r(ALNUM, 24))
    add("openai legacy key", "openai.api_key = '{s}'", "sk-" + r(ALNUM, 48))
    add(
        "private key in env, one line",
        "TLS_KEY=-----BEGIN EC PRIVATE KEY-----{s}-----END EC PRIVATE KEY-----",
        "MHcCAQEE" + r(B64, 100),
    )
    add("set-cookie session", "Set-Cookie: session={s}; HttpOnly; Secure", r(URLSAFE, 43))
    add("x-auth-token header", "X-Auth-Token: {s}", r(HEX, 40))
    add("azure client secret", "AZURE_CLIENT_SECRET={s}", r(ALNUM, 8) + "~" + r(URLSAFE, 31))
    add("passphrase", "ssh key passphrase: {s}", pw())
    add("password in yaml list", "- name: DB_PASS\n  value: {s}", pw())
    return cases


def heldout_negatives() -> list[tuple[str, str]]:
    return [
        ("terraform masked", "+ password = (sensitive value)"),
        ("jenkins masked", "docker login -u ci -p ****"),
        ("kubernetes secret ref", "valueFrom: secretKeyRef: name=db key=password"),
        ("password policy", "password must be at least 12 characters"),
        ("token bucket", "rate limiter token bucket refill=10/s"),
        ("csrf missing", "CSRF token missing or incorrect"),
        ("sha1 in url", "https://github.com/acme/api/commit/" + r(HEX, 40)),
        ("docker sha", "Digest: sha256:" + r(HEX, 64)),
        ("request id header", "X-Request-Id: " + r(HEX, 32)),
        ("jwks kid", '{"kid": "' + r(URLSAFE, 43) + '", "kty": "RSA"}'),
        ("s3 url", "s3://acme-prod-backups/db/2026-09-24.dump"),
        ("oauth scope", "scope=openid profile email"),
        ("pagination token", '{"nextToken": null, "items": []}'),
        ("json schema", '{"password": {"type": "string", "minLength": 12}}'),
        ("placeholder", "DB_PASSWORD=<your password here>"),
        ("env reference", "PGPASSWORD=${DB_PASSWORD} psql"),
        ("hash algorithm", "passwords are hashed with argon2id, m=65536,t=3,p=4"),
        ("metric", 'auth_token_refresh_failures_total{provider="keycloak"} 3'),
        ("ci job name", "job secret-scan passed in 42s"),
        ("git branch", "branch fix/token-refresh-race merged"),
    ]


def score(
    label: str, positives: list[tuple[str, str, str]], negatives: list[tuple[str, str]]
) -> None:
    missed = []
    for name, text, secret in positives:
        out, _ = redact(text)
        parts = [p for p in secret.replace("\\n", "\n").split("\n") if p]
        if any(p[i : i + 8] in out for p in parts for i in range(max(1, len(p) - 7))):
            missed.append(name)
    changed = [(name, redact(text)[0]) for name, text in negatives if redact(text)[0] != text]
    print(
        f"{label}: caught {len(positives) - len(missed)} of {len(positives)} secrets; "
        f"changed {len(changed)} of {len(negatives)} ordinary lines"
    )
    for name in missed:
        print(f"  missed:  {name}")
    for name, out in changed:
        print(f"  changed: {name}: {out[:100]}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which in ("tuning", "both"):
        score("tuning set", positives(), negatives())
    if which in ("heldout", "both"):
        score("held-out set", heldout_positives(), heldout_negatives())
