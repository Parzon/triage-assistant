# shellcheck shell=bash
# Signed-in requests for scripts. Every data endpoint needs a session, and
# every state-changing request an Origin header naming the site (the api's
# CSRF check). Two ways to get a session:
#
#   mint_session <api container> <email> <group>...
#       Prints "name=value", a session cookie made by app.cli inside the api
#       container: no identity provider involved. For drills and load tests.
#   sign_in <base url> <user> <password> <cookie jar> [curl options...]
#       Signs in the way a browser does: the api's /api/auth/login, the
#       identity provider's login form, back through the callback. Leaves the
#       session in the jar. For smoke tests: it proves the whole path works.

mint_session() {
  local api=$1 email=$2 groups=() g
  shift 2
  for g in "$@"; do groups+=(--group "$g"); done
  docker exec "$api" python -m app.cli session --email "$email" "${groups[@]}" --hours 4
}

sign_in() {
  local base=$1 user=$2 password=$3 jar=$4
  shift 4
  local c=(curl -sf -c "$jar" -b "$jar" "$@") authorize page action back
  authorize=$("${c[@]}" -o /dev/null -w '%{redirect_url}' "$base/api/auth/login") || return 1
  page=$("${c[@]}" "$authorize") || { echo "no login page at $authorize" >&2; return 1; }
  action=$(grep -o 'id="kc-form-login"[^>]*action="[^"]*"' <<<"$page" | sed 's/.*action="//; s/"$//; s/&amp;/\&/g')
  [ -n "$action" ] || { echo "no login form at $authorize" >&2; return 1; }
  back=$("${c[@]}" -o /dev/null -w '%{redirect_url}' \
    --data-urlencode "username=$user" --data-urlencode "password=$password" "$action") || return 1
  [[ $back == "$base/api/auth/callback?"* ]] || { echo "not sent back to the app: ${back:-no redirect}" >&2; return 1; }
  "${c[@]}" -o /dev/null "$back" || return 1
  "${c[@]}" "$base/api/me" | grep -q '"email"'
}
