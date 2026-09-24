"""Runbooks: split into sections, embedded, searched (RFC-0001, ADR-0017).

Three steps, each one the place to look when an answer cites the wrong
thing:
- split_sections: Markdown to sections at its headings. Pure: no I/O.
- save_runbook: sections embedded, then stored. The embedding call happens
  with no transaction open, so no database connection waits on the model.
- search_runbooks: hybrid retrieval. Keyword (Postgres full-text search) and
  meaning (pgvector) each rank their best candidates; reciprocal rank
  fusion merges the two lists. When the question cannot be embedded in
  time, keyword search alone answers: a slow embedding model degrades the
  answer, it does not delay or break it.

Row-level security applies throughout: a caller reads and writes only the
runbooks of their teams, exactly as with alerts.
"""

import hashlib
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.llm import Embedder, LLMError
from app.metrics import embedding_requests, retrieval_duration
from app.models import Runbook, RunbookChunk, Team
from app.vector import to_text

log = logging.getLogger(__name__)

# About 400 tokens: specific enough to rank, long enough to hold a step
# with its context. The RAG debugging lab shows what too small and too
# large do to retrieval.
MAX_SECTION_WORDS = 300
# Candidates each retriever contributes before fusion.
CANDIDATES = 20
# Reciprocal rank fusion's constant (Cormack et al., 2009): 1/(60 + rank).
# Large enough that one list's first place does not drown the other list.
RRF_K = 60
# Texts per embedding request: providers cap the batch size.
EMBED_BATCH = 32

_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")


@dataclass(frozen=True)
class Section:
    # The heading path, "Disk full > Free space": what a citation shows.
    heading: str
    content: str


def split_sections(title: str, markdown: str, max_words: int = MAX_SECTION_WORDS) -> list[Section]:
    """One section per heading, carrying the path of headings above it.
    Text before the first heading belongs to the title, and a top-level
    heading repeating the title (a pasted document's own) is skipped. A
    heading inside a fenced code block is not a heading: a shell comment
    ("# restart it") would otherwise cut a procedure in two. Sections over
    max_words are split at blank lines, then at word boundaries."""
    sections: list[Section] = []
    path: list[tuple[int, str]] = []
    lines: list[str] = []
    in_fence = False

    def flush() -> None:
        content = "\n".join(lines).strip()
        if content:
            heading = " > ".join([title, *(h for _, h in path)])
            sections.extend(_limit(heading, content, max_words))
        lines.clear()

    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match and len(match[1]) == 1 and match[2].strip().casefold() == title.casefold():
            continue
        if match:
            flush()
            level = len(match[1])
            path[:] = [(lvl, h) for lvl, h in path if lvl < level]
            path.append((level, match[2]))
        else:
            lines.append(line)
    flush()
    return sections


def _limit(heading: str, content: str, max_words: int) -> list[Section]:
    if len(content.split()) <= max_words:
        return [Section(heading, content)]
    pieces: list[str] = []
    for paragraph in re.split(r"\n\s*\n", content):
        words = paragraph.split()
        if len(words) <= max_words:
            pieces.append(paragraph)
        else:  # a paragraph longer than a section: cut at word boundaries
            pieces += [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for piece in pieces:
        n = len(piece.split())
        if current and size + n > max_words:
            parts.append("\n\n".join(current))
            current, size = [], 0
        current.append(piece)
        size += n
    if current:
        parts.append("\n\n".join(current))
    return [Section(f"{heading} ({n}/{len(parts)})", part) for n, part in enumerate(parts, 1)]


def document_text(section: Section) -> str:
    """What is embedded: the heading path gives a short section its topic."""
    return f"{section.heading}\n\n{section.content}"


async def _embed(
    embedder: Embedder,
    texts: Sequence[str],
    kind: Literal["documents", "query"],
    timeout_s: float | None = None,
) -> list[list[float]]:
    try:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start : start + EMBED_BATCH]
            vectors += await embedder.embed(batch, timeout_s=timeout_s)
    except LLMError as exc:
        embedding_requests.labels(kind, exc.code).inc()
        raise
    embedding_requests.labels(kind, "ok").inc()
    return vectors


@dataclass(frozen=True)
class Saved:
    runbook_id: int
    sections: int
    # False: the same text was already stored, embedded by the same model.
    changed: bool


