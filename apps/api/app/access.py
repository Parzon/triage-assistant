"""Who may do what: the authorization model, with no I/O (ADR-0013).

- Teams own alerts. A user sees a team's alerts only as a member of it.
- Roles are ranked, per team: each can do everything the ones below it can.
    viewer     read the team's alerts; ask the assistant about them
    responder  + create alerts for the team
    admin      + delete the team's alerts; see who is in the team
- An org admin holds the admin role in every team.
- The identity provider is the source of truth. At every sign-in its groups
  claim replaces the user's memberships: "team:<slug>:<role>" grants a
  role, "org:admin" grants org admin, anything else is ignored.

How it is enforced: routes take the caller's Principal and ask it which
teams to read (team_ids) or whether a role is held (require_role). A
resource outside the caller's teams is a 404, not a 403: its existence is
none of their business. Postgres enforces the same rule a second time with
row-level security, fed by tenant_settings() (ADR-0014).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum
from typing import Literal

from fastapi import HTTPException

from app.models import TEAM_SLUG


class Role(IntEnum):
    VIEWER = 1
    RESPONDER = 2
    ADMIN = 3

    @property
    def label(self) -> "RoleName":
        return _LABELS[self]


RoleName = Literal["viewer", "responder", "admin"]
_LABELS: dict[Role, RoleName] = {
    Role.VIEWER: "viewer",
    Role.RESPONDER: "responder",
    Role.ADMIN: "admin",
}


@dataclass(frozen=True)
class TeamAccess:
    id: int
    slug: str
    name: str
    role: Role


@dataclass(frozen=True)
class Principal:
    """The signed-in user a request acts for."""

    user_id: int
    email: str | None
    name: str | None
    org_admin: bool
    teams: tuple[TeamAccess, ...]

    def role_in(self, team_id: int) -> Role | None:
        if self.org_admin:
            return Role.ADMIN
        return next((t.role for t in self.teams if t.id == team_id), None)

    def team_ids(self, at_least: Role = Role.VIEWER) -> list[int] | None:
        """Teams in which the user holds `at_least`; None means every team."""
        if self.org_admin:
            return None
        return [t.id for t in self.teams if t.role >= at_least]


_GROUP = re.compile(rf"^team:(?P<slug>{TEAM_SLUG[1:-1]}):(?P<role>viewer|responder|admin)$")
ORG_ADMIN_GROUP = "org:admin"


def access_from_claims(groups: Iterable[str]) -> tuple[dict[str, Role], bool]:
    """Team roles and the org-admin flag named by a groups claim.

    Keycloak's group mapper sends full paths ("/team:payments:admin") unless
    told not to; the leading slash is accepted either way. The highest role
    wins when a user is in several groups of one team.
    """
    roles: dict[str, Role] = {}
    org_admin = False
    for value in groups:
        value = value.removeprefix("/")
        if value == ORG_ADMIN_GROUP:
            org_admin = True
        elif match := _GROUP.match(value):
            role = Role[match["role"].upper()]
            roles[match["slug"]] = max(role, roles.get(match["slug"], role))
    return roles, org_admin


def require_role(principal: Principal, team_id: int, needed: Role, what: str) -> None:
    """404 when the caller cannot see the team - the same answer as for a
    resource that does not exist, so ids cannot be probed - and 403 when
    they can see it but their role is too low."""
    role = principal.role_in(team_id)
    if role is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    if role < needed:
        raise HTTPException(status_code=403, detail=f"needs the {needed.label} role in this team")


def tenant_settings(principal: Principal) -> dict[str, str]:
    """What Postgres is told about the caller in each transaction, for its
    row-level security policies. Arrays in Postgres literal form."""

    def array(ids: list[int] | None) -> str:
        return "{" + ",".join(str(i) for i in ids or ()) + "}"

    return {
        "app.user_id": str(principal.user_id),
        "app.org_admin": "on" if principal.org_admin else "off",
        "app.read_team_ids": array(principal.team_ids(Role.VIEWER)),
        "app.write_team_ids": array(principal.team_ids(Role.RESPONDER)),
        "app.admin_team_ids": array(principal.team_ids(Role.ADMIN)),
    }
