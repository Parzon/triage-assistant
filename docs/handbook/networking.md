# Networking: containers, the proxy, the cloud

How requests reach the api, what Docker does to your firewall, and what
changes when the stack moves to a cloud network. ✅ = measured or shown
on this repo's host, 📘 = cloud guidance, not exercised here.

## Inside the stack ✅

```
browser ──:443 HTTPS──► edge (Caddy) ──http://web:8080──► nginx (web) ──http://api:8010──► api ──► PgBouncer ──► Postgres
          :80 → 308 ─┘   TLS, certificates,                  │ static files (React)     ├──► Valkey
                         HTTP/3, retries                     └ /api/* → api             ├──► the model provider (or mock-llm)
                         │                                                               └──► Keycloak, back channel
                         └ /auth/realms/triage/*, /auth/resources/* ──http://keycloak:8080──► Keycloak, front channel
```

- **One bridge network per compose project**
  (`triage-assistant-prod_default`). Services find each other by
  **service name** through Docker's DNS server at `127.0.0.11`, which
  every container's `/etc/resolv.conf` points at (`make netshoot`, then
  `dig api`).
- **A stopped container disappears from DNS.** Clients see a
  name-resolution error (`socket.gaierror`, "Temporary failure in name
  resolution"), not "connection refused". The api maps it to 503.
  PgBouncer caches that negative answer for 15 s by default, which
  delayed recovery after Postgres restarted by 15 s; `DNS_NXDOMAIN_TTL: 1`
  fixed it (ADR-0010).
- **IP addresses are reused.** Docker hands out the lowest free address.
  Two short-lived containers started one after the other get the same
  IP. That made a rate-limit test look like a single global limit until
  the clients ran concurrently.
- **Only the TLS edge is published** in the production shape (80 and
  443). nginx is published on the host's loopback only (`HTTP_PORT`,
  for drills, load tests and debugging). The api, PgBouncer, Postgres and
  Valkey have no host ports at all: nothing outside the Docker network
  can reach them.

## Published ports bypass the host firewall ✅

A published port is not a socket the host firewall filters: Docker
rewrites the destination address in the NAT table *before* ufw's rules
run. On this host (`iptables v1.8.11 (nf_tables)`):

```
-A PREROUTING -m addrtype --dst-type LOCAL -j DOCKER
-A DOCKER -d 127.0.0.1/32 ! -i br-27df0875c83d -p tcp --dport 8010 -j DNAT --to-destination 172.19.0.2:8010    <- dev api: loopback only
-A DOCKER -d 127.0.0.1/32 ! -i br-e769709faf1f -p tcp --dport 3000 -j DNAT --to-destination 172.21.0.3:3000    <- Grafana: loopback only
-A DOCKER ! -i br-e769709faf1f -p tcp --dport 8088 -j DNAT --to-destination 172.21.0.12:8080                    <- nginx: every interface
```

The packet is rewritten in `PREROUTING`, then *forwarded* to the
container. It never passes the `INPUT` chain where ufw's "deny" rules
live. The `FORWARD` chain (policy DROP) runs `ts-forward` (Tailscale),
then `DOCKER-USER`, then Docker's own rules. **`DOCKER-USER` is the
only place for your own filtering of container traffic.**

So:
- Publish only what must be public (nginx).
- Bind everything else to `127.0.0.1`: dev ports (`BIND_ADDR`),
  dashboards, debugpy. The rules above show those DNATs apply only to
  `-d 127.0.0.1`.
- In the cloud, the real firewall is the security group in front of the
  VM (📘).

Where the client IP comes from:
- Requests from the host itself arrive at the container from the bridge
  gateway (`172.21.0.1`), because `docker-proxy` relays host-originated
  connections.
- External clients come through DNAT, which keeps their source address
  (📘 not tested from outside).
- nginx then *overwrites* `X-Forwarded-For` with `$remote_addr`.

## nginx in front of the api ✅

