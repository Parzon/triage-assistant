"""audit events: calls from an MCP client (via "mcp", ADR-0020)

Widens a check constraint: the previous release never writes the new value,
so rolling back is safe. The downgrade puts the narrower check back NOT
VALID: rows already written by MCP calls stay, as an audit trail must.

Revision ID: 5b7f0c2d9e41
Revises: e46b40a7952a
Create Date: 2026-09-24 23:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5b7f0c2d9e41"
down_revision: str | None = "e46b40a7952a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_audit_events_via", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_via", "audit_events", "via IN ('api', 'mcp', 'alertmanager', 'cli')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_events_via", "audit_events", type_="check")
    op.execute(
        "ALTER TABLE audit_events ADD CONSTRAINT ck_audit_events_via "
        "CHECK (via IN ('api', 'alertmanager', 'cli')) NOT VALID"
    )
