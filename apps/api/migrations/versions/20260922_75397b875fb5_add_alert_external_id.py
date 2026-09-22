"""add alert external id

Zero-downtime shape, the pattern for any index on a table in use:
- ADD COLUMN ... NULL is instant (metadata only, no table rewrite).
- A plain CREATE INDEX blocks every INSERT/UPDATE/DELETE until it
  finishes; CONCURRENTLY builds without blocking writes, but cannot run
  inside a transaction - hence the autocommit block. If it fails midway
  it leaves an INVALID index that must be dropped before retrying.

Revision ID: 75397b875fb5
Revises: 39b0de0263bc
Create Date: 2026-09-22 18:54:41.243503
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "75397b875fb5"
down_revision: str | None = "39b0de0263bc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("external_id", sa.Text(), nullable=True))
    with op.get_context().autocommit_block():
        op.create_index(
            "uq_alerts_external_id",
            "alerts",
            ["external_id"],
            unique=True,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("uq_alerts_external_id", table_name="alerts", postgresql_concurrently=True)
    op.drop_column("alerts", "external_id")
