"""Sessions: who a browser is signed in as (ADR-0013).

After a sign-in, the browser holds one cookie: a random token, httpOnly
(scripts cannot read it, so an XSS bug cannot steal it), Secure, SameSite=
Lax. The server keeps the session in Postgres under the token's SHA-256.
Server-side rather than a signed token (JWT) in the cookie: signing out
really ends the session, an admin can end every session of a user at once,
and nothing about the user travels in the cookie.

Also here: the sign-in in progress (between the redirect to the identity
provider and back), and `current_principal`, the dependency every
authenticated route takes.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import Principal, Role, TeamAccess, access_from_claims, tenant_settings
from app.config import Settings
from app.db import DbSession, set_transaction_settings
from app.errors import ApiError
from app.metrics import auth_rejections
from app.models import LoginRequest, Membership, Team, User, UserSession
from app.oidc import Identity

LOGIN_TTL_S = 600
# last_seen_at is written at most this often per session: a write on every
# request would turn every read into a write.
TOUCH_INTERVAL_S = 300
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


# --- Cookies ----------------------------------------------------------------
# The __Host- prefix makes the browser refuse the cookie unless it is Secure,
# has Path=/ and no Domain: no subdomain can set or overwrite it. Plain-HTTP
# development cannot use it (session_cookie_secure=false).


def session_cookie(settings: Settings) -> str:
    return "__Host-triage_session" if settings.session_cookie_secure else "triage_session"


def login_cookie(settings: Settings) -> str:
    return "__Host-triage_login" if settings.session_cookie_secure else "triage_login"


def set_cookie(response: Response, settings: Settings, name: str, value: str, max_age: int) -> None:
    # Lax, not Strict: the identity provider sends the browser back to the
    # callback from another site, and a Strict cookie would not come along.
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_cookie(response: Response, settings: Settings, name: str) -> None:
    # A __Host- cookie is only deleted by a Set-Cookie that also says Secure.
    response.delete_cookie(
        name, path="/", secure=settings.session_cookie_secure, httponly=True, samesite="lax"
    )


# --- Sign-in in progress ------------------------------------------------------


async def start_login(
    db: AsyncSession, *, state: str, nonce: str, code_verifier: str, next_path: str
) -> None:
    await db.execute(delete(LoginRequest).where(LoginRequest.expires_at < func.now()))
    db.add(
        LoginRequest(
            state_hash=_digest(state),
            nonce=nonce,
            code_verifier=code_verifier,
            next_path=next_path,
            expires_at=func.now() + timedelta(seconds=LOGIN_TTL_S),
        )
    )


async def finish_login(db: AsyncSession, state: str) -> LoginRequest | None:
    """The sign-in `state` belongs to, removed so it can be used only once."""
    return (
        await db.scalars(
            delete(LoginRequest)
            .where(LoginRequest.state_hash == _digest(state), LoginRequest.expires_at > func.now())
            .returning(LoginRequest)
        )
    ).one_or_none()


# --- Sessions -----------------------------------------------------------------


async def sign_in(db: AsyncSession, *, issuer: str, identity: Identity, settings: Settings) -> str:
    """Record the user, replace their memberships from the claims, open a
    session. Returns the token for the cookie. The caller commits."""
    roles, org_admin = access_from_claims(identity.groups)
    upsert_user = insert(User).values(
        issuer=issuer,
        subject=identity.subject,
        email=identity.email,
        name=identity.name,
        is_org_admin=org_admin,
    )
    user_id = (
        await db.execute(
            upsert_user.on_conflict_do_update(
                constraint="uq_users_issuer_subject",
                set_={
                    "email": upsert_user.excluded.email,
                    "name": upsert_user.excluded.name,
                    "is_org_admin": upsert_user.excluded.is_org_admin,
                    "last_login_at": func.now(),
                },
            ).returning(User.id)
        )
    ).scalar_one()

    team_ids: dict[str, int] = {}
    if roles:
        await db.execute(
            insert(Team)
            .values([{"slug": slug, "name": slug} for slug in roles])
            .on_conflict_do_nothing(constraint="uq_teams_slug")
        )
        found = await db.execute(select(Team.slug, Team.id).where(Team.slug.in_(roles)))
        team_ids = dict(found.tuples().all())
    # Upsert, then delete the rest - not delete-all-then-insert, which fails
    # when two sign-ins of the same user overlap (two tabs).
    await db.execute(
        delete(Membership).where(
            Membership.user_id == user_id, Membership.team_id.not_in(list(team_ids.values()))
        )
    )
    if team_ids:
        upsert = insert(Membership).values(
            [
                {"user_id": user_id, "team_id": team_ids[slug], "role": role.label}
                for slug, role in roles.items()
            ]
        )
        await db.execute(
            upsert.on_conflict_do_update(
                index_elements=["user_id", "team_id"], set_={"role": upsert.excluded.role}
            )
        )

    await db.execute(delete(UserSession).where(UserSession.expires_at < func.now()))
    token = new_token()
    db.add(
        UserSession(
            id_hash=_digest(token),
            user_id=user_id,
            expires_at=func.now() + timedelta(seconds=settings.session_max_age_s),
            id_token=identity.id_token,
        )
    )
    return token


async def authenticate(db: AsyncSession, token: str, settings: Settings) -> Principal | None:
    """The principal behind a session token, or None if it is unknown,
    expired or idle for too long.

    One query - session, user and memberships together, a row per team - in
    the request's own transaction. Measured: a query per table plus a
    transaction of its own doubled the CPU of the cheapest request
    (handbook, performance chapter).
    """
    now = func.now()
    rows = (
        await db.execute(
            select(
                User.id,
                User.email,
                User.name,
                User.is_org_admin,
                (UserSession.last_seen_at < now - timedelta(seconds=TOUCH_INTERVAL_S)).label(
                    "stale"
                ),
                Team.id.label("team_id"),
                Team.slug.label("team_slug"),
                Team.name.label("team_name"),
                Membership.role,
            )
            .select_from(UserSession)
            .join(User, User.id == UserSession.user_id)
            .outerjoin(Membership, Membership.user_id == User.id)
            .outerjoin(Team, Team.id == Membership.team_id)
            .where(
                UserSession.id_hash == _digest(token),
                UserSession.expires_at > now,
                UserSession.last_seen_at > now - timedelta(seconds=settings.session_idle_timeout_s),
            )
            .order_by(Team.slug)
        )
    ).all()
    if not rows:
        return None
    first = rows[0]
    principal = Principal(
        user_id=first.id,
        email=first.email,
        name=first.name,
        org_admin=first.is_org_admin,
        teams=tuple(
            TeamAccess(r.team_id, r.team_slug, r.team_name, Role[r.role.upper()])
            for r in rows
            if r.team_id is not None
        ),
    )
    if first.stale:
        await db.execute(
            update(UserSession)
            .where(UserSession.id_hash == _digest(token))
            .values(last_seen_at=now)
        )
        # Committed now: a request that only reads never commits, and the
        # touch must persist. At most once per session per TOUCH_INTERVAL_S.
        await db.commit()
    return principal


async def sign_out(db: AsyncSession, token: str) -> str | None:
    """End one session; returns its ID token for the provider's sign-out."""
    return (
        await db.scalars(
            delete(UserSession)
            .where(UserSession.id_hash == _digest(token))
            .returning(UserSession.id_token)
        )
    ).one_or_none()


