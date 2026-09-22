from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routes.alerts import decode_cursor, encode_cursor


def test_cursor_round_trips_timestamp_and_id() -> None:
    created = datetime(2026, 9, 22, 18, 30, 5, 123456, tzinfo=UTC)
    cursor = encode_cursor(SimpleNamespace(created_at=created, id=41))  # type: ignore[arg-type]
    assert decode_cursor(cursor) == (created, 41)


@pytest.mark.parametrize("bad", ["not-base64!!", "aGVsbG8=", "MjAyNi0wOS0yMnxub3QtYW4taWQ="])
def test_garbage_cursors_are_a_400(bad: str) -> None:
    with pytest.raises(HTTPException) as info:
        decode_cursor(bad)
    assert info.value.status_code == 400
