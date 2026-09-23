# Contributing

## Local setup

```
make setup      # creates .env from .env.example, builds images
make up         # dev stack with hot reload
```

Machine setup per operating system: `docs/handbook/dev-environment.md`.
How to add a dependency, an endpoint, a setting, a migration or a metric:
`docs/handbook/daily-work.md`.

## Workflow

Trunk-based: branch off `main`, open a PR, squash-merge after review +
green CI. `main` is always deployable.

- Branch names: `type/<issue-number>-<short-desc>` (e.g. `feat/12-post-alerts`)
- Commits: [Conventional Commits](https://www.conventionalcommits.org/) —
  `feat:`, `fix:`, `chore:`, etc.
- PRs must reference an issue (`Closes #N`) and pass CI before merge.

## Definition of Done

- [ ] Code merged via a reviewed, green PR (`make check` locally first)
- [ ] Tests for new behavior, including its failure paths (dependency
      down, dependency hung, invalid input): `docs/handbook/testing.md`
- [ ] If a browser or nginx sees it: `make prod-up && make e2e`
- [ ] If it touches the database path, pools or timeouts: the database
      drills (`make drills d="db-freeze db-hang pgbouncer-freeze"`)
- [ ] New metrics have bounded labels; new alerts have a promtool test
- [ ] Docs updated if behavior changed: the handbook chapter, an ADR for a
      decision, a runbook for a procedure, the gotcha list for a trap
