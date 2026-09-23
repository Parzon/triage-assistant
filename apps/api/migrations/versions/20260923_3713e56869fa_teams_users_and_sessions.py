"""teams users and sessions

Sign-in and team ownership (ADR-0013), the EXPAND half of an
expand/contract change. Written to run while the previous release is still
serving traffic, which knows nothing about teams:

- alerts.team_id arrives with a DEFAULT (the "default" team), so the old
  release's INSERTs keep working during a rolling deploy. Adding a column
  with a constant default is instant on Postgres 11+ (no table rewrite).
  The contract migration drops the default once no old release can run.
- The foreign key is added NOT VALID (instant; checks only new rows), then
  validated in its own transaction: VALIDATE CONSTRAINT scans the table
  under a lock that does not block reads or writes. Adding it valid in one
  step would block every write to alerts for the whole scan.
- The team index is built CONCURRENTLY (no write lock), outside a
  transaction - see 20260922_75397b875fb5 for the rules.

Revision ID: 3713e56869fa
Revises: 51ef9f5449b0
Create Date: 2026-09-23 09:20:01.372302
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3713e56869fa"
down_revision: str | None = "51ef9f5449b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'", name="ck_teams_slug"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_teams_slug"),
    )
    # The first row of a table created in this same transaction: its
    # identity is 1, which the alerts.team_id default below relies on.
    op.execute("INSERT INTO teams (slug, name) VALUES ('default', 'Default')")

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("is_org_admin", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
    )
    op.create_table(
        "memberships",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.CheckConstraint("role IN ('viewer', 'responder', 'admin')", name="ck_memberships_role"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "team_id"),
    )
    op.create_index("ix_memberships_team_id", "memberships", ["team_id"])
    op.create_table(
        "sessions",
        sa.Column("id_hash", sa.LargeBinary(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id_token", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id_hash"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_table(
        "login_requests",
        sa.Column("state_hash", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("next_path", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state_hash"),
    )
    op.create_index("ix_login_requests_expires_at", "login_requests", ["expires_at"])

    op.add_column(
        "alerts",
        sa.Column("team_id", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.execute(
        "ALTER TABLE alerts ADD CONSTRAINT fk_alerts_team_id "
        "FOREIGN KEY (team_id) REFERENCES teams (id) NOT VALID"
    )
    with op.get_context().autocommit_block():
        op.execute("ALTER TABLE alerts VALIDATE CONSTRAINT fk_alerts_team_id")
        op.create_index(
            "ix_alerts_team_id_created_at_id",
            "alerts",
            ["team_id", "created_at", "id"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_alerts_team_id_created_at_id", table_name="alerts", postgresql_concurrently=True
        )
    op.drop_constraint("fk_alerts_team_id", "alerts", type_="foreignkey")
    op.drop_column("alerts", "team_id")
    op.drop_table("login_requests")
    op.drop_table("sessions")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("teams")
