"""audit events: who wrote what the assistant reads, and what it was given

One append-only table (ADR-0019):
- the app's role may INSERT and SELECT, never UPDATE or DELETE: both are
  revoked from every role the default privileges granted them to
  (infra/postgres/initdb), and row-level security has no policy for them
  either. Pruning is the owner's (`make audit-prune`);
- row-level security: org admins read; a row names as its actor only the
  user the transaction acts for (app.user_id), or no user (the webhook, an
  operator's command).

A new table: the previous release never reads it, so rolling back is safe
and nothing needs expand/contract.

Revision ID: e46b40a7952a
Revises: 9c9fd8814a68
Create Date: 2026-09-24 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e46b40a7952a"
down_revision: str | None = "9c9fd8814a68"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_ADMIN = "(SELECT current_setting('app.org_admin', true)) = 'on'"
# NULL before app.user_id was ever set in the connection, '' after: no user.
USER_ID = "(SELECT nullif(current_setting('app.user_id', true), '')::bigint)"


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("via", sa.Text(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=True),
        sa.Column("target_id", sa.BigInteger(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.CheckConstraint("action ~ '^[a-z_]+\\.[a-z_]+$'", name="ck_audit_events_action"),
        sa.CheckConstraint("via IN ('api', 'alertmanager', 'cli')", name="ck_audit_events_via"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_created_at_id", "audit_events", ["created_at", "id"])
    op.create_index("ix_audit_events_target_id", "audit_events", ["target_id"])

    # Append-only for everyone but the owner. The default privileges grant
    # UPDATE and DELETE on every new table to the app's role, whose name is
    # a setting (APP_DB_USER): revoke them from whoever got them.
    op.execute(
        """
        DO $$
        DECLARE role_name text;
        BEGIN
          FOR role_name IN
            SELECT DISTINCT grantee FROM information_schema.role_table_grants
            WHERE table_schema = 'public' AND table_name = 'audit_events'
              AND grantee <> current_user
          LOOP
            EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM %I', role_name);
          END LOOP;
        END $$
        """
    )
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY audit_events_select ON audit_events FOR SELECT USING ({ORG_ADMIN})")
    op.execute(
        f"CREATE POLICY audit_events_insert ON audit_events FOR INSERT "
        f"WITH CHECK (actor_user_id IS NULL OR actor_user_id = {USER_ID})"
    )


def downgrade() -> None:
    op.drop_table("audit_events")
