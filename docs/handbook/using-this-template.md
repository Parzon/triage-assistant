# Starting a new service from this template

This repository is a GitHub template. A new service starts as a copy of
it, then changes three kinds of thing:
- **the domain:** alerts become claims, contracts, tickets;
- **the AI part:** the prompt, the context, the eval cases;
- **the configuration:** the identity provider, the model provider, the
  cloud.

Everything else is the platform, and stays as it is.

## What to keep, replace and configure

| | What | Where |
|---|---|---|
| **Keep** | Compose files, Makefile, Dockerfiles, CI and release workflows | root, `.github/` |
| **Keep** | Sign-in, sessions, roles (teams own data) | `app/oidc.py`, `app/sessions.py`, `app/access.py` |
| **Keep** | Database access, pools, timeouts, row-level security plumbing | `app/db.py`, ADR-0005, ADR-0010, ADR-0014 |
| **Keep** | Rate limits, SSE streaming, errors, logging, metrics | `app/ratelimit.py`, `app/sse.py`, `app/errors.py`, `app/logs.py`, `app/metrics.py` |
| **Keep** | The model seam and the mock | `app/llm.py`, `tools/mock-llm/` |
| **Keep** | The eval harness | `apps/api/evals/*.py` |
| **Keep** | Deploys, backups, drills, monitoring | `scripts/`, `infra/observability/` |
| **Replace** | The data model and its migrations | `app/models.py`, `apps/api/migrations/versions/` |
| **Replace** | Routes, the visibility queries, request and response shapes | `app/routes/alerts.py`, `app/queries.py`, `app/schemas.py` |
| **Replace** | The prompt and its context | `app/triage.py` |
| **Replace** | Eval cases, judge calibration answers, the baseline | `apps/api/evals/cases/`, `judge_calibration.toml`, `baselines/` |
| **Replace** | The mock's canned answer | `tools/mock-llm/mock_llm.py` |
| **Replace** | The UI | `apps/web/src/` |
| **Replace** | Browser tests, load scripts | `tests/e2e/specs/`, `tests/load/` |
| **Replace** | The demo identity realm's users and groups, if your teams differ | `infra/keycloak/triage-realm.json` |
| **Configure** | The model provider, the identity provider, the public URL, secrets | `.env` (from `.env.example`) |
| **Configure** | The registry and the hosts | `IMAGE_PREFIX`; [the VM runbook](../runbooks/demo-vm.md), [environments](environments-and-shipping.md) |

## 1. Create the repository

On GitHub, **Use this template** → **Create a new repository**. Or:

```bash
gh repo create acme-corp/claims-helper --template Parzon/triage-assistant --private --clone
```

The copy starts with a single commit. It has no history and no link back
to the template (see "Keeping up with the template" below).

## 2. Rename it ✅

```bash
scripts/new-project.sh claims-helper acme-corp
make setup && make check
git commit -am "chore: rename the template to claims-helper"
```

The script replaces the template's identifiers everywhere outside the
docs:
- the compose project names, the image names and the page heading;
- the owner in `CODEOWNERS` and in the alert rules' runbook links;
- the registry path.

It also renames the cookie prefix (`COOKIE_PREFIX` in
`app/sessions.py`). Browsers scope cookies by host, not port: two
services from this template on `localhost` with the same cookie names
would sign each other out.

It leaves alone:
- the docs, which describe the template and are yours to rewrite;
- the database role names;
- the demo realm (`triage`, client `triage-web`), which your
  organisation's identity provider replaces;
- `app/triage.py`, which you rewrite anyway.

Tested on a fresh clone, with the longest name the script accepts (40
characters): `make setup`, `make check` (229 api and 49 web tests) and
`make obs-check` all pass. The first run failed lint: even a
13-character name pushed the cookie-name line in `app/sessions.py`
past 100 characters. The names now come from one constant, and the
tests ask the app for them instead of repeating them.

## 3. Repository settings

What this repository uses (read from GitHub's API), and why:

| Setting | Value | Why |
|---|---|---|
| Visibility | public here; **private** for company code | Branch protection on a *private* repository needs a paid plan (Pro, Team or Enterprise) |
| Merge buttons | squash only; delete the branch on merge | one commit per PR on `main`, titled by the PR |
| Branch protection on `main` | see the next list | nothing reaches `main` without CI and a review |
| Secret scanning, with push protection | on | refuses a push containing a recognised credential |
| Dependabot security updates | on | PRs for vulnerable dependencies; `.github/dependabot.yml` adds weekly grouped version updates |
| Private vulnerability reporting | linked from `.github/ISSUE_TEMPLATE/config.yml` | security reports never land in a public issue |
| Template repository | on here only | leave it off on your copy |

Branch protection on `main`:
- **Required checks:** `lint`, `test-api`, `build`, `web-build` and
  `e2e`, with the branch up to date with `main`.
- **Reviews:** one approval, from a code owner (`.github/CODEOWNERS`:
  replace the handles with teams, since a person is a bottleneck).
- **Conversations** resolved before merging.
- **History:** linear; no force pushes; no deletion.
- **Admins can bypass.**
  - A team of one cannot approve its own PR, so the owner merges with
    `gh pr merge --squash --admin` once every check is green. This repo
    was built that way.
  - With two or more people, turn on "Do not allow bypassing" and
    review each other's work.

**Actions permissions:** the workflows declare what they need, and
nothing needs configuring:
- `contents: read` everywhere;
- `packages: write` only for the release jobs that push images.

## 4. Package visibility (GHCR)

The first `vX.Y.Z` tag publishes three images under the repository's
owner:
- `ghcr.io/<owner>/<name>-api`;
- `ghcr.io/<owner>/<name>-web`;
- `ghcr.io/<owner>/<name>-edge`.

Each is a *package*, with its own visibility, set in the package's
settings (Danger Zone → Change visibility). Check it after the first
release.

- **Public:** anyone can pull, with no login. That suits open source,
  and it is how this template's images are published (checked with an
  anonymous pull of each manifest: HTTP 200).
