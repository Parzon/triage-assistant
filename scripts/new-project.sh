#!/usr/bin/env bash
# Make a fresh copy of this template your own project:
#   scripts/new-project.sh <name> <github-owner>      e.g. claims-helper acme-corp
#
# Replaces the template's identifiers everywhere outside the docs:
# - "Parzon/triage-assistant" (repository links: runbook URLs in the alert
#   rules, the security-advisory link, the VM bootstrap's clone URL);
# - "@Parzon" (CODEOWNERS);
# - "triage-assistant" (compose projects, image names, the Grafana
#   folder, the page heading, the api's title);
# - the cookie names' prefix (COOKIE_PREFIX in app/sessions.py). Cookies
#   are scoped by host, not port: two projects from this template on
#   localhost would otherwise sign each other out.
#
# Left alone on purpose:
# - the docs, which describe the template and are yours to rewrite;
# - the database role names in .env.example;
# - the demo realm ("triage", client "triage-web"), which your
#   organisation's identity provider replaces;
# - app/triage.py, the AI module a new project rewrites anyway.
#
# Run it once, on a fresh copy, then `make setup && make check`.
# Portable: GNU and BSD (macOS) sed both accept `sed -i.bak`.
set -euo pipefail

usage='usage: scripts/new-project.sh <name> <github-owner>'
name=${1:?$usage}
owner=${2:?$usage}
if ! [[ $name =~ ^[a-z][a-z0-9-]{1,38}[a-z0-9]$ ]]; then
  echo "name: 3-40 lowercase letters, digits and dashes (it names containers, images and a cookie)" >&2
  exit 2
fi
if ! [[ $owner =~ ^[A-Za-z0-9][A-Za-z0-9-]*$ ]]; then
  echo "github-owner: a GitHub user or organisation name" >&2
  exit 2
fi
snake=${name//-/_}
# Registry paths are lowercase (ghcr.io/<owner>/...).
owner_lc=$(printf '%s' "$owner" | tr '[:upper:]' '[:lower:]')

cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain)" ]; then
  echo "commit or stash your changes first: this rewrites files in place" >&2
  exit 1
fi

files=$(git grep -l -I -i -e 'triage-assistant' -e 'COOKIE_PREFIX = "triage"' -e 'parzon' \
  -- ':!docs/' ':!*.md' ':!scripts/new-project.sh' || true)
if [ -z "$files" ]; then
  echo "nothing to rename: already done?"
  exit 0
fi

for f in $files; do
  sed -i.bak \
    -e "s#Parzon/triage-assistant#$owner/$name#g" \
    -e "s#@Parzon#@$owner#g" \
    -e "s#ghcr.io/parzon/#ghcr.io/$owner_lc/#g" \
    -e "s#triage-assistant#$name#g" \
    -e "s#COOKIE_PREFIX = \"triage\"#COOKIE_PREFIX = \"$snake\"#" \
    "$f"
  rm -f "$f.bak"
done

echo "renamed in $(echo "$files" | wc -l | tr -d ' ') files:"
git diff --stat | tail -1
left=$(git grep -n -I -i -e 'triage-assistant' -e 'parzon' -- ':!docs/' ':!*.md' ':!scripts/new-project.sh' || true)
if [ -n "$left" ]; then
  echo "still mentioning the template (check these by hand):" >&2
  echo "$left" >&2
fi
cat <<EOF

Next:
  make setup && make check     # everything still passes under the new name
  git commit -am "chore: rename the template to $name"
Then docs/handbook/using-this-template.md, from "Replace the domain",
and TECH_STACK.md: what your service needs on day one, and what waits.
EOF