async def save_runbook(
    db: AsyncSession,
    embedder: Embedder,
    settings: Settings,
    team: Team,
    title: str,
    body: str,
    source_url: str | None,
) -> Saved:
    """Create the runbook, or replace its text (same team and title)."""
    model = embedder.embedding_model
    if model is None:
        raise LLMError("runbook search is off: EMBEDDING_MODEL is not set")
    digest = hashlib.sha256(body.encode()).hexdigest()
    sections = split_sections(title, body)

    existing = (
        await db.execute(
            select(
                Runbook.id, Runbook.body_sha256, Runbook.embedding_model, Runbook.source_url
            ).where(Runbook.team_id == team.id, Runbook.title == title)
        )
    ).one_or_none()
    if existing is not None and (existing[1], existing[2], existing[3]) == (
        digest,
        model,
        source_url,
    ):
        return Saved(existing[0], len(sections), changed=False)
    # End the read before the embedding call: no connection held while the
    # model works, and the write below is its own short transaction.
    await db.commit()

    prefix = settings.embedding_document_prefix
    vectors = await _embed(embedder, [prefix + document_text(s) for s in sections], "documents")

    upsert = (
        insert(Runbook)
        .values(
            team_id=team.id,
            title=title,
            body=body,
            body_sha256=digest,
            embedding_model=model,
            source_url=source_url,
        )
        .on_conflict_do_update(
            index_elements=[Runbook.team_id, Runbook.title],
            set_={
                "body": body,
                "body_sha256": digest,
                "embedding_model": model,
                "source_url": source_url,
                "updated_at": text("now()"),
            },
        )
        .returning(Runbook.id)
    )
    runbook_id = (await db.execute(upsert)).scalar_one()
    await db.execute(delete(RunbookChunk).where(RunbookChunk.runbook_id == runbook_id))
    if sections:
        await db.execute(
            insert(RunbookChunk),
            [
                {
                    "runbook_id": runbook_id,
                    "team_id": team.id,
                    "ordinal": n,
                    "heading": section.heading,
                    "content": section.content,
                    "embedding": vector,
                    "embedding_model": model,
                }
                for n, (section, vector) in enumerate(zip(sections, vectors, strict=True))
            ],
        )
    await db.commit()
    return Saved(runbook_id, len(sections), changed=True)


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    runbook_id: int
    team: str
    title: str
    heading: str
    content: str
    updated_at: datetime
    # Reciprocal rank fusion: the sum of 1/(RRF_K + rank) over both lists.
    score: float
    # Each retriever's rank for this section (1 = best); None: not in its list.
    keyword_rank: int | None
    semantic_rank: int | None
    # Cosine distance to the question (0 = same direction; 2 = opposite).
    distance: float | None


@dataclass(frozen=True)
class Retrieval:
    hits: list[Hit]
    # As asked (hybrid, keyword, semantic), or keyword_only: a hybrid search
    # whose question could not be embedded.
    mode: Literal["hybrid", "keyword", "semantic", "keyword_only"]
    # Why the question was not embedded, else None.
    embedding_error: str | None = None


# The question's own words, as Postgres parses and stems them, joined with
# OR: websearch_to_tsquery would AND them, and a question rarely uses every
# word of the section that answers it. ts_rank_cd then favours sections
# covering more of them. Quoted lexemes cannot break the query syntax.
_QUERY_TERMS = """
    to_tsquery('english', coalesce(array_to_string(array(
        SELECT quote_literal(lexeme)
        FROM unnest(tsvector_to_array(to_tsvector('english', :query))) AS lexeme
    ), ' | '), ''))
"""
# The SQL below is assembled once, from this module's constants, never from
# input: every value is a bound parameter (:query, :team_ids, :embedding...).
# Bandit's S608 cannot tell the two apart, hence its noqa on each assembly.
#
# The team filter comes in two forms, not one "(:team_ids IS NULL OR ...)":
# measured on 50,000 sections in 100 teams, a one-team caller's semantic
# search behind that OR (and row-level security's own) got the HNSW index:
# 0 results without iterative scans (all 40 candidates were other teams'),
# 20 in 71 ms with them. The plain "team_id = ANY(...)" lets the planner read
# the team's rows by its index and sort them exactly: 20 in 1.6 ms.
_ALL_TEAMS = "TRUE"
_SOME_TEAMS = "c.team_id = ANY(CAST(:team_ids AS bigint[]))"
_KEYWORD = """
keyword AS (
    SELECT id, row_number() OVER (ORDER BY rank DESC, id) AS rank_no
    FROM (
        SELECT c.id, ts_rank_cd(c.search, q.query) AS rank
        FROM runbook_chunks c, (SELECT {terms} AS query) q
        WHERE c.search @@ q.query AND {teams}
        ORDER BY rank DESC, c.id
        LIMIT :candidates
    ) k
)"""
# The inner ORDER BY distance ... LIMIT is what the HNSW index serves; the
# ranks are numbered outside it, so no window sorts the whole table.
_SEMANTIC = """
semantic AS (
    SELECT id, distance, row_number() OVER (ORDER BY distance, id) AS rank_no
    FROM (
        SELECT c.id, c.embedding <=> CAST(CAST(:embedding AS text) AS vector) AS distance
        FROM runbook_chunks c
        WHERE c.embedding_model = :model AND {teams}
        ORDER BY c.embedding <=> CAST(CAST(:embedding AS text) AS vector)
        LIMIT :candidates
    ) s
)"""
_RESULT = """
SELECT c.id, c.runbook_id, t.slug AS team, r.title, c.heading, c.content, r.updated_at,
       {score} AS score, {keyword} AS keyword_rank, {semantic}
FROM ({ids}) ids
JOIN runbook_chunks c ON c.id = ids.id
JOIN runbooks r ON r.id = c.runbook_id
JOIN teams t ON t.id = c.team_id
{joins}
ORDER BY score DESC, c.id
LIMIT :k
"""
_KW_SCORE = "coalesce(1.0 / (:rrf_k + kw.rank_no), 0)"
_SE_SCORE = "coalesce(1.0 / (:rrf_k + se.rank_no), 0)"
_NO_SEMANTIC = "NULL::bigint AS semantic_rank, NULL::float8 AS distance"


