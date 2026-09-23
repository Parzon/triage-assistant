"""The authorization model (app/access.py) and the sign-in redirect guard,
without a database."""

import pytest
from fastapi import HTTPException

from app.access import (
    Principal,
    Role,
    TeamAccess,
    access_from_claims,
    require_role,
    tenant_settings,
)
from app.routes.auth import safe_next


def principal(*teams: tuple[int, Role], org_admin: bool = False) -> Principal:
    return Principal(
        user_id=7,
        email="u@example.com",
        name="U",
        org_admin=org_admin,
        teams=tuple(TeamAccess(tid, f"t{tid}", f"T{tid}", role) for tid, role in teams),
    )


def test_roles_are_ranked() -> None:
    assert Role.VIEWER < Role.RESPONDER < Role.ADMIN
    assert [r.label for r in Role] == ["viewer", "responder", "admin"]


def test_claims_become_team_roles() -> None:
    roles, org_admin = access_from_claims(
        [
            "team:payments:viewer",
            "team:payments:admin",  # the highest role in a team wins...
            "team:payments:responder",  # ...whatever the order
            "/team:platform:responder",  # Keycloak full-path style
            "team:Bad Slug:admin",  # invalid slug: ignored
            "team:ops:owner",  # unknown role: ignored
            "payments-admins",  # someone else's group: ignored
        ]
    )
    assert roles == {"payments": Role.ADMIN, "platform": Role.RESPONDER}
    assert org_admin is False
    assert access_from_claims(["org:admin"]) == ({}, True)
    assert access_from_claims([]) == ({}, False)


def test_principal_answers_role_and_team_questions() -> None:
    p = principal((1, Role.VIEWER), (2, Role.RESPONDER), (3, Role.ADMIN))
    assert p.role_in(2) is Role.RESPONDER
    assert p.role_in(99) is None
    assert p.team_ids() == [1, 2, 3]
    assert p.team_ids(Role.RESPONDER) == [2, 3]
    assert p.team_ids(Role.ADMIN) == [3]


def test_an_org_admin_is_admin_everywhere() -> None:
    p = principal(org_admin=True)
    assert p.role_in(12345) is Role.ADMIN
    assert p.team_ids() is None  # every team


def test_require_role_hides_what_you_cannot_see() -> None:
    p = principal((1, Role.VIEWER))
    require_role(p, 1, Role.VIEWER, "alert")  # passes
    with pytest.raises(HTTPException) as hidden:
        require_role(p, 2, Role.VIEWER, "alert")
    assert (hidden.value.status_code, hidden.value.detail) == (404, "alert not found")
    with pytest.raises(HTTPException) as too_low:
        require_role(p, 1, Role.RESPONDER, "alert")
    assert (too_low.value.status_code, too_low.value.detail) == (
        403,
        "needs the responder role in this team",
    )


def test_tenant_settings_for_postgres() -> None:
    p = principal((1, Role.VIEWER), (2, Role.RESPONDER), (3, Role.ADMIN))
    assert tenant_settings(p) == {
        "app.user_id": "7",
        "app.org_admin": "off",
        "app.read_team_ids": "{1,2,3}",
        "app.write_team_ids": "{2,3}",
        "app.admin_team_ids": "{3}",
    }
    admin = tenant_settings(principal(org_admin=True))
    assert admin["app.org_admin"] == "on"
    assert admin["app.read_team_ids"] == "{}"  # the org_admin flag grants access, not ids


@pytest.mark.parametrize(
    ("given", "safe"),
    [
        ("/", "/"),
        ("/alerts?team=payments", "/alerts?team=payments"),
        ("//evil.example/x", "/"),  # protocol-relative: another site
        ("/\\evil.example", "/"),  # browsers read "\" as "/"
        ("https://evil.example", "/"),
        ("evil", "/"),
        ("/ok\r\nSet-Cookie: x=1", "/"),  # header injection
        ("/" + "a" * 3000, "/"),
    ],
)
def test_after_sign_in_only_paths_on_this_site(given: str, safe: str) -> None:
    assert safe_next(given) == safe
