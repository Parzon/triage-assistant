"""Runbooks: team admins write them, members read and search them, and the
assistant cites them (RFC-0001, ADR-0017). Same access rule as alerts, with
row-level security underneath."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select

from app.access import Role, require_role
from app.audit import Actor, record
from app.db import DbSession
from app.errors import ApiError
from app.llm import Embedder, LLMError
from app.models import Runbook, RunbookChunk, Team
from app.queries import team_by_slug
from app.ratelimit import rate_limit
from app.runbooks import save_runbook, search_runbooks
from app.schemas import (
    RunbookIn,
    RunbookList,
    RunbookOut,
    RunbookSaved,
    RunbookSummary,
    SearchHit,
    SearchIn,
    SearchOut,
)
from app.sessions import CurrentUser

router = APIRouter(prefix="/runbooks", tags=["runbooks"])


def embedder(request: Request) -> Embedder:
    llm: Embedder = request.app.state.llm
    if llm.embedding_model is None:
        raise ApiError(503, "runbooks_off", "runbook search is off: EMBEDDING_MODEL is not set")
    return llm


@router.post(
    "",
    response_model=RunbookSaved,
    dependencies=[Depends(rate_limit("runbooks", "runbooks_rate_limit"))],
)
async def save(
    payload: RunbookIn, request: Request, principal: CurrentUser, db: DbSession
) -> RunbookSaved:
    """Create a runbook, or replace the text of the team's runbook with the
    same title. Saving an unchanged text again costs nothing."""
    team = await team_by_slug(db, payload.team)
    if team is None:
        raise HTTPException(status_code=404, detail="team not found")
    require_role(principal, team.id, Role.ADMIN, "team")
    try:
        saved = await save_runbook(
            db,
            embedder(request),
            request.app.state.settings,
            team,
            payload.title,
            payload.body,
            payload.source_url,
            Actor.of(principal),
        )
    except LLMError as exc:
        raise ApiError(503, exc.code, f"the embedding model failed: {exc}") from exc
    return RunbookSaved(
        id=saved.runbook_id,
        team=team.slug,
        title=payload.title,
        sections=saved.sections,
        changed=saved.changed,
    )


@router.get("", response_model=RunbookList)
async def list_runbooks(principal: CurrentUser, db: DbSession) -> RunbookList:
    team_ids = principal.team_ids()
    if team_ids is not None and not team_ids:
        return RunbookList(items=[])
    sections = (
        select(func.count())
        .where(RunbookChunk.runbook_id == Runbook.id)
        .correlate(Runbook)
        .scalar_subquery()
    )
    stmt = select(Runbook, Team.slug, sections).join(Team, Team.id == Runbook.team_id)
    if team_ids is not None:
        stmt = stmt.where(Runbook.team_id.in_(team_ids))
    rows = (await db.execute(stmt.order_by(Team.slug, Runbook.title))).tuples()
    return RunbookList(items=[_summary(runbook, slug, n) for runbook, slug, n in rows])


@router.get("/{runbook_id}", response_model=RunbookOut)
async def get_runbook(runbook_id: int, principal: CurrentUser, db: DbSession) -> RunbookOut:
    runbook, slug, n = await _visible(db, principal, runbook_id, Role.VIEWER)
    return RunbookOut(**_summary(runbook, slug, n).model_dump(), body=runbook.body)


@router.delete(
    "/{runbook_id}",
    status_code=204,
    dependencies=[Depends(rate_limit("runbooks", "runbooks_rate_limit"))],
)
async def delete_runbook(runbook_id: int, principal: CurrentUser, db: DbSession) -> Response:
    """Team admins only. Its sections go with it, so no answer can cite a
    deleted runbook."""
    runbook, _, _ = await _visible(db, principal, runbook_id, Role.ADMIN)
    await db.delete(runbook)
    await record(
        db,
        Actor.of(principal),
        "runbook.deleted",
        team_id=runbook.team_id,
        target_id=runbook.id,
        title=runbook.title,
        body_sha256=runbook.body_sha256,
    )
    await db.commit()
    return Response(status_code=204)


@router.post(
    "/search",
    response_model=SearchOut,
    # Each search embeds the question: a model call, so a cost.
    dependencies=[Depends(rate_limit("runbooks_search", "runbooks_rate_limit"))],
)
async def search(
    payload: SearchIn, request: Request, principal: CurrentUser, db: DbSession
) -> SearchOut:
    """What retrieval finds for a question, with each retriever's rank: the
    first thing to look at when the assistant cites the wrong section."""
    team_ids = principal.team_ids()
    if payload.team is not None:
        team = await team_by_slug(db, payload.team)
        if team is None:
            raise HTTPException(status_code=404, detail="team not found")
        require_role(principal, team.id, Role.VIEWER, "team")
        team_ids = [team.id]
    if team_ids is not None and not team_ids:
        return SearchOut(mode="keyword_only", embedding_error=None, hits=[])
    retrieval = await search_runbooks(
        db,
        request.app.state.llm,
        request.app.state.settings,
        payload.query,
        team_ids,
        k=payload.k,
        mode=payload.mode,
    )
    return SearchOut(
        mode=retrieval.mode,
        embedding_error=retrieval.embedding_error,
        hits=[
            SearchHit(
                runbook_id=hit.runbook_id,
                team=hit.team,
                title=hit.title,
                heading=hit.heading,
                content=hit.content,
                updated_at=hit.updated_at,
                score=hit.score,
                keyword_rank=hit.keyword_rank,
                semantic_rank=hit.semantic_rank,
                distance=hit.distance,
            )
            for hit in retrieval.hits
        ],
    )


async def _visible(
    db: DbSession, principal: CurrentUser, runbook_id: int, needed: Role
) -> tuple[Runbook, str, int]:
    sections = select(func.count()).where(RunbookChunk.runbook_id == runbook_id).scalar_subquery()
    row = (
        await db.execute(
            select(Runbook, Team.slug, sections)
            .join(Team, Team.id == Runbook.team_id)
            .where(Runbook.id == runbook_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="runbook not found")
    runbook, slug, n = row
    require_role(principal, runbook.team_id, needed, "runbook")
    return runbook, slug, n


def _summary(runbook: Runbook, slug: str, sections: int) -> RunbookSummary:
    return RunbookSummary(
        id=runbook.id,
        team=slug,
        title=runbook.title,
        source_url=runbook.source_url,
        sections=sections,
        updated_at=runbook.updated_at,
    )