| Setting | Why |
|---|---|
| `resolver 127.0.0.11 valid=10s` + `server api:8010 resolve` | re-resolves `api` at runtime. Without it nginx resolves once at start and keeps connecting to a dead IP forever after the api is recreated (measured: `connect() failed (111)` to the old address). It's also what lets `make deploy` add the new api before removing the old one |
| `proxy_buffering off` on `/api/chat/stream` | nginx buffers responses by default. Measured: every token of a streamed answer arrived at once at the end. The api also sends `X-Accel-Buffering: no` for any other nginx in the path |
| `proxy_read_timeout` 30 s (`/api/`), 120 s (the stream) | the longest silence nginx waits for; the stream sends a heartbeat every 15 s |
| `proxy_connect_timeout 2s` | how long a dead upstream costs a request. When the api's address vanished (network cut, container gone), requests failed in 2.0 s |
| upstream `keepalive 16`, `keepalive_timeout 60s` < gunicorn's 75 s | the proxy closes idle connections first. If the backend closes one that nginx is about to reuse, nginx does not retry a POST: 502 |
| `proxy_set_header X-Forwarded-For $remote_addr` (overwrite, not append), after `realip` | the api gets exactly one address: the real client, taken from a trusted proxy's header, or the connection itself. A client cannot forge its IP to escape the rate limit (measured, both directly and through the edge) |
| `error_page 502 504 @api_error` | when nginx answers for the api, it answers in the api's JSON error format, with the request id |
| `redirect_slashes=False` in the api | behind a proxy that strips `/api`, Starlette built redirects to `/alerts` on the wrong host and port. `/alerts/` is now a 404, not a broken redirect |

Measured once, and worth knowing:
- **Keep-alive hides a network partition.** With the api cut off the
  network, requests needing a new connection failed in 2 s. The first
  request, sent on an existing keep-alive connection, waited out the
  whole read timeout.
- **`$host` drops the port.** For absolute URLs behind a non-default
  port, use `$http_host`.

## The TLS edge ✅

Caddy (`tools/edge`, ADR-0012) terminates HTTPS in front of nginx.

- **Certificates:**
  - `SITE_ADDRESS` set to a domain: Let's Encrypt over ACME, obtained on
    first use and renewed by Caddy.
  - `localhost`: Caddy's local CA, with 12-hour leaf certificates.
  - `make acme-test` rehearses the ACME exchange against Pebble, Let's
    Encrypt's test CA.
- **Measured here:**
  - TLS 1.3 and HTTP/2; plain HTTP answered with `308` to HTTPS; HTTP/3
    advertised (`alt-svc`).
  - SSE unbuffered through both proxies (`flush_interval -1`): 50 events
    spread over 1.3 s, first token at 0.34 s.
- **Client addresses through two proxies.** Caddy replaces whatever
  `X-Forwarded-For` a client sends. nginx takes the client from Caddy's
  header (`set_real_ip_from` the private ranges, `real_ip_recursive
  on`). Measured: a request sent with `X-Forwarded-For: 6.6.6.6` reached
  nginx and the api as the real client. Before this, the api would have
  seen Caddy's address for every user, and so one rate limit for
  everyone.
- **nginx restarts are absorbed.** `lb_try_duration 5s`: a request that
  cannot reach nginx is held and retried. A rolling deploy through the
  edge failed zero requests; the worst case was one request held 2.0 s.
- **When nginx stays down,** the edge answers `/api` requests with the
  api's JSON error shape (`upstream_unavailable`), after the 5 s it
  waited.
- **HSTS** (`HSTS_MAX_AGE`) is 0 for `localhost`: it would force HTTPS on
  every other local port. Use a year for a real domain.
- **Remapped host ports:** Caddy knows its container ports (80/443), so
  with remapped host ports (this box uses 8081/8443 because 80 is taken)
  its redirect and `alt-svc` name 443. On a VM using 80/443 they are
  right.
- **Non-root on 80/443:** the edge runs as UID 10001 with no
  capabilities. Binding low ports inside its own network namespace comes
  from the `net.ipv4.ip_unprivileged_port_start=0` sysctl compose sets.
  The official image's file capability on the binary had to be removed:
  under `no-new-privileges` the kernel refuses to run it.

## Signing in: two channels to the identity provider ✅

Sign-in talks to the identity provider on two paths:

| Channel | Who | What | Bundled Keycloak |
|---|---|---|---|
| front | the browser, redirected | the login page, sign-out | `<PUBLIC_URL>/auth/...`: the edge in the production shape, the Vite proxy in dev |
| back | the api, directly | the discovery document, signing keys, the code-for-token exchange | `http://keycloak:8080/auth/...` inside the Docker network |

