# Name resolution failures

When services cannot resolve each other's names, every call between them
fails at once, often with "Temporary failure in name resolution" or
"no such host".

## Check the resolver

From an affected container, run `getent hosts db` and `dig @127.0.0.11 db`. A
timeout points at the resolver; NXDOMAIN points at the name.

## Common causes

A stopped container disappears from Docker's embedded DNS, so its name stops
resolving immediately. On a host, a full `/etc/resolv.conf` search list or an
upstream resolver outage produces the same symptom for external names.

## Recover

Start the missing container first. For external names, fail over to the
secondary resolver in the network settings, and keep the TTLs of your own
records short enough to change them during an incident.
