"""index alerts by created_at id

Found by the load test (performance chapter): every newest-first read was a
parallel sequential scan of the whole table. Built CONCURRENTLY so writes
continue during the build (0.75s on 2M rows); see the external_id migration
for why that needs the autocommit block.

Revision ID: 51ef9f5449b0
Revises: 75397b875fb5
Create Date: 2026-09-22 19:24:23.377353
"""

from collections.abc import Sequence

from alembic import op

revision: str = "51ef9f5449b0"
down_revision: str | None = "75397b875fb5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_alerts_created_at_id",
            "alerts",
            ["created_at", "id"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("ix_alerts_created_at_id", table_name="alerts", postgresql_concurrently=True)
