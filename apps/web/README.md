# apps/web

React + Vite + TypeScript. Streaming chat scaffold — see `src/App.tsx`
for the client-side streaming logic, and `docs/adr/0001-standard-project-shape.md`
in the repo root for why this stack was chosen.

## Local dev (containerized, the recommended path)

Run `docker compose up` from the repo root — this service is included.

## Local dev (native, if you need it)

```
npm install
npm run dev
```

Requires Node 20+. Requests to `/api/*` are proxied to the `api`
service during `docker compose` dev (see `vite.config.ts`); outside
Compose you'll need the backend reachable at that path yourself.