async def sign_out_everywhere(db: AsyncSession, user_id: int) -> int:
    """End every session of a user (access revoked, device lost)."""
    ended = await db.execute(
        delete(UserSession).where(UserSession.user_id == user_id).returning(UserSession.user_id)
    )
    return len(ended.all())


# --- The request's principal --------------------------------------------------


def check_same_origin(request: Request, settings: Settings) -> None:
    """CSRF defence for cookie-authenticated requests that change state.

    Browsers attach cookies to requests other sites make (a form posting
    here from evil.example). They also send an Origin header with every
    POST/PUT/PATCH/DELETE, which a page cannot forge: it must be this
    service's own. A client without one (curl, a load test) must send it
    explicitly. SameSite=Lax on the cookie is the second layer.
    """
    if request.headers.get("origin") != settings.public_url:
        auth_rejections.labels("cross_origin").inc()
        raise ApiError(403, "csrf_failed", "cross-site request refused")


async def current_principal(request: Request, db: DbSession) -> Principal:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(session_cookie(settings))
    if not token:
        auth_rejections.labels("no_session").inc()
        raise HTTPException(status_code=401, detail="sign in required")
    if request.method not in SAFE_METHODS:
        check_same_origin(request, settings)
    principal = await authenticate(db, token, settings)
    if principal is None:
        auth_rejections.labels("expired").inc()
        raise HTTPException(status_code=401, detail="session expired, sign in again")
    await set_transaction_settings(db, tenant_settings(principal))
    request.state.principal = principal  # rate limits and the access log
    return principal


CurrentUser = Annotated[Principal, Depends(current_principal)]


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