def _assemble(mode: str, teams: str) -> str:
    keyword = _KEYWORD.format(terms=_QUERY_TERMS, teams=teams)
    semantic = _SEMANTIC.format(teams=teams)
    if mode == "hybrid":
        return f"WITH {keyword}, {semantic}" + _RESULT.format(  # noqa: S608
            score=f"{_KW_SCORE} + {_SE_SCORE}",
            keyword="kw.rank_no",
            semantic="se.rank_no AS semantic_rank, se.distance",
            ids="SELECT id FROM keyword UNION SELECT id FROM semantic",
            joins="LEFT JOIN keyword kw ON kw.id = c.id LEFT JOIN semantic se ON se.id = c.id",
        )
    if mode == "keyword":
        return f"WITH {keyword}" + _RESULT.format(  # noqa: S608
            score=_KW_SCORE,
            keyword="kw.rank_no",
            semantic=_NO_SEMANTIC,
            ids="SELECT id FROM keyword",
            joins="LEFT JOIN keyword kw ON kw.id = c.id",
        )
    return f"WITH {semantic}" + _RESULT.format(  # noqa: S608
        score=_SE_SCORE,
        keyword="NULL::bigint",
        semantic="se.rank_no AS semantic_rank, se.distance",
        ids="SELECT id FROM semantic",
        joins="LEFT JOIN semantic se ON se.id = c.id",
    )


# (mode, scoped to some teams) -> SQL; unscoped is an org admin's search.
_SQL = {
    (mode, scoped): _assemble(mode, _SOME_TEAMS if scoped else _ALL_TEAMS)
    for mode in ("hybrid", "keyword", "semantic")
    for scoped in (True, False)
}
# What a search is asked to do. keyword and semantic run one retriever
# alone: for debugging and for measuring each retriever's recall.
Mode = Literal["hybrid", "keyword", "semantic"]


async def search_runbooks(
    db: AsyncSession,
    embedder: Embedder,
    settings: Settings,
    query: str,
    team_ids: Sequence[int] | None,
    *,
    k: int,
    mode: Mode = "hybrid",
) -> Retrieval:
    """The k sections most relevant to `query` among `team_ids`' runbooks
    (None: every team).

    Commits the session's open transaction first - the sign-in check's
    read, in a route - so no pooled connection waits while the embedding
    model works (up to EMBEDDING_TIMEOUT_S)."""
    await db.commit()
    start = time.perf_counter()
    embedding: list[float] | None = None
    error: str | None = None
    if mode == "keyword":
        pass
    elif embedder.embedding_model is None:
        error = "embeddings_off"
    else:
        try:
            prefixed = settings.embedding_query_prefix + query
            embedding = (await _embed(embedder, [prefixed], "query", settings.embedding_timeout_s))[
                0
            ]
        except LLMError as exc:
            error = exc.code
            log.warning("question not embedded: keyword search only", extra={"code": exc.code})
    # hybrid degrades to keyword_only; semantic alone has nothing to fall back on.
    done: Literal["hybrid", "keyword", "semantic", "keyword_only"] = mode
    if embedding is None and mode == "hybrid":
        done = "keyword_only"
    if embedding is None and mode == "semantic":
        retrieval_duration.labels(mode).observe(time.perf_counter() - start)
        return Retrieval(hits=[], mode="semantic", embedding_error=error)

    # Filters (team, embedding model) are applied after an approximate
    # index scan, so a scan can end with fewer than `candidates` rows left.
    # Iterative scans keep going until enough rows pass (pgvector 0.8+).
    await db.execute(text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)"))
    params: dict[str, object] = {
        "query": query,
        "candidates": CANDIDATES,
        "rrf_k": RRF_K,
        "k": k,
    }
    if team_ids is not None:
        params["team_ids"] = list(team_ids)
    if embedding is not None:
        params |= {"embedding": to_text(embedding), "model": embedder.embedding_model}
    sql = _SQL[("keyword" if done == "keyword_only" else done, team_ids is not None)]
    rows = (await db.execute(text(sql), params)).all()
    retrieval_duration.labels(done).observe(time.perf_counter() - start)
    return Retrieval(
        hits=[
            Hit(
                chunk_id=row.id,
                runbook_id=row.runbook_id,
                team=row.team,
                title=row.title,
                heading=row.heading,
                content=row.content,
                updated_at=row.updated_at,
                score=float(row.score),
                keyword_rank=row.keyword_rank,
                semantic_rank=row.semantic_rank,
                distance=None if row.distance is None else float(row.distance),
            )
            for row in rows
        ],
        mode=done,
        embedding_error=error,
    )
