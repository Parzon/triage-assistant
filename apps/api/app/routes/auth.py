"""Sign-in, sign-out, and who am I (ADR-0013).

The browser-facing half of the OIDC flow. Sign-in is two top-level
navigations, not API calls:
  GET /auth/login     -> 302 to the identity provider, which shows its
                         login page (password, MFA, SSO - its business)
  GET /auth/callback  <- the provider sends the browser back with a
                         one-time code; we exchange it, verify the ID
                         token, open a session and 302 to the app.
Errors in that flow cannot be JSON (the user would see raw JSON): they
redirect to the app with ?auth_error=<code>, which the sign-in page shows.
"""

import logging
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.access import Role, TeamAccess, require_role
from app.config import Settings
from app.db import DbSession
from app.metrics import auth_logins
from app.models import Membership, Team, User
from app.oidc import OIDCClient, OIDCError, pkce_challenge
from app.queries import team_by_slug
from app.ratelimit import rate_limit_by_ip
from app.schemas import LogoutOut, MemberOut, MeOut, TeamOut
from app.sessions import (
    LOGIN_TTL_S,
    CurrentUser,
    check_same_origin,
    clear_cookie,
    constant_time_equal,
    finish_login,
    login_cookie,
    new_token,
    session_cookie,
    set_cookie,
    sign_in,
    sign_out,
    start_login,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

# Redirects in the sign-in flow must never be cached: each carries a fresh
# state, and a cached one would replay another sign-in.
NO_STORE = {"Cache-Control": "no-store"}


def safe_next(path: str) -> str:
    """Where to land after signing in: a path on this site only. "//evil.
    example/x" and "/\\evil.example" are other sites to a browser - accepting
    them would make the sign-in an open redirect for phishing."""
    if (
        not path.startswith("/")
        or path.startswith(("//", "/\\"))
        or len(path) > 2000
        or any(c < " " for c in path)
    ):
        return "/"
    return path


def failed(settings: Settings, code: str) -> RedirectResponse:
    auth_logins.labels(code).inc()
    response = RedirectResponse(f"/?auth_error={quote(code)}", status_code=302, headers=NO_STORE)
    clear_cookie(response, settings, login_cookie(settings))
    return response


@router.get("/auth/login", dependencies=[Depends(rate_limit_by_ip("auth", "auth_rate_limit"))])
async def login(request: Request, db: DbSession, next: str = "/") -> RedirectResponse:
    settings: Settings = request.app.state.settings
    oidc: OIDCClient = request.app.state.oidc
    state, nonce, verifier = new_token(), new_token(), secrets.token_urlsafe(64)
    try:
        url = await oidc.authorization_url(
            state=state, nonce=nonce, code_challenge=pkce_challenge(verifier)
        )
    except OIDCError as exc:
        return failed(settings, exc.code)
    await start_login(
        db, state=state, nonce=nonce, code_verifier=verifier, next_path=safe_next(next)
    )
    await db.commit()
    response = RedirectResponse(url, status_code=302, headers=NO_STORE)
    # Binds the sign-in to this browser: the callback must come back with
    # the same state in the URL and in this cookie. Without it, an attacker
    # could send a victim to the callback with the attacker's own code and
    # sign them in as the attacker (login CSRF).
    set_cookie(response, settings, login_cookie(settings), state, LOGIN_TTL_S)
    return response


@router.get("/auth/callback", dependencies=[Depends(rate_limit_by_ip("auth", "auth_rate_limit"))])
async def callback(
    request: Request,
    db: DbSession,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    iss: str | None = None,
) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    oidc: OIDCClient = request.app.state.oidc
    # RFC 9207: a provider that names itself in the redirect must be the one
    # this sign-in went to - a response from another provider (mix-up attack)
    # is refused before its code is used anywhere.
    if iss is not None and iss != settings.oidc_issuer:
        return failed(settings, "login_failed")
    if error is not None:
        # The user cancelled, or the provider refused them (not assigned to
        # the application): "access_denied". Anything else is a failure.
        log.info("sign-in refused by the identity provider", extra={"error": error[:100]})
        return failed(settings, "access_denied" if error == "access_denied" else "login_failed")
    bound = request.cookies.get(login_cookie(settings))
    if not code or not state or bound is None or not constant_time_equal(bound, state):
        return failed(settings, "invalid_state")
    pending = await finish_login(db, state)
    await db.commit()  # used up, even if what follows fails
    if pending is None:
        return failed(settings, "expired")
    try:
        id_token = await oidc.exchange_code(code, code_verifier=pending.code_verifier)
        identity = await oidc.verify(id_token, nonce=pending.nonce)
    except OIDCError as exc:
        log.warning("sign-in failed", extra={"code": exc.code, "reason": str(exc)})
        return failed(settings, exc.code)

    # A browser signing in again (or as someone else) never keeps its old
    # session id: a fresh one is issued, the old one ended.
    if old := request.cookies.get(session_cookie(settings)):
        await sign_out(db, old)
    token = await sign_in(db, issuer=settings.oidc_issuer, identity=identity, settings=settings)
    await db.commit()
    auth_logins.labels("ok").inc()
    log.info("signed in", extra={"subject": identity.subject})
    response = RedirectResponse(pending.next_path, status_code=302, headers=NO_STORE)
    set_cookie(response, settings, session_cookie(settings), token, settings.session_max_age_s)
    clear_cookie(response, settings, login_cookie(settings))
    return response


@router.post("/auth/logout", response_model=LogoutOut)
async def logout(request: Request, response: Response, db: DbSession) -> LogoutOut:
    """Ends this session, and returns the provider's sign-out URL for the
    browser to visit: otherwise the provider's own session would sign the
    user straight back in on the next "Sign in"."""
    settings: Settings = request.app.state.settings
    check_same_origin(request, settings)
    id_token = None
    if token := request.cookies.get(session_cookie(settings)):
        id_token = await sign_out(db, token)
        await db.commit()
    clear_cookie(response, settings, session_cookie(settings))
    oidc: OIDCClient = request.app.state.oidc
    return LogoutOut(logout_url=await oidc.end_session_url(id_token=id_token) or "/")


@router.get("/me", response_model=MeOut)
async def me(principal: CurrentUser, db: DbSession) -> MeOut:
    teams: tuple[TeamAccess, ...] = principal.teams
    if principal.org_admin:
        teams = tuple(
            TeamAccess(t.id, t.slug, t.name, Role.ADMIN)
            for t in await db.scalars(select(Team).order_by(Team.slug))
        )
    return MeOut(
        id=principal.user_id,
        email=principal.email,
        name=principal.name,
        org_admin=principal.org_admin,
        teams=[TeamOut(slug=t.slug, name=t.name, role=t.role.label) for t in teams],
    )


@router.get("/teams/{slug}/members", response_model=list[MemberOut])
async def team_members(slug: str, principal: CurrentUser, db: DbSession) -> list[MemberOut]:
    """Team admins: who has access, and with which role."""
    team = await team_by_slug(db, slug)
    if team is None:
        raise HTTPException(status_code=404, detail="team not found")
    require_role(principal, team.id, Role.ADMIN, "team")
    rows = await db.execute(
        select(User.email, User.name, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.team_id == team.id)
        .order_by(User.email)
    )
    return [MemberOut(email=r.email, name=r.name, role=Role[r.role.upper()].label) for r in rows]
