# Certificate renewal

An expired TLS certificate makes every client connection fail with a
certificate error, even though the service itself is healthy.

## Check expiry

Run `openssl s_client -connect pay.example.com:443 -servername pay.example.com`
and read the notAfter date, or check the certificate expiry panel.

## Renew

Certificates renew automatically 30 days before expiry. When renewal failed,
run the renewal job by hand with `make renew-certs`, then reload the edge.
Check that port 80 is reachable from the internet: the ACME challenge needs it.
