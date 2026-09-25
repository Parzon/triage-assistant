"""The redactor against its corpus (evals/redaction.py): a change to
app/redact.py may catch more, never less, and never touch ordinary text."""

import pytest

from evals.redaction import scores


@pytest.mark.parametrize("which", ["tuning", "heldout"])
def test_every_secret_in_the_corpus_is_caught_and_no_ordinary_line_changes(which: str) -> None:
    result = scores()[which]
    assert result.missed == []
    assert result.changed == []
    assert (result.secrets, result.lines) == {"tuning": (54, 36), "heldout": (24, 20)}[which]
