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

Every secret must differ from `.env.example` (cloud-init already
generated the database passwords, the Grafana password and the webhook
token). The rest:

| Setting | Demo without a model provider | With a provider |
|---|---|---|
| `COMPOSE_PROFILES` | `mock` | empty (`mock` off) |
| `LLM_BASE_URL` | leave | the provider's OpenAI-compatible URL |
| `LLM_API_KEY` | leave | the key (never committed) |
| `LLM_MODEL` | leave | the model name |
| `HTTP_PORT` | `80` | `80` |
| `IMAGE_PREFIX` | `ghcr.io/<owner>/triage-assistant` to run released images, or leave `triage-assistant` to build on the VM | same |

## 4. First start

Released images from GHCR (📘 needs a first release, see 8):

```
cd /srv/triage-assistant
# Packages are private until made public in GitHub's package settings;
# while private, log in with a token that has read:packages.
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github user> --password-stdin
make deploy tag=1.0.0
```

Or build on the VM from the checkout (✅ exactly what `make
fresh-host-test` does, on a clean Docker host: 41 s from checkout to
serving):

```
make prod-up
```

Then check (✅ the same checks the fresh-host test makes):

```
curl -s localhost/api/ready          # {"status":"ready","checks":{"database":"ok","redis":"ok"}}
open http://<vm>/                    # the UI; ask the assistant something
```

## 5. HTTPS

The stack speaks HTTP on port 80. Pick one way to put TLS in front of it:

| Option | Needs | Good for |
|---|---|---|
| **Cloud load balancer + managed certificate** (AWS ALB + ACM, Azure Application Gateway, GCP HTTPS LB) | the VM in that cloud; DNS | anything customer-facing. Set the LB's idle timeout above 15 s (the SSE heartbeat) or long answers get cut; ALB's default of 60 s works |
| **Caddy on the VM** (automatic Let's Encrypt) | a public DNS name, ports 80 + 443 open to the internet | a quick public demo. Run Caddy on 443 → `localhost:80`, and move nginx's `HTTP_BIND` to 127.0.0.1 |
| **Cloudflare Tunnel / Tailscale Funnel** | an account; no inbound ports at all | a VM inside a corporate network that cannot accept inbound traffic |
| **Self-signed certificate** | nothing | internal only; browsers warn |

📘 None of these is exercised in this repo. Whichever you choose, the
proxy in front must not buffer `text/event-stream`, and must pass
`X-Forwarded-For`. Then set up nginx's `realip` module (the comment in
`apps/web/nginx/snippets/proxy.conf`), or every user shares the proxy's
IP address, and so one rate limit.

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
- **The one gap:** nginx itself is replaced last. It refused connections
  for ~0.3 s, because one container owns port 80. Only a load balancer in
  front of two nginx containers removes that.
- If the new api never becomes healthy, it's removed, and the old one
  keeps serving.

`make deploy` records the tag in `.env` (`IMAGE_TAG`). Every later
compose command then uses the same images. Without that, the next
`docker compose up` quietly switched back to the default tag: measured,
when `make restore` did exactly that.

Migrations run while the old version is still serving, so they must be
backward compatible. Add a column first; stop reading it in one release;
drop it in the next.

**Rollback:** `make deploy tag=<previous>`. It works as long as no
migration removed something the previous version needs, which is the
rule above.

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
  checkout had the dump restored and was ready in 11 s
  (`DUMP=... make fresh-host-test`).

Schedule it, and get the dumps off the VM: a backup on the VM's only
disk dies with the disk. 📘 A cron line for the `deploy` user:

```
15 2 * * * cd /srv/triage-assistant && make backup ENV=prod >> backups/backup.log 2>&1 && <copy the newest dump off the host: aws s3 cp / az storage blob upload / rsync>
```

A backup nobody has restored is a hope, not a backup. Rehearse a restore
onto a clean host (`DUMP=... make fresh-host-test`) after schema changes,
and before relying on a backup.

## 8. Cutting a release

```
git switch main && git pull
git tag -a v1.1.0 -m "v1.1.0" && git push origin v1.1.0
```

The Release workflow (`.github/workflows/release.yml`):
- checks that the tag points at a commit on main;
- runs the image checks;
- builds both production images with provenance and an SBOM;
- pushes `1.1.0`, `1.1` and `sha-<commit>` to
  `ghcr.io/<owner>/triage-assistant-{api,web}`;
- creates a GitHub release with notes generated from the merged PRs.

Pull requests that change the workflow or a Dockerfile run the same build
without pushing. 📘 No tag has been pushed yet, so the first real
publish is still ahead.

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
- [ ] Ask the assistant a question in the UI: the answer streams in
- [ ] Recent alerts show in the panel
- [ ] Grafana's dashboard has data (through the tunnel)
- [ ] A backup from today exists, off the host
- [ ] The failure story is ready if asked: `docs/handbook/failure-modes.md`

## 11. Teardown

`make prod-down` stops everything and keeps the data.
`docker compose -p triage-assistant-prod -f compose.yaml -f compose.prod.yaml down -v`
deletes the data too. Then ask IT to delete the VM, its disk snapshots and
its DNS record.
