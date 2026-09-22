# triage-assistant

AI Ops / Incident Triage Assistant — small end-to-end system: an API
that receives and normalizes incident alerts, backed by Postgres.

## Quickstart

```
cp .env.example .env      # edit if you want different local credentials
docker compose up --build
curl localhost:8010/health
```

## Structure

```
apps/api/       FastAPI service
infra/          infrastructure-as-code (Terraform, k8s manifests) — not yet populated
docs/adr/       architecture decision records
scripts/        one-off ops scripts
```

## Contributing

See `CONTRIBUTING.md`. Agent-specific instructions are in `AGENTS.md`.
