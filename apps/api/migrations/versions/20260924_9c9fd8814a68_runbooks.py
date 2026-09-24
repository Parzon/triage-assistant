"""runbooks, searched to ground the assistant's answers

Runbooks and their sections (chunks), for answers that cite the team's
runbooks (RFC-0001, ADR-0017):
- the pgvector extension: the database image is pgvector's build of the
  same PostgreSQL 17 (compose.yaml);
- runbook_chunks.search, a generated tsvector with a GIN index: the
  keyword half of hybrid retrieval;
- runbook_chunks.embedding, vector(768) with an HNSW index on cosine
  distance: the meaning half;
- row-level security as on alerts (ADR-0014): team members read, team
  admins write and delete, org admins do everything; no context, no rows.

New tables only: the previous release never reads them, so there is no
expand/contract to do, and indexes on empty tables need no CONCURRENTLY.

Revision ID: 9c9fd8814a68
Revises: ce83ff21ab01
Create Date: 2026-09-24 09:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.vector import Vector

revision: str = "9c9fd8814a68"
down_revision: str | None = "ce83ff21ab01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG_ADMIN = "(SELECT current_setting('app.org_admin', true)) = 'on'"


def teams(setting: str) -> str:
    # As in ce83ff21ab01: the cast keeps ANY in its array form.
    return f"team_id = ANY ((SELECT tenant_team_ids('app.{setting}'))::bigint[])"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "runbooks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_runbooks_team_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "title", name="uq_runbooks_team_id_title"),
    )
    op.create_table(
        "runbook_chunks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("runbook_id", sa.BigInteger(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "search",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', heading || ' ' || content)", persisted=True),
            nullable=False,
        ),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["runbook_id"],
            ["runbooks.id"],
            name="fk_runbook_chunks_runbook_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_runbook_chunks_team_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runbook_chunks_runbook_id", "runbook_chunks", ["runbook_id"])
    op.create_index("ix_runbook_chunks_team_id", "runbook_chunks", ["team_id"])
    op.create_index(
        "ix_runbook_chunks_search", "runbook_chunks", ["search"], postgresql_using="gin"
    )
    op.create_index(
        "ix_runbook_chunks_embedding",
        "runbook_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    for table in ("runbooks", "runbook_chunks"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_select ON {table} FOR SELECT "
            f"USING ({ORG_ADMIN} OR {teams('read_team_ids')})"
        )
        op.execute(
            f"CREATE POLICY {table}_insert ON {table} FOR INSERT "
            f"WITH CHECK ({ORG_ADMIN} OR {teams('admin_team_ids')})"
        )
        op.execute(
            f"CREATE POLICY {table}_delete ON {table} FOR DELETE "
            f"USING ({ORG_ADMIN} OR {teams('admin_team_ids')})"
        )
    # A runbook's text is replaced in place (same team, same title); its
    # chunks are deleted and inserted again, never updated.
    op.execute(
        f"CREATE POLICY runbooks_update ON runbooks FOR UPDATE "
        f"USING ({ORG_ADMIN} OR {teams('admin_team_ids')}) "
        f"WITH CHECK ({ORG_ADMIN} OR {teams('admin_team_ids')})"
    )


def downgrade() -> None:
    op.drop_table("runbook_chunks")
    op.drop_table("runbooks")
    op.execute("DROP EXTENSION IF EXISTS vector")
