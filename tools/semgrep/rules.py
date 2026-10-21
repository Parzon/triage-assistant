# Cases for rules.yml: `make semgrep` fails if a rule misses a `ruleid:` line
# or flags an `ok:` one.
import hmac

from app.audit import record
from app.models import AuditEvent
from app.redact import redact
from app.sessions import CurrentUser
from fastapi import APIRouter

router = APIRouter()


def prompt_line(text: str, clean, limit: int) -> str:
    # ruleid: redact-before-cut
    first = clean(text[:limit])
    # ruleid: redact-before-cut
    second = redact(text[:limit])
    # ok: redact-before-cut
    third = clean(text)[:limit]
    return first + second + third


def nonce_matches(claims: dict, nonce: str) -> bool:
    # ruleid: compare-digest-on-str
    if hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        return True
    # ruleid: compare-digest-on-str
    if hmac.compare_digest(nonce, str(claims.get("nonce", ""))):
        return True
    claimed = str(claims.get("nonce", "")).encode("utf-8", "surrogatepass")
    # ok: compare-digest-on-str
    return hmac.compare_digest(claimed, nonce.encode())


async def save(db, principal, runbook) -> None:
    # ruleid: audit-event-via-db-add
    db.add(AuditEvent(action="runbook.saved", target=runbook.id))
    # ok: audit-event-via-db-add
    await record(db, principal, "runbook.saved", target=runbook.id)


# ruleid: route-without-current-user
@router.get("/alerts")
async def list_alerts(db) -> list:
    return []


# ruleid: route-without-current-user
@router.get("/teams")
def list_teams() -> list:
    return []


# ok: route-without-current-user
@router.get("/runbooks")
async def list_runbooks(principal: CurrentUser, db) -> list:
    return []


# ok: route-without-current-user
@router.post("/runbooks/{runbook_id}")
async def save_runbook(runbook_id: int, principal: CurrentUser, db) -> None:
    return None


# Public: probes carry no session.
# nosemgrep: route-without-current-user
@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}