Both must lead to one issuer. A token names the issuer it came from.
Keycloak would otherwise name whichever address a request arrived on,
and the api would reject tokens as "issued by another provider".
- `KC_HOSTNAME=<PUBLIC_URL>/auth` fixes the issuer and the browser-facing
  URLs.
- `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true` answers the api's direct calls
  with `keycloak:8080` addresses.
- `OIDC_DISCOVERY_URL` tells the api where to read the metadata.

✅ Measured: the discovery document fetched by the api names
`http://localhost:5173/auth/realms/triage` as issuer and authorization
endpoint, and `http://keycloak:8080/...` as token and key endpoints.

With a real provider, both channels are its public HTTPS address. The api
then needs outbound HTTPS to it: an egress rule, a proxy
(`HTTPS_PROXY`), and the provider's CA in the image if a corporate CA
signs it.

**What the edge exposes.** Only the triage realm's user-facing paths
(`/auth/realms/triage/*`) and the static resources its pages load
(`/auth/resources/*`). Every other `/auth` path answers 404: the admin
console, the admin REST API, the master realm where Keycloak's own
administrator signs in. Keycloak's reverse-proxy guide lists these as
the paths to keep private. The Vite dev proxy forwards all of `/auth`,
so the admin console works in dev.

## Behind a load balancer instead of the edge

The same images; the edge is simply not started. Remove `edge` from
`COMPOSE_PROFILES`, and publish nginx with `HTTP_BIND=0.0.0.0`,
`HTTP_PORT=80`, reachable only from the load balancer's security group.

- **The client IP.** nginx trusts `X-Forwarded-For` from private ranges
  (✅ the same realip settings as behind the edge). Load balancers
  *append* the client address; `real_ip_recursive on` takes the
  rightmost untrusted address, which is the load balancer's view of the
  client, not anything the client wrote.
- **Idle timeouts versus streaming.** A load balancer closes connections
  idle longer than its idle timeout. AWS ALB defaults to 60 s; other load
  balancers and corporate proxies use as little as 30 s. The SSE
  heartbeat (every 15 s) keeps an answer's connection alive under any of
  them. 📘
- **No buffering of `text/event-stream`** anywhere in the path. CDNs and
  some proxies buffer by default; the symptom is answers that appear all
  at once. 📘
- **TLS** terminates at the load balancer, with a managed certificate. 📘
- **The bundled Keycloak is not routed** without the edge: nginx serves
  the app, not `/auth`. Use the organisation's identity provider there,
  or route `/auth/realms/triage/*` and `/auth/resources/*` to Keycloak at
  the load balancer. 📘

## A cloud network for this stack 📘

```
                     Internet
                        │
                ┌───────┴────────┐  public subnets (2 AZs)
                │ load balancer  │  security group: 443 (and 80 → redirect) from the internet
                └───────┬────────┘
                        │ only from the LB's security group
        ┌───────────────┴───────────────┐  private subnets (2 AZs)
        │ api tasks / VMs (nginx + api) │  no public IPs
        └──┬─────────────┬───────────┬──┘
           │             │           └──► NAT gateway (public subnet) ──► model provider API, registries
           ▼             ▼
   managed Postgres   managed Valkey      private subnets, security groups allow only the app's SG
   (RDS; Multi-AZ)    (ElastiCache)
```

- **Private subnets for everything but the load balancer.** No public
  IPs on application or database hosts, and administrative access
  through a bastion or SSM Session Manager, not an open port 22.
- **Security groups** are stateful and attach to resources: allow by
  *source security group* (the database accepts 5432 only from the
  app's group), not by IP range. Network ACLs are stateless subnet-wide
  filters; leave them at their defaults unless compliance requires more.
- **Egress: the NAT gateway** gives private hosts outbound internet
  access: the LLM provider's API, image registries, package mirrors. It
  is billed per GB processed. Heavy image pulls through it cost money, so
  use VPC endpoints for the registry (ECR) and S3. If the security team
  restricts egress, the provider's API hostname must be allowed, or
  every chat request fails with `llm_unavailable`.
- **DNS:** a private hosted zone for internal names, and the public
  record pointing at the load balancer (an alias, not an IP).
- **Corporate networks** often send outbound traffic through an HTTP
  proxy. The api's model client needs `HTTPS_PROXY`, and `NO_PROXY` for
  the internal service names (dev-environment chapter).

The managed-service equivalents of each container, and what changes in
this repo to use them, are in the environments-and-shipping chapter.
