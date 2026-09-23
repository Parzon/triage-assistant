# The development environment

What a developer's machine needs, per operating system, and the traps
each one has. Everything runs in containers: nobody installs Python,
Node, Postgres or Valkey to work on this repo, and everyone runs the
same versions as CI and production.

✅ = verified on this repo's Linux box (Ubuntu, Docker Engine 28.4), 📘 =
standard guidance for the other platforms, not exercised here.

## What the host needs

| Tool | Why | Required |
|---|---|---|
| Docker Engine + the compose v2 plugin (`docker compose`, not `docker-compose`) | runs everything | yes |
| GNU make (3.81+; macOS's is fine) | the command entry point | yes |
| bash, git | scripts, the repo | yes |
| python3 | `make obs-check`, `make dashboard` (plain standard-library scripts) | for observability work |
| jq | `make trace` | for debugging |
| curl | `make drills` | for drills |

✅ A clean Docker host with only bash, make and curl added brings the
whole production stack up from a checkout (`make fresh-host-test`).

Not needed, and deliberately so: Python, Node, uv, npm, psql, a local
Postgres. Tool versions live in the images:
- `ghcr.io/astral-sh/uv:0.12.17` with Python 3.13 for the api
- `node:24` for the web
- `postgres:17`, `valkey/valkey:8.1-alpine`

A developer with the wrong local Node version cannot break anything.

## Linux ✅

1. Docker Engine from Docker's apt/dnf repository
   (docs.docker.com/engine/install). Distribution packages
   (`docker.io`) lag behind and may lack the compose plugin.
2. `sudo usermod -aG docker $USER`, then log out and in. Membership of
   the `docker` group is equivalent to root: anyone in it can mount `/`
   into a container.
3. `git clone`, then `make setup && make up`.

Linux-only traps:
- **Files created by containers can come out owned by root.** The
  targets that write into the repo run as your UID (`AS_ME` in the
  Makefile); for anything else, `make fix-perms` hands the files back.
- **Published ports skip the firewall.** Docker writes NAT rules that
  run before ufw's. The dev ports therefore bind to 127.0.0.1
  (`BIND_ADDR`); see the networking chapter.

## macOS 📘

Containers need a Linux VM on a Mac. The options:

| Tool | Cost | Notes |
|---|---|---|
| Docker Desktop | free for companies under 250 employees **and** under $10 M revenue; a paid subscription otherwise | the reference; check your company's licence before installing |
| Colima | free, open source | CLI only; `colima start --cpu 4 --memory 8` |
| OrbStack | paid for commercial use | fast file sharing |
| Rancher Desktop | free, open source | choose the "dockerd (moby)" engine, not containerd, so `docker compose` works |

- **Give the VM at least 4 CPUs and 8 GB.** The full stack with
  monitoring idles at ~880 MiB, but image builds and load tests need
  more. Docker Desktop's default can be too small.
- **Apple Silicon (arm64):** every image in the stack publishes a native
  arm64 variant: Python, Node, nginx, Postgres, Valkey, PgBouncer,
  Prometheus, Grafana, cAdvisor and the exporters (✅ checked in the
  registries' manifests). The one exception is the Artillery load-test
  image, which is amd64-only and runs under emulation: its numbers are
  meaningless there.
- **File sharing is the slow part.** The dev stack bind-mounts the
  source for hot reload, but keeps `.venv` and `node_modules` in volumes
  inside the VM. The thousands of dependency files never cross the
  file-sharing layer.
- macOS ships bash 3.2. Everything developers run works with it; the
  deploy script (for Linux servers) uses bash 4 features.

## Windows 📘

Use WSL2, then work *inside* it:

1. Install WSL2 with Ubuntu, then either Docker Desktop (WSL2 backend,
   same licence rules as macOS) or Docker Engine directly inside the WSL
   distribution (no licence question).
2. **Clone into the WSL filesystem** (`~/code/triage-assistant`), never
   under `/mnt/c/...`. Bind mounts from the Windows side cross a slow
   network filesystem, and file-change events don't arrive, so hot
   reload silently stops working.
3. Open it with VS Code's WSL extension (`code .` from the WSL shell).

Line endings are handled: `.gitattributes` forces LF. Without it, a
Windows checkout turns shell scripts into CRLF, and containers fail with
`/usr/bin/env: 'bash\r': No such file or directory`.

## Corporate networks 📘

- **Docker Hub limits** anonymous pulls per IP address. A whole office
  behind one NAT shares that limit, and builds start failing with
  `toomanyrequests`. `docker login` (a free account doubles the limit),
  or a registry mirror (`registry-mirrors` in `daemon.json`, or an
  Artifactory/Nexus pull-through cache).
- **HTTP proxy:** set it for the Docker daemon (Docker Desktop settings,
  or a systemd drop-in), and for builds and containers in
  `~/.docker/config.json` (`"proxies": {"default": {"httpProxy": ...,
  "noProxy": "localhost,127.0.0.1,api,web,db,pgbouncer,redis,mock-llm"}}`).
  The service names belong in `noProxy`, or containers send each other's
  traffic to the proxy.
- **TLS-inspecting proxies** re-sign HTTPS with a company root CA. The
  daemon needs that CA for pulls. Builds that download packages need it
  inside the build: `SSL_CERT_FILE` for uv/pip, `NODE_EXTRA_CA_CERTS`
  for npm. That's a Dockerfile change: keep it in a local override, not
  in the shared Dockerfiles.

## Editor: dev containers ✅

The editor needs the project's packages for completion, type checking
and debugging. Rather than installing Python and Node on every laptop,
open the code *inside* the running containers:

- **VS Code:** install the Dev Containers extension. Run `make up`, then
  "Dev Containers: Reopen in Container" and pick **api (Python 3.13)** or
  **web (Node 24)**.
- **JetBrains Gateway** and **GitHub Codespaces** read the same files
  (`.devcontainer/api/devcontainer.json`, `.devcontainer/web/devcontainer.json`).

Each config attaches to the `make up` stack as a non-root user: `dev` in
the api container, `node` in the web one. The api image's `dev` user is
built with your UID and GID (`make` passes `DEV_UID`/`DEV_GID`), so on
Linux files you create stay yours.

✅ Verified with the Dev Containers CLI, against a running dev stack:
- In the api container: user `dev`; Python imports; ruff; a new file in
  the source tree belongs to UID 1000.
- In the web container: user `node`; Node 24; `node_modules` resolves;
  `tsc` 6.0.3.

Upgrading a checkout from before the non-root dev image? Run `make
fix-perms` once: caches written when the container ran as root
(`.mypy_cache`) are otherwise unwritable, and mypy fails with an "INTERNAL
ERROR".

Without dev containers, the fallback is to install uv and Node on the
host: `cd apps/api && uv sync` gives the editor a local `.venv`, and `npm
ci` in `apps/web` a local `node_modules`. Inside the containers those
paths are volumes, so nothing leaks either way.

`.vscode/extensions.json` recommends the extensions, and
`.vscode/launch.json` attaches the debugger to the containerised api
(`make debug-up`; the debugging chapter).

`make hooks` installs a pre-push hook that runs lint, types and unit
tests in containers (~10 s) before anything leaves your machine. CI still
runs everything.

## Ports on your machine

| Port | What | Bound to |
|---|---|---|
| 8010 | api (dev, hot reload) | 127.0.0.1 |
| 5173 | web (Vite dev server; proxies `/api`) | 127.0.0.1 |
| 5678 | debugpy (`make debug-up` only) | 127.0.0.1 |
| 3000 / 9090 / 9093 | Grafana / Prometheus / Alertmanager (`make obs-up`) | 127.0.0.1 |
| `HTTP_PORT` (80; this box uses 8088) | production-shaped nginx (`make prod-up`) | `HTTP_BIND` (0.0.0.0) |

A port already taken: `ss -ltnp | grep :8010` (Linux) or `lsof -i :8010`
(macOS) shows the owner. Often it's a forgotten stack: `docker ps`.

## Keeping the disk

Images and build cache grow with every build. This development box had
60 GB of images and 13 GB of build cache. Check with `docker system
df`. Reclaim with `docker builder prune` (build cache) and `docker image
prune` (dangling images). `docker volume prune` also deletes unused
volumes, *including databases*: know what you are deleting.

## Problems on day one

| Symptom | Cause | Fix |
|---|---|---|
| `permission denied ... /var/run/docker.sock` | not in the `docker` group | `usermod -aG docker`, new login |
| `port is already allocated` | another stack or process holds the port | `docker ps`; `ss -ltnp` |
| `ModuleNotFoundError` after pulling someone's dependency change | the old `.venv` volume | `make rebuild` |
| Postgres restarts with "database files are incompatible with server" | a volume initialised by an older major version (16 → 17) | dev: `make nuke`; with data you need: dump and restore, or `pg_upgrade` |
| files in the repo owned by root | a container wrote them as root | `make fix-perms` |
| hot reload does nothing (Windows) | the repo is under `/mnt/c` | clone into the WSL filesystem |
| `toomanyrequests` pulling images | Docker Hub's anonymous limit, shared by the office | `docker login`, or a mirror |
