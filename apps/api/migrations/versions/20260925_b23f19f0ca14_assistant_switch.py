"""assistant switch: turn the assistant off without a deploy (ADR-0024)

One row, for the whole organisation, inserted here, switched on:
- everyone reads it: every question does, before anything else;
- only org admins change it (row-level security, as the api checks), and
  a row names as its author only the user the transaction acts for, or
  no user (an operator's command);
- nobody adds or removes it: INSERT and DELETE are revoked from every role
  the default privileges granted them to, and have no policy either.

A new table: the previous release never reads it, so rolling back is safe
and nothing needs expand/contract.

Revision ID: b23f19f0ca14
Revises: 5b7f0c2d9e41
Create Date: 2026-09-25 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b23f19f0ca14"
down_revision: str | None = "5b7f0c2d9e41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_ADMIN = "(SELECT current_setting('app.org_admin', true)) = 'on'"
# NULL before app.user_id was ever set in the connection, '' after: no user.
USER_ID = "(SELECT nullif(current_setting('app.user_id', true), '')::bigint)"


def upgrade() -> None:
    op.create_table(
        "assistant_switch",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_assistant_switch_one_row"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("INSERT INTO assistant_switch (id) VALUES (1)")

    op.execute(
        """
        DO $$
        DECLARE role_name text;
        BEGIN
          FOR role_name IN
            SELECT DISTINCT grantee FROM information_schema.role_table_grants
            WHERE table_schema = 'public' AND table_name = 'assistant_switch'
              AND grantee <> current_user
          LOOP
            EXECUTE format('REVOKE INSERT, DELETE, TRUNCATE ON assistant_switch FROM %I',
                           role_name);
          END LOOP;
        END $$
        """
    )
    op.execute("ALTER TABLE assistant_switch ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY assistant_switch_select ON assistant_switch FOR SELECT USING (true)")
    op.execute(
        f"CREATE POLICY assistant_switch_update ON assistant_switch FOR UPDATE "
        f"USING ({ORG_ADMIN}) "
        f"WITH CHECK ({ORG_ADMIN} AND (changed_by IS NULL OR changed_by = {USER_ID}))"
    )


def downgrade() -> None:
    op.drop_table("assistant_switch")
