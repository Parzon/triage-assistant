"""Session rules that need no database (app/sessions.py): how often a
session's activity is recorded, and which Origin a change needs. With the
database: tests/integration/test_sessions.py."""

import pytest
from starlette.requests import Request

from app.errors import ApiError
from app.sessions import TOUCH_INTERVAL_S, check_same_origin, touch_interval_s
from tests.unit.test_config import make


@pytest.mark.parametrize("idle_s", [60, 120, 299, 600, 3000, 7200])
def test_activity_is_recorded_often_enough_for_the_idle_timeout(idle_s: int) -> None:
    # Recorded at least ten times per idle timeout: a session in use is never
    # ended for idleness more than a tenth early.
    interval = touch_interval_s(make(session_idle_timeout_s=idle_s))
    assert 0 < interval <= idle_s / 10
    assert interval <= TOUCH_INTERVAL_S


def test_a_long_idle_timeout_keeps_the_write_rare() -> None:
    assert touch_interval_s(make(session_idle_timeout_s=7200)) == TOUCH_INTERVAL_S


def request(origin: str | None) -> Request:
    headers = [(b"origin", origin.encode())] if origin is not None else []
    return Request({"type": "http", "method": "POST", "headers": headers})


@pytest.mark.parametrize(
    "public_url",
    ["https://Triage.Example.com", "https://triage.example.com:443", "HTTPS://TRIAGE.EXAMPLE.COM/"],
)
def test_a_public_url_written_another_way_is_still_this_site(public_url: str) -> None:
    # Browsers send Origin in lower case, without the default port.
    check_same_origin(request("https://triage.example.com"), make(public_url=public_url))


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "https://evil.example",
        "http://triage.example.com",
        "https://triage.example.com:8443",
        "https://triage.example.com.evil.example",
        "https://triage.example.com/elsewhere",
        "https://someone@triage.example.com",
        "https://[not-an-address",
        "https://triage.example.com:99999",
    ],
)
def test_any_other_origin_is_refused(origin: str | None) -> None:
    with pytest.raises(ApiError) as refused:
        check_same_origin(request(origin), make(public_url="https://triage.example.com"))
    assert refused.value.code == "csrf_failed"
