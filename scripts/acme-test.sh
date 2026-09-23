#!/usr/bin/env bash
# Automatic certificates, rehearsed: the production edge image gets a
# certificate over ACME (HTTP-01 on port 80) from Pebble - the protocol
# Let's Encrypt speaks - then serves it, keeps it in its volume, and does
# not ask again after a restart. `make acme-test`; tests/acme/compose.yaml.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f tests/acme/compose.yaml)
NETSHOOT=nicolaka/netshoot:v0.14
tmp=$(mktemp -d)
cleanup() { "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true; rm -rf "$tmp"; }
trap cleanup EXIT
step() { printf '== %4ss  %s\n' "$SECONDS" "$*"; }
on_net() { docker run --rm --network triage-assistant-acme_default -v "$tmp:/t:ro" "$NETSHOOT" "$@"; }

docker build -q -t triage-assistant-edge:local tools/edge >/dev/null
# Pebble's API certificate is signed by its test root, shipped in its image.
docker rm -f acme-pebble-root >/dev/null 2>&1 || true
docker create --name acme-pebble-root ghcr.io/letsencrypt/pebble:2.10.1 >/dev/null
docker cp acme-pebble-root:/test/certs/pebble.minica.pem "$tmp/pebble-api-root.pem"
docker rm -f acme-pebble-root >/dev/null
export PEBBLE_TLS_ROOT=$tmp/pebble-api-root.pem

"${COMPOSE[@]}" up -d --quiet-pull
for _ in $(seq 60); do
  "${COMPOSE[@]}" logs edge 2>/dev/null | grep -q '"certificate obtained successfully"' && break
  sleep 1
done
"${COMPOSE[@]}" logs edge | grep -q '"certificate obtained successfully"' \
  || { "${COMPOSE[@]}" logs --tail 30 edge pebble; exit 1; }
step "the edge obtained a certificate for edge.test over ACME (HTTP-01)"

# Pebble generates its issuing root at start-up; verify the chain against it.
on_net curl -sk https://pebble:15000/roots/0 > "$tmp/issuing-root.pem"
on_net curl -sS --cacert /t/issuing-root.pem -o /dev/null -w '%{http_code}\n' https://edge.test/ | grep -q 200
issuer=$(on_net sh -c 'echo | openssl s_client -connect edge.test:443 -servername edge.test 2>/dev/null | openssl x509 -noout -issuer')
step "https://edge.test verifies against Pebble's root ($issuer)"
[ "$(on_net curl -s -o /dev/null -w '%{http_code}' http://edge.test/)" = 308 ]
step "plain HTTP redirects to HTTPS"

"${COMPOSE[@]}" restart edge >/dev/null
sleep 3
obtained=$("${COMPOSE[@]}" logs edge | grep -c '"certificate obtained successfully"')
[ "$obtained" = 1 ] || { echo "the certificate was obtained $obtained times: it did not persist" >&2; exit 1; }
on_net curl -sS --cacert /t/issuing-root.pem -o /dev/null -w '%{http_code}\n' https://edge.test/ | grep -q 200
step "after a restart it serves the stored certificate; no second request to the CA"
step "PASS"
