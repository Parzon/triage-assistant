# Contributing

## Local setup

```
cp .env.example .env
docker compose up --build
```

## Workflow

Trunk-based: branch off `main`, open a PR, squash-merge after review +
green CI. `main` is always deployable.

- Branch names: `type/<issue-number>-<short-desc>` (e.g. `feat/12-post-alerts`)
- Commits: [Conventional Commits](https://www.conventionalcommits.org/) —
  `feat:`, `fix:`, `chore:`, etc.
- PRs must reference an issue (`Closes #N`) and pass CI before merge.

## Definition of Done

- [ ] Code merged via a reviewed, green PR
- [ ] Tests written and passing for new behavior
- [ ] Docs updated if behavior changed
