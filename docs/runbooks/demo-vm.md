# Runbook: the stack on one VM (demo, staging, pilot)

For showing the product to a manager or a customer, running a pilot, or a
shared staging host. One VM runs the production-shaped stack: the same
images, limits and nginx as production. This is not high availability; the
single points of failure are listed in the failure-modes chapter.

✅ = done in this repo (the step or the whole path was exercised), 📘 =
the procedure for a real cloud VM, not exercised here: no VM was created
for this repo.

## 1. What to ask IT for

Copy, fill in the brackets, send:

> **VM request: triage-assistant [demo | staging | pilot]**
> - **OS:** Ubuntu Server 24.04 LTS, x86_64
> - **Size:** 4 vCPU, 8 GB RAM, 40 GB SSD (2 vCPU / 4 GB is the minimum without the monitoring stack)
> - **Inbound:** TCP 443 and 80 from [the audience: office range / VPN / internet]; TCP 22 from [our VPN / bastion only]
> - **Outbound:** TCP 443 to ghcr.io and pkg-containers.githubusercontent.com (our images), registry-1.docker.io, auth.docker.io and production.cloudflare.docker.com (Docker Hub), download.docker.com and the Ubuntu mirrors (packages), github.com (code), and [the LLM provider's API host]
> - **DNS:** [triage-demo.example.com] → the VM's public IP (needed for a TLS certificate)
> - **Access:** my SSH public key for the user `deploy` (attached)
> - **Backups:** a daily disk snapshot kept 7 days; the database is also dumped daily (step 7)
> - **User data:** attached `cloud-init.yaml` (installs Docker, the firewall and the app)
> - **Lifetime:** [until DATE], then delete

Why those numbers, measured on this stack:
- **Memory:** the whole stack with monitoring idles at ~880 MiB. Its
  memory *limits* add up to ~5 GiB: api 1 GiB, Postgres 1 GiB,
  Prometheus 1 GiB, Grafana 512 MiB, the rest 128–256 MiB each. 8 GB
  leaves the OS and the page cache room.
- **CPU:** the api is capped at 2 CPUs and Postgres at 2. On 2 api CPUs
  the lab measured ~1,000 simple reads/s and 500 concurrent streams
  before saturating. A demo is far below either.
- **Disk:** images are 2.9 GB (Grafana alone 1.4 GB). Logs are capped per
  container (3 × 10 MB), Prometheus at 2 GB, and 14 daily dumps of 2 M
  alerts are ~1 GB (73 MB each). 40 GB is comfortable.
- **Outbound hosts:** image pulls are HTTPS to the registries. Without
  them, images must be built on the VM from the checkout, which needs
  Docker Hub, PyPI and npm instead.

## 2. Bootstrap the VM

**With cloud-init (📘).** Put your SSH public key into
`infra/vm/cloud-init.yaml` and pass the file as the VM's user data. On
first boot it:
- installs Docker Engine and the compose plugin from Docker's apt
  repository;
- creates the `deploy` user;
- enables the firewall (22, 80, 443) and automatic security updates;
- keeps container events in journald;
- clones the repository to `/srv/triage-assistant` with a `.env` whose
  passwords are generated.

The file is validated against cloud-init's schema (✅ `cloud-init schema`,
cloud-init 26.1). `cloud-init status --wait` on the VM tells you when it
has finished.

**By hand (📘)** — the same steps, when user data is not an option:

```
# Docker from Docker's repository: https://docs.docker.com/engine/install/ubuntu/
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin make git jq
sudo usermod -aG docker deploy              # root-equivalent: guard this account's key
sudo ufw default deny incoming && sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw --force enable
git clone https://github.com/Parzon/triage-assistant.git /srv/triage-assistant
cd /srv/triage-assistant && cp .env.example .env && chmod 600 .env   # then edit the secrets
```

The firewall caveat: Docker's published ports bypass ufw. Docker rewrites
the destination in the NAT table before ufw's rules run. Here that's
safe, because `compose.prod.yaml` publishes only nginx and binds the
dashboards to 127.0.0.1. Anything you add with `ports:` is reachable even
if ufw says otherwise. See the networking chapter.

## 3. Configure `.env`

Every secret must differ from `.env.example`. cloud-init already
generated:
- the database passwords, the Grafana password and the webhook token;
- the identity provider's client secret, the Keycloak administrator's
  password and the demo users' password.

