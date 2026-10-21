#!/bin/sh
# What depends on where the container runs, written at start-up into /tmp
# (the root filesystem is read-only) and included by default.conf:
# - resolver: the nameservers in /etc/resolv.conf - Docker's 127.0.0.11 in
#   Compose, the cluster's DNS on Kubernetes, the VPC resolver on ECS;
# - set_real_ip_from: the proxies whose X-Forwarded-For is believed.
#   REAL_IP_FROM (space-separated CIDRs) when set, e.g. a load balancer's
#   subnets; otherwise this container's own networks, where the TLS edge
#   runs. Not every private range: a client on a private address could
#   then name its own address through a proxy that appends to the header;
# - the api's address: API_UPSTREAM (default api:8010). On Kubernetes use
#   the service's full name: nginx's resolver ignores search domains.
set -eu
out=/tmp/nginx
mkdir -p "$out"

ns=$(awk '$1 == "nameserver" { printf "%s ", ($2 ~ ":" ? "[" $2 "]" : $2) }' /etc/resolv.conf)
echo "resolver ${ns:-127.0.0.11} valid=10s ipv6=off;" > "$out/resolver.conf"

trusted=${REAL_IP_FROM:-$(ip -o -f inet route show scope link | awk '{ print $1 }')}
: > "$out/real-ip.conf"
for cidr in $trusted; do
  echo "set_real_ip_from $cidr;" >> "$out/real-ip.conf"
done

cat > "$out/upstream.conf" <<CONF
upstream api_backend {
    zone api_backend 64k;
    server ${API_UPSTREAM:-api:8010} resolve;
    keepalive 16;
    # Below gunicorn's keepalive: the side that closes idle connections
    # first must be the proxy, never the backend.
    keepalive_timeout 60s;
}
CONF
echo "$0: resolver ${ns:-127.0.0.11}; trusted proxies: ${trusted:-none}; api: ${API_UPSTREAM:-api:8010}"
