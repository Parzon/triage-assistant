# Checkout error rate high

Checkout failures cost revenue by the minute. This runbook covers a high
error rate on checkout-api.

## First look

Open the checkout dashboard and split errors by status code. 5xx errors point
at checkout-api or its dependencies; 4xx errors usually mean a client release
sends bad requests.

## Check the payment provider

Most checkout incidents start at the payment provider. Check its status page
and the `payment_provider_errors_total` panel. If the provider is down,
enable the fallback provider with the `checkout.fallback_provider` flag.

## Recent changes

If errors began within minutes of a checkout-api deploy, roll it back (see the
platform runbook "Roll back a deploy") before investigating further.