- **Private:** the right default for company code. **The image contains
  your code.** A host pulls only after `docker login ghcr.io` with a
  token holding `read:packages`, stored like any other secret.
  Alternatively, push to your cloud's registry (ECR, ACR, Artifact
  Registry) and set `IMAGE_PREFIX` to it.

To check what a host will see, without logging in:

```bash
img=ghcr.io/<owner>/<name>-api; tag=0.1.0
token=$(curl -s "https://ghcr.io/token?scope=repository:${img#ghcr.io/}:pull" | sed 's/.*"token":"\([^"]*\)".*/\1/')
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $token" \
  -H "Accept: application/vnd.oci.image.index.v1+json" "https://ghcr.io/v2/${img#ghcr.io/}/manifests/$tag"
# 200: public. 401 or 403: private, or no such image or tag.
```

## 5. First run, first release

1. `make setup && make up`, then sign in at http://localhost:5173 as
   alice. The demo password is `DEMO_USER_PASSWORD` in `.env`.
2. `make prod-up && make e2e`: the production shape, in a real browser.
3. On `main`, run `git tag -a v0.1.0 -m "..."` and `git push origin
   v0.1.0`. The release workflow builds for amd64 and arm64, smoke-tests
   both, and writes release notes.
4. Set the package visibility (above).
5. Set up a host with [the VM runbook](../runbooks/demo-vm.md).

## 6. Replace the domain, in this order

1. **A PRD** ([template](../prd/TEMPLATE.md); [this template's
   own](../prd/0001-triage-assistant.md)): what, for whom, how success is
   measured.
2. **The data model:**
   - edit `app/models.py`, then `make migration m="..."`;
   - keep users, teams, memberships and sessions;
   - every new table holding team data gets:
     - a `team_id`;
     - row-level security policies;
     - database-level tests, in the same migration (AGENTS.md).
3. **Routes and queries:**
   - every data route takes `principal: CurrentUser`;
   - reads go through a visibility query in `app/queries.py`;
   - writes check `require_role`;
   - what a caller cannot see is a 404.

   Tests for each route: success, validation, 401, 404 for another team,
   403 for a role too low, `csrf_failed` ([testing](testing.md)).
4. **The eval cases, before the prompt.** Write down what good answers
   look like for the new domain, in the four kinds. Include isolation
   cases: the new context must come from the visibility query. Label a
   YES and a NO for every judge criterion
   ([AI engineering](ai-engineering.md)).
5. **The prompt and its context** (`app/triage.py`). Measure with `make
   evals` against a real model, and commit a baseline.
6. **The mock's canned answer**, so plumbing-mode evals and the tests
   make sense.
7. **The UI**, then **browser tests** for the flows only a browser
   shows (sign-in, streaming, two users seeing different data).
8. **Load and drills:**
   - point `tests/load/` at the new endpoints;
   - re-run `make load` and `make drills`;
   - update the numbers in the docs, or delete the ones that no longer
     apply.
9. **Docs:**
   - README and [the overview](../overview.md);
   - a runbook section per new alert;
   - an ADR for each decision a newcomer would question.

## Keeping up with the template

A copy has no upstream. To take a fix from the template:

```bash
git remote add template https://github.com/Parzon/triage-assistant.git
git fetch template
git log --oneline template/main        # read the release notes too
git cherry-pick <commit>               # one fix at a time
```

Never merge `template/main` wholesale. The histories are unrelated, and
it would bring the template's domain back. Watch the template's
releases (Watch → Custom → Releases): each release note says what an
operator must do.

## What not to change without an ADR

These are the rules that stop copies drifting into different platforms:
- the access model (roles, 404 for invisible data, row-level security);
- the deploy path (`make deploy`, expand/contract migrations);
- the timeout chain;
- the health endpoints' split;
- the eval gate for prompt and model changes.

Change one only on purpose, and write down why
([the guide's rules](../../gold_standard_development_guide.md#the-rules)).
