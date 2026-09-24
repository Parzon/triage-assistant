"""ORM models. Every change here needs an Alembic migration; CI runs
`alembic check`, which fails when models and migrations disagree."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.vector import Vector

SEVERITIES = ("info", "warning", "high", "critical")
ROLES = ("viewer", "responder", "admin")
# Lowercase, digits and dashes: safe in URLs, claims and log lines.
TEAM_SLUG = r"^[a-z0-9][a-z0-9-]{0,62}$"
# Owns the alerts nobody routed to a team (created before teams existed,
# or sent by Alertmanager without a `team` label). Created by the migration
# that introduced teams.
DEFAULT_TEAM = "default"
# Every stored embedding has this many dimensions: the column's type. An
# embedding model with another native size must be asked for this one
# (EMBEDDING_DIMENSIONS), or a migration must change the column.
EMBEDDING_DIM = 768


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


class Runbook(Base):
    """A team's runbook, in Markdown, searched section by section
    (RunbookChunk) to ground the assistant's answers (ADR-0017). Every member
    of the team reads it; only its admins write it. Row-level security, as
    on alerts."""

    __tablename__ = "runbooks"
    __table_args__ = (UniqueConstraint("team_id", "title", name="uq_runbooks_team_id_title"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE", name="fk_runbooks_team_id")
    )
    title: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    # A re-upload with the same text and the same embedding model changes
    # nothing: no chunks rewritten, no embedding calls paid for.
    body_sha256: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunbookChunk(Base):
    """One section of a runbook: the unit of retrieval. team_id repeats the
    runbook's, so row-level security and retrieval filter without a join."""

    __tablename__ = "runbook_chunks"
    __table_args__ = (
        Index("ix_runbook_chunks_runbook_id", "runbook_id"),
        Index("ix_runbook_chunks_team_id", "team_id"),
        # Keyword half of hybrid retrieval: full-text search.
        Index("ix_runbook_chunks_search", "search", postgresql_using="gin"),
        # Meaning half: approximate nearest neighbours by cosine distance.
        Index(
            "ix_runbook_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    runbook_id: Mapped[int] = mapped_column(
        ForeignKey("runbooks.id", ondelete="CASCADE", name="fk_runbook_chunks_runbook_id")
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE", name="fk_runbook_chunks_team_id")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    # The heading path, "Disk full > Free space": what a citation shows.
    heading: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    search: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', heading || ' ' || content)", persisted=True),
        deferred=True,
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), deferred=True)
    # Vectors from different models are not comparable: retrieval only
    # uses chunks embedded by the model it asks with.
    embedding_model: Mapped[str] = mapped_column(Text)
