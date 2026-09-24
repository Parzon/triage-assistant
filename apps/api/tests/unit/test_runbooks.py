"""The parts of runbook retrieval with no I/O: splitting runbooks into
sections, numbering them in the prompt, reading citations back out of an
answer, and the vector wire format (app/runbooks.py, app/triage.py,
app/vector.py)."""

from collections.abc import Sequence
from datetime import UTC, datetime

from app.runbooks import Hit, _embed, document_text, split_sections
from app.triage import answer_events, build_messages, citations
from app.vector import Vector, to_text
from tests.unit.test_triage import FakeLLM, parse

RUNBOOK = """Steps for when a database host runs out of disk.

## Check what is using space
Run `df -h`.

```bash
# this shell comment is not a heading
du -sh /var/log/*
```

## Free space
Delete old WAL archives after a backup.

### Still full
Expand the volume.

## Empty section
"""


def hit(n: int, heading: str) -> Hit:
    return Hit(
        chunk_id=n,
        runbook_id=10 + n,
        team="payments",
        title="Disk full",
        heading=heading,
        content=f"step {n}",
        updated_at=datetime(2026, 9, 20, tzinfo=UTC),
        score=0.03,
        keyword_rank=n,
        semantic_rank=n,
        distance=0.2,
    )


def test_sections_carry_the_path_of_headings_above_them() -> None:
    headings = [s.heading for s in split_sections("Disk full", RUNBOOK)]
    assert headings == [
        "Disk full",  # the text before the first heading belongs to the title
        "Disk full > Check what is using space",
        "Disk full > Free space",
        "Disk full > Free space > Still full",
    ]  # and the empty section is dropped


def test_a_top_level_heading_repeating_the_title_is_skipped() -> None:
    body = "# Disk full\nIntro.\n\n## Free space\nDelete logs."
    assert [s.heading for s in split_sections("Disk full", body)] == [
        "Disk full",
        "Disk full > Free space",
    ]


def test_a_comment_in_a_code_block_is_not_a_heading() -> None:
    check = split_sections("Disk full", RUNBOOK)[1]
    assert "# this shell comment is not a heading" in check.content
    assert "du -sh /var/log/*" in check.content


def test_a_long_section_is_split_at_paragraphs_then_at_words() -> None:
    body = "## Long\n" + "\n\n".join(" ".join(["word"] * 40) for _ in range(5))
    body += "\n\n" + " ".join(["huge"] * 130)
    sections = split_sections("T", body, max_words=100)
    # Pieces of 40, 40, 40, 40, 40, 100 and 30 words, packed greedily.
    assert [len(s.content.split()) for s in sections] == [80, 80, 40, 100, 30]
    assert [s.heading for s in sections] == [f"T > Long ({n}/5)" for n in range(1, 6)]
    assert all(len(s.content.split()) <= 100 for s in sections)
    assert sum(len(s.content.split()) for s in sections) == 5 * 40 + 130


def test_the_embedded_text_carries_the_heading() -> None:
    section = split_sections("Disk full", RUNBOOK)[2]
    assert document_text(section).startswith("Disk full > Free space\n\n")


class RecordingEmbedder:
    embedding_model: str | None = "fake-embed"

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def embed(
        self, texts: Sequence[str], *, timeout_s: float | None = None
    ) -> list[list[float]]:
        self.sent += texts
        return [[0.0] * 768 for _ in texts]


async def test_credentials_are_redacted_before_embedding() -> None:
    embedder = RecordingEmbedder()
    await _embed(embedder, ["Log in with password=Winter2026! first."], "documents")
    await _embed(embedder, ["why does api_key=sk-abc123def456ghi789 fail?"], "query")
    assert embedder.sent == [
        "Log in with password=[redacted] first.",
        "why does api_key=[redacted] fail?",
    ]


def test_sections_are_numbered_for_citation_in_the_prompt() -> None:
    system = build_messages("what now?", [], [hit(1, "Disk full > Free space")])[0]["content"]
    assert "[R1] Disk full > Free space (team payments, updated 2026-09-20)\nstep 1" in system
    none = build_messages("what now?", [])[0]["content"]
    assert none.endswith(
        "Runbook sections of the asker's teams, most relevant first:\n(no runbook sections)"
    )


def test_citations_are_the_sections_cited_and_the_numbers_invented() -> None:
    sections = [hit(1, "A > One"), hit(2, "A > Two")]
    cited, invalid = citations("First [R2]. Then [R1], and [R2] again; see [R9].", sections)
    assert [c["ref"] for c in cited] == ["R2", "R1"]  # order of first mention, once each
    assert cited[0] == {"ref": "R2", "runbook_id": 12, "title": "Disk full", "heading": "A > Two"}
    assert invalid == ["R9"]
    assert citations("no citation at all", sections) == ([], [])
    # A model does not always keep the brackets it was asked for.
    for styled in ("(R2)", "[**R2**]", "【R2】", "the rule in R2 says"):
        assert [c["ref"] for c in citations(f"Delete WAL archives {styled}.", sections)[0]] == [
            "R2"
        ], styled
    assert citations("R2D2 and XR1 are not citations", sections) == ([], [])


async def test_the_answer_reports_its_context_and_citations() -> None:
    events = parse(
        [
            event
            async for event in answer_events(
                FakeLLM(["Free space first [R1]", ", not [R3]."]),
                [],
                request_id="rid",
                alerts_in_context=0,
                stream_timeout_s=5,
                heartbeat_s=5,
                sections=[hit(1, "Disk full > Free space")],
                retrieval="hybrid",
            )
        ]
    )
    meta, done = events[0][1], events[-1][1]
    assert (meta["runbooks_in_context"], meta["retrieval"]) == (1, "hybrid")  # type: ignore[index]
    assert [c["ref"] for c in done["citations"]] == ["R1"]  # type: ignore[index]
    assert done["invalid_citations"] == ["R3"]  # type: ignore[index]


def test_vectors_travel_as_pgvector_text() -> None:
    assert to_text([0.1, 1 / 3, -2.0]) == "[0.1,0.3333333,-2]"
    vector = Vector(768)
    assert vector.get_col_spec() == "vector(768)"
    parse_back = vector.result_processor(None, None)  # type: ignore[arg-type]
    assert parse_back("[0.1,0.3333333,-2]") == [0.1, 0.3333333, -2.0]
    assert parse_back("[]") == []
    assert parse_back(None) is None
