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
whole production stack up from a checkout.

Not needed, and deliberately so: Python, Node, uv, npm, psql, a local
Postgres. Tool versions live in the images:
- `ghcr.io/astral-sh/uv:0.12.18` with Python 3.13 for the api
- `node:24` for the web
- `pgvector/pgvector:0.8.6-pg17-trixie` (PostgreSQL 17 with pgvector), `valkey/valkey:8.1-alpine`
- `quay.io/keycloak/keycloak:26.7.4`, the bundled identity provider

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
  monitoring idles at ~880 MiB, and Keycloak adds ~430 MiB. Image builds
  and load tests need more. Docker Desktop's default can be too small.
- **Apple Silicon (arm64):** every image in the stack publishes a native
  arm64 variant: Python, Node, nginx, Postgres, Valkey, PgBouncer,
  Prometheus, Grafana, cAdvisor, the exporters and Keycloak (✅ checked
  in the registries' manifests).
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

## The same workflow on every system

The commands are identical everywhere: `make up`, `make check`, `make
e2e`. What differs is underneath them.

| | Linux ✅ | macOS 📘 | Windows 📘 |
|---|---|---|---|
| Docker | Docker Engine, native | a Linux VM: Docker Desktop, Colima, OrbStack or Rancher Desktop | WSL2, with Docker Desktop or Docker Engine inside it |
| Where to clone | anywhere | anywhere | inside WSL (`~/code`), never `/mnt/c` |
| Run `make` from | any shell | Terminal (make 3.81 ships with the Xcode command-line tools) | the WSL shell only |
| Files written by containers | your UID (`AS_ME`); `make fix-perms` if not | yours: the VM maps ownership | your WSL user's |
| File names | case-sensitive | **case-insensitive** by default | case-sensitive inside WSL |
| Bind-mount speed | native | slower: dependencies stay in volumes inside the VM | native inside WSL, slow from `/mnt/c` |
| A GPU for Ollama | NVIDIA driver + Container Toolkit | the Ollama app natively (containers cannot use Apple's GPU) | the NVIDIA driver for WSL |
| Editor | VS Code or JetBrains, with dev containers | the same | VS Code with the WSL extension, then dev containers |

Traps that only show up across systems:
- **Case-insensitive file names (macOS).**
  - An import that gets a file name's case wrong (`./chat` for
    `Chat.tsx`) works on a Mac, then fails in Linux CI.
  - A rename that only changes case is invisible to git there: use
    `git mv Chat.tsx chat.tsx`.
- **Reserved ports (Windows).** Hyper-V and WinNAT reserve port ranges,
  so a bind to 8010 or 5173 can fail with nothing listening. See them
  with `netsh interface ipv4 show excludedportrange protocol=tcp`.
  Choose other ports in `.env` (`API_PORT`, `WEB_PORT`).
- **The WSL2 clock can drift** after the laptop sleeps. The bundled
  Keycloak shares the VM's clock, so development sign-in is unaffected.
  Against an external identity provider, a skew over 60 s fails the
  token time checks. Fix it with `wsl --shutdown`, or `sudo hwclock -s`.
- **Line endings.** `.gitattributes` forces LF (the Windows section
  above). Editors that ignore it still show CRLF diffs: set VS Code's
  `files.eol` to `\n`.
- **Scripts** start with `#!/usr/bin/env bash` and run on bash 3.2 when
  developers run them: macOS's default shell is zsh, and its bash is
  3.2.

## Corporate networks 📘

- **Docker Hub limits** anonymous pulls per IP address. A whole office
  behind one NAT shares that limit, and builds start failing with
  `toomanyrequests`. `docker login` (a free account doubles the limit),
  or a registry mirror (`registry-mirrors` in `daemon.json`, or an
  Artifactory/Nexus pull-through cache).
- **HTTP proxy:** set it for the Docker daemon (Docker Desktop settings,
  or a systemd drop-in), and for builds and containers in
  `~/.docker/config.json` (`"proxies": {"default": {"httpProxy": ...,
  "noProxy": "localhost,127.0.0.1,api,web,db,pgbouncer,redis,mock-llm,keycloak"}}`).
  The service names belong in `noProxy`, or containers send each other's
  traffic to the proxy.
- **TLS-inspecting proxies** re-sign HTTPS with a company root CA. The
  daemon needs that CA for pulls. Builds that download packages need it
  inside the build: `SSL_CERT_FILE` for uv/pip, `NODE_EXTRA_CA_CERTS`
  for npm. That's a Dockerfile change: keep it in a local override, not
  in the shared Dockerfiles.

## Editor

The editor needs the project's packages for completion, type checking and
debugging. Rather than installing Python and Node on every laptop, attach
the editor to the running container: in VS Code, "Dev Containers: Attach
to Running Container", then pick the api or web container of `make up`.
The api image's `dev` user has your UID and GID (`make` passes
`DEV_UID`/`DEV_GID`), so on Linux files you create stay yours.

## Ports on your machine

| Port | What | Bound to |
|---|---|---|
| 8010 | api (dev, hot reload) | 127.0.0.1 |
| 5173 | web (Vite dev server; proxies `/api` to the api and `/auth` to Keycloak, so sign-in stays on one origin) | 127.0.0.1 |
| 5678 | debugpy (`make debug-up` only) | 127.0.0.1 |
| 3000 / 9090 / 9093 | Grafana / Prometheus / Alertmanager (`make obs-up`) | 127.0.0.1 |
| `EDGE_HTTPS_PORT` / `EDGE_HTTP_PORT` (443 / 80; this box uses 8443 / 8081) | the production-shaped stack's TLS edge (`make prod-up`); HTTP only redirects | `EDGE_BIND` (0.0.0.0) |
| `HTTP_PORT` (8088) | production-shaped nginx, plain HTTP, for drills and load tests | `HTTP_BIND` (127.0.0.1) |

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
| "Sign in" shows an error page for ~30 s after `make up` | Keycloak is still starting (`make ps`: `health: starting`) | wait for `(healthy)` |
| the keycloak container exits: "set DEMO_USER_PASSWORD in .env" | an `.env` from before sign-in existed | copy the sign-in block from `.env.example` into `.env` |
| a realm change (users, groups) does not show up | Keycloak imports the realm file only into an empty database, which lives inside its container | `docker compose up -d --force-recreate keycloak` |
| signed in on the prod stack, but every POST answers 403 `csrf_failed` | the page's origin is not `PUBLIC_URL` (e.g. `https://127.0.0.1:8443` vs `https://localhost:8443`) | open the site at exactly `PUBLIC_URL` |