The rest:

| Setting | Demo without a model provider | With a provider |
|---|---|---|
| `COMPOSE_PROFILES` | `mock,edge,idp` | `edge,idp` (`mock` off) |
| `PUBLIC_URL` | `https://<domain>` (the same name as `SITE_ADDRESS`) | same |
| `LLM_BASE_URL` | leave | the provider's OpenAI-compatible URL |
| `LLM_API_KEY` | leave | the key (never committed) |
| `LLM_MODEL` | leave | the model name |
| `HTTP_PORT` | `80` | `80` |
| `IMAGE_PREFIX` | `ghcr.io/<owner>/triage-assistant` to run released images, or leave `triage-assistant` to build on the VM | same |

**Who signs in.** With `idp` in the profiles, the bundled Keycloak serves
the demo users: `alice` (payments responder, platform viewer), `bob`
(platform admin), `carol` (org admin), `dave` (in no team). Their
password is `DEMO_USER_PASSWORD`: `grep DEMO_USER_PASSWORD .env`. For a
pilot with real people, connect the organisation's provider instead:
remove `idp`, and set `OIDC_ISSUER`, `OIDC_DISCOVERY_URL=` (empty),
`OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` (security chapter, "Connecting your
organisation's provider"). The provider's administrators need the
redirect URI `https://<domain>/api/auth/callback`.

## 4. First start

Released images from GHCR (📘 needs a first release, see 8):

```
cd /srv/triage-assistant
# Packages are private until made public in GitHub's package settings;
# while private, log in with a token that has read:packages.
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github user> --password-stdin
make deploy tag=1.0.0
```

Or build on the VM from the checkout (✅ 41 s from checkout to serving,
on a clean Docker host):

```
make prod-up
```

Then check:

```
curl -s https://<domain>/api/ready   # {"status":"ready","checks":{"database":"ok","redis":"ok","identity_provider":"ok"}}
curl -sI http://<domain>/ | head -1  # 308: plain HTTP is redirected to HTTPS
open https://<domain>/               # Sign in (alice), then ask the assistant something
```

## 5. HTTPS

HTTPS is built in (ADR-0012). The TLS edge (Caddy, `tools/edge`) is the
only thing the stack publishes: HTTPS on 443, and HTTP on 80 answering
with a redirect to HTTPS. nginx listens on the VM's loopback only.

**With a domain (the normal case):**

```
# .env
SITE_ADDRESS=triage-demo.example.com    # DNS A/AAAA record -> this VM
HSTS_MAX_AGE=31536000                   # once HTTPS works: browsers then refuse plain HTTP
# first setup only, to avoid Let's Encrypt's rate limits while you experiment:
# ACME_CA=https://acme-staging-v02.api.letsencrypt.org/directory
make prod-up        # or make deploy tag=X.Y.Z
curl -sI https://triage-demo.example.com/ | head -1        # HTTP/2 200
```

Caddy obtains the certificate from Let's Encrypt the first time the domain
is requested, and renews it on its own (~30 days before expiry). It keeps
it in the `edge-data` volume. Needs: ports 80 and 443 reachable from the
internet (the ACME challenge arrives on 80), and DNS pointing at the VM
before the first start.

- ✅ **Rehearsed here:** the same edge image obtained a certificate over
  ACME HTTP-01 from Pebble, Let's Encrypt's test CA, served it with a
  verified chain, and did not ask again after a restart.
- 📘 **Not here:** the real Let's Encrypt, which needs a public domain.
- Let's Encrypt stopped sending expiry emails in 2025, so watch expiry
  yourself: an uptime check that alerts on certificates close to expiry
  (section 9).

**Without a public domain** (an internal demo): leave
`SITE_ADDRESS=localhost`, or set an internal name. Caddy then issues from
its own local CA. Browsers warn until that CA is trusted:

```
docker compose -p triage-assistant-prod -f compose.yaml -f compose.prod.yaml \
  cp edge:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
# then import caddy-root.crt as a trusted root on the demo laptop
```

**Behind a cloud load balancer** (ALB + ACM, Azure Application Gateway,
GCP HTTPS LB): the load balancer terminates TLS. Remove `edge` from
`COMPOSE_PROFILES`, and publish nginx: `HTTP_BIND=0.0.0.0`,
`HTTP_PORT=80`, with the security group allowing only the load balancer.
Set the load balancer's idle timeout above 15 s (the SSE heartbeat); ALB's
default 60 s works. nginx takes the client address from the load
balancer's `X-Forwarded-For` (trusted from private ranges only).

**No inbound ports at all** (a VM inside a corporate network): a
Cloudflare Tunnel or Tailscale Funnel reaches out from the VM. 📘 Not
exercised here.

## 6. Deploy a new version

```
make deploy tag=1.1.0
```

✅ Measured (`make drills d="deploy rollout"`):
- **Plain `docker compose up -d`:** the old api drains its in-flight
  requests (up to 120 s) *before* the new one starts. New requests got
  502 for 6.7 s, and for as long as the longest answer in flight.
- **`make deploy`:** runs migrations, then starts the new api next to the
  old one. It waits until the new one is healthy and nginx has resolved
  it, then drains the old one. Zero failed or slow requests during the
  api swap, and all in-flight streams finished.
- **Through the TLS edge, nginx's replacement costs nothing:** the edge
  holds requests while nginx restarts. Deploying v0.2.0 from GHCR under
  steady signed-in load: 134,917 requests (reads and streamed answers),
  **0 failed**; the slowest waited 1.5 s.
- **The one gap left: replacing the edge itself.** It owns ports 80/443,
  so for **~2 s** new connections are refused and in-flight ones cut
  (measured under the same load). `make deploy` replaces it only when its
  build inputs (`tools/edge/**`) changed: CI stamps the image with their
  hash. Only a load balancer in front of two hosts removes that.
- **The contract migration of v0.3.0** (row-level security) ran while
  v0.2.0 served the same load: no request failed.
- If the new api never becomes healthy, it's removed, and the old one
  keeps serving.

`make deploy` records the tag in `.env` (`IMAGE_TAG`). Every later
compose command then uses the same images. On a host that runs releases
(`IMAGE_PREFIX=ghcr.io/...` in `.env`), `make prod-up` refuses to run: it
would build the checkout and label it with the release's name. Without that, the next
`docker compose up` quietly switched back to the default tag: measured,
when `make restore` did exactly that.

Migrations run while the old version is still serving, so they must be
backward compatible. Add a column first; stop reading it in one release;
drop it in the next.

**When a release changes the database's image,** replace the database
container before the deploy. v0.5.0 did: `postgres:17` became
`pgvector/pgvector:0.8.6-pg17-trixie`, the same PostgreSQL build with the
vector extension. The data directory is untouched.

```
cd /srv/triage-assistant && git fetch --tags && git checkout v0.5.0
docker compose -p triage-assistant-prod -f compose.yaml -f compose.prod.yaml up -d --no-deps db
# only on hosts running the mock model: its new embeddings endpoint
docker compose -p triage-assistant-prod -f compose.yaml -f compose.prod.yaml up -d --build mock-llm
make deploy tag=0.5.0
```

Postgres restarts, so requests fail for a few seconds. ✅ Measured on
this box's v0.4.0 → v0.5.0 upgrade, under steady signed-in reads and
streamed answers (302,951 requests):
- **replacing the database:** healthy again in 5.5 s. The api answered a
  JSON 503 for about 4 s (268 requests), and nothing hung;
- **rebuilding the mock:** cut the 2 answers it was streaming;
- **the deploy:** 0 failed, and the edge was left running.

`make deploy` compares the running database's image with the compose
files first, and stops with these instructions if they differ. When
rolling back, keep the newer database image: it runs the older release
too. ✅ Measured: v0.5.0 → v0.4.0 (the deploy skipped the migrations)
failed 0 of 101,880 requests, and back to v0.5.0, 0 of 102,183.

**Rollback:** `make deploy tag=<previous>`. It rolls back the code, never
the schema (ADR-0015). When the database is at a revision the older image
does not know, the deploy says "the database (...) is ahead of <tag>:
rolling back the code only" and skips the migrations. ✅ Measured: v0.3.0
→ v0.2.0 under load, no failed request outside the edge's swap.
- **Safe only down to a release that runs on the current schema:** the
  one just before a contract migration, never past it (v0.3.0 → v0.2.0
  yes, → v0.1.0 no).
- **A migration that is itself the fault** is reversed separately and
  deliberately, with the newer image, which knows it:
  `IMAGE_TAG=<newer> docker compose -p triage-assistant-prod -f
  compose.yaml -f compose.prod.yaml run --rm migrate alembic downgrade
  <revision>`.

## 7. Backups and restore

```
make backup ENV=prod                 # backups/triage-prod-<time>.dump, keeps the newest 14
make restore ENV=prod file=backups/triage-prod-<time>.dump
```

✅ Measured with 2,000,015 alerts (a 355 MB database):
- **Backup:** 4 s, 73 MB (pg_dump custom format, consistent without
  blocking the app).
- **Restore:** 6 s in one transaction (all or nothing); the api was
  healthy 13 s after the restore started. Row count and a checksum
  matched the data before it was damaged.
- **Onto a brand-new host:** a clean Docker host rebuilt from the
  checkout had the dump restored and was ready in 11 s.

Schedule it, and get the dumps off the VM: a backup on the VM's only
disk dies with the disk. 📘 A cron line for the `deploy` user:

```
15 2 * * * cd /srv/triage-assistant && make backup ENV=prod >> backups/backup.log 2>&1 && <copy the newest dump off the host: aws s3 cp / az storage blob upload / rsync>
```

A backup nobody has restored is a hope, not a backup. Rehearse a restore
onto a clean host (`make restore file=...` on a fresh `make prod-up`) after
schema changes, and before relying on a backup.

## 8. Cutting a release

```
git switch main && git pull
git tag -a v1.1.0 -m "v1.1.0" && git push origin v1.1.0
```

The Release workflow (`.github/workflows/release.yml`):
1. Checks that the tag points at a commit on main, and runs the image
   checks.
2. Builds both production images for **linux/amd64 and linux/arm64** on
   native runners (no emulation), with provenance and an SBOM.
3. Pushes them under one multi-architecture tag: `1.1.0`, `1.1` and
   `sha-<commit>` in `ghcr.io/<owner>/triage-assistant-{api,web}`. An
   Intel VM, a Graviton VM and an Apple Silicon laptop each pull their
   native image.
4. **Pulls what it published**, on an amd64 and an arm64 runner, and runs
   the whole stack from those images (`scripts/smoke-release.sh`):
   readiness, the UI, a write and a read, a streamed answer.
5. Only then creates the GitHub release, with notes generated from the
   merged PRs.

Pull requests that change the images' inputs (the workflow, a Dockerfile,
a lockfile) run the same four builds without pushing. So an arm64 break
shows up in review, not at release time.

