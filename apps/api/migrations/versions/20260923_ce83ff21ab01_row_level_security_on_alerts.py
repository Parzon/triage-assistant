"""row level security on alerts

The CONTRACT half of the change that introduced teams (ADR-0014): Postgres
itself now refuses rows outside the caller's teams, behind the app's own
checks. Safe to run while v0.2.0+ serves, because that release tells
Postgres who is asking in every transaction (app/db.py). Against an older
release it would hide every alert: never skip a version to get here.

- The policies read what the app sets with set_config(..., true):
  app.read_team_ids, app.write_team_ids, app.admin_team_ids (Postgres
  array literals), app.org_admin ('on'), app.service ('alertmanager').
- No context, no rows: a query that forgot to say who is asking sees
  nothing and can change nothing. The owner role (migrations, seeding,
  backups) is not subject to RLS; the app role is.
- A setting is NULL before it was ever set in a connection, and '' after a
  transaction that set it ended; both mean "no teams", not an error.
- Each policy reads a setting through a scalar subquery: an InitPlan,
  evaluated once per query, instead of once per row.
- UPDATE has no policy: nothing may update an alert (the app never does).
- alerts.team_id loses its default: an INSERT without a team now fails
  instead of landing in the default team.

Revision ID: ce83ff21ab01
Revises: 3713e56869fa
Create Date: 2026-09-23 14:31:02.114372
"""

from collections.abc import Sequence

from alembic import op

revision: str = "ce83ff21ab01"
down_revision: str | None = "3713e56869fa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_ADMIN = "(SELECT current_setting('app.org_admin', true)) = 'on'"
SERVICE = "(SELECT current_setting('app.service', true)) = 'alertmanager'"


def teams(setting: str) -> str:
    # The cast keeps ANY in its array form: `= ANY ((SELECT ...))` without
    # it is the subquery form, comparing bigint with bigint[].
    return f"team_id = ANY ((SELECT tenant_team_ids('app.{setting}'))::bigint[])"


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION tenant_team_ids(setting text) RETURNS bigint[]
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$ SELECT coalesce(nullif(current_setting(setting, true), ''), '{}')::bigint[] $$
        """
    )
    op.execute("ALTER TABLE alerts ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY alerts_select ON alerts FOR SELECT "
        f"USING ({ORG_ADMIN} OR {SERVICE} OR {teams('read_team_ids')})"
    )
    op.execute(
        f"CREATE POLICY alerts_insert ON alerts FOR INSERT "
        f"WITH CHECK ({ORG_ADMIN} OR {SERVICE} OR {teams('write_team_ids')})"
    )
    op.execute(
        f"CREATE POLICY alerts_delete ON alerts FOR DELETE "
        f"USING ({ORG_ADMIN} OR {teams('admin_team_ids')})"
    )
    op.alter_column("alerts", "team_id", server_default=None)


def downgrade() -> None:
    # The default team is the first row the expand migration inserted: id 1.
    op.execute("ALTER TABLE alerts ALTER COLUMN team_id SET DEFAULT 1")
    for policy in ("alerts_delete", "alerts_insert", "alerts_select"):
        op.execute(f"DROP POLICY {policy} ON alerts")
    op.execute("ALTER TABLE alerts DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION tenant_team_ids(text)")
