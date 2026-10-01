# Schema migrations as the owner, before the api starts (an init container).
# On a rollback the database can be ahead of this release: alembic then cannot find the
# current revision, and the right move is a code-only rollback, not a failed deploy.
# Any other error (wrong password, unreachable host) still fails the task.
export MIGRATIONS_DATABASE_URL="postgresql://${DB_OWNER_USER}:${DB_OWNER_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
if ! out=$(alembic current 2>&1); then
  if printf '%s' "$out" | grep -q "Can't locate revision"; then
    echo "the database is ahead of this release: code-only rollback, no migration"; exit 0
  fi
  printf '%s\n' "$out" >&2; exit 1
fi
exec alembic upgrade head