`scripts/smoke-release.sh <prefix> <tag>` also works on any host: run it
before `make deploy` to check a release where it's about to run.

## 9. Watching it

- **Dashboards** listen on the VM's loopback only. Reach them through an
  SSH tunnel: `ssh -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090
  deploy@<vm>`, then http://localhost:3000.
- **Alerts:** Alertmanager posts them into the app (the alerts panel).
  Add an email, Slack or pager receiver in
  `infra/observability/alertmanager/alertmanager.yml` for a real
  audience.
- **Down checks from outside:** nothing inside the VM notices when the
  VM or nginx is down. 📘 Add an external uptime check (the cloud's
  health checks, or an uptime service) on `https://<name>/healthz`.
- **Events:** `journalctl -u docker-events` (cloud-init) has every
  container start, die, OOM and health change.

## 10. Before the demo

- [ ] `make ps ENV=prod`: every container up and healthy (`make logs ENV=prod S=api` if not)
- [ ] `curl https://<name>/api/ready` returns 200
- [ ] Sign in as `alice`, ask the assistant a question: the answer streams in
- [ ] Recent alerts show in the panel, with their teams; `carol` (org
      admin) sees every team's, `dave` sees none
- [ ] `/api/ready` says `"identity_provider": "ok"`
- [ ] Grafana's dashboard has data (through the tunnel)
- [ ] A backup from today exists, off the host
- [ ] The failure story is ready if asked: `docs/handbook/failure-modes.md`

## 11. Teardown

`make prod-down` stops everything and keeps the data.
`docker compose -p triage-assistant-prod -f compose.yaml -f compose.prod.yaml down -v`
deletes the data too. Then ask IT to delete the VM, its disk snapshots and
its DNS record.
