"""ORM models. Every change here needs an Alembic migration; CI runs
`alembic check`, which fails when models and migrations disagree."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SEVERITIES = ("info", "warning", "high", "critical")
ROLES = ("viewer", "responder", "admin")
# Lowercase, digits and dashes: safe in URLs, claims and log lines.
TEAM_SLUG = r"^[a-z0-9][a-z0-9-]{0,62}$"
# Owns the alerts nobody routed to a team (created before teams existed,
# or sent by Alertmanager without a `team` label). Created by the migration
# that introduced teams.
DEFAULT_TEAM = "default"


class Base(DeclarativeBase):
    pass


class Team(Base):
    """Teams come from the identity provider: a sign-in whose claims name
    an unknown team creates it (see app/access.py)."""

    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint(f"slug ~ '{TEAM_SLUG}'", name="ck_teams_slug"),
        UniqueConstraint("slug", name="uq_teams_slug"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    slug: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    """A person, as the identity provider identifies them. (issuer, subject)
    is the only stable identifier OIDC guarantees: email addresses change
    and get reassigned, so they are stored for display, never matched on."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    issuer: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    # Refreshed from the claims at every sign-in, like the memberships.
    is_org_admin: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Membership(Base):
    """A user's role in a team. Replaced from the claims at every sign-in:
    the identity provider is the source of truth."""

    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("role IN ('viewer', 'responder', 'admin')", name="ck_memberships_role"),
        # "Who is in this team", and the cascade when a team is deleted.
        Index("ix_memberships_team_id", "team_id"),
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text)


class UserSession(Base):
    """A signed-in browser. The cookie holds a random token; only its
    SHA-256 is stored, so a leaked table or backup cannot be replayed as
    cookies."""

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_user_id", "user_id"),  # sign a user out everywhere
        Index("ix_sessions_expires_at", "expires_at"),  # delete expired sessions
    )
    id_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Sent back to the identity provider at sign-out (id_token_hint), so it
    # ends its own session without asking the user to confirm.
    id_token: Mapped[str | None] = mapped_column(Text)


class LoginRequest(Base):
    """A sign-in in progress, between the redirect to the identity provider
    and its redirect back. Single use: the callback deletes it."""

    __tablename__ = "login_requests"
    __table_args__ = (Index("ix_login_requests_expires_at", "expires_at"),)
    state_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    nonce: Mapped[str] = mapped_column(Text)
    code_verifier: Mapped[str] = mapped_column(Text)
    next_path: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
        # Serves every newest-first read across all teams (an org admin's
        # list) as a backward index scan. Measured on 2M rows: 152ms ->
        # 0.1ms, severity=critical 43ms -> 3.8ms. A second index on
        # (severity, created_at, id) takes that to 0.06ms but costs every
        # insert (~2x this one's); add it when a rarer filter shows up in
        # the SlowRequests alert, not before. See the performance chapter.
        Index("ix_alerts_created_at_id", "created_at", "id"),
        # One team's alerts, newest first: what almost every user reads.
        # Several teams are read one team at a time and merged (see
        # app/routes/alerts.py), each through this index.
        Index("ix_alerts_team_id_created_at_id", "team_id", "created_at", "id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # Row-level security (migration ce83ff21ab01, ADR-0014): the app role
    # sees and changes only rows of the teams the transaction names.
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", name="fk_alerts_team_id"))
    source: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    external_id: Mapped[str | None] = mapped_column(Text)
