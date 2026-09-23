# Security policy

## Reporting a vulnerability

Report it privately through GitHub: **Security → Report a vulnerability**
(https://github.com/Parzon/triage-assistant/security/advisories/new).
Never in a public issue, pull request or chat channel.

Include what you found, how to reproduce it, and what an attacker could
do with it. You will get an answer within two working days.

## What is in scope

The code in this repository and the images it publishes
(`ghcr.io/parzon/triage-assistant-*`). The stand-ins that exist only for
development and testing - the mock LLM, the bundled Keycloak realm and its
test users - are in scope only if the issue would carry over to a real
deployment.

## How this repository protects itself

The controls (secrets, least privilege, supply chain, the LLM-specific
risks) are described in `docs/handbook/security.md`.
