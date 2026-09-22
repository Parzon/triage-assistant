"""ORM models. Every change here needs an Alembic migration; CI runs
`alembic check`, which fails when models and migrations disagree."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SEVERITIES = ("info", "warning", "high", "critical")


class Base(DeclarativeBase):
    pass


class Alert(Base):
    """Server-generated columns (id, created_at) come back from the INSERT
    via RETURNING: SQLAlchemy 2's default eager_defaults="auto" on Postgres."""

    __tablename__ = "alerts"
    __table_args__ = (
        # Text + CHECK instead of a Postgres ENUM: adding a value later is a
        # one-line constraint change, not an ALTER TYPE.
        CheckConstraint(
            "severity IN ('info', 'warning', 'high', 'critical')", name="ck_alerts_severity"
        ),
        # Idempotency key for alerts pushed by other systems: Alertmanager
        # re-sends a firing alert every repeat_interval. NULLs never
        # collide, so manually created alerts are unaffected.
        Index("uq_alerts_external_id", "external_id", unique=True),
        # Serves every newest-first read (the list, keyset pagination, the
        # chat's context) as a backward index scan. Measured on 2M rows:
        # 152ms -> 0.1ms, severity=critical 43ms -> 3.8ms. A second index on
        # (severity, created_at, id) takes that to 0.06ms but costs every
        # insert (~2x this one's); add it when a rarer filter shows up in
        # the SlowRequests alert, not before. See the performance chapter.
        Index("ix_alerts_created_at_id", "created_at", "id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    external_id: Mapped[str | None] = mapped_column(Text)
